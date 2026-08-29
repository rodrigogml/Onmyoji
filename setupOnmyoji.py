#!/usr/bin/env python3
"""Menu principal de configuração de uma instância Onmyōji."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
MANAGED_BEGIN = "# BEGIN ONMYOJI MANAGED SKILLS"
MANAGED_END = "# END ONMYOJI MANAGED SKILLS"
SYSTEM_BEGIN = "# BEGIN ONMYOJI MANAGED CODEX SETTINGS"
SYSTEM_END = "# END ONMYOJI MANAGED CODEX SETTINGS"
CATALOG_DIRECTORY = "available-skills"
ACTIVE_SKILLS_DIRECTORY = "skills"
SKILL_STATE_FILE = "onmyoji-skills.toml"
REASONING_EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"]
SANDBOXES = ["read-only", "workspace-write", "danger-full-access"]
APPROVALS = ["untrusted", "on-request", "never"]


class Ui:
    """Camada visual ANSI sem dependências e segura para saídas não interativas."""
    enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    reset = "\x1b[0m"; bold = "\x1b[1m"; dim = "\x1b[2m"
    violet = "\x1b[38;5;141m"; cyan = "\x1b[38;5;80m"; green = "\x1b[38;5;78m"; amber = "\x1b[38;5;221m"; red = "\x1b[38;5;203m"; slate = "\x1b[38;5;245m"

    @classmethod
    def text(cls, value: str, *styles: str) -> str:
        return "".join(styles) + value + cls.reset if cls.enabled and styles else value

    @classmethod
    def badge(cls, label: str, state: str) -> str:
        colors = {"ready": cls.green, "active": cls.green, "warning": cls.amber, "inactive": cls.slate, "danger": cls.red}
        return cls.text(f" {label} ", cls.bold, colors[state])


def prompt(label: str) -> str:
    return input(Ui.text(f"› {label}", Ui.violet, Ui.bold))


def result(ok: bool, message: str) -> None:
    label, color = ("+ OK", Ui.green) if ok else ("! ERRO", Ui.red)
    print("\n    " + Ui.text(label, Ui.bold, color) + "  " + Ui.text(message, color))


def note(message: str) -> None:
    print("    " + Ui.text("›", Ui.cyan, Ui.bold) + "  " + Ui.text(message, Ui.slate))


@dataclass(frozen=True)
class Skill:
    identifier: str
    title: str
    script: Path
    description: str


def discover(root: Path = ROOT) -> list[Skill]:
    skills: list[Skill] = []
    for script in sorted((root / CATALOG_DIRECTORY).glob("*/setupSkill.py")):
        result = subprocess.run([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "describe", "--json"], text=True, capture_output=True)
        try:
            data = json.loads(result.stdout) if result.returncode == 0 else {}
            skills.append(Skill(data["id"], data["title"], script, data.get("description", "")))
        except (json.JSONDecodeError, KeyError):
            continue
    return skills


def config_path(root: Path) -> Path: return root / "config.toml"
def system_path(root: Path) -> Path: return root / "configs" / "onmyoji-system.toml"
def skill_state_path(root: Path) -> Path: return root / "configs" / SKILL_STATE_FILE
def active_skills_path(root: Path) -> Path: return root / ACTIVE_SKILLS_DIRECTORY


def default_system() -> dict[str, object]:
    return {
        "executable": "codex",
        "model": "gpt-5.6-terra",
        "model_reasoning_effort": "medium",
        "project_directory": "",
        "sandbox_mode": "workspace-write",
        "approval_policy": "on-request",
        "additional_writable_directories": [],
    }


def load_system(root: Path) -> dict[str, object]:
    path = system_path(root)
    if not path.exists(): return default_system()
    try: raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError: return default_system()
    values = default_system()
    for key, value in raw.get("codex", {}).items():
        if key in values and isinstance(value, str): values[key] = value
        if key == "additional_writable_directories" and isinstance(value, list) and all(isinstance(item, str) for item in value): values[key] = value
    return values


def render_system(data: dict[str, object]) -> str:
    return "\n".join(["schema_version = 1", "", "[codex]", *[f"{key} = {json.dumps(data[key], ensure_ascii=False)}" for key in default_system()]]) + "\n"


def replace_block(text: str, begin: str, end: str, block: str) -> str:
    if begin in text and end in text:
        before, remainder = text.split(begin, 1); _, after = remainder.split(end, 1)
        return before.rstrip() + "\n\n" + block + after
    return text.rstrip() + ("\n\n" if text.strip() else "") + block + "\n"


def render_codex_system(data: dict[str, object]) -> str:
    lines = [SYSTEM_BEGIN, "# Gerado por setupOnmyoji.py."]
    for key in ("model", "model_reasoning_effort", "approval_policy", "sandbox_mode"): lines.append(f"{key} = {json.dumps(data[key], ensure_ascii=False)}")
    lines.extend(["", "[sandbox_workspace_write]", f"writable_roots = {json.dumps(data['additional_writable_directories'], ensure_ascii=False)}"])
    lines.append(SYSTEM_END)
    return "\n".join(lines)


def validate_system(data: dict[str, object], require_ready: bool = False) -> tuple[bool, str]:
    if not data["model"].strip(): return False, "Informe um modelo do Codex."
    if data["model_reasoning_effort"] not in REASONING_EFFORTS: return False, "Esforço de raciocínio inválido."
    if data["sandbox_mode"] not in SANDBOXES: return False, "Modo de sandbox inválido."
    if data["approval_policy"] not in APPROVALS: return False, "Política de aprovação inválida."
    executable = str(data["executable"])
    if executable and not (Path(executable).is_file() or shutil.which(executable)): return False, f"Executável Codex não encontrado: {executable}."
    project = str(data["project_directory"])
    if project and not Path(project).is_dir(): return False, f"Pasta de projeto não encontrada: {project}."
    directories = data["additional_writable_directories"]
    if not isinstance(directories, list) or not all(isinstance(directory, str) for directory in directories): return False, "Lista de diretórios adicionais inválida."
    normalized = [str(Path(directory).resolve()) for directory in directories]
    if len(normalized) != len(set(normalized)): return False, "A lista de diretórios adicionais contém caminhos repetidos."
    for directory in directories:
        if not Path(directory).is_dir(): return False, f"Diretório adicional não encontrado: {directory}."
    if require_ready and not project: return False, "Defina a pasta do projeto antes de executar o Codex."
    return True, "Configuração do Codex-CLI válida." if project else "Configuração salva; defina a pasta do projeto antes de executar o Codex."


def save_system(root: Path, data: dict[str, object]) -> tuple[bool, str]:
    valid, message = validate_system(data)
    if not valid: return False, message
    system, codex = system_path(root), config_path(root)
    old_system = system.read_text(encoding="utf-8") if system.exists() else None
    old_codex = codex.read_text(encoding="utf-8") if codex.exists() else None
    try:
        system.parent.mkdir(parents=True, exist_ok=True)
        system.write_text(render_system(data), encoding="utf-8", newline="\n")
        codex.write_text(replace_block(old_codex or "", SYSTEM_BEGIN, SYSTEM_END, render_codex_system(data)), encoding="utf-8", newline="\n")
        tomllib.loads(system.read_text(encoding="utf-8")); tomllib.loads(codex.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        if old_system is None: system.unlink(missing_ok=True)
        else: system.write_text(old_system, encoding="utf-8", newline="\n")
        if old_codex is None: codex.unlink(missing_ok=True)
        else: codex.write_text(old_codex, encoding="utf-8", newline="\n")
        return False, f"Falha ao salvar; alteração desfeita: {error}"
    return True, message


def legacy_enabled_ids(root: Path) -> set[str]:
    path = config_path(root)
    if not path.exists(): return set()
    text = path.read_text(encoding="utf-8")
    if MANAGED_BEGIN not in text or MANAGED_END not in text: return set()
    block = text.split(MANAGED_BEGIN, 1)[1].split(MANAGED_END, 1)[0]
    return {Path(line.split('"', 2)[1]).name for line in block.splitlines() if line.strip().startswith("path =") and '"' in line}


def enabled_ids(root: Path) -> set[str]:
    path = skill_state_path(root)
    if not path.exists(): return legacy_enabled_ids(root)
    try: data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError: return set()
    values = data.get("skills", {}).get("enabled", [])
    return set(values) if isinstance(values, list) and all(isinstance(value, str) for value in values) else set()


def render_enabled_state(identifiers: set[str]) -> str:
    return "\n".join(["schema_version = 1", "", "[skills]", f"enabled = {json.dumps(sorted(identifiers), ensure_ascii=False)}"]) + "\n"


def remove_legacy_skill_block(root: Path) -> None:
    path = config_path(root)
    if not path.exists(): return
    text = path.read_text(encoding="utf-8")
    if MANAGED_BEGIN not in text or MANAGED_END not in text: return
    before, remainder = text.split(MANAGED_BEGIN, 1); _, after = remainder.split(MANAGED_END, 1)
    path.write_text((before.rstrip() + "\n" + after.lstrip()).rstrip() + "\n", encoding="utf-8", newline="\n")
    tomllib.loads(path.read_text(encoding="utf-8"))


def is_managed_link(path: Path) -> bool:
    if path.is_symlink(): return True
    if os.name != "nt" or not path.exists(): return False
    return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)


def create_skill_link(source: Path, destination: Path) -> None:
    try:
        os.symlink(source, destination, target_is_directory=True)
        return
    except OSError as first_error:
        if os.name != "nt": raise first_error
    command = ["cmd.exe", "/d", "/c", "mklink", "/J", str(destination), str(source)]
    created = subprocess.run(command, text=True, capture_output=True, check=False)
    if created.returncode != 0: raise OSError(created.stderr.strip() or created.stdout.strip() or "Não foi possível criar o junction da skill.")


def remove_skill_link(path: Path) -> None:
    if not is_managed_link(path): raise OSError(f"{path} existe e não é um link gerenciado pelo Onmyōji.")
    if path.is_symlink(): path.unlink()
    else: os.rmdir(path)


def is_stale_runtime_cache(path: Path) -> bool:
    """Reconhece somente os caches .pyc que um git mv pode deixar em skills/."""
    if not path.is_dir() or is_managed_link(path): return False
    for item in path.rglob("*"):
        if item.is_dir(): continue
        if item.is_file() and item.suffix == ".pyc" and item.parent.name == "__pycache__": continue
        return False
    return True


def sync_active_skill_links(root: Path, identifiers: set[str], skills: list[Skill]) -> tuple[bool, str]:
    catalog = {skill.identifier: skill.script.parent.resolve() for skill in skills}
    unknown = identifiers - set(catalog)
    if unknown: return False, f"Skills não encontradas no catálogo: {', '.join(sorted(unknown))}."
    active = active_skills_path(root)
    try:
        active.mkdir(parents=True, exist_ok=True)
        for identifier, source in catalog.items():
            destination = active / identifier
            should_exist = identifier in identifiers
            if should_exist:
                if destination.exists() or destination.is_symlink():
                    if destination.resolve() != source:
                        if is_stale_runtime_cache(destination): shutil.rmtree(destination)
                        else: return False, f"O destino da skill {identifier} já está ocupado por um item não gerenciado."
                    if not (destination.exists() or destination.is_symlink()): create_skill_link(source, destination)
                else: create_skill_link(source, destination)
            elif destination.exists() or destination.is_symlink():
                remove_skill_link(destination)
    except OSError as error:
        return False, f"Não foi possível sincronizar as skills ativas: {error}"
    return True, "Links das skills ativas sincronizados."


def save_enabled(root: Path, identifiers: set[str], skills: list[Skill]) -> tuple[bool, str]:
    path = skill_state_path(root)
    previous = enabled_ids(root)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    ok, message = sync_active_skill_links(root, identifiers, skills)
    if not ok: return False, message
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_enabled_state(identifiers), encoding="utf-8", newline="\n")
        tomllib.loads(path.read_text(encoding="utf-8"))
        remove_legacy_skill_block(root)
    except (OSError, tomllib.TOMLDecodeError) as error:
        sync_active_skill_links(root, previous, skills)
        if old is None: path.unlink(missing_ok=True)
        else: path.write_text(old, encoding="utf-8", newline="\n")
        return False, f"Não foi possível salvar o estado das skills: {error}"
    return True, "Configuração atualizada e links das skills sincronizados."


def ensure_skill_state(root: Path, skills: list[Skill]) -> tuple[bool, str]:
    if skill_state_path(root).exists(): return sync_active_skill_links(root, enabled_ids(root), skills)
    return save_enabled(root, legacy_enabled_ids(root), skills)


def invoke(skill: Skill, root: Path) -> None:
    subprocess.run([sys.executable, str(skill.script), "--onmyoji-root", str(root), "--action", "configure"], check=False)


def screen(title: str, subtitle: str = "") -> None:
    width = max(56, min(shutil.get_terminal_size((78, 24)).columns, 96))
    print("\n" + Ui.text("╭" + "─" * (width - 2) + "╮", Ui.violet))
    heading = f" ONMYŌJI  /  {title.upper()}"
    print("│" + Ui.text(heading.ljust(width - 2), Ui.bold, Ui.violet) + "│")
    if subtitle: print("│ " + Ui.text(subtitle[:width - 4].ljust(width - 4), Ui.slate) + " │")
    print(Ui.text("╰" + "─" * (width - 2) + "╯", Ui.violet))


def item(key: str, label: str, value: str = "") -> None:
    shortcut = Ui.text(f"{key:>4}", Ui.bold, Ui.cyan)
    print(f"  {shortcut}  {label:<30}" + (f"   {Ui.text(value, Ui.slate)}" if value else ""))


def skill_item(index: int, skill: Skill, state: str) -> None:
    """Renderiza a linha da skill na grade fixa do menu principal."""
    option = Ui.text(f"{index}.".rjust(4), Ui.bold, Ui.cyan)
    name = skill.title
    value_column = 48
    prefix_width = 8  # margem, opção de quatro caracteres e dois espaços
    fill_width = max(1, value_column - prefix_width - len(name))
    filler = "." * fill_width if index % 2 == 0 else " " * fill_width
    print(f"  {option}  {name}{Ui.text(filler, Ui.slate)}{state}")


def choose(label: str, values: list[str], current: str) -> str | None:
    screen(label, "Escolha uma opção ou pressione X para voltar")
    for index, value in enumerate(values, 1): item(f"{index}.", value)
    item("X.", "Voltar")
    while True:
        choice = prompt(f"Opção [{current}]: ").strip().casefold()
        if choice in {"x", "\x1b"}: return None
        if not choice: return current
        if choice.isdigit() and 1 <= int(choice) <= len(values): return values[int(choice) - 1]
        result(False, "Opção inválida.")


def update_system(root: Path, data: dict[str, object], key: str, value: object) -> dict[str, object]:
    candidate = dict(data); candidate[key] = value
    ok, message = save_system(root, candidate)
    result(ok, message if ok else f"Não salvo: {message}")
    return candidate if ok else data


def manage_writable_directories(root: Path, data: dict[str, object]) -> dict[str, object]:
    while True:
        directories = list(data["additional_writable_directories"])
        screen("Diretórios adicionais de escrita", "Eles são adicionados ao sandbox além da pasta do projeto")
        if directories:
            print("  Permitidos:")
            for index, directory in enumerate(directories, 1): print(f"    {index}. {directory}")
        else:
            print("  Nenhum diretório adicional configurado.")
        print()
        item("1.", "Adicionar diretório")
        item("2.", "Remover diretório selecionado")
        item("3.", "Remover todos os diretórios")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return data
        if choice == "1":
            value = prompt("Diretório a permitir escrita [X cancela]: ").strip()
            if value.casefold() in {"x", "\x1b"}: continue
            if not value: result(False, "Nenhum caminho informado."); continue
            candidate = str(Path(value).expanduser())
            if candidate in directories: result(False, "Esse diretório já está configurado."); continue
            data = update_system(root, data, "additional_writable_directories", [*directories, candidate])
        elif choice == "2":
            if not directories: result(False, "Não há diretórios para remover."); continue
            selected = prompt("Número do diretório [X cancela]: ").strip().casefold()
            if selected in {"x", "\x1b"}: continue
            if not selected.isdigit() or not 1 <= int(selected) <= len(directories): result(False, "Seleção inválida."); continue
            removed = directories[int(selected) - 1]
            data = update_system(root, data, "additional_writable_directories", [item for item in directories if item != removed])
        elif choice == "3":
            if not directories: result(False, "Não há diretórios para remover."); continue
            confirmation = prompt("Digite REMOVER para apagar toda a lista: ").strip()
            if confirmation == "REMOVER": data = update_system(root, data, "additional_writable_directories", [])
            else: result(False, "Operação cancelada.")
        else: result(False, "Opção inválida.")


def launch_codex(root: Path, data: dict[str, object], login: bool = False) -> None:
    ready, message = validate_system(data, require_ready=not login)
    if not ready: result(False, f"Não iniciado: {message}"); return
    if not login:
        synced, sync_message = sync_active_skill_links(root, enabled_ids(root), discover(root))
        if not synced: result(False, f"Não iniciado: {sync_message}"); return
    command = [str(data["executable"]), "login"] if login else [
        str(data["executable"]), "-C", str(data["project_directory"]), "-m", str(data["model"]),
        "-c", f"model_reasoning_effort = {json.dumps(data['model_reasoning_effort'])}",
        "-s", str(data["sandbox_mode"]), "-a", str(data["approval_policy"]),
    ]
    if not login:
        for directory in data["additional_writable_directories"]:
            command.extend(["--add-dir", str(directory)])
    environment = dict(os.environ); environment["CODEX_HOME"] = str(root)
    try: subprocess.run(command, cwd=str(data["project_directory"]) if not login else root, env=environment, check=False)
    except OSError as error: result(False, f"Não foi possível iniciar o Codex-CLI: {error}")


def codex_menu(root: Path) -> None:
    data = load_system(root)
    while True:
        ready, message = validate_system(data, require_ready=True)
        screen("Codex-CLI", "Configuração da instância do Shikigami")
        item("1.", "Executável", str(data["executable"]))
        item("2.", "Modelo", str(data["model"]))
        item("3.", "Esforço de raciocínio", str(data["model_reasoning_effort"]))
        item("4.", "Pasta do projeto", str(data["project_directory"] or "(não definida)"))
        item("5.", "Sandbox", str(data["sandbox_mode"]))
        item("6.", "Política de aprovação", str(data["approval_policy"]))
        item("7.", "Diretórios adicionais de escrita", f"{len(data['additional_writable_directories'])} configurado(s)")
        item("8.", "Executar Codex-CLI", "Abre sessão interativa com todas as opções acima")
        item("9.", "Login no Codex-CLI")
        print(f"\n  Estado: {'✓ PRONTO' if ready else '○ ' + message}")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            value = prompt(f"Executável Codex [{data['executable']}]: ").strip()
            if value: data = update_system(root, data, "executable", value)
        elif choice == "2":
            value = choose("Modelo", ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "Outro (informar manualmente)"], data["model"])
            if value == "Outro (informar manualmente)": value = prompt("Modelo: ").strip()
            if value: data = update_system(root, data, "model", value)
        elif choice == "3":
            value = choose("Esforço de raciocínio", REASONING_EFFORTS, data["model_reasoning_effort"])
            if value: data = update_system(root, data, "model_reasoning_effort", value)
        elif choice == "4":
            value = prompt(f"Pasta do projeto [{data['project_directory']}]: ").strip()
            if value: data = update_system(root, data, "project_directory", value)
        elif choice == "5":
            value = choose("Sandbox", SANDBOXES, data["sandbox_mode"])
            if value: data = update_system(root, data, "sandbox_mode", value)
        elif choice == "6":
            value = choose("Política de aprovação", APPROVALS, data["approval_policy"])
            if value: data = update_system(root, data, "approval_policy", value)
        elif choice == "7": data = manage_writable_directories(root, data)
        elif choice == "8": launch_codex(root, data)
        elif choice == "9": launch_codex(root, data, login=True)
        else: result(False, "Opção inválida.")


def daemon_menu(root: Path) -> None:
    project = root / "onmyoji-daemon"
    script = project / "setupDaemon.py"
    if not script.is_file():
        result(False, "Projeto do daemon não encontrado.")
        return
    environment = dict(os.environ); source = str(project / "src")
    environment["PYTHONPATH"] = source + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")

    def command(arguments: list[str], quiet: bool = False, input_value: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(arguments, cwd=str(project), env=environment, text=True, capture_output=quiet, input=input_value)

    def lifecycle(action: str, extra: list[str] | None = None) -> bool:
        completed = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), action, *(extra or [])], quiet=True)
        result(completed.returncode == 0, (completed.stdout or completed.stderr or "Operação concluída.").strip())
        return completed.returncode == 0

    def validation() -> list[dict[str, str]]:
        completed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "validation-json"], quiet=True)
        try: return json.loads(completed.stdout)
        except json.JSONDecodeError: return []

    def show_validation() -> bool:
        checks = validation(); print()
        for check in checks:
            state = check.get("state", "error"); symbol, color = ({"ok": ("✓", Ui.green), "pending": ("○", Ui.amber), "error": ("✗", Ui.red)}).get(state, ("!", Ui.red))
            print("    " + Ui.text(symbol, Ui.bold, color) + "  " + check.get("label", "Validação") + (Ui.text(" — " + check.get("detail", ""), color) if check.get("detail") else ""))
        required = {"Daemon da instância", "Estrutura da configuração Telegram", "Perfil KeePass", "Referência do token", "Workspace do Shikigami", "Execução do agente Codex", "Conexão Telegram"}
        return bool(checks) and all(check.get("state") == "ok" for check in checks if check.get("label") in required)

    def installed() -> bool: return (root / "configs" / "daemon" / "daemon.toml").is_file()
    def service_installed() -> bool: return (root / "configs" / "daemon" / "service.json").is_file()
    def daemon_running() -> bool:
        return command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "list-services"], quiet=True).returncode == 0

    def offer_restart() -> None:
        if not daemon_running(): return
        answer = prompt("O daemon está em execução; reiniciar agora para efetivar a alteração? [S/n]: ").strip().casefold()
        if answer not in {"", "s", "sim"}: result(False, "Alteração salva; reinicie o daemon depois para aplicá-la."); return
        if service_installed(): lifecycle("service-restart")
        elif lifecycle("process-stop"): lifecycle("process-start")

    def service_running() -> bool:
        status = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "service-status"], quiet=True)
        text = (status.stdout + "\n" + status.stderr).upper()
        return "RUNNING" in text or "ACTIVE: ACTIVE" in text

    def process_menu() -> None:
        while True:
            screen("Processo do daemon", "Execução local em segundo plano; não bloqueia este configurador")
            item("1.", "Status do processo")
            item("2.", "Iniciar processo", "Segundo plano")
            item("3.", "Finalizar processo", "Parada graciosa")
            item("4.", "Forçar finalização", "Somente se a parada normal falhar")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            action = {"1": "process-status", "2": "process-start", "3": "process-stop", "4": "process-force-stop"}.get(choice)
            if not action: result(False, "Opção inválida."); continue
            if action == "process-status":
                status = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), action], quiet=True)
                try:
                    values = json.loads(status.stdout); message = f"Em execução · PID {values['pid']} · iniciado em {time.ctime(values['started_at'])}" if values.get("pid") else "Processo local parado."
                    result(True, message)
                except (json.JSONDecodeError, KeyError): result(False, (status.stdout or status.stderr).strip() or "Não foi possível consultar o processo.")
            else: lifecycle(action)

    def service_menu() -> None:
        while True:
            screen("Serviço do sistema operacional", "O daemon será iniciado e controlado pelo sistema operacional")
            if not service_installed():
                item("1.", "Instalar serviço")
                item("X.", "Voltar")
                choice = prompt("Opção: ").strip().casefold()
                if choice in {"x", "\x1b"}: return
                if choice != "1": result(False, "Opção inválida."); continue
                default_name = "Shikigami-" + "".join(part[:1].upper() + part[1:] for part in root.name.removeprefix("Onmyoji-").split())
                visible = root.name.removeprefix("Onmyoji-") or "Shikigami"
                name = prompt(f"Nome do serviço [{default_name}]: ").strip() or default_name
                description = prompt(f"Descrição [{f'Shikigami {visible} Daemon'}]: ").strip() or f"Shikigami {visible} Daemon"
                lifecycle("install-service", ["--name", name, "--description", description]); continue
            item("1.", "Status do serviço")
            item("2.", "Parar serviço" if service_running() else "Iniciar serviço")
            item("3.", "Reiniciar serviço")
            item("4.", "Forçar parada do serviço")
            item("5.", "Remover serviço")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "1": lifecycle("service-status")
            elif choice == "2": lifecycle("service-stop" if service_running() else "service-start")
            elif choice == "3": lifecycle("service-restart")
            elif choice == "4": lifecycle("service-force-stop")
            elif choice == "5":
                confirmation = prompt("Digite remover para confirmar: ").strip()
                if confirmation == "remover": lifecycle("remove-service")
                else: result(False, "Remoção cancelada; a confirmação deve ser exatamente remover.")
            else: result(False, "Opção inválida.")

    def telegram_menu() -> None:
        while True:
            telegram_path = root / "configs" / "daemon" / "services" / "telegram" / "telegram.toml"
            if not telegram_path.exists(): command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "bootstrap"])
            try:
                enabled = bool(json.loads((root / "configs" / "daemon" / "services.json").read_text(encoding="utf-8")).get("telegram", {}).get("enabled", False))
            except (OSError, ValueError): enabled = False
            try:
                telegram_settings = tomllib.loads(telegram_path.read_text(encoding="utf-8")); totp_enabled = bool(telegram_settings.get("totp", {}).get("enabled", False)); app_server_enabled = bool(telegram_settings.get("app_server", {}).get("enabled", False)); app_server_idle = int(telegram_settings.get("app_server", {}).get("idle_timeout_seconds") or 1800); voice_enabled = bool(telegram_settings.get("voice_reply", {}).get("enabled", False)); voice_profile = str(telegram_settings.get("voice_reply", {}).get("eccovox_profile") or ""); voice_auto_off = int(telegram_settings.get("voice_reply", {}).get("auto_off_minutes") or 15); outbound_media = bool(telegram_settings.get("voice_reply", {}).get("agent_outbound_media", False))
            except (OSError, tomllib.TOMLDecodeError): totp_enabled, app_server_enabled, app_server_idle, voice_enabled, voice_profile, voice_auto_off, outbound_media = False, False, 1800, False, "", 15, False
            screen("Gateway Telegram", "Credenciais permanecem exclusivamente no KeePass")
            item("1.", "Desabilitar Telegram" if enabled else "Habilitar Telegram")
            item("2.", "Configurar credencial do bot", "Perfil KeePass e referência da entrada")
            item("3.", "Testar conexão com Telegram")
            item("4.", "Parear owner", "Exige gateway em execução")
            item("5.", "Diagnóstico detalhado")
            item("6.", "Desabilitar TOTP" if totp_enabled else "Configurar e habilitar TOTP")
            item("7.", "Testar agente Codex", "Execução controlada, sem mensagem ao bot")
            item("8.", "Atualizar comandos privados", "Regrava e confirma comandos de cada owner")
            item("9.", "Desabilitar App Server" if app_server_enabled else "Habilitar App Server", "Conversa persistente por Shikigami" if app_server_enabled else "Usa codex exec por turno")
            item("10.", "Tempo de inatividade do App Server", f"{app_server_idle // 60} min · encerra somente sem turnos ativos")
            item("11.", "Configurar respostas em áudio", (f"EccoVox: {voice_profile} · auto-off: {voice_auto_off} min" if voice_enabled else "Desabilitadas"))
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "1":
                if enabled:
                    if lifecycle("disable", ["telegram"]):
                        result(True, "Telegram desabilitado; a configuração foi preservada."); offer_restart()
                else:
                    if not show_validation(): result(False, "Corrija os itens marcados antes de habilitar o gateway."); continue
                    if lifecycle("enable", ["telegram"]):
                        result(True, "Telegram habilitado; ele iniciará junto com o daemon."); offer_restart()
            elif choice == "2":
                listed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "profiles"], quiet=True)
                try: values = json.loads(listed.stdout)
                except json.JSONDecodeError: values = []
                if not values: result(False, "Nenhum perfil KeePass disponível. Configure e habilite primeiro a skill KeePass."); continue
                print()
                for index, profile in enumerate(values, 1): item(f"{index}.", profile)
                item("X.", "Voltar")
                selected = prompt("Perfil KeePass: ").strip().casefold()
                if selected in {"x", "\x1b"}: continue
                if not selected.isdigit() or not 1 <= int(selected) <= len(values): result(False, "Perfil inválido."); continue
                instance = "".join(part[:1].upper() + part[1:] for part in root.name.removeprefix("Onmyoji-").split()) or "Shikigami"
                default_entry = f"APIs/Telegram:{instance}"
                profile = values[int(selected)-1]
                while True:
                    entry = prompt(f"Entrada KeePass [{default_entry}]: ").strip() or default_entry
                    saved = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-credential", "--profile", profile, "--entry", entry], quiet=True)
                    if saved.returncode != 0: result(False, (saved.stdout or saved.stderr).strip()); break
                    checked = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "check-entry", "--profile", profile, "--entry", entry], quiet=True)
                    try: probe = json.loads(checked.stdout); exists, detail = bool(probe["exists"]), str(probe["message"])
                    except (json.JSONDecodeError, KeyError): exists, detail = False, "Não foi possível verificar a entrada no KeePass."
                    if exists:
                        result(True, detail)
                        action = prompt("Utilizar a entrada existente [U], substituir seu token [S] ou corrigir a chave [C]? [U]: ").strip().casefold() or "u"
                        if action == "u": result(True, "Credencial configurada com a entrada existente."); break
                        if action == "c": continue
                        if action != "s": result(False, "Escolha U, S ou C."); continue
                    else:
                        result(False, f"A entrada não foi encontrada ou não pôde ser lida: {detail}")
                        action = prompt("Corrigir chave [C], criar token no KeePass [N] ou configurar e incluir depois [A]? [A]: ").strip().casefold() or "a"
                        if action == "c": continue
                        if action == "a": result(True, "Referência salva; inclua o token no KeePass antes de habilitar o gateway."); break
                        if action != "n": result(False, "Escolha C, N ou A."); continue
                    token = getpass.getpass(Ui.text("› Token do bot (não será exibido): ", Ui.violet, Ui.bold))
                    written = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "write-token", "--profile", profile, "--entry", entry], quiet=True, input_value=token)
                    result(written.returncode == 0, (written.stdout or written.stderr).strip())
                    if written.returncode == 0: break
            elif choice == "3":
                tested = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "test-telegram"], quiet=True)
                result(tested.returncode == 0, (tested.stdout or tested.stderr).strip())
            elif choice == "4":
                request = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "telegram", "pair-request"], quiet=True)
                if request.returncode != 0: result(False, "Inicie o daemon e habilite o Telegram antes de parear um owner."); continue
                try: pin = json.loads(request.stdout)["pin"]
                except (json.JSONDecodeError, KeyError): result(False, "Não foi possível gerar o código de pareamento."); continue
                result(True, f"Envie /pair {pin} em uma DM para o bot. O código expira em 5 minutos e só pode ser usado uma vez.")
            elif choice == "5": show_validation()
            elif choice == "6":
                if totp_enabled:
                    changed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-totp"], quiet=True)
                    result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
                    if changed.returncode == 0: offer_restart()
                else:
                    instance = "".join(part[:1].upper() + part[1:] for part in root.name.removeprefix("Onmyoji-").split()) or "Shikigami"
                    real = prompt(f"Entrada KeePass da senha TOTP real [APIs/TelegramTOTP:{instance}:Real]: ").strip() or f"APIs/TelegramTOTP:{instance}:Real"
                    fake = prompt(f"Entrada KeePass da senha TOTP falsa [APIs/TelegramTOTP:{instance}:Fake]: ").strip() or f"APIs/TelegramTOTP:{instance}:Fake"
                    changed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-totp", "--enabled", "--real-entry", real, "--fake-entry", fake], quiet=True)
                    result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
                    if changed.returncode == 0: offer_restart()
            elif choice == "7":
                tested = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "test-agent"], quiet=True)
                result(tested.returncode == 0, (tested.stdout or tested.stderr).strip())
            elif choice == "8":
                synced = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "telegram", "sync-commands"], quiet=True)
                if synced.returncode != 0: result(False, "Inicie o gateway Telegram antes de atualizar seus comandos privados.")
                else:
                    try:
                        data = json.loads(synced.stdout); result(data.get("failed", 1) == 0, f"Comandos confirmados para {data.get('verified', 0)}/{data.get('owners', 0)} owner(s).")
                    except json.JSONDecodeError: result(False, "O Telegram não retornou a confirmação dos comandos.")
            elif choice == "9":
                changed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-app-server", "--disabled" if app_server_enabled else "--enabled"], quiet=True)
                result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
                if changed.returncode == 0: offer_restart()
            elif choice == "10":
                raw = prompt(f"Tempo sem turnos antes de encerrar o App Server, em minutos [{max(1, app_server_idle // 60)}]: ").strip()
                if raw.casefold() in {"x", "\x1b"}: continue
                try: minutes = int(raw) if raw else max(1, app_server_idle // 60)
                except ValueError: result(False, "Informe um número inteiro de minutos."); continue
                changed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-app-server", "--idle-timeout", str(minutes * 60)], quiet=True)
                result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
                if changed.returncode == 0: offer_restart()
            elif choice == "11":
                active = prompt("Habilitar respostas em áudio? [S/n]: ").strip().casefold() not in {"n", "x", "\x1b"}
                profile = prompt(f"Perfil EccoVox [{voice_profile or 'eccovox'}]: ").strip() or voice_profile or "eccovox"
                raw = prompt(f"Desligar áudio após minutos sem interação [{voice_auto_off}]: ").strip()
                try: minutes = int(raw) if raw else voice_auto_off
                except ValueError: result(False, "Informe minutos inteiros."); continue
                media = prompt(f"Permitir que o agente envie arquivos e fotos? [{'S' if outbound_media else 'n'}]: ").strip().casefold() or ("s" if outbound_media else "n")
                if active or media == "s":
                    try:
                        system = load_system(root); project_directory = Path(str(system["project_directory"])).resolve()
                        if not project_directory.is_dir(): raise ValueError("Configure primeiro a pasta de projeto no menu Codex-CLI.")
                        staging = project_directory / ".onmyoji" / "telegram" / "staging"
                    except ValueError as error: result(False, str(error)); continue
                    print()
                    note("Para áudios, anexos e respostas com arquivos, o Onmyōji precisa de uma área temporária restrita no workspace.")
                    note("O setup criará e autorizará somente este diretório, para leitura e escrita do perfil EccoVox e para o sandbox do agente:")
                    print("      " + Ui.text(str(staging), Ui.cyan))
                    note("Nada será gravado em CODEX_HOME/configs; nenhum diretório mais amplo será liberado.")
                    confirm = prompt("Aplicar essa autorização agora? [S/n]: ").strip().casefold()
                    if confirm not in {"", "s", "sim"}: result(False, "Configuração cancelada; nenhuma permissão foi alterada."); continue
                arguments = [sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-voice-reply", "--profile", profile, "--auto-off-minutes", str(minutes)] + (["--enabled"] if active else []) + (["--agent-outbound-media"] if media == "s" else [])
                changed = command(arguments, quiet=True); result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
                if changed.returncode == 0: offer_restart()
            else: result(False, "Opção inválida.")

    while True:
        status = "INSTALADO" if installed() else "NÃO INSTALADO"
        screen("Daemon", f"Supervisor e serviços da instância · {status}")
        item("1.", "Desinstalar daemon da instância" if installed() else "Instalar daemon da instância", status)
        if installed():
            item("2.", "Serviço do sistema operacional", "Instalado" if service_installed() else "Não instalado")
            if not service_installed(): item("3.", "Gerenciar processo", "Execução local em segundo plano")
            else: print("\n  " + Ui.text("O processo é gerenciado pelo serviço do sistema operacional.", Ui.slate))
            print()
            item("4.", "Gateway Telegram", "Configuração, teste e pareamento")
            item("5.", "Diagnóstico detalhado")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            if not installed(): lifecycle("install-instance")
            else:
                confirmation = prompt("ATENÇÃO: isto apagará todas as configurações, credenciais referenciadas, pareamentos, conversas e estado do daemon desta instância. Uma reinstalação exigirá configurar tudo novamente. Digite desinstalar para confirmar: ").strip()
                if confirmation == "desinstalar": lifecycle("remove-instance")
                else: result(False, "Desinstalação cancelada; a confirmação deve ser exatamente desinstalar.")
        elif installed() and choice == "2": service_menu()
        elif installed() and choice == "3" and not service_installed(): process_menu()
        elif installed() and choice == "4": telegram_menu()
        elif installed() and choice == "5": show_validation()
        else: result(False, "Opção inválida.")


def skill_menu(skill: Skill, root: Path, skills: list[Skill]) -> None:
    while True:
        enabled = skill.identifier in enabled_ids(root)
        state = "Desabilitar" if enabled else "Habilitar"
        screen(skill.title, f"Skill de integração · {'ATIVA' if enabled else 'INATIVA'}")
        if skill.description: print(f"  {skill.description}\n")
        item("1.", state)
        item("2.", "Configurar")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return
        if choice == "1":
            identifiers = enabled_ids(root)
            (identifiers.discard if enabled else identifiers.add)(skill.identifier)
            ok, message = save_enabled(root, identifiers, skills)
            result(ok, message if ok else f"{message} A alteração foi desfeita.")
        elif choice == "2": invoke(skill, root)
        else: result(False, "Opção inválida.")


def menu(skills: list[Skill], root: Path) -> int:
    while True:
        enabled = enabled_ids(root)
        screen("Central de configuração", "Onmyōji · integrações do Shikigami")
        item("A.", "Codex-CLI", "Modelo, projeto, sandbox e permissões")
        item("D.", "Daemon", "Supervisor local e serviços da instância")
        print("\n  SKILLS DE INTEGRAÇÃO")
        for index, skill in enumerate(skills, 1):
            active = skill.identifier in enabled
            state = Ui.badge("ATIVA", "active") if active else Ui.badge("inativa", "inactive")
            skill_item(index, skill, state)
        print()
        item("X.", "Sair")
        choice = prompt("Selecione uma opção: ").strip().casefold()
        if choice == "x": return 0
        if choice == "a": codex_menu(root); continue
        if choice == "d": daemon_menu(root); continue
        if choice.isdigit() and 1 <= int(choice) <= len(skills): skill_menu(skills[int(choice) - 1], root, skills)
        else: result(False, "Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--action", choices=["menu", "list", "enable", "disable", "configure"], default="menu"); parser.add_argument("--skill"); args = parser.parse_args()
    skills = discover(); by_id = {skill.identifier: skill for skill in skills}
    initialized, initialization_message = ensure_skill_state(ROOT, skills)
    if not initialized:
        result(False, initialization_message)
        return 2
    if args.action == "menu": return menu(skills, ROOT)
    if args.action == "list":
        active = enabled_ids(ROOT)
        for skill in skills: print(f"{skill.identifier}\t{'enabled' if skill.identifier in active else 'disabled'}\t{skill.description}")
        return 0
    if args.skill not in by_id: parser.error("--skill deve identificar uma skill descoberta")
    if args.action == "configure": invoke(by_id[args.skill], ROOT); return 0
    active = enabled_ids(ROOT)
    (active.add if args.action == "enable" else active.discard)(args.skill)
    ok, message = save_enabled(ROOT, active, skills); print(message)
    return 0 if ok else 2


if __name__ == "__main__": raise SystemExit(main())
