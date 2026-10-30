"""Cria uma instância local de Shikigami a partir do upstream Onmyōji."""
from __future__ import annotations

import re
import os
import json
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_UPSTREAM_URL = "https://github.com/rodrigogml/Onmyoji.git"
TEMPLATE_DIRECTORY = Path("shikigami-template")
PROTECTED_FILES = ("README.md", "AGENTS.md", ".gitignore")
WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)), *(f"LPT{number}" for number in range(1, 10))}


class BootstrapError(RuntimeError):
    """Indica que não foi possível criar uma instância com segurança."""


class Ui:
    """Camada visual compartilhada com o setup, sem dependências externas."""
    enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    reset = "\x1b[0m"; bold = "\x1b[1m"
    violet = "\x1b[38;5;141m"; cyan = "\x1b[38;5;80m"; green = "\x1b[38;5;78m"; red = "\x1b[38;5;203m"; slate = "\x1b[38;5;245m"

    @classmethod
    def text(cls, value: str, *styles: str) -> str:
        return "".join(styles) + value + cls.reset if cls.enabled and styles else value


def identifier_for(name: str) -> str:
    """Normaliza o nome exibido em um identificador seguro para diretórios."""
    normalized = unicodedata.normalize("NFKD", name.strip())
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    identifier = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    if not identifier or identifier in {".", ".."} or identifier.upper() in WINDOWS_RESERVED_NAMES:
        raise BootstrapError("Informe um nome de Shikigami válido.")
    return identifier


def directory_component_for(name: str) -> str:
    """Preserva o nome informado para a pasta, normalizando apenas espaços."""
    component = "-".join(name.strip().split())
    if not component or "/" in component or "\\" in component or component.rstrip(". ").upper() in WINDOWS_RESERVED_NAMES:
        raise BootstrapError("Informe um nome de Shikigami válido.")
    return component


def directory_name_for(value: str, label: str) -> str:
    """Aceita somente o nome de uma pasta filha da base selecionada."""
    name = value.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name or Path(name).name != name or name.rstrip(". ").upper() in WINDOWS_RESERVED_NAMES:
        raise BootstrapError(f"{label} deve conter somente o nome de uma pasta válida.")
    return name


def git(arguments: list[str], *, cwd: Path | None = None, capture: bool = False, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    """Executa Git sem depender de um shell e converte falhas em erro orientativo."""
    try:
        completed = subprocess.run(["git", *arguments], cwd=str(cwd) if cwd else None, text=True, encoding="utf-8", errors="replace", capture_output=capture, check=False)
    except OSError as error:
        raise BootstrapError("Git não está disponível. Instale o Git e execute novamente.") from error
    if completed.returncode and not allow_failure:
        detail = (completed.stderr or completed.stdout or "falha sem detalhes").strip()
        raise BootstrapError(f"Git não concluiu {' '.join(arguments)}: {detail}")
    return completed


def require_new_directory(path: Path, label: str) -> None:
    """Impede que a instalação sobrescreva diretórios existentes ou caminhos inválidos."""
    if path.exists():
        raise BootstrapError(f"{label} já existe: {path}")
    if not path.parent.is_dir():
        raise BootstrapError(f"A pasta pai de {label} não existe: {path.parent}")


def remote_is_empty(remote_url: str) -> bool:
    """Confirma que o origin informado não contém branches ou tags."""
    completed = git(["ls-remote", "--heads", "--tags", remote_url], capture=True)
    return not completed.stdout.strip()


def copy_template(template: Path, destination: Path, replacements: dict[str, str]) -> None:
    """Projeta os arquivos básicos da instância preservando a estrutura do template."""
    if not template.is_dir():
        raise BootstrapError(f"Template da instância não foi encontrado: {template}")
    for source in sorted(template.rglob("*")):
        if source.is_symlink():
            raise BootstrapError(f"O template não pode conter links simbólicos: {source}")
        relative = source.relative_to(template)
        target = destination / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        content = source.read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def configure_merge_protection(repository: Path) -> None:
    """Mantém os arquivos de identidade locais quando a instância recebe o upstream."""
    driver = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
    git(["config", "merge.shikigami-local.name", "Preserva arquivos locais do Shikigami"], cwd=repository)
    git(["config", "merge.shikigami-local.driver", driver], cwd=repository)
    attributes = repository / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    marker = "# Arquivos de identidade preservados pelo bootstrap do Shikigami."
    existing = attributes.read_text(encoding="utf-8") if attributes.exists() else ""
    retained = [line for line in existing.splitlines() if line not in {marker, *(f"{path} merge=shikigami-local" for path in PROTECTED_FILES)}]
    retained.extend([marker, *(f"{path} merge=shikigami-local" for path in PROTECTED_FILES)])
    attributes.write_text("\n".join(retained).strip() + "\n", encoding="utf-8")


def configure_identity(repository: Path, ask: Callable[[str], str]) -> None:
    """Garante identidade Git local para o commit inicial da instância."""
    for field, label in (("user.name", "Nome do autor Git"), ("user.email", "E-mail do autor Git")):
        current = git(["config", "--get", field], cwd=repository, capture=True, allow_failure=True).stdout.strip()
        if current:
            continue
        value = ask(f"{label}: ").strip()
        if not value:
            raise BootstrapError(f"{label} é obrigatório para registrar a instância.")
        git(["config", field, value], cwd=repository)


def write_initial_configuration(repository: Path, name: str) -> None:
    """Registra a identidade escolhida para que o setup a ofereça como padrão."""
    path = repository / "configs" / "onmyoji-system.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(("schema_version = 1", "", "[shikigami]", f"identity = {json.dumps(name, ensure_ascii=False)}", "installed = false", ""))
    path.write_text(content, encoding="utf-8", newline="\n")


def create_instance(name: str, instance_directory: Path, workspace_directory: Path, upstream_url: str, origin_url: str | None, ask: Callable[[str], str]) -> None:
    """Clona a base, aplica a identidade inicial e opcionalmente publica no origin vazio."""
    require_new_directory(instance_directory, "A pasta da instância")
    require_new_directory(workspace_directory, "A pasta de trabalho")
    if origin_url and not remote_is_empty(origin_url):
        raise BootstrapError("O repositório origin já contém conteúdo. Configure essa migração manualmente para tratar conflitos.")
    git(["clone", "--origin", "upstream", "--branch", "main", upstream_url, str(instance_directory)])
    try:
        workspace_directory.mkdir()
        copy_template(instance_directory / TEMPLATE_DIRECTORY, instance_directory, {"{{SHIKIGAMI_NAME}}": name, "{{SHIKIGAMI_ID}}": identifier_for(name)})
        write_initial_configuration(instance_directory, name)
        configure_identity(instance_directory, ask)
        configure_merge_protection(instance_directory)
        git(["add", "--", *PROTECTED_FILES], cwd=instance_directory)
        git(["commit", "-m", f"Cria a instância {name}"], cwd=instance_directory)
        if origin_url:
            git(["remote", "add", "origin", origin_url], cwd=instance_directory)
            git(["push", "-u", "origin", "main"], cwd=instance_directory)
    except Exception:
        raise


def clear_screen() -> None:
    """Limpa a tela antes de transferir a instalação para o setup da instância."""
    os.system("cls" if os.name == "nt" else "clear")


def run_instance_setup(instance_directory: Path) -> int:
    """Inicia o setup recém-clonado usando o mesmo Python do instalador."""
    setup = instance_directory / "setupOnmyoji.py"
    if not setup.is_file():
        raise BootstrapError("O setup da instância não foi encontrado após o clone.")
    try:
        completed = subprocess.run([sys.executable, str(setup)], cwd=str(instance_directory), check=False)
    except OSError as error:
        raise BootstrapError("Não foi possível iniciar o setup da nova instância.") from error
    if completed.returncode:
        raise BootstrapError(f"O setup da nova instância terminou com código {completed.returncode}.")
    return 0


def prompt_path(message: str, default: Path, ask: Callable[[str], str]) -> Path:
    """Lê um caminho, usando o destino sugerido quando a resposta é vazia."""
    value = ask(Ui.text(f"› {message} [{default}]: ", Ui.violet, Ui.bold)).strip()
    return Path(value).expanduser().resolve() if value else default.resolve()


def prompt_directory_name(message: str, default: str, base: Path, ask: Callable[[str], str]) -> Path:
    """Lê o nome de uma pasta a ser criada diretamente na pasta-base."""
    value = ask(Ui.text(f"› {message} [{default}]: ", Ui.violet, Ui.bold)).strip() or default
    return base / directory_name_for(value, message)


def interactive(ask: Callable[[str], str] = input, output: Callable[[str], None] = print) -> int:
    """Conduz a criação interativa de uma instância de Shikigami."""
    if output is print:
        print("\n  " + Ui.text("ONMYŌJI / INSTALADOR DE SHIKIGAMI", Ui.bold, Ui.violet))
        print("  " + Ui.text("Cria uma instância, seu workspace e a identidade local", Ui.slate))
    else:
        output("ONMYŌJI / INSTALADOR DE SHIKIGAMI")
    try:
        name = ask(Ui.text("› Nome do Shikigami: ", Ui.violet, Ui.bold)).strip()
        identifier_for(name)
        directory_component = directory_component_for(name)
        base = prompt_path("Pasta base", Path.cwd(), ask)
        instance = prompt_directory_name("Nome da pasta da instância", f"Shikigami-{directory_component}", base, ask)
        workspace = prompt_directory_name("Nome da pasta de trabalho", f"Shikigami-{directory_component}-Work", base, ask)
        upstream = ask(Ui.text(f"› Repositório do Onmyōji [{DEFAULT_UPSTREAM_URL}]: ", Ui.violet, Ui.bold)).strip() or DEFAULT_UPSTREAM_URL
        origin = ask(Ui.text("› Repositório remoto próprio (vazio, opcional): ", Ui.violet, Ui.bold)).strip() or None
        output("\n  " + Ui.text("Instância:", Ui.cyan, Ui.bold) + f" {instance}\n  " + Ui.text("Workspace:", Ui.cyan, Ui.bold) + f" {workspace}\n  " + Ui.text("Upstream:", Ui.cyan, Ui.bold) + f"  {upstream}\n  " + Ui.text("Origin:", Ui.cyan, Ui.bold) + f"    {origin or '(não configurado)'}")
        if ask(Ui.text("› Confirmar criação? [s/N]: ", Ui.violet, Ui.bold)).strip().lower() not in {"s", "sim"}:
            output("\n  Instalação cancelada.")
            return 0
        create_instance(name, instance, workspace, upstream, origin, ask)
        clear_screen()
        return run_instance_setup(instance)
    except BootstrapError as error:
        output("\n  " + Ui.text("!", Ui.red, Ui.bold) + f" {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(interactive())
