#!/usr/bin/env python3
"""Menu principal de configuração de uma instância Onmyōji."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
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
INSTANCE_SKILLS_DIRECTORY = "shikigami/skills"
ACTIVE_SKILLS_DIRECTORY = "skills"
DESKTOP_SKILLS_DIRECTORY = ".agents/skills"
SKILL_STATE_FILE = "onmyoji-skills.toml"
REASONING_EFFORTS = ["low", "medium", "high", "xhigh", "max", "ultra"]
CODEX_MODELS = ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
LUNA_MODELS = {"gpt-6-luna", "gpt-5.6-luna"}
MODEL_REASONING_DEFAULTS = {"gpt-6-astra": "low", "gpt-6.1-sol": "medium", "gpt-6-sol": "medium", "gpt-6-luna": "high", "gpt-5.6-sol": "medium", "gpt-5.6-terra": "medium", "gpt-5.6-luna": "high"}
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
        return cls.text(f" {label:<8} ", cls.bold, colors[state])


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
    source: Path
    description: str
    setup_script: Path | None = None
    catalog_managed: bool = True


def discover(root: Path = ROOT) -> list[Skill]:
    skills: list[Skill] = []
    for directory, catalog_managed in ((root / CATALOG_DIRECTORY, True), (root / INSTANCE_SKILLS_DIRECTORY, False)):
        for script in sorted(directory.glob("*/setupSkill.py")):
            result = subprocess.run([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "describe", "--json"], text=True, encoding="utf-8", errors="replace", capture_output=True, env={**os.environ, "PYTHONUTF8": "1"})
            try:
                data = json.loads(result.stdout) if result.returncode == 0 else {}
                skills.append(Skill(data["id"], data["title"], script.parent, data.get("description", ""), script, catalog_managed))
            except (json.JSONDecodeError, KeyError):
                continue
    instance_directory = root / INSTANCE_SKILLS_DIRECTORY
    for skill_file in sorted(instance_directory.glob("*/SKILL.md")):
        source = skill_file.parent
        if (source / "setupSkill.py").is_file():
            continue
        identifier = source.name
        title = identifier.replace("-", " ").replace("_", " ").title()
        skills.append(Skill(identifier, title, source, "Skill do Shikigami sem configuração.", None, False))
    return skills


def config_path(root: Path) -> Path: return root / "config.toml"
def system_path(root: Path) -> Path: return root / "configs" / "onmyoji-system.toml"
def skill_state_path(root: Path) -> Path: return root / "configs" / SKILL_STATE_FILE
def active_skills_path(root: Path) -> Path: return root / ACTIVE_SKILLS_DIRECTORY


def desktop_skills_path(root: Path, data: dict[str, object]) -> Path:
    return root / DESKTOP_SKILLS_DIRECTORY


def instance_identity(root: Path) -> str:
    try:
        data = tomllib.loads(system_path(root).read_text(encoding="utf-8"))
        value = data.get("shikigami", {}).get("identity", "")
        if isinstance(value, str) and value.strip(): return value.strip()
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    name = root.name
    for prefix in ("Shikigami-", "Onmyoji-", "Onmyōji-"):
        if name.startswith(prefix): return name.removeprefix(prefix).strip() or "Shikigami"
    return name.strip() or "Shikigami"


def ensure_shikigami_resources(root: Path) -> None:
    """Cria recursos versionáveis do Shikigami após a instalação inicial."""
    directory = root / "shikigami"; identity = instance_identity(root)
    directory.mkdir(parents=True, exist_ok=True)
    readme = directory / "README.md"
    if not readme.exists(): readme.write_text(f"# {identity}\n\nDefinição versionada deste Shikigami. Arquivos da base Onmyōji não são personalizados aqui; particularidades versionadas ficam nesta pasta. A identidade e o estado de execução são locais, em `../configs/`. O workspace externo é a única área para arquivos de trabalho do agente.\n", encoding="utf-8", newline="\n")
    agents = directory / "AGENTS.md"
    if not agents.exists(): agents.write_text(f"# {identity}\n\nLeia este arquivo e `README.md` antes de trabalhar nesta instância. Não altere arquivos da base Onmyōji (`available-skills/`, `onmyoji-daemon/`, `setupOnmyoji.py` e `docs/`) para personalizar {identity}; registre personalizações em `shikigami/` ou proponha melhorias genéricas ao upstream.\n\nNão armazene segredos, tokens, chaves, bancos de conversa, logs, staging ou arquivos de trabalho nesta pasta. O workspace externo é a única área de escrita para a atividade do agente.\n", encoding="utf-8", newline="\n")
    instructions = directory / "instructions.md"
    if not instructions.exists():
        model = root / "onmyoji-daemon" / "configs" / "shikigami.md.model"
        if model.exists(): shutil.copy2(model, instructions)


def default_system() -> dict[str, object]:
    return {
        "shikigami_identity": "",
        "shikigami_installed": False,
        "executable": "",
        "model": "gpt-6.1-sol",
        "model_reasoning_effort": "medium",
        "project_directory": "",
        "sandbox_mode": "workspace-write",
        "approval_policy": "on-request",
        "additional_writable_directories": [],
    }


def discover_codex_executables() -> list[str]:
    """Encontra todas as instalações locais do Codex visíveis pelo PATH atual."""
    candidates: list[str] = []

    def add(path: str | Path | None) -> None:
        if not path:
            return
        candidate = Path(path).expanduser()
        if candidate.is_file():
            resolved = str(candidate.resolve())
            if resolved.casefold() not in {value.casefold() for value in candidates}:
                candidates.append(resolved)

    names = ("codex", "codex.exe", "codex.cmd", "codex.bat")
    for name in names:
        add(shutil.which(name))
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_directory:
            continue
        directory = Path(raw_directory.strip('"'))
        for name in names:
            add(directory / name)
    return candidates


def choose_codex_executable(current: str) -> str | None:
    """Oferece executáveis encontrados e permite informar somente um caminho completo."""
    found = discover_codex_executables()
    manual = "Informar caminho completo"
    if not found:
        note("Nenhum executável Codex foi encontrado no PATH atual.")
    selected = choose("Executável Codex", [*found, manual], current or "(não configurado)")
    if selected is None:
        return None
    if selected != manual:
        return selected
    while True:
        value = prompt("Caminho completo do executável [X cancela]: ").strip()
        if value.casefold() in {"x", "\x1b"}:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            result(False, "Informe o caminho completo do executável.")
            continue
        if not path.is_file():
            result(False, f"Executável não encontrado: {path}")
            continue
        return str(path.resolve())


def choose_codex_model(current: str) -> str | None:
    """Permite selecionar um modelo conhecido ou informar outro identificador."""
    value = choose("Modelo", [*CODEX_MODELS, "Outro (informar manualmente)"], current)
    if value != "Outro (informar manualmente)":
        return value
    value = prompt("Modelo [X cancela]: ").strip()
    return None if value.casefold() in {"x", "\x1b"} else value or None


def reasoning_efforts_for_model(model: str) -> list[str]:
    """Retorna os esforços conhecidos para o modelo, sem presumir Ultra para Luna."""
    return REASONING_EFFORTS[:-1] if model in LUNA_MODELS else REASONING_EFFORTS


def preferred_reasoning_effort(model: str) -> str:
    """Escolhe a sugestão inicial documentada para cada família de modelo."""
    return MODEL_REASONING_DEFAULTS.get(model, "medium")


def choose_reasoning_effort(model: str, current: str, reset_to_model_default: bool = False) -> str | None:
    """Seleciona somente esforços compatíveis com o modelo informado."""
    values = reasoning_efforts_for_model(model)
    selected = preferred_reasoning_effort(model) if reset_to_model_default or current not in values else current
    return choose("Esforço de raciocínio", values, selected)


def diagnostic_detail(completed: subprocess.CompletedProcess[str]) -> str:
    """Resume a saída de um comando sem expor valores sensíveis em telas de diagnóstico."""
    detail = (completed.stdout or completed.stderr or "").strip()
    return re.sub(r"(?i)(token|password|secret|access[_-]?token)\s*[=:]\s*\S+", r"\1=[redacted]", detail)[-500:]


def validate_codex_executable(executable: str) -> tuple[bool, str]:
    """Confirma que o executável configurado inicia e responde à consulta de versão."""
    if not executable:
        return False, "Executável do Codex-CLI não configurado."
    try:
        completed = subprocess.run([executable, "--version"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=20)
    except OSError as error:
        return False, f"Não foi possível iniciar o executável: {error}"
    except subprocess.TimeoutExpired:
        return False, "A consulta de versão do Codex-CLI excedeu 20 segundos."
    if completed.returncode != 0:
        return False, diagnostic_detail(completed) or f"O Codex-CLI terminou com código {completed.returncode}."
    return True, diagnostic_detail(completed) or "Codex-CLI respondeu à consulta de versão."


def validate_codex_model(root: Path, data: dict[str, object]) -> tuple[bool, str]:
    """Executa uma solicitação efêmera e somente leitura com o modelo selecionado."""
    executable = str(data["executable"])
    executable_ok, executable_detail = validate_codex_executable(executable)
    if not executable_ok:
        return False, executable_detail
    model = str(data["model"]).strip()
    if not model:
        return False, "Modelo do Codex-CLI não configurado."
    effort = str(data["model_reasoning_effort"])
    if effort not in reasoning_efforts_for_model(model):
        return False, f"O esforço {effort} não é compatível com o modelo {model}."
    environment = dict(os.environ); environment["CODEX_HOME"] = str(root)
    command = [executable, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--model", model, "--config", f"model_reasoning_effort={json.dumps(effort)}", "Responda exatamente: OK"]
    try:
        completed = subprocess.run(command, cwd=str(root), env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=120)
    except OSError as error:
        return False, f"Não foi possível iniciar a validação do modelo: {error}"
    except subprocess.TimeoutExpired:
        return False, "A validação do modelo excedeu 120 segundos."
    if completed.returncode != 0:
        return False, diagnostic_detail(completed) or f"O Codex-CLI recusou o modelo com código {completed.returncode}."
    return True, f"Modelo {model} respondeu à execução de validação."


def load_system(root: Path) -> dict[str, object]:
    path = system_path(root)
    if not path.exists(): return default_system()
    try: raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError: return default_system()
    values = default_system()
    shikigami = raw.get("shikigami", {})
    if isinstance(shikigami, dict):
        if isinstance(shikigami.get("identity"), str): values["shikigami_identity"] = shikigami["identity"].strip()
        if isinstance(shikigami.get("installed"), bool): values["shikigami_installed"] = shikigami["installed"]
    for key, value in raw.get("codex", {}).items():
        if key in values and isinstance(value, str): values[key] = value
        if key == "additional_writable_directories" and isinstance(value, list) and all(isinstance(item, str) for item in value): values[key] = value
    return values


def render_system(data: dict[str, object]) -> str:
    codex_keys = tuple(key for key in default_system() if not key.startswith("shikigami_"))
    return "\n".join(["schema_version = 1", "", "[shikigami]", f"identity = {json.dumps(data['shikigami_identity'], ensure_ascii=False)}", f"installed = {str(bool(data['shikigami_installed'])).lower()}", "", "[codex]", *[f"{key} = {json.dumps(data[key], ensure_ascii=False)}" for key in codex_keys]]) + "\n"


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
    if not isinstance(data.get("shikigami_identity"), str): return False, "Nome do Shikigami inválido."
    if not isinstance(data.get("shikigami_installed"), bool): return False, "Estado de instalação do Shikigami inválido."
    if data["shikigami_installed"] and not str(data["shikigami_identity"]).strip(): return False, "Informe o nome do Shikigami antes de concluir a instalação."
    if not data["model"].strip(): return False, "Informe um modelo do Codex."
    if data["model_reasoning_effort"] not in reasoning_efforts_for_model(str(data["model"])): return False, "Esforço de raciocínio incompatível com o modelo selecionado."
    if data["sandbox_mode"] not in SANDBOXES: return False, "Modo de sandbox inválido."
    if data["approval_policy"] not in APPROVALS: return False, "Política de aprovação inválida."
    executable = str(data["executable"])
    if not executable: return False, "Configure o executável do Codex-CLI."
    if not (Path(executable).is_file() or shutil.which(executable)): return False, f"Executável Codex não encontrado: {executable}."
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


def save_system(root: Path, data: dict[str, object], validate: bool = True) -> tuple[bool, str]:
    valid, message = validate_system(data)
    if validate and not valid: return False, message
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
    return True, message if valid else "Configuração inicial salva."


def legacy_instance_identity(root: Path) -> str:
    try:
        data = tomllib.loads((root / "shikigami" / "instance.toml").read_text(encoding="utf-8"))
        value = data.get("shikigami", {}).get("identity", "")
        return " ".join(value.split()) if isinstance(value, str) else ""
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return ""


def remove_legacy_instance_file(root: Path) -> None:
    """Remove a definição legada somente depois de persistir a configuração local."""
    legacy = root / "shikigami" / "instance.toml"
    if legacy.is_file() and not legacy.is_symlink(): legacy.unlink()


def ensure_shikigami_configuration(root: Path) -> tuple[dict[str, object] | None, str]:
    """Inicializa ou migra a seção local do Shikigami sem perder a configuração Codex."""
    data, legacy = load_system(root), legacy_instance_identity(root)
    path = system_path(root)
    try: raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except tomllib.TOMLDecodeError: raw = {}
    section = raw.get("shikigami", {}) if isinstance(raw, dict) else {}
    needs_save = not isinstance(section, dict) or not isinstance(section.get("identity"), str) or not isinstance(section.get("installed"), bool)
    if legacy and not str(data["shikigami_identity"]):
        data["shikigami_identity"], data["shikigami_installed"], needs_save = legacy, True, True
    if needs_save:
        ok, message = save_system(root, data, validate=False)
        if not ok: return None, message
    if bool(data["shikigami_installed"]) and str(data["shikigami_identity"]): remove_legacy_instance_file(root)
    return data, "Configuração inicial pronta."


def suggested_project_directory(root: Path) -> Path:
    """Sugere um workspace irmão da instância, preservando seu nome base."""
    root = root.resolve()
    return root.parent / f"{root.name}-Work"


def choose_project_directory(root: Path, current: str) -> str | None:
    """Seleciona um workspace existente ou cria explicitamente a sugestão ausente."""
    default = Path(current).expanduser() if current else suggested_project_directory(root)
    value = prompt(f"Pasta do projeto [{default}]: ").strip()
    if value.casefold() in {"x", "\x1b"}:
        return None
    directory = Path(value).expanduser() if value else default
    if directory.is_dir():
        return str(directory.resolve())
    if directory.exists():
        result(False, f"O caminho existe, mas não é uma pasta: {directory}")
        return None
    create = prompt(f"A pasta {directory} não existe. Deseja criá-la? [S/n]: ").strip().casefold() or "s"
    if create != "s":
        result(False, "A pasta de trabalho não foi criada; configuração não alterada.")
        return None
    try:
        directory.mkdir()
    except OSError as error:
        result(False, f"Não foi possível criar a pasta de trabalho: {error}")
        return None
    if not directory.is_dir():
        result(False, "A pasta de trabalho não foi criada; configuração não alterada.")
        return None
    result(True, f"Pasta de trabalho criada: {directory}")
    return str(directory.resolve())


def configure_initial_codex(root: Path, data: dict[str, object]) -> dict[str, object] | None:
    """Exige um executável e um modelo do Codex funcionais antes de concluir a instalação."""
    while True:
        screen("Instalação inicial", "Codex-CLI")
        note("Selecione o executável e valide o modelo com uma chamada efêmera e somente leitura.")
        executable = choose_codex_executable(str(data["executable"]))
        if executable is None:
            return None
        executable_ok, executable_detail = validate_codex_executable(executable)
        result(executable_ok, executable_detail)
        if not executable_ok:
            continue
        candidate = dict(data); candidate["executable"] = executable
        model = choose_codex_model(str(candidate["model"]))
        if model is None:
            return None
        candidate["model"] = model
        effort = choose_reasoning_effort(model, str(candidate["model_reasoning_effort"]), reset_to_model_default=True)
        if effort is None:
            return None
        candidate["model_reasoning_effort"] = effort
        model_ok, model_detail = validate_codex_model(root, candidate)
        result(model_ok, model_detail)
        if model_ok:
            return candidate
        next_step = choose("Validação do modelo", ["Tentar novamente", "Fazer login por dispositivo", "Fazer login no navegador"], "Tentar novamente")
        if next_step is None:
            return None
        if next_step.startswith("Fazer login"):
            launch_codex(root, candidate, login=True, device_auth=next_step.endswith("dispositivo"))


def initial_installation_wizard(root: Path, data: dict[str, object]) -> dict[str, object] | None:
    """Coleta a identidade e confirma um Codex-CLI funcional antes de instalar o Shikigami."""
    while True:
        screen("Instalação inicial", "Identidade do Shikigami")
        note("Informe o nome exibido pelo Shikigami. Digite X para cancelar.")
        raw = prompt("Nome do Shikigami: ").strip()
        if raw.casefold() in {"x", "\x1b"}: return None
        identity = " ".join(raw.split())
        if not identity:
            result(False, "Informe um nome não vazio.")
            continue
        candidate = dict(data); candidate["shikigami_identity"] = identity
        candidate = configure_initial_codex(root, candidate)
        if candidate is None:
            return None
        project_directory = choose_project_directory(root, str(candidate["project_directory"]))
        if project_directory is None:
            return None
        candidate["project_directory"] = project_directory
        candidate["shikigami_installed"] = True
        ok, message = save_system(root, candidate)
        if ok:
            remove_legacy_instance_file(root)
            return candidate
        result(False, message)


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


def desktop_skills_enabled(root: Path, data: dict[str, object]) -> bool:
    destination, source = desktop_skills_path(root, data), active_skills_path(root).resolve()
    try: return (destination.exists() or destination.is_symlink()) and is_managed_link(destination) and destination.resolve() == source
    except OSError: return False


def set_desktop_skills_enabled(root: Path, data: dict[str, object], enabled: bool) -> tuple[bool, str]:
    try:
        destination = desktop_skills_path(root, data)
        source = active_skills_path(root).resolve()
        agents = destination.parent
        exists = destination.exists() or destination.is_symlink()
        if enabled:
            if exists:
                if is_managed_link(destination) and destination.resolve() == source: return True, "Skills do Codex Desktop já estão habilitadas."
                if is_managed_link(destination): return False, "Não foi possível criar a junction skills porque .agents/skills já aponta para outro destino."
                if destination.is_dir(): return False, "Não foi possível criar a junction skills porque já existe uma pasta skills em .agents."
                if destination.is_file(): return False, "Não foi possível criar a junction skills porque já existe um arquivo skills em .agents."
                return False, "Não foi possível criar a junction skills porque .agents/skills já está ocupado por outro item."
            source.mkdir(parents=True, exist_ok=True)
            if agents.exists() and (not agents.is_dir() or is_managed_link(agents)):
                return False, "Não foi possível criar a pasta .agents porque o caminho já existe e não é uma pasta comum."
            agents.mkdir(parents=True, exist_ok=True)
            create_skill_link(source, destination)
            return True, "Skills do Codex Desktop habilitadas para esta instância do Shikigami."
        if exists:
            if not is_managed_link(destination) or destination.resolve() != source:
                return False, "O destino .agents/skills não aponta para as skills desta instância e não será removido."
            remove_skill_link(destination)
        if agents.exists() and agents.is_dir() and not is_managed_link(agents):
            try: agents.rmdir()
            except OSError: return True, "Link do Codex Desktop removido; a pasta .agents foi preservada porque contém outros itens."
        return True, "Skills do Codex Desktop desabilitadas."
    except (OSError, ValueError) as error:
        return False, f"Não foi possível atualizar as skills do Codex Desktop: {error}"


def is_stale_runtime_cache(path: Path) -> bool:
    """Reconhece somente os caches .pyc que um git mv pode deixar em skills/."""
    if not path.is_dir() or is_managed_link(path): return False
    for item in path.rglob("*"):
        if item.is_dir(): continue
        if item.is_file() and item.suffix == ".pyc" and item.parent.name == "__pycache__": continue
        return False
    return True


def conflicting_skill_ids(skills: list[Skill]) -> set[str]:
    """IDs devem ser únicos porque catálogo e Shikigami compartilham skills/<id>."""
    identifiers = [skill.identifier for skill in skills]
    return {identifier for identifier in identifiers if identifiers.count(identifier) > 1}


def sync_active_skill_links(root: Path, identifiers: set[str], skills: list[Skill]) -> tuple[bool, str]:
    catalog = {skill.identifier: skill.source.resolve() for skill in skills if skill.catalog_managed}
    conflicts = conflicting_skill_ids(skills)
    enabled_conflicts = identifiers & conflicts
    if enabled_conflicts:
        return False, f"IDs de skills em conflito entre catálogo e Shikigami: {', '.join(sorted(enabled_conflicts))}. Renomeie uma delas antes de habilitá-la."
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
                if destination.resolve() == source:
                    remove_skill_link(destination)
    except OSError as error:
        return False, f"Não foi possível sincronizar as skills ativas: {error}"
    return True, "Links das skills ativas sincronizados."


def is_skill_enabled(root: Path, skill: Skill) -> bool:
    destination = active_skills_path(root) / skill.identifier
    linked = (destination.exists() or destination.is_symlink()) and destination.resolve() == skill.source.resolve()
    return skill.identifier in enabled_ids(root) and linked if skill.catalog_managed else linked


def set_instance_skill_enabled(root: Path, skill: Skill, enabled: bool) -> tuple[bool, str]:
    if skill.catalog_managed: return False, "Esta operação é exclusiva de skills próprias do Shikigami."
    if skill.identifier in conflicting_skill_ids(discover(root)):
        return False, f"O ID da skill {skill.identifier} conflita com uma skill do catálogo. Renomeie uma delas antes de habilitá-la."
    destination = active_skills_path(root) / skill.identifier
    source = skill.source.resolve()
    try:
        active_skills_path(root).mkdir(parents=True, exist_ok=True)
        if enabled:
            if destination.exists() or destination.is_symlink():
                if destination.resolve() != source: return False, f"O destino da skill {skill.identifier} já está ocupado por um item não gerenciado."
            else: create_skill_link(source, destination)
        elif destination.exists() or destination.is_symlink():
            if destination.resolve() != source: return False, f"O destino da skill {skill.identifier} não aponta para esta skill do Shikigami."
            remove_skill_link(destination)
    except OSError as error:
        return False, f"Não foi possível atualizar o link da skill: {error}"
    return True, ("Skill habilitada." if enabled else "Skill desabilitada.")


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


def invoke(skill: Skill, root: Path) -> tuple[bool, str]:
    if skill.setup_script is None:
        return False, "Esta skill não possui rotina de configuração."
    subprocess.run([sys.executable, str(skill.setup_script), "--onmyoji-root", str(root), "--action", "configure"], check=False)
    return True, "Configuração concluída."


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


def skill_item(index: int, skill: Skill, state: str, width: int = 50) -> str:
    """Renderiza a linha da skill na grade fixa do menu principal."""
    option = Ui.text(f"{index}.".rjust(4), Ui.bold, Ui.cyan)
    name = skill.title[:max(1, width - 18)]
    prefix_width = 8  # margem, opção de quatro caracteres e dois espaços
    state_width = 10  # badge: espaço externo, rótulo de até oito caracteres e espaço externo
    fill_width = max(1, width - prefix_width - state_width - len(name))
    filler = "." * fill_width if index % 2 == 0 else " " * fill_width
    return f"  {option}  {name}{Ui.text(filler, Ui.slate)}{state}"


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


def launch_codex(root: Path, data: dict[str, object], login: bool = False, device_auth: bool = False) -> None:
    """Abre o Codex CLI nativo; config.toml e --add-dir são seu contrato público."""
    ready, message = validate_system(data, require_ready=not login)
    if not ready: result(False, f"Não iniciado: {message}"); return
    if not login:
        synced, sync_message = sync_active_skill_links(root, enabled_ids(root), discover(root))
        if not synced: result(False, f"Não iniciado: {sync_message}"); return
    command = [str(data["executable"]), "login", *( ["--device-auth"] if device_auth else [] )] if login else [str(data["executable"])]
    if not login:
        for directory in data["additional_writable_directories"]: command.extend(["--add-dir", str(directory)])
    environment = dict(os.environ); environment["CODEX_HOME"] = str(root)
    try: subprocess.run(command, cwd=str(data["project_directory"]) if not login else root, env=environment, check=False)
    except OSError as error: result(False, f"Não foi possível iniciar o Codex-CLI: {error}")


def launch_onmyoji_interactive(root: Path, data: dict[str, object]) -> None:
    """Modo opcional via App Server, para testar a composição Onmyōji de developer instructions."""
    ready, message = validate_system(data, require_ready=True)
    if not ready: result(False, f"Não iniciado: {message}"); return
    synced, sync_message = sync_active_skill_links(root, enabled_ids(root), discover(root))
    if not synced: result(False, f"Não iniciado: {sync_message}"); return
    environment = dict(os.environ); environment["CODEX_HOME"] = str(root); environment["PYTHONPATH"] = str(root / "onmyoji-daemon" / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    command = [sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "interactive"]
    try: subprocess.run(command, cwd=str(data["project_directory"]), env=environment, check=False)
    except OSError as error: result(False, f"Não foi possível iniciar o console Onmyōji: {error}")


def shikigami_diagnostics(root: Path) -> list[dict[str, str]]:
    """Retorna apenas os diagnósticos básicos explicitamente definidos para a instância."""
    data = load_system(root); diagnostics: list[dict[str, str]] = []
    identity = str(data["shikigami_identity"])
    if not identity.strip():
        diagnostics.append({"state": "pending", "label": "Nome do Shikigami", "detail": "A instalação inicial ainda não definiu um nome."})
    elif identity != " ".join(identity.split()):
        diagnostics.append({"state": "error", "label": "Nome do Shikigami", "detail": "O nome contém espaços adicionais e precisa ser normalizado."})
    else:
        diagnostics.append({"state": "ok", "label": "Nome do Shikigami", "detail": identity})
    executable_ok, executable_detail = validate_codex_executable(str(data["executable"]))
    diagnostics.append({"state": "ok" if executable_ok else "error", "label": "Executável do Codex-CLI", "detail": executable_detail})
    model_ok, model_detail = validate_codex_model(root, data)
    diagnostics.append({"state": "ok" if model_ok else "error", "label": "Modelo do Codex-CLI", "detail": model_detail})
    return diagnostics


def diagnostics_menu(root: Path) -> None:
    """Exibe os resultados detalhados do diagnóstico básico da instância."""
    screen("Diagnóstico", "Estado da instalação e do Codex-CLI")
    for diagnostic in shikigami_diagnostics(root):
        state = diagnostic["state"]
        symbol, color = {"ok": ("✓", Ui.green), "pending": ("○", Ui.amber), "error": ("✗", Ui.red)}[state]
        print("  " + Ui.text(symbol, Ui.bold, color) + "  " + diagnostic["label"] + " — " + Ui.text(diagnostic["detail"], color))
    prompt("Pressione Enter para voltar: ")


def codex_menu(root: Path) -> None:
    data = load_system(root)
    while True:
        ready, message = validate_system(data, require_ready=True)
        screen("Codex-CLI", "Configuração da instância do Shikigami")
        item("1.", "Executável", str(data["executable"] or "(não configurado)"))
        item("2.", "Modelo", str(data["model"]))
        item("3.", "Esforço de raciocínio", str(data["model_reasoning_effort"]))
        item("4.", "Pasta do projeto", str(data["project_directory"] or "(não definida)"))
        item("5.", "Sandbox", str(data["sandbox_mode"]))
        item("6.", "Política de aprovação", str(data["approval_policy"]))
        item("7.", "Diretórios adicionais de escrita", f"{len(data['additional_writable_directories'])} configurado(s)")
        item("8.", "Executar Codex-CLI", "CLI nativo com modelo, sandbox e diretórios configurados")
        item("9.", "Console interativo Onmyōji", "App Server com developer instructions compostas")
        item("10.", "Login no Codex-CLI")
        print(f"\n  Estado: {'✓ PRONTO' if ready else '○ ' + message}")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            value = choose_codex_executable(str(data["executable"]))
            if value:
                valid, message = validate_codex_executable(value)
                result(valid, message)
                if valid: data = update_system(root, data, "executable", value)
        elif choice == "2":
            value = choose_codex_model(str(data["model"]))
            if value:
                effort = choose_reasoning_effort(value, str(data["model_reasoning_effort"]), reset_to_model_default=True)
                if effort is None:
                    continue
                candidate = dict(data); candidate["model"], candidate["model_reasoning_effort"] = value, effort
                valid, message = validate_codex_model(root, candidate)
                result(valid, message)
                if valid:
                    data = update_system(root, data, "model", value)
                    data = update_system(root, data, "model_reasoning_effort", effort)
        elif choice == "3":
            value = choose_reasoning_effort(str(data["model"]), str(data["model_reasoning_effort"]))
            if value:
                candidate = dict(data); candidate["model_reasoning_effort"] = value
                valid, message = validate_codex_model(root, candidate)
                result(valid, message)
                if valid: data = update_system(root, data, "model_reasoning_effort", value)
        elif choice == "4":
            value = choose_project_directory(root, str(data["project_directory"]))
            if value: data = update_system(root, data, "project_directory", value)
        elif choice == "5":
            value = choose("Sandbox", SANDBOXES, data["sandbox_mode"])
            if value: data = update_system(root, data, "sandbox_mode", value)
        elif choice == "6":
            value = choose("Política de aprovação", APPROVALS, data["approval_policy"])
            if value: data = update_system(root, data, "approval_policy", value)
        elif choice == "7": data = manage_writable_directories(root, data)
        elif choice == "8": launch_codex(root, data)
        elif choice == "9": launch_onmyoji_interactive(root, data)
        elif choice == "10":
            method = choose("Login no Codex-CLI", ["Autenticação por dispositivo (recomendado para SSH/headless)", "Navegador local (callback em localhost)"], "Autenticação por dispositivo (recomendado para SSH/headless)")
            if method: launch_codex(root, data, login=True, device_auth=method.startswith("Autenticação por dispositivo"))
        else: result(False, "Opção inválida.")


def desktop_skills_menu(root: Path) -> None:
    while True:
        data = load_system(root)
        enabled = desktop_skills_enabled(root, data)
        destination = desktop_skills_path(root, data)
        status = "ATIVA" if enabled else "inativa"
        screen("Codex Desktop", "Skills do projeto")
        item("1.", "Desabilitar" if enabled else "Habilitar")
        item("X.", "Voltar")
        print(f"\n  Instância: {root}")
        print(f"  Link: {destination}")
        print(f"  Estado: {status}")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return
        if choice == "1":
            ok, message = set_desktop_skills_enabled(root, data, not enabled)
            result(ok, message)
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
        return subprocess.run(arguments, cwd=str(project), env=environment, text=True, encoding="utf-8", errors="replace", capture_output=quiet, input=input_value)

    def lifecycle(action: str, extra: list[str] | None = None) -> bool:
        completed = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), action, *(extra or [])], quiet=True)
        result(completed.returncode == 0, (completed.stdout or completed.stderr or "Operação concluída.").strip())
        return completed.returncode == 0

    def validation() -> list[dict[str, str]]:
        completed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "validation-json"], quiet=True)
        detail = (completed.stderr or completed.stdout or "").strip()
        detail = re.sub(r"(?i)(token|password|secret|access[_-]?token)\s*[=:]\s*\S+", r"\1=[redacted]", detail)[-500:]
        if completed.returncode != 0:
            return [{"state": "error", "label": "Diagnóstico do daemon", "detail": detail or f"O comando de diagnóstico terminou com código {completed.returncode}."}]
        try: values = json.loads(completed.stdout or "[]")
        except (json.JSONDecodeError, TypeError):
            return [{"state": "error", "label": "Diagnóstico do daemon", "detail": detail or "O daemon retornou um diagnóstico inválido."}]
        return values if isinstance(values, list) else [{"state": "error", "label": "Diagnóstico do daemon", "detail": "O daemon retornou um diagnóstico em formato inválido."}]

    def show_validation() -> bool:
        checks = validation(); print()
        for check in checks:
            state = check.get("state", "error"); symbol, color = ({"ok": ("✓", Ui.green), "pending": ("○", Ui.amber), "error": ("✗", Ui.red)}).get(state, ("!", Ui.red))
            print("    " + Ui.text(symbol, Ui.bold, color) + "  " + check.get("label", "Validação") + (Ui.text(" — " + check.get("detail", ""), color) if check.get("detail") else ""))
        required = {"Daemon da instância", "Estrutura da configuração Telegram", "Perfil KeePass", "Referência do token", "Executável Codex para serviço", "Workspace do Shikigami", "Execução do agente Codex", "Conexão Telegram"}
        return bool(checks) and all(check.get("state") == "ok" for check in checks if check.get("label") in required)

    def installed() -> bool: return (root / "configs" / "daemon" / "daemon.toml").is_file()
    def service_installed() -> bool: return (root / "configs" / "daemon" / "service.json").is_file()
    def daemon_running() -> bool:
        return command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "list-services"], quiet=True).returncode == 0

    def miniapps_state() -> dict[str, object]:
        completed = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "list-services"], quiet=True)
        if completed.returncode != 0: return {}
        try:
            services = json.loads(completed.stdout)
            return next((item for item in services if item.get("name") == "mini-apps"), {})
        except (json.JSONDecodeError, TypeError, AttributeError):
            return {}

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
                default_name = "Shikigami-" + "".join(part[:1].upper() + part[1:] for part in instance_identity(root).split())
                visible = instance_identity(root)
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
            command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "bootstrap"], quiet=True)
            try:
                enabled = bool(json.loads((root / "configs" / "daemon" / "services.json").read_text(encoding="utf-8")).get("telegram", {}).get("enabled", False))
            except (OSError, ValueError): enabled = False
            try:
                telegram_settings = tomllib.loads(telegram_path.read_text(encoding="utf-8")); totp_enabled = bool(telegram_settings.get("totp", {}).get("enabled", False)); app_server_enabled = bool(telegram_settings.get("app_server", {}).get("enabled", False)); app_server_idle = int(telegram_settings.get("app_server", {}).get("idle_timeout_seconds") or 1800); voice_enabled = bool(telegram_settings.get("voice_reply", {}).get("enabled", False)); voice_profile = str(telegram_settings.get("voice_reply", {}).get("eccovox_profile") or ""); voice_auto_off = int(telegram_settings.get("voice_reply", {}).get("auto_off_minutes") or 15); outbound_media = bool(telegram_settings.get("voice_reply", {}).get("agent_outbound_media", False))
            except (OSError, tomllib.TOMLDecodeError): telegram_settings = {}; totp_enabled, app_server_enabled, app_server_idle, voice_enabled, voice_profile, voice_auto_off, outbound_media = False, False, 1800, False, "", 15, False
            agent_preferences = telegram_settings.get("agent", {}) if "telegram_settings" in locals() and isinstance(telegram_settings.get("agent", {}), dict) else {}
            owner_preferences = bool(agent_preferences.get("owner_execution_preferences", True)); owner_models = ";".join(str(value) for value in agent_preferences.get("owner_allowed_models", []) if isinstance(value, str)); owner_efforts = ";".join(str(value) for value in agent_preferences.get("owner_allowed_reasoning_efforts", []) if isinstance(value, str))
            instruction_settings = telegram_settings.get("instructions", {}) if isinstance(telegram_settings.get("instructions", {}), dict) else {}
            instruction_file = str(instruction_settings.get("shikigami_file") or "instructions.md")
            screen("Gateway Telegram", "Credenciais permanecem exclusivamente no KeePass")
            item("1.", "Desabilitar Telegram" if enabled else "Habilitar Telegram")
            item("2.", "Configurar credencial do bot", "Perfil KeePass e referência da entrada")
            item("3.", "Testar conexão com Telegram")
            item("4.", "Parear owner", "Exige gateway em execução")
            item("5.", "Diagnóstico detalhado")
            item("6.", "Desabilitar TOTP" if totp_enabled else "Configurar e habilitar TOTP")
            item("7.", "Testar agente Codex", "Execução controlada, sem mensagem ao bot")
            item("8.", "Atualizar comandos privados", "Regrava e confirma comandos de cada owner")
            item("9.", "Desabilitar App Server" if app_server_enabled else "Habilitar App Server", "Conversa persistente por Shikigami" if app_server_enabled else "Obrigatório para o Gateway Telegram")
            item("10.", "Tempo de inatividade do App Server", f"{app_server_idle // 60} min · encerra somente sem turnos ativos")
            item("11.", "Configurar respostas em áudio", (f"EccoVox: {voice_profile} · auto-off: {voice_auto_off} min" if voice_enabled else "Desabilitadas"))
            item("12.", "Preferências de execução do owner", "Habilitadas" if owner_preferences else "Desabilitadas")
            item("13.", "Instruções do Shikigami", instruction_file)
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
                instance = "".join(part[:1].upper() + part[1:] for part in instance_identity(root).split()) or "Shikigami"
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
                    instance = "".join(part[:1].upper() + part[1:] for part in instance_identity(root).split()) or "Shikigami"
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
            elif choice == "12":
                enabled = prompt(f"Permitir que o owner escolha modelo e raciocínio por conversa? [{'S' if owner_preferences else 'n'}]: ").strip().casefold() or ("s" if owner_preferences else "n")
                models = prompt(f"Modelos permitidos, separados por ; (vazio = padrão do setup) [{owner_models}]: ").strip() or owner_models
                efforts = prompt(f"Esforços permitidos, separados por ; (vazio = padrão do setup) [{owner_efforts}]: ").strip() or owner_efforts
                arguments = [sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-owner-execution-preferences", "--models", models, "--efforts", efforts] + (["--disabled"] if enabled != "s" else [])
                changed = command(arguments, quiet=True); result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
                if changed.returncode == 0: offer_restart()
            elif choice == "13":
                status = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "instructions-status"], quiet=True)
                if status.returncode != 0:
                    result(False, (status.stdout or status.stderr).strip()); continue
                try:
                    details = json.loads(status.stdout)
                    result(True, f"Composição válida. Fontes: {', '.join(details.get('sources', []))}. Baseline: {details.get('baseline_hash', '')}.")
                    local_file = Path(str(details["local_file"]))
                except (json.JSONDecodeError, KeyError):
                    result(False, "Não foi possível ler o estado das instruções."); continue
                action = prompt("Abrir instruções particulares no editor? [S/n]: ").strip().casefold()
                if action in {"", "s", "sim"}:
                    try:
                        if os.name == "nt": subprocess.run(["notepad.exe", str(local_file)], check=False)
                        else:
                            editor = os.environ.get("EDITOR")
                            if not editor: raise ValueError("Defina a variável EDITOR para abrir o arquivo neste sistema.")
                            subprocess.run([editor, str(local_file)], check=False)
                        checked = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "instructions-status"], quiet=True)
                        result(checked.returncode == 0, "Instruções salvas e validadas." if checked.returncode == 0 else (checked.stdout or checked.stderr).strip())
                        if checked.returncode == 0: note("A alteração cria uma nova conversa Codex na próxima mensagem do Telegram.")
                    except (OSError, ValueError) as error: result(False, f"Não foi possível abrir o editor: {error}")
            else: result(False, "Opção inválida.")

    def miniapps_command(*arguments: str) -> subprocess.CompletedProcess[str]:
        return command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "mini-apps", *arguments], quiet=True)

    def require_miniapps_running() -> bool:
        if not daemon_running():
            result(False, "Inicie o daemon antes de administrar Mini Apps.")
            return False
        service = miniapps_state()
        if service.get("state") == "running": return True
        if service.get("state") == "failed": result(False, "Falha ao iniciar Mini Apps: " + str(service.get("last_error") or "consulte o log do serviço"))
        elif service.get("enabled") is True: result(False, "O serviço Mini Apps está habilitado, mas parado. Reinicie o daemon ou desabilite e habilite o serviço novamente.")
        else: result(False, "O serviço Mini Apps está desabilitado. Habilite-o primeiro em 'Gateway HTTPS e confiança local'.")
        return False

    def miniapps_diagnosis() -> dict[str, object] | None:
        if not require_miniapps_running(): return None
        completed = miniapps_command("diagnose")
        if completed.returncode != 0:
            result(False, (completed.stdout or completed.stderr).strip())
            return None
        try:
            value = json.loads(completed.stdout)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            result(False, "O daemon retornou um diagnóstico Mini Apps inválido.")
            return None

    def show_miniapps_diagnosis() -> dict[str, object] | None:
        diagnosis = miniapps_diagnosis()
        if diagnosis is None: return None
        local = "PRONTO" if diagnosis.get("local_ready") else "AÇÃO NECESSÁRIA"
        public = "PRONTO" if diagnosis.get("public_ready") else "OPCIONAL / PENDENTE"
        screen("Diagnóstico Mini Apps", f"Local: {local} · Público: {public}")
        symbols = {"ok": "✓", "action": "!", "info": "i"}
        for check in diagnosis.get("checks", []):
            if not isinstance(check, dict): continue
            state = str(check.get("state") or "info")
            print(f"  {symbols.get(state, 'i')}  {check.get('label', 'Verificação')} — {check.get('detail', '')}")
            if check.get("action"): print(f"     Próximo passo: {check['action']}")
        return diagnosis

    def miniapps_local_menu() -> None:
        while True:
            service = miniapps_state(); enabled = service.get("enabled") is True
            state = "EM EXECUÇÃO" if service.get("state") == "running" else ("FALHOU" if service.get("state") == "failed" else ("PARADO" if enabled else "DESABILITADO"))
            screen("Gateway HTTPS local", "O acesso público não é necessário para usar Mini Apps neste computador")
            item("1.", "Habilitar/desabilitar gateway", state)
            item("2.", "Consultar certificados TLS locais")
            item("3.", "Instalar confiança da CA local", "Altera a confiança deste computador")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "1":
                if not service: result(False, "Não foi possível consultar o serviço; confirme que o daemon está em execução.")
                elif enabled: lifecycle("disable", ["mini-apps"])
                elif lifecycle("enable", ["mini-apps"]): lifecycle("start", ["mini-apps"])
                continue
            if not require_miniapps_running(): continue
            if choice == "2": completed = miniapps_command("tls", "status")
            elif choice == "3":
                if prompt("Digite confiar para instalar a CA no computador: ").strip() != "confiar": result(False, "Cancelado."); continue
                completed = miniapps_command("tls", "install-trust", "--confirm")
            else: result(False, "Opção inválida."); continue
            result(completed.returncode == 0, (completed.stdout or completed.stderr).strip())

    def miniapps_public_menu() -> None:
        while True:
            diagnosis = miniapps_diagnosis()
            if diagnosis is None: return
            tunnel = diagnosis.get("tunnel", {}) if isinstance(diagnosis.get("tunnel"), dict) else {}
            screen("Domínio e Cloudflare Tunnel", "Exposição pública opcional; todas as Mini Apps continuam protegidas por autenticação")
            item("1.", "Entender pré-requisitos e configurar", "Domínio, permissões Cloudflare e referência KeePass")
            item("2.", "Provisionar Tunnel e DNS", "Cria/altera recursos Cloudflare após confirmação")
            item("3.", "Consultar/iniciar/reiniciar Tunnel", "Em execução" if tunnel.get("running") else "Parado")
            item("4.", "Baixar cloudflared oficial", "Somente se o executável não estiver disponível")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "1":
                screen("Preparar acesso público", "Reúna estes dados antes de criar qualquer recurso na Cloudflare")
                print("  1. Um domínio já adicionado à Cloudflare.")
                print("  2. Um token Cloudflare guardado no KeePass, com permissão Account > Cloudflare Tunnel > Edit e Zone > DNS > Edit.")
                print("  3. Account ID e Zone ID, encontrados no painel Overview da conta e da zona.")
                print("  4. Um hostname livre dentro da zona, por exemplo apps.seudominio.com.")
                print("\n  O Onmyōji recebe somente os IDs e a referência do token no KeePass; o token nunca é solicitado nem gravado aqui.")
                if prompt("Digite configurar quando os dados estiverem prontos, ou X para voltar: ").strip().casefold() != "configurar": result(False, "Configuração pública adiada; o Gateway local continua disponível."); continue
                print("\n  Account ID identifica sua conta Cloudflare; copie-o do painel da conta.")
                account_id = prompt("Cloudflare Account ID: ").strip()
                print("\n  Zone ID identifica a zona DNS do domínio; copie-o do Overview do domínio.")
                zone_id = prompt("Cloudflare Zone ID: ").strip()
                print("\n  Hostname é o endereço que será criado no DNS, por exemplo apps.seudominio.com.")
                hostname = prompt("Hostname público: ").strip()
                print("\n  Perfil e entrada apenas apontam para o token já salvo no KeePass.")
                keepass_profile = prompt("Perfil KeePass: ").strip()
                token_entry = prompt("Entrada KeePass do token de API Cloudflare: ").strip()
                print("\n  Pressione Enter se cloudflared estiver no PATH; caso contrário, informe o caminho do executável.")
                executable = prompt("Executável cloudflared [cloudflared]: ").strip() or "cloudflared"
                values = {"account_id": account_id, "zone_id": zone_id, "hostname": hostname, "keepass_profile": keepass_profile, "token_entry": token_entry, "cloudflared_executable": executable}
                completed = miniapps_command("tunnel", "configure", "--values", json.dumps(values))
            elif choice == "2":
                if prompt("Digite provisionar para criar/atualizar Tunnel, ingress e DNS: ").strip() != "provisionar": result(False, "Cancelado."); continue
                completed = miniapps_command("tunnel", "provision", "--confirm")
            elif choice == "3":
                action = prompt("Digite iniciar, reiniciar ou pressione Enter para consultar: ").strip().casefold()
                completed = miniapps_command("tunnel", "start" if action == "iniciar" else "restart" if action == "reiniciar" else "status")
            elif choice == "4":
                if prompt("Digite baixar para obter cloudflared oficial: ").strip() != "baixar": result(False, "Cancelado."); continue
                completed = miniapps_command("tunnel", "install-cloudflared", "--confirm")
            else: result(False, "Opção inválida."); continue
            result(completed.returncode == 0, (completed.stdout or completed.stderr).strip())

    def miniapps_publications() -> None:
        if not require_miniapps_running(): return
        completed = miniapps_command("list")
        try: apps = json.loads(completed.stdout) if completed.returncode == 0 else None
        except json.JSONDecodeError: apps = None
        if not isinstance(apps, list): result(False, (completed.stdout or completed.stderr).strip()); return
        screen("Publicações Mini App", "Os arquivos permanecem no workspace; o Gateway mantém somente o registro")
        if not apps:
            print("  i  Nenhuma Mini App publicada. Crie o código no workspace e publique-o pela skill Mini Apps.")
            return
        for app in apps:
            if not isinstance(app, dict): continue
            item(str(app.get("id", ""))[:8] + ".", str(app.get("alias") or "Sem nome"), f"{app.get('state')} · {app.get('health')}")
            print(f"     URL: {app.get('url')}")
            if app.get("last_error"): print("     Atenção: a publicação registrou um erro; consulte os eventos pela skill Mini Apps.")

    def miniapps_wizard() -> None:
        screen("Assistente Mini Apps", "O acesso público é opcional; comece pelo objetivo de uso")
        item("L.", "Somente neste computador", "Gateway HTTPS local e publicação no workspace")
        item("P.", "Também em domínio público", "Cloudflare Tunnel, DNS e proteção da Mini App")
        item("X.", "Voltar")
        choice = prompt("Objetivo: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice not in {"l", "p"}: result(False, "Escolha L ou P."); return
        service = miniapps_state()
        if service.get("state") != "running":
            if prompt("O gateway local precisa estar ativo. Habilitar e iniciar agora? [S/n]: ").strip().casefold() not in {"", "s", "sim"}: result(False, "O assistente precisa do gateway ativo para continuar."); return
            if service.get("enabled") is not True and not lifecycle("enable", ["mini-apps"]): return
            lifecycle("start", ["mini-apps"])
        diagnosis = show_miniapps_diagnosis()
        if diagnosis is None: return
        if prompt("Instalar a confiança da CA local agora? [s/N]: ").strip().casefold() in {"s", "sim"}:
            completed = miniapps_command("tls", "install-trust", "--confirm")
            if completed.returncode == 0: result(True, (completed.stdout or completed.stderr).strip())
            else: result(False, "A confiança local não foi confirmada. Isso não bloqueia o uso público; tente novamente em 'Gateway HTTPS e confiança local'. " + (completed.stdout or completed.stderr).strip())
        apps = diagnosis.get("apps", {}) if isinstance(diagnosis.get("apps"), dict) else {}
        if int(apps.get("total", 0)) == 0: print("\n  Próximo passo local: crie a Mini App dentro do workspace e publique-a pela skill Mini Apps.")
        if choice == "p":
            print("\n  Agora você verá os pré-requisitos do domínio. Se ainda não tiver domínio, token ou IDs Cloudflare, escolha voltar: nenhuma configuração pública é obrigatória neste momento.")
            miniapps_public_menu()

    def miniapps_menu() -> None:
        while True:
            service = miniapps_state()
            local = "EM EXECUÇÃO" if service.get("state") == "running" else ("FALHOU" if service.get("state") == "failed" else "PENDENTE")
            screen("Mini Apps", f"Gateway local: {local} · Domínio público é opcional")
            item("1.", "Assistente de início", "Orienta a configuração local ou pública")
            item("2.", "Diagnóstico de disponibilidade", "Mostra estado e próximos passos sem expor segredos")
            print()
            item("3.", "Gateway HTTPS e confiança local")
            item("4.", "Publicações Mini App")
            print()
            item("5.", "Domínio e Cloudflare Tunnel")
            item("6.", "Estado do Tunnel")
            print()
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "1": miniapps_wizard()
            elif choice == "2": show_miniapps_diagnosis()
            elif choice == "3": miniapps_local_menu()
            elif choice == "4": miniapps_publications()
            elif choice == "5": miniapps_public_menu()
            elif choice == "6":
                diagnosis = miniapps_diagnosis()
                if diagnosis is not None:
                    result(True, json.dumps(diagnosis.get("tunnel", {}), ensure_ascii=False))
            else: result(False, "Opção inválida.")

    while True:
        status = "INSTALADO" if installed() else "NÃO INSTALADO"
        screen("Daemon", f"Supervisor e serviços da instância · {status}")
        item("1.", "Desinstalar daemon da instância" if installed() else "Instalar daemon da instância", status)
        if installed():
            item("2.", "Serviço do sistema operacional", "Instalado" if service_installed() else "Não instalado")
            if not service_installed(): item("3.", "Gerenciar processo", "Execução local em segundo plano")
            else: print("\n  " + Ui.text("O processo é gerenciado pelo serviço do sistema operacional.", Ui.slate))
            item("4.", "Diagnóstico detalhado")
            item("5.", "Reparar ambiente Python", "Dependências privadas do daemon")
            print()
            item("T.", "Gateway Telegram", "Configuração, teste e pareamento")
            item("M.", "MiniApp", "Gateway HTTPS e Cloudflare Tunnel")
            print()
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
        elif installed() and choice == "4": show_validation()
        elif installed() and choice == "5": lifecycle("install-runtime")
        elif installed() and choice == "t": telegram_menu()
        elif installed() and choice == "m": miniapps_menu()
        else: result(False, "Opção inválida.")


def skill_menu(skill: Skill, root: Path, skills: list[Skill]) -> None:
    while True:
        conflicted = skill.identifier in conflicting_skill_ids(skills)
        enabled = is_skill_enabled(root, skill)
        state = "Desabilitar" if enabled else "Habilitar"
        screen(skill.title, f"Skill de integração · {'CONFLITO' if conflicted else ('ATIVA' if enabled else 'INATIVA')}")
        if skill.description: print(f"  {skill.description}\n")
        if conflicted:
            note(f"O ID {skill.identifier} também existe no catálogo ou no Shikigami. Renomeie uma das skills antes de habilitá-la.")
        else:
            item("1.", state)
            if skill.setup_script is not None: item("2.", "Configurar")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return
        if conflicted:
            result(False, "Esta skill possui um ID em conflito e não pode ser alterada.")
        elif choice == "1":
            if skill.catalog_managed:
                identifiers = enabled_ids(root)
                (identifiers.discard if enabled else identifiers.add)(skill.identifier)
                ok, message = save_enabled(root, identifiers, skills)
            else: ok, message = set_instance_skill_enabled(root, skill, not enabled)
            result(ok, message if ok else f"{message} A alteração foi desfeita.")
        elif choice == "2" and skill.setup_script is not None:
            ok, message = invoke(skill, root)
            result(ok, message)
        else: result(False, "Opção inválida.")


def menu(skills: list[Skill], root: Path) -> int:
    while True:
        screen("Central de configuração", "Onmyōji · integrações do Shikigami")
        item("!.", "Diagnóstico", "Valida nome, executável e modelo do Codex-CLI")
        item("A.", "Codex-CLI", "Modelo, projeto, sandbox e permissões")
        item("B.", "Codex Desktop", "Habilitar ou desabilitar skills do projeto")
        item("D.", "Daemon", "Supervisor local e serviços da instância")
        integration = [skill for skill in skills if skill.catalog_managed]
        domain = [skill for skill in skills if not skill.catalog_managed]
        def rendered(index: int, skill: Skill, width: int = 50) -> str:
            if skill.identifier in conflicting_skill_ids(skills): state = Ui.badge("CONFLITO", "warning")
            else:
                active = is_skill_enabled(root, skill)
                state = Ui.badge("ATIVA", "active") if active else Ui.badge("inativa", "inactive")
            return skill_item(index, skill, state, width)
        wide = len(domain) > 0 and shutil.get_terminal_size((78, 24)).columns >= 96
        if wide:
            print("\n" + "  SKILLS DE INTEGRAÇÃO".ljust(51) + "SKILLS DE DOMÍNIO")
            rows = max(len(integration), len(domain))
            for row in range(rows):
                left = rendered(row + 1, integration[row]) if row < len(integration) else " " * 50
                right = rendered(len(integration) + row + 1, domain[row]) if row < len(domain) else ""
                print(left + right)
        else:
            print("\n  SKILLS DE INTEGRAÇÃO")
            for index, skill in enumerate(integration, 1): print(rendered(index, skill))
            if domain:
                print("\n  SKILLS DE DOMÍNIO")
                for index, skill in enumerate(domain, len(integration) + 1): print(rendered(index, skill))
        print()
        item("X.", "Sair")
        choice = prompt("Selecione uma opção: ").strip().casefold()
        if choice == "x": return 0
        if choice == "!": diagnostics_menu(root); continue
        if choice == "a": codex_menu(root); continue
        if choice == "b": desktop_skills_menu(root); continue
        if choice == "d": daemon_menu(root); continue
        if choice.isdigit() and 1 <= int(choice) <= len(skills): skill_menu(skills[int(choice) - 1], root, skills)
        else: result(False, "Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--action", choices=["menu", "list", "enable", "disable", "configure", "status"], default="menu"); parser.add_argument("--skill"); parser.add_argument("--json", action="store_true"); args = parser.parse_args()
    system, message = ensure_shikigami_configuration(ROOT)
    if system is None:
        if args.json: print(json.dumps({"ok": False, "error": {"code": "system_configuration_failed", "message": message}}, ensure_ascii=False))
        else: result(False, message)
        return 2
    if not bool(system["shikigami_installed"]):
        if args.action != "menu":
            message = "A instalação inicial do Shikigami está pendente; execute o setup sem --action para concluí-la."
            if args.json: print(json.dumps({"ok": False, "error": {"code": "initial_installation_pending", "message": message}}, ensure_ascii=False))
            else: print(message)
            return 2
        system = initial_installation_wizard(ROOT, system)
        if system is None: return 0
    ensure_shikigami_resources(ROOT)
    skills = discover(); by_id = {skill.identifier: skill for skill in skills if skill.identifier not in conflicting_skill_ids(skills)}
    initialized, initialization_message = ensure_skill_state(ROOT, skills)
    if not initialized:
        result(False, initialization_message)
        return 2
    if args.action == "menu": return menu(skills, ROOT)
    if args.action == "list":
        values = [{"id": skill.identifier, "title": skill.title, "enabled": is_skill_enabled(ROOT, skill), "description": skill.description, "conflict": skill.identifier in conflicting_skill_ids(skills)} for skill in skills]
        if args.json: print(json.dumps({"ok": True, "skills": values}, ensure_ascii=False))
        else:
            for skill in skills: print(f"{skill.identifier}\t{'enabled' if is_skill_enabled(ROOT, skill) else 'disabled'}\t{skill.description}")
        return 0
    if args.action == "status":
        if args.json: print(json.dumps({"ok": True, "skills": sorted(active) if (active := enabled_ids(ROOT)) else []}, ensure_ascii=False))
        else: print("\n".join(sorted(enabled_ids(ROOT))))
        return 0
    if args.skill in conflicting_skill_ids(skills): parser.error("--skill identifica skills em conflito entre catálogo e Shikigami; renomeie uma delas")
    if args.skill not in by_id: parser.error("--skill deve identificar uma skill descoberta")
    if args.action == "configure":
        if by_id[args.skill].setup_script is None:
            message = "Esta skill não possui rotina de configuração."
            if args.json: print(json.dumps({"ok": False, "error": {"code": "configuration_unavailable", "message": message}}, ensure_ascii=False))
            else: print(message)
            return 2
        if args.json: print(json.dumps({"ok": False, "error": {"code": "interactive_only", "message": "Use as ações não interativas declaradas pela própria skill."}}, ensure_ascii=False)); return 2
        ok, _message = invoke(by_id[args.skill], ROOT); return 0 if ok else 2
    skill = by_id[args.skill]
    if skill.catalog_managed:
        active = enabled_ids(ROOT)
        (active.add if args.action == "enable" else active.discard)(args.skill)
        ok, message = save_enabled(ROOT, active, skills)
    else: ok, message = set_instance_skill_enabled(ROOT, skill, args.action == "enable")
    print(json.dumps({"ok": ok, "skill": args.skill, "enabled": args.action == "enable", "message": message}, ensure_ascii=False) if args.json else message)
    return 0 if ok else 2


if __name__ == "__main__": raise SystemExit(main())
