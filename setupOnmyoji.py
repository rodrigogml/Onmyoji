#!/usr/bin/env python3
"""Menu principal de configuração de uma instância Onmyōji."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
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
