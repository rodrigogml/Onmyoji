#!/usr/bin/env python3
"""Menu principal de configuração de uma instância Onmyōji."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import queue
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
DAEMON_SOURCE = ROOT / "onmyoji-daemon" / "src"
if str(DAEMON_SOURCE) not in sys.path:
    sys.path.insert(0, str(DAEMON_SOURCE))
from onmyoji_daemon.codex_execution import CodexExecutionError, CodexLaunch
from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.credentials import CredentialError, KeePassCredentialResolver
SYSTEM_BEGIN = "# BEGIN ONMYOJI MANAGED CODEX SETTINGS"
SYSTEM_END = "# END ONMYOJI MANAGED CODEX SETTINGS"
INSTANCE_SKILLS_DIRECTORY = "shikigami/skills"
SHIKIGAMI_SKILLS_STATE = "configs/shikigami/skills.toml"
SANDBOXES = ["read-only", "workspace-write", "danger-full-access"]
APPROVALS = ["on-request", "never"]


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


def discover(root: Path = ROOT) -> list[Skill]:
    skills: list[Skill] = []
    directory = root / INSTANCE_SKILLS_DIRECTORY
    for script in sorted(directory.glob("*/setupSkill.py")):
        result = subprocess.run([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "describe", "--json"], text=True, encoding="utf-8", errors="replace", capture_output=True, env={**os.environ, "PYTHONUTF8": "1"})
        try:
            data = json.loads(result.stdout) if result.returncode == 0 else {}
            skills.append(Skill(data["id"], data["title"], script.parent, data.get("description", ""), script))
        except (json.JSONDecodeError, KeyError):
            continue
    instance_directory = root / INSTANCE_SKILLS_DIRECTORY
    for skill_file in sorted(instance_directory.glob("*/SKILL.md")):
        source = skill_file.parent
        if (source / "setupSkill.py").is_file():
            continue
        identifier = source.name
        title = identifier.replace("-", " ").replace("_", " ").title()
        skills.append(Skill(identifier, title, source, "Skill do Shikigami sem configuração."))
    return skills


def config_path(root: Path) -> Path: return root / "config.toml"
def system_path(root: Path) -> Path: return root / "configs" / "onmyoji-system.toml"
def skill_state_path(root: Path) -> Path: return root / SHIKIGAMI_SKILLS_STATE


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
    if not agents.exists(): agents.write_text(f"# {identity}\n\nLeia este arquivo e `README.md` antes de trabalhar nesta instância. Não altere arquivos da base Onmyōji (`onmyoji-daemon/`, `setupOnmyoji.py` e `docs/`) para personalizar {identity}; registre personalizações em `shikigami/` ou proponha melhorias genéricas ao upstream.\n\nNão armazene segredos, tokens, chaves, bancos de conversa, logs, staging ou arquivos de trabalho nesta pasta. O workspace externo é a única área de escrita para a atividade do agente.\n", encoding="utf-8", newline="\n")
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


def model_identifier(model: dict[str, object]) -> str:
    """Retorna o identificador configurável de uma entrada recebida do App Server."""
    value = model.get("model", model.get("id", ""))
    return value.strip() if isinstance(value, str) else ""


def reasoning_efforts_for_model(model: dict[str, object]) -> list[str]:
    """Extrai somente os esforços que o catálogo atual declara como compatíveis."""
    values = model.get("supportedReasoningEfforts", [])
    if not isinstance(values, list):
        return []
    return [entry["reasoningEffort"] for entry in values if isinstance(entry, dict) and isinstance(entry.get("reasoningEffort"), str)]


def preferred_reasoning_effort(model: dict[str, object]) -> str:
    """Retorna a sugestão do catálogo, quando ela também é uma opção compatível."""
    effort = model.get("defaultReasoningEffort")
    values = reasoning_efforts_for_model(model)
    return effort if isinstance(effort, str) and effort in values else (values[0] if values else "")


def catalog_model(catalog: list[dict[str, object]], identifier: str) -> dict[str, object] | None:
    """Localiza um modelo pelo identificador persistido na configuração."""
    return next((model for model in catalog if model_identifier(model) == identifier), None)


def choose_codex_model(catalog: list[dict[str, object]], current: str) -> dict[str, object] | None:
    """Permite selecionar exclusivamente modelos disponíveis para a conta atual."""
    options = [model_identifier(model) for model in catalog]
    selected = choose("Modelo", options, current if current in options else "")
    return catalog_model(catalog, selected) if selected else None


def choose_reasoning_effort(model: dict[str, object], current: str, reset_to_model_default: bool = False) -> str | None:
    """Seleciona somente esforços declarados pelo catálogo para o modelo escolhido."""
    values = reasoning_efforts_for_model(model)
    selected = preferred_reasoning_effort(model) if reset_to_model_default or current not in values else current
    return choose("Esforço de raciocínio", values, selected) if values else None


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


def validate_codex_login(root: Path, data: dict[str, object]) -> tuple[bool, str]:
    """Confirma a autenticação do CODEX_HOME da instância sem revelar credenciais."""
    executable_ok, executable_detail = validate_codex_executable(str(data["executable"]))
    if not executable_ok:
        return False, executable_detail
    environment = dict(os.environ); environment["CODEX_HOME"] = str(root)
    try:
        completed = subprocess.run([str(data["executable"]), "login", "status"], cwd=str(root), env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=20)
    except OSError as error:
        return False, f"Não foi possível consultar o login do Codex-CLI: {error}"
    except subprocess.TimeoutExpired:
        return False, "A consulta de login do Codex-CLI excedeu 20 segundos."
    if completed.returncode != 0:
        return False, diagnostic_detail(completed) or "Nenhum login ativo foi encontrado para esta instância."
    return True, diagnostic_detail(completed) or "Login do Codex-CLI confirmado."


def fetch_codex_model_catalog(root: Path, executable: str) -> tuple[list[dict[str, object]] | None, str]:
    """Consulta o catálogo visível da conta pelo protocolo JSON-RPC do App Server."""
    environment = dict(os.environ); environment["CODEX_HOME"] = str(root)
    try:
        process = subprocess.Popen([executable, "app-server"], cwd=str(root), env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    except OSError as error:
        return None, f"Não foi possível iniciar o App Server do Codex: {error}"
    assert process.stdin and process.stdout
    messages: queue.Queue[str] = queue.Queue()

    def read_output() -> None:
        for line in process.stdout:
            messages.put(line)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    def send(message: dict[str, object]) -> None:
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def receive(identifier: int) -> dict[str, object] | None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                line = messages.get(timeout=max(0.1, deadline - time.monotonic()))
                message = json.loads(line)
            except (queue.Empty, json.JSONDecodeError):
                continue
            if isinstance(message, dict) and message.get("id") == identifier:
                return message
        return None

    response: dict[str, object] | None = None
    try:
        send({"method": "initialize", "id": 0, "params": {"clientInfo": {"name": "onmyoji_setup", "title": "Onmyōji Setup", "version": "1"}}})
        if receive(0) is None:
            return None, "O App Server não confirmou a inicialização em 30 segundos."
        send({"method": "initialized", "params": {}})
        send({"method": "model/list", "id": 1, "params": {"limit": 100, "includeHidden": False}})
        response = receive(1)
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    if not response or not isinstance(response.get("result"), dict):
        return None, "O App Server não retornou o catálogo de modelos. Atualize o Codex-CLI e tente novamente."
    data = response["result"].get("data")
    if not isinstance(data, list):
        return None, "O App Server retornou um catálogo de modelos inválido."
    catalog: list[dict[str, object]] = []
    for entry in data:
        if not isinstance(entry, dict) or entry.get("hidden") is True:
            continue
        model = dict(entry)
        if model_identifier(model) and reasoning_efforts_for_model(model):
            catalog.append(model)
    if not catalog:
        return None, "A conta não possui modelos configuráveis visíveis no catálogo do Codex."
    return catalog, f"{len(catalog)} modelo(s) disponível(is) para a conta atual."


def codex_model_catalog(root: Path, data: dict[str, object]) -> tuple[list[dict[str, object]] | None, str]:
    """Retorna o catálogo atual somente depois de validar executável e autenticação."""
    logged_in, login_detail = validate_codex_login(root, data)
    if not logged_in:
        return None, login_detail
    return fetch_codex_model_catalog(root, str(data["executable"]))


def validate_codex_selection(root: Path, data: dict[str, object]) -> tuple[bool, str]:
    """Garante que modelo e raciocínio ainda pertencem ao catálogo autenticado atual."""
    catalog, detail = codex_model_catalog(root, data)
    if catalog is None:
        return False, detail
    model = catalog_model(catalog, str(data["model"]).strip())
    if model is None:
        return False, f"O modelo configurado ({data['model']}) não está disponível para a conta atual."
    effort = str(data["model_reasoning_effort"])
    if effort not in reasoning_efforts_for_model(model):
        return False, f"O esforço {effort} não é compatível com o modelo {model_identifier(model)} na conta atual."
    return True, f"Modelo {model_identifier(model)} e esforço {effort} confirmados pelo catálogo atual."


def validate_codex_model(root: Path, data: dict[str, object]) -> tuple[bool, str]:
    """Executa uma solicitação efêmera e somente leitura com o modelo selecionado."""
    selected, selection_detail = validate_codex_selection(root, data)
    if not selected:
        return False, selection_detail
    executable = str(data["executable"])
    model = str(data["model"]).strip()
    effort = str(data["model_reasoning_effort"])
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


def workspace_directory(data: dict[str, object]) -> Path | None:
    """Retorna a pasta de trabalho resolvida, quando ela foi configurada."""
    project = str(data["project_directory"]).strip()
    return Path(project).expanduser().resolve() if project else None


def resolve_writable_directory(data: dict[str, object], directory: str) -> Path | None:
    """Resolve uma raiz extra relativa à pasta de trabalho, sem alterar o texto salvo."""
    path = Path(directory).expanduser()
    if path.is_absolute():
        return path.resolve()
    workspace = workspace_directory(data)
    return (workspace / path).resolve() if workspace else None


def writable_directory_relation(root: Path, data: dict[str, object], candidate: Path, directories: list[str]) -> tuple[bool, str]:
    """Impede raízes que concederiam escrita à instância ou ao workspace por herança."""
    root = root.resolve()
    workspace = workspace_directory(data)
    protected = [("CODEX_HOME", root)]
    if workspace:
        protected.append(("pasta de trabalho", workspace))
    for label, protected_path in protected:
        if candidate == protected_path or protected_path.is_relative_to(candidate):
            return False, f"O diretório inclui {label} ou um de seus diretórios anteriores e não pode ser liberado para escrita adicional."
    for directory in directories:
        existing = resolve_writable_directory(data, directory)
        if not existing:
            continue
        if candidate == existing:
            return False, f"Esse diretório já está configurado como {directory}."
        if candidate.is_relative_to(existing):
            return False, f"Esse diretório já está coberto pela raiz configurada {directory}."
        if existing.is_relative_to(candidate):
            return True, f"A nova raiz também abrange {directory}; ela amplia a área de escrita já configurada."
    return True, ""


def is_broad_writable_directory(directory: Path) -> bool:
    """Identifica raízes que abrangem o perfil do usuário ou toda uma unidade."""
    home = Path.home().resolve()
    return directory == directory.parent or directory == home or home.is_relative_to(directory)


def validate_system(data: dict[str, object], require_ready: bool = False, root: Path | None = None) -> tuple[bool, str]:
    if not isinstance(data.get("shikigami_identity"), str): return False, "Nome do Shikigami inválido."
    if not isinstance(data.get("shikigami_installed"), bool): return False, "Estado de instalação do Shikigami inválido."
    if data["shikigami_installed"] and not str(data["shikigami_identity"]).strip(): return False, "Informe o nome do Shikigami antes de concluir a instalação."
    if not isinstance(data.get("model"), str) or not data["model"].strip(): return False, "Informe um modelo do Codex."
    if not isinstance(data.get("model_reasoning_effort"), str) or not data["model_reasoning_effort"].strip(): return False, "Informe o esforço de raciocínio do Codex."
    if data["sandbox_mode"] not in SANDBOXES: return False, "Modo de sandbox inválido."
    if data["approval_policy"] not in APPROVALS: return False, "Política de aprovação inválida."
    executable = str(data["executable"])
    if not executable: return False, "Configure o executável do Codex-CLI."
    if not (Path(executable).is_file() or shutil.which(executable)): return False, f"Executável Codex não encontrado: {executable}."
    project = str(data["project_directory"])
    if project and not Path(project).is_dir(): return False, f"Pasta de projeto não encontrada: {project}."
    directories = data["additional_writable_directories"]
    if not isinstance(directories, list) or not all(isinstance(directory, str) for directory in directories): return False, "Lista de diretórios adicionais inválida."
    normalized: list[str] = []
    for directory in directories:
        resolved = resolve_writable_directory(data, directory)
        if not resolved: return False, f"Defina a pasta do projeto antes de usar um diretório adicional relativo: {directory}."
        normalized.append(str(resolved))
        if not resolved.is_dir(): return False, f"Diretório adicional não encontrado: {directory}."
        if root:
            allowed, message = writable_directory_relation(root, data, resolved, [value for value in directories if value != directory])
            if not allowed: return False, message
    if len(normalized) != len(set(normalized)): return False, "A lista de diretórios adicionais contém caminhos repetidos."
    if require_ready and not project: return False, "Defina a pasta do projeto antes de executar o Codex."
    if require_ready and root:
        selected, selection_message = validate_codex_selection(root, data)
        if not selected: return False, selection_message
    return True, "Configuração do Codex-CLI válida." if project else "Configuração salva; defina a pasta do projeto antes de executar o Codex."


def save_system(root: Path, data: dict[str, object], validate: bool = True) -> tuple[bool, str]:
    valid, message = validate_system(data, root=root)
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


def ensure_shikigami_configuration(root: Path) -> tuple[dict[str, object] | None, str]:
    """Inicializa a seção local do Shikigami sem alterar artefatos antigos."""
    data = load_system(root)
    path = system_path(root)
    try: raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except tomllib.TOMLDecodeError: raw = {}
    section = raw.get("shikigami", {}) if isinstance(raw, dict) else {}
    needs_save = not isinstance(section, dict) or not isinstance(section.get("identity"), str) or not isinstance(section.get("installed"), bool)
    if needs_save:
        ok, message = save_system(root, data, validate=False)
        if not ok: return None, message
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


def choose_sandbox(root: Path, workspace: Path, current: str) -> str | None:
    """Explica os limites de cada sandbox antes de permitir sua seleção."""
    root, workspace = root.resolve(), workspace.resolve()
    screen("Sandbox", "Limites técnicos dos comandos gerados pelo Codex")
    note("O sistema operacional continua sendo a autoridade final sobre permissões.")
    print(f"\n  Instância / CODEX_HOME: {root}")
    print(f"  Workspace de trabalho:  {workspace}\n")
    print("  read-only")
    print(f"    Pode inspecionar arquivos, mas não editar nem executar comandos sem aprovação. Use")
    print(f"    para revisão e planejamento; nem {workspace} nem {root} recebem alterações automáticas.")
    print("\n  workspace-write  (recomendado)")
    print(f"    Pode editar {workspace} e os diretórios adicionais explicitamente liberados. Não libera")
    print(f"    escrita em {root}; caminhos protegidos, rede e ações fora dessas áreas podem exigir aprovação.")
    print("\n  danger-full-access")
    print(f"    Remove as restrições locais do sandbox. O agente pode acessar {workspace}, {root} e outros")
    print("    recursos permitidos ao usuário do sistema, inclusive rede. Use somente em ambiente controlado.\n")
    for index, value in enumerate(SANDBOXES, 1): item(f"{index}.", value)
    item("X.", "Voltar")
    while True:
        choice = prompt(f"Opção [{current}]: ").strip().casefold()
        if choice in {"x", "\x1b"}: return None
        if not choice: return current
        if choice.isdigit() and 1 <= int(choice) <= len(SANDBOXES): return SANDBOXES[int(choice) - 1]
        result(False, "Opção inválida.")


def choose_approval_policy(root: Path, workspace: Path, sandbox: str, current: str) -> str | None:
    """Explica quando o Codex interrompe a execução para pedir autorização local."""
    root, workspace = root.resolve(), workspace.resolve()
    screen("Política de aprovação", "Quando o Codex-CLI deve interromper a execução para pedir confirmação")
    note("A política não amplia o sandbox; ela apenas define como tratar ações que exigem aprovação.")
    print(f"\n  Instância / CODEX_HOME: {root}")
    print(f"  Workspace de trabalho:  {workspace}")
    print(f"  Sandbox selecionado:    {sandbox}\n")
    print("  on-request  (recomendado)")
    print(f"    Permite automaticamente apenas o que o sandbox {sandbox} já permite. Quando uma ação")
    print(f"    precisar sair desse limite — por exemplo, escrever fora de {workspace}, alterar {root}")
    print("    ou acessar recursos protegidos — o Codex-CLI pausa e mostra o pedido para decisão local.")
    print("\n  never")
    print("    Nunca mostra pedidos de aprovação. Não concede mais acesso: o Codex executa somente")
    print("    o que o sandbox já permite e ações que precisariam de autorização são recusadas ou bloqueadas.")
    print("    Use em automações sem operador acompanhando a execução.\n")
    for index, value in enumerate(APPROVALS, 1): item(f"{index}.", value)
    item("X.", "Voltar")
    while True:
        choice = prompt(f"Opção [{current}]: ").strip().casefold()
        if choice in {"x", "\x1b"}: return None
        if not choice: return current
        if choice.isdigit() and 1 <= int(choice) <= len(APPROVALS): return APPROVALS[int(choice) - 1]
        result(False, "Opção inválida.")


def configure_initial_codex(root: Path, data: dict[str, object]) -> dict[str, object] | None:
    """Configura executável, login e seleção dinâmica de modelo antes da instalação."""
    while True:
        screen("Instalação inicial", "Codex-CLI")
        note("Primeiro confirme o executável e o login; depois escolha um modelo da conta autenticada.")
        executable = choose_codex_executable(str(data["executable"]))
        if executable is None:
            return None
        executable_ok, executable_detail = validate_codex_executable(executable)
        result(executable_ok, executable_detail)
        if not executable_ok:
            continue
        candidate = dict(data); candidate["executable"] = executable
        logged_in, login_detail = validate_codex_login(root, candidate)
        result(logged_in, login_detail)
        if not logged_in:
            if not configure_codex_login(root, candidate):
                return None
            logged_in, login_detail = validate_codex_login(root, candidate)
            result(logged_in, login_detail)
            if not logged_in:
                continue
        catalog, catalog_detail = codex_model_catalog(root, candidate)
        result(catalog is not None, catalog_detail)
        if catalog is None:
            continue
        model = choose_codex_model(catalog, str(candidate["model"]))
        if model is None:
            return None
        candidate["model"] = model_identifier(model)
        effort = choose_reasoning_effort(model, str(candidate["model_reasoning_effort"]), reset_to_model_default=True)
        if effort is None:
            result(False, "O catálogo não informou esforços compatíveis para o modelo selecionado.")
            continue
        candidate["model_reasoning_effort"] = effort
        model_ok, model_detail = validate_codex_model(root, candidate)
        result(model_ok, model_detail)
        if model_ok:
            return candidate


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
        sandbox = choose_sandbox(root, Path(project_directory), str(candidate["sandbox_mode"]))
        if sandbox is None:
            return None
        candidate["sandbox_mode"] = sandbox
        approval = choose_approval_policy(root, Path(project_directory), sandbox, str(candidate["approval_policy"]))
        if approval is None:
            return None
        candidate["approval_policy"] = approval
        candidate["shikigami_installed"] = True
        ok, message = save_system(root, candidate)
        if ok:
            return candidate
        result(False, message)


def enabled_ids(root: Path, skills: list[Skill] | None = None) -> set[str]:
    discovered = skills if skills is not None else discover(root)
    path = skill_state_path(root)
    if not path.exists(): return set()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    states = data.get("skills", {})
    if not isinstance(states, dict): return set()
    known = {skill.identifier for skill in discovered}
    return {identifier for identifier, state in states.items() if identifier in known and isinstance(state, dict) and state.get("enabled") is True}


def render_skill_state(identifiers: set[str], skills: list[Skill]) -> str:
    """Gera o estado local de publicação das skills próprias do Shikigami."""
    lines = ["schema_version = 1", ""]
    for skill in sorted(skills, key=lambda item: item.identifier):
        lines.extend([f"[skills.{json.dumps(skill.identifier, ensure_ascii=False)}]", f"enabled = {'true' if skill.identifier in identifiers else 'false'}", 'mode = "managed_copy"', ""])
    return "\n".join(lines)


def conflicting_skill_ids(skills: list[Skill]) -> set[str]:
    """IDs devem ser únicos no estado local de publicação."""
    identifiers = [skill.identifier for skill in skills]
    return {identifier for identifier in identifiers if identifiers.count(identifier) > 1}


def is_skill_enabled(root: Path, skill: Skill, skills: list[Skill] | None = None) -> bool:
    return skill.identifier in enabled_ids(root, skills)


def save_enabled(root: Path, identifiers: set[str], skills: list[Skill]) -> tuple[bool, str]:
    conflicts = identifiers & conflicting_skill_ids(skills)
    if conflicts: return False, f"IDs de skills do Shikigami em conflito: {', '.join(sorted(conflicts))}. Renomeie uma delas antes de habilitá-la."
    known = {skill.identifier for skill in skills}
    unknown = identifiers - known
    if unknown: return False, f"Skills não encontradas: {', '.join(sorted(unknown))}."
    path = skill_state_path(root)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_skill_state(identifiers, skills), encoding="utf-8", newline="\n")
        tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        if old is None: path.unlink(missing_ok=True)
        else: path.write_text(old, encoding="utf-8", newline="\n")
        return False, f"Não foi possível salvar o estado das skills: {error}"
    return True, "Estado das skills atualizado."


def ensure_skill_state(root: Path, skills: list[Skill]) -> tuple[bool, str]:
    if skill_state_path(root).exists():
        try:
            data = tomllib.loads(skill_state_path(root).read_text(encoding="utf-8"))
            records = data.get("skills", {})
            if data.get("schema_version") != 1 or not isinstance(records, dict): raise ValueError("estrutura inválida")
            for identifier, state in records.items():
                if not isinstance(identifier, str) or not isinstance(state, dict) or type(state.get("enabled")) is not bool or state.get("mode") != "managed_copy": raise ValueError("registro inválido")
        except (OSError, tomllib.TOMLDecodeError, ValueError) as error: return False, f"O estado das skills é inválido: {error}"
        return True, "Estado das skills válido."
    return save_enabled(root, set(), skills)


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


def configure_mcp_password_reference(root: Path, integration: str, default_entry: str, current_profile: str = "", current_entry: str = "") -> tuple[str, str] | None:
    """Seleciona um perfil MCP e garante uma senha na entrada escolhida."""
    profiles = McpConfiguration(root).vault_profile_names()
    if not profiles:
        result(False, "Nenhum perfil de vault MCP disponível. Configure-o primeiro no menu MCP.")
        return None
    selected = current_profile if current_profile in profiles else profiles[0]
    profile = choose(f"{integration} · Perfil de vault MCP", profiles, selected)
    if profile is None:
        return None
    resolver = KeePassCredentialResolver(root)
    entry = current_entry or default_entry
    while True:
        informed = prompt(f"Entrada KeePass [{entry}]: ").strip()
        if informed.casefold() in {"x", "\x1b"}:
            return None
        entry = informed or entry
        try:
            exists = resolver.entry_exists(profile, entry)
            has_password = exists and resolver.password_exists(profile, entry)
        except CredentialError as error:
            result(False, str(error))
            return None
        if has_password:
            action = choose(f"Credencial · {integration}", ["Usar a chave já salva", "Informar nova chave e substituir", "Reinformar entrada KeePass"], "Usar a chave já salva")
            if action is None: return None
            if action.startswith("Usar"): return profile, entry
            if action.startswith("Reinformar"): continue
        else:
            verb = "salvar nesta entrada" if exists else "criar esta entrada"
            action = choose(f"Credencial · {integration}", [f"Informar a chave e {verb}", "Reinformar entrada KeePass"], f"Informar a chave e {verb}")
            if action is None: return None
            if action.startswith("Reinformar"): continue
        secret = getpass.getpass(Ui.text(f"› Chave de autenticação {integration} (não será exibida): ", Ui.violet, Ui.bold)).strip()
        if not secret:
            result(False, "A chave de autenticação é obrigatória.")
            continue
        try:
            created = resolver.save_password(profile, entry, secret)
        except CredentialError as error:
            result(False, str(error))
            return None
        result(True, "Entrada criada no cofre MCP." if created else "Chave atualizada no cofre MCP.")
        return profile, entry


def update_system(root: Path, data: dict[str, object], key: str, value: object) -> dict[str, object]:
    candidate = dict(data); candidate[key] = value
    ok, message = save_system(root, candidate)
    result(ok, message if ok else f"Não salvo: {message}")
    return candidate if ok else data


def manage_writable_directories(root: Path, data: dict[str, object]) -> dict[str, object]:
    while True:
        directories = list(data["additional_writable_directories"])
        screen("Diretórios adicionais de escrita", "Eles são adicionados ao sandbox além da pasta do projeto")
        sandbox = str(data["sandbox_mode"])
        if sandbox == "read-only":
            note("O sandbox atual é read-only: a lista é preservada, mas não permite escrita até escolher workspace-write.")
        elif sandbox == "danger-full-access":
            note("O sandbox atual é danger-full-access: a lista é preservada, mas não restringe nem amplia o acesso atual.")
        else:
            note("Cada entrada amplia a escrita do sandbox workspace-write; conceda somente as raízes estritamente necessárias.")
        if directories:
            print("  Permitidos:")
            for index, directory in enumerate(directories, 1):
                resolved = resolve_writable_directory(data, directory)
                suffix = f"  →  {resolved}" if resolved and not Path(directory).expanduser().is_absolute() else ""
                print(f"    {index}. {directory}{suffix}")
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
            resolved = resolve_writable_directory(data, candidate)
            if not resolved:
                result(False, "Defina a pasta do projeto antes de usar um diretório adicional relativo."); continue
            if not resolved.is_dir():
                result(False, f"Diretório não encontrado: {candidate}."); continue
            allowed, relation = writable_directory_relation(root, data, resolved, directories)
            if not allowed:
                result(False, relation); continue
            note(f"O Codex poderá escrever em {candidate}{f' (equivale a {resolved})' if not Path(candidate).is_absolute() else ''}.")
            confirmation = "PERMITIR AMPLA" if is_broad_writable_directory(resolved) else "PERMITIR"
            if relation:
                note(relation)
                confirmation = "AMPLIAR"
            elif confirmation == "PERMITIR AMPLA":
                note("Esta é uma raiz abrangente: ela inclui o perfil do usuário ou uma unidade inteira.")
            if prompt(f"Digite {confirmation} para confirmar: ").strip() != confirmation:
                result(False, "Operação cancelada."); continue
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


def configure_codex_login(root: Path, data: dict[str, object], device_auth: bool | None = None) -> bool:
    """Abre o fluxo local de login e confirma seu resultado antes de prosseguir."""
    if device_auth is None:
        method = choose("Login no Codex-CLI", ["Autenticação por dispositivo (recomendado para SSH/headless)", "Navegador local (callback em localhost)"], "Autenticação por dispositivo (recomendado para SSH/headless)")
        if method is None:
            return False
        device_auth = method.startswith("Autenticação por dispositivo")
    executable_ok, executable_detail = validate_codex_executable(str(data["executable"]))
    if not executable_ok:
        result(False, executable_detail)
        return False
    command = [str(data["executable"]), "login", *(["--device-auth"] if device_auth else [])]
    environment = dict(os.environ); environment["CODEX_HOME"] = str(root)
    try:
        completed = subprocess.run(command, cwd=str(root), env=environment, check=False)
    except OSError as error:
        result(False, f"Não foi possível iniciar o login do Codex-CLI: {error}")
        return False
    if completed.returncode != 0:
        result(False, f"O login do Codex-CLI terminou com código {completed.returncode}.")
        return False
    logged_in, detail = validate_codex_login(root, data)
    result(logged_in, detail)
    return logged_in


def launch_codex(root: Path, data: dict[str, object], login: bool = False, device_auth: bool = False) -> None:
    """Abre o Codex CLI nativo com o perfil de skills da instância."""
    ready, message = validate_system(data, require_ready=not login, root=root)
    if not ready: result(False, f"Não iniciado: {message}"); return
    if login:
        configure_codex_login(root, data, device_auth)
        return
    try:
        launch = CodexLaunch.from_data(root, data)
        command, workspace, environment = launch.direct_launch()
        subprocess.run(command, cwd=str(workspace), env=environment, check=False)
    except CodexExecutionError as error:
        result(False, str(error))
    except OSError as error:
        result(False, f"Não foi possível iniciar o Codex-CLI: {error}")


def launch_onmyoji_interactive(root: Path, data: dict[str, object]) -> None:
    """Modo opcional via App Server, para testar a composição Onmyōji de developer instructions."""
    ready, message = validate_system(data, require_ready=True, root=root)
    if not ready: result(False, f"Não iniciado: {message}"); return
    try:
        launch = CodexLaunch.from_data(root, data)
        command, workspace, environment = launch.interactive_launcher()
        subprocess.run(command, cwd=str(workspace), env=environment, check=False)
    except CodexExecutionError as error:
        result(False, str(error))
    except OSError as error:
        result(False, f"Não foi possível iniciar o console Onmyōji: {error}")


def writable_directories_diagnostics(root: Path, data: dict[str, object]) -> list[dict[str, str]]:
    """Valida individualmente as raízes extras e informa quando o sandbox não as aplica."""
    directories = data["additional_writable_directories"]
    if not isinstance(directories, list) or not all(isinstance(directory, str) for directory in directories):
        return [{"state": "error", "label": "Diretórios adicionais de escrita", "detail": "Lista de diretórios inválida."}]
    if not directories:
        return [{"state": "ok", "label": "Diretórios adicionais de escrita", "detail": "Nenhum diretório adicional configurado."}]
    sandbox = str(data["sandbox_mode"])
    diagnostics: list[dict[str, str]] = []
    for index, directory in enumerate(directories, 1):
        resolved = resolve_writable_directory(data, directory)
        label = f"Diretório adicional de escrita {index}"
        if not resolved:
            diagnostics.append({"state": "error", "label": label, "detail": f"A pasta de trabalho é necessária para resolver o caminho relativo: {directory}."})
            continue
        if not resolved.is_dir():
            diagnostics.append({"state": "error", "label": label, "detail": f"Diretório não encontrado: {directory}."})
            continue
        allowed, relation = writable_directory_relation(root, data, resolved, [value for position, value in enumerate(directories) if position != index - 1])
        if not allowed:
            diagnostics.append({"state": "error", "label": label, "detail": relation})
            continue
        detail = f"{directory} → {resolved}" if not Path(directory).expanduser().is_absolute() else str(resolved)
        if relation:
            detail += f". {relation}"
        if is_broad_writable_directory(resolved):
            detail += " Escopo abrangente confirmado na configuração."
        if sandbox not in SANDBOXES:
            diagnostics.append({"state": "error", "label": label, "detail": detail + f" Sandbox inválido: {sandbox or '(não configurado)'}."})
        elif sandbox == "read-only":
            diagnostics.append({"state": "pending", "label": label, "detail": detail + " O sandbox read-only não aplica escrita."})
        elif sandbox == "danger-full-access":
            diagnostics.append({"state": "pending", "label": label, "detail": detail + " O sandbox danger-full-access torna esta raiz redundante."})
        else:
            diagnostics.append({"state": "ok", "label": label, "detail": detail})
    return diagnostics


def shikigami_diagnostics(root: Path, progress=None) -> list[dict[str, str]]:
    """Retorna apenas os diagnósticos básicos explicitamente definidos para a instância."""
    def checking(label: str) -> None:
        if progress: progress(label)
    data = load_system(root); diagnostics: list[dict[str, str]] = []
    identity = str(data["shikigami_identity"])
    if not identity.strip():
        diagnostics.append({"state": "pending", "label": "Nome do Shikigami", "detail": "A instalação inicial ainda não definiu um nome."})
    elif identity != " ".join(identity.split()):
        diagnostics.append({"state": "error", "label": "Nome do Shikigami", "detail": "O nome contém espaços adicionais e precisa ser normalizado."})
    else:
        diagnostics.append({"state": "ok", "label": "Nome do Shikigami", "detail": identity})
    checking("Verificando o executável do Codex-CLI...")
    executable_ok, executable_detail = validate_codex_executable(str(data["executable"]))
    diagnostics.append({"state": "ok" if executable_ok else "error", "label": "Executável do Codex-CLI", "detail": executable_detail})
    if not executable_ok:
        for label in ("Login do Codex-CLI", "Catálogo de modelos", "Modelo do Codex-CLI", "Esforço de raciocínio", "Validação do modelo"):
            diagnostics.append({"state": "pending", "label": label, "detail": "Não verificado porque o executável do Codex-CLI não está disponível."})
    else:
        checking("Verificando o login do Codex-CLI...")
        logged_in, login_detail = validate_codex_login(root, data)
        diagnostics.append({"state": "ok" if logged_in else "error", "label": "Login do Codex-CLI", "detail": login_detail})
        if not logged_in:
            for label in ("Catálogo de modelos", "Modelo do Codex-CLI", "Esforço de raciocínio", "Validação do modelo"):
                diagnostics.append({"state": "pending", "label": label, "detail": "Não verificado porque não há login ativo no Codex-CLI."})
        else:
            checking("Consultando o catálogo de modelos da conta...")
            catalog, catalog_detail = fetch_codex_model_catalog(root, str(data["executable"]))
            diagnostics.append({"state": "ok" if catalog else "error", "label": "Catálogo de modelos", "detail": catalog_detail})
            model = catalog_model(catalog, str(data["model"])) if catalog else None
            if model is None:
                detail = "Não verificado porque o catálogo não está disponível." if catalog is None else f"O modelo configurado ({data['model']}) não está disponível para a conta atual."
                diagnostics.append({"state": "pending" if catalog is None else "error", "label": "Modelo do Codex-CLI", "detail": detail})
                diagnostics.append({"state": "pending", "label": "Esforço de raciocínio", "detail": "Não verificado porque o modelo configurado não está disponível."})
                diagnostics.append({"state": "pending", "label": "Validação do modelo", "detail": "Não executada porque a seleção de modelo não foi confirmada."})
            else:
                diagnostics.append({"state": "ok", "label": "Modelo do Codex-CLI", "detail": model_identifier(model)})
                effort = str(data["model_reasoning_effort"])
                effort_ok = effort in reasoning_efforts_for_model(model)
                diagnostics.append({"state": "ok" if effort_ok else "error", "label": "Esforço de raciocínio", "detail": effort if effort_ok else f"{effort} não é compatível com {model_identifier(model)}."})
                if effort_ok:
                    checking(f"Validando o modelo {model_identifier(model)}; esta etapa pode levar até 120 segundos...")
                    model_ok, model_detail = validate_codex_model(root, data)
                    diagnostics.append({"state": "ok" if model_ok else "error", "label": "Validação do modelo", "detail": model_detail})
                else:
                    diagnostics.append({"state": "pending", "label": "Validação do modelo", "detail": "Não executada porque o esforço de raciocínio é incompatível."})
    project_directory = str(data["project_directory"]).strip()
    if not project_directory:
        diagnostics.append({"state": "pending", "label": "Pasta de trabalho", "detail": "Nenhuma pasta de trabalho foi configurada."})
    else:
        project = Path(project_directory).expanduser()
        if project.is_dir():
            diagnostics.append({"state": "ok", "label": "Pasta de trabalho", "detail": str(project.resolve())})
        elif project.exists():
            diagnostics.append({"state": "error", "label": "Pasta de trabalho", "detail": f"O caminho existe, mas não é uma pasta: {project}"})
        else:
            diagnostics.append({"state": "error", "label": "Pasta de trabalho", "detail": f"Pasta não encontrada: {project}"})
    sandbox = str(data["sandbox_mode"])
    diagnostics.append({
        "state": "ok" if sandbox in SANDBOXES else "error",
        "label": "Sandbox",
        "detail": sandbox if sandbox in SANDBOXES else f"Modo inválido: {sandbox or '(não configurado)'}",
    })
    approval = str(data["approval_policy"])
    diagnostics.append({
        "state": "ok" if approval in APPROVALS else "error",
        "label": "Política de aprovação",
        "detail": approval if approval in APPROVALS else f"Política inválida: {approval or '(não configurada)'}",
    })
    diagnostics.extend(writable_directories_diagnostics(root, data))
    return diagnostics


def diagnostics_menu(root: Path) -> None:
    """Exibe os resultados detalhados do diagnóstico básico da instância."""
    screen("Diagnóstico", "Estado da instalação e do Codex-CLI")
    diagnostics = shikigami_diagnostics(root, lambda message: print("  → " + message))
    print()
    for diagnostic in diagnostics:
        state = diagnostic["state"]
        symbol, color = {"ok": ("✓", Ui.green), "pending": ("○", Ui.amber), "error": ("✗", Ui.red)}[state]
        print("  " + Ui.text(symbol, Ui.bold, color) + "  " + diagnostic["label"] + " — " + Ui.text(diagnostic["detail"], color))
    prompt("Pressione Enter para voltar: ")


def codex_menu(root: Path) -> None:
    data = load_system(root)
    while True:
        ready, message = validate_system(data, require_ready=True, root=root)
        screen("Codex-CLI", "Configuração da instância do Shikigami")
        item("1.", "Executável", str(data["executable"] or "(não configurado)"))
        item("2.", "Login no Codex-CLI")
        item("3.", "Modelo", str(data["model"]))
        item("4.", "Esforço de raciocínio", str(data["model_reasoning_effort"]))
        item("5.", "Pasta do projeto", str(data["project_directory"] or "(não definida)"))
        item("6.", "Sandbox", str(data["sandbox_mode"]))
        item("7.", "Política de aprovação", str(data["approval_policy"]))
        item("8.", "Diretórios adicionais de escrita", f"{len(data['additional_writable_directories'])} configurado(s)")
        item("9.", "Executar Codex-CLI", "CLI nativo com modelo, sandbox e diretórios configurados")
        item("10.", "Console interativo Onmyōji", "App Server com developer instructions compostas")
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
            configure_codex_login(root, data)
        elif choice == "3":
            catalog, catalog_detail = codex_model_catalog(root, data)
            if catalog is None:
                result(False, catalog_detail)
                continue
            value = choose_codex_model(catalog, str(data["model"]))
            if value:
                effort = choose_reasoning_effort(value, str(data["model_reasoning_effort"]), reset_to_model_default=True)
                if effort is None:
                    continue
                candidate = dict(data); candidate["model"], candidate["model_reasoning_effort"] = model_identifier(value), effort
                valid, message = validate_codex_model(root, candidate)
                result(valid, message)
                if valid:
                    data = update_system(root, data, "model", model_identifier(value))
                    data = update_system(root, data, "model_reasoning_effort", effort)
        elif choice == "4":
            catalog, catalog_detail = codex_model_catalog(root, data)
            model = catalog_model(catalog, str(data["model"])) if catalog else None
            if model is None:
                result(False, catalog_detail if catalog is None else "O modelo configurado não está disponível para a conta atual.")
                continue
            value = choose_reasoning_effort(model, str(data["model_reasoning_effort"]))
            if value:
                candidate = dict(data); candidate["model_reasoning_effort"] = value
                valid, message = validate_codex_model(root, candidate)
                result(valid, message)
                if valid: data = update_system(root, data, "model_reasoning_effort", value)
        elif choice == "5":
            value = choose_project_directory(root, str(data["project_directory"]))
            if value: data = update_system(root, data, "project_directory", value)
        elif choice == "6":
            workspace = Path(str(data["project_directory"])).expanduser() if data["project_directory"] else suggested_project_directory(root)
            value = choose_sandbox(root, workspace, str(data["sandbox_mode"]))
            if value: data = update_system(root, data, "sandbox_mode", value)
        elif choice == "7":
            workspace = Path(str(data["project_directory"])).expanduser() if data["project_directory"] else suggested_project_directory(root)
            value = choose_approval_policy(root, workspace, str(data["sandbox_mode"]), str(data["approval_policy"]))
            if value: data = update_system(root, data, "approval_policy", value)
        elif choice == "8": data = manage_writable_directories(root, data)
        elif choice == "9": launch_codex(root, data)
        elif choice == "10": launch_onmyoji_interactive(root, data)
        else: result(False, "Opção inválida.")


def daemon_menu(root: Path) -> None:
    project = root / "onmyoji-daemon"
    script = project / "setupDaemon.py"
    if not script.is_file():
        result(False, "Projeto do daemon não encontrado.")
        return
    environment = dict(os.environ); source = str(project / "src")
    environment["PYTHONPATH"] = source + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    telegram_token_changed = False

    def command(arguments: list[str], quiet: bool = False, input_value: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(arguments, cwd=str(project), env=environment, text=True, encoding="utf-8", errors="replace", capture_output=quiet, input=input_value)

    def lifecycle(action: str, extra: list[str] | None = None, input_value: str | None = None) -> bool:
        completed = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), action, *(extra or [])], quiet=True, input_value=input_value)
        result(completed.returncode == 0, (completed.stdout or completed.stderr or "Operação concluída.").strip())
        return completed.returncode == 0

    def inspect_python(executable: str) -> tuple[bool, str]:
        try:
            checked = subprocess.run([executable, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10)
            version = (checked.stdout or "").strip()
            major, minor, *_ = (int(value) for value in version.split("."))
            return (checked.returncode == 0 and (major, minor) >= (3, 11), version)
        except (OSError, ValueError, subprocess.TimeoutExpired): return False, "indisponível"

    def discovered_pythons() -> list[tuple[str, str]]:
        candidates = [sys.executable, shutil.which("python") or ""]
        if os.name == "nt":
            try:
                listed = subprocess.run(["py", "-0p"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10)
                for line in listed.stdout.splitlines():
                    match = re.search(r"([A-Za-z]:\\[^\r\n]+?\.exe)\s*$", line.strip(), re.IGNORECASE)
                    if match: candidates.append(match.group(1))
            except (OSError, subprocess.TimeoutExpired): pass
        found: list[tuple[str, str]] = []
        for candidate in candidates:
            if not candidate or any(Path(candidate).resolve() == Path(item[0]).resolve() for item in found): continue
            valid, version = inspect_python(candidate)
            if valid: found.append((str(Path(candidate).resolve()), version))
        return found

    def choose_daemon_runtime() -> bool:
        try:
            stored = tomllib.loads((root / "configs" / "daemon" / "daemon.toml").read_text(encoding="utf-8")).get("runtime", {})
        except (OSError, tomllib.TOMLDecodeError): stored = {}
        mode = str(stored.get("mode") or "dedicated") if isinstance(stored, dict) else "dedicated"
        default = {"dedicated": "1", "existing": "2", "global": "3"}.get(mode, "1")
        screen("Runtime Python do daemon", "Escolha onde o daemon executará suas dependências")
        print("  1. Ambiente dedicado — recomendado; isola os pacotes desta instância.")
        print("  2. Ambiente virtual existente — usa um venv já preparado.")
        print("  3. Python global da máquina — avançado; pode alterar pacotes compartilhados.\n")
        while True:
            selected = prompt(f"Modo [{default}]: ").strip() or default
            if selected in {"x", "X", "\x1b"}: return False
            if selected in {"1", "2", "3"}: break
            result(False, "Escolha 1, 2 ou 3.")
        selected_mode = {"1": "dedicated", "2": "existing", "3": "global"}[selected]
        if selected_mode == "global": note("O setup poderá instalar componentes no Python global escolhido somente após sua confirmação.")
        candidates = discovered_pythons()
        if not candidates: result(False, "Nenhum Python 3.11 ou superior foi encontrado. Informe um caminho manual.")
        else:
            print("\n  Interpretadores compatíveis encontrados:")
            for index, (executable, version) in enumerate(candidates, 1): item(f"{index}.", executable, f"Python {version}")
        item("M.", "Informar caminho manual")
        item("X.", "Cancelar")
        while True:
            answer = prompt("Python: ").strip()
            if answer.casefold() in {"x", "\x1b"}: return False
            if answer.casefold() == "m": candidate = prompt("Caminho completo do Python: ").strip()
            elif answer.isdigit() and 1 <= int(answer) <= len(candidates): candidate = candidates[int(answer) - 1][0]
            else: result(False, "Escolha um interpretador listado, M ou X."); continue
            valid, version = inspect_python(candidate)
            if not valid: result(False, "O executável deve ser um Python funcional na versão 3.11 ou superior."); continue
            if prompt(f"Usar {candidate} (Python {version})? [S/n]: ").strip().casefold() not in {"", "s", "sim"}: continue
            saved = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-runtime", "--runtime-mode", selected_mode, "--python", candidate, "--base-python", candidate], quiet=True)
            result(saved.returncode == 0, (saved.stdout or saved.stderr).strip())
            return saved.returncode == 0

    def install_daemon() -> bool:
        """Prepara, configura e valida o runtime escolhido pelo usuário."""
        print()
        print("  [1/4] Preparando a instância do daemon...")
        prepared = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "prepare-instance"], quiet=True)
        result(prepared.returncode == 0, (prepared.stdout or prepared.stderr).strip())
        if prepared.returncode != 0: return False
        print("\n  [2/4] Escolha do runtime Python")
        if not choose_daemon_runtime(): return False
        print("\n  [3/4] Componentes necessários")
        print("    • aiohttp — comunicação HTTP do daemon e seus gateways.")
        print("    • cryptography — recursos criptográficos e certificados locais.")
        if os.name == "nt": print("    • pywin32 — integração opcional com Serviço do Windows.")
        if prompt("\n  Verificar e instalar/atualizar esses componentes no runtime escolhido? [S/n]: ").strip().casefold() not in {"", "s", "sim"}:
            result(False, "Instalação cancelada antes de alterar componentes Python.")
            return False
        print("\n  [4/4] Preparando o runtime e os componentes")
        note("A saída do instalador Python será exibida abaixo; downloads podem levar alguns minutos.")
        completed = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "install-runtime"], quiet=False)
        result(completed.returncode == 0, "Daemon instalado e runtime Python validado." if completed.returncode == 0 else "A instalação do daemon não foi concluída.")
        return completed.returncode == 0

    def validation(action: str = "validation-json") -> list[dict[str, str]]:
        completed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", action], quiet=True)
        detail = (completed.stderr or completed.stdout or "").strip()
        detail = re.sub(r"(?i)(token|password|secret|access[_-]?token)\s*[=:]\s*\S+", r"\1=[redacted]", detail)[-500:]
        try: values = json.loads(completed.stdout or "[]")
        except (json.JSONDecodeError, TypeError):
            return [{"state": "error", "label": "Diagnóstico do daemon", "detail": detail or f"O comando de diagnóstico terminou com código {completed.returncode}."}]
        if not isinstance(values, list):
            return [{"state": "error", "label": "Diagnóstico do daemon", "detail": "O daemon retornou um diagnóstico em formato inválido."}]
        return values

    def print_checks(checks: list[dict[str, str]]) -> None:
        for check in checks:
            state = check.get("state", "error"); symbol, color = ({"ok": ("✓", Ui.green), "pending": ("○", Ui.amber), "error": ("✗", Ui.red)}).get(state, ("!", Ui.red))
            print("    " + Ui.text(symbol, Ui.bold, color) + "  " + check.get("label", "Validação") + (Ui.text(" — " + check.get("detail", ""), color) if check.get("detail") else ""))

    def show_validation() -> bool:
        checks = validation(); print()
        print_checks(checks)
        required = {"Daemon da instância", "Estrutura da configuração Telegram", "Perfil de vault MCP", "Referência do token", "Executável Codex para serviço", "Workspace do Shikigami", "Execução do agente Codex", "Conexão Telegram"}
        return bool(checks) and all(check.get("state") == "ok" for check in checks if check.get("label") in required)

    def show_daemon_diagnosis() -> bool:
        """Mostra primeiro os pré-requisitos do Shikigami e depois a plataforma."""
        screen("Diagnóstico do Daemon", "Pré-requisitos do Shikigami e estado da plataforma; serviços não são avaliados aqui")
        print("\n  Diagnóstico principal do Shikigami")
        main_checks = shikigami_diagnostics(root, lambda message: print("    → " + message)); print_checks(main_checks)
        print("\n  Diagnóstico da plataforma do daemon")
        daemon_checks = validation("daemon-validation-json"); print_checks(daemon_checks)
        return all(check.get("state") == "ok" for check in [*main_checks, *daemon_checks])

    def show_telegram_diagnosis() -> bool:
        """Exibe dependências da plataforma antes das verificações do gateway."""
        screen("Diagnóstico do Telegram", "Plataforma, vault MCP e estado do gateway")
        print("\n  Diagnóstico principal do Shikigami")
        main_checks = shikigami_diagnostics(root, lambda message: print("    → " + message)); print_checks(main_checks)
        print("\n  Diagnóstico da plataforma do daemon")
        daemon_checks = validation("daemon-validation-json"); print_checks(daemon_checks)
        print("\n  Diagnóstico específico do Telegram")
        telegram_checks = validation(); print_checks(telegram_checks)
        required = {"Perfis de vault MCP", "Perfil de vault MCP", "Referência do token", "Codex App Server", "Execução do agente Codex", "Conexão Telegram"}
        return all(check.get("state") == "ok" for check in telegram_checks if check.get("label") in required)

    def installed() -> bool: return (root / "configs" / "daemon" / "daemon.toml").is_file()
    def runtime_validated() -> bool:
        checks = validation("daemon-validation-json")
        return any(check.get("label") == "Runtime Python do daemon" and check.get("state") == "ok" for check in checks)
    def service_installed() -> bool: return (root / "configs" / "daemon" / "service.json").is_file()
    def daemon_running() -> bool:
        return command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "list-services"], quiet=True).returncode == 0

    def wait_for_daemon_ready() -> bool:
        """Espera o supervisor responder após uma inicialização ou reinício."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if daemon_running(): return True
            time.sleep(0.2)
        return False

    def wait_for_telegram_ready() -> bool:
        """Aguarda o módulo Telegram responder após iniciar ou reiniciar o daemon."""
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "telegram", "status"], quiet=True)
            if status.returncode == 0: return True
            time.sleep(0.3)
        return False

    def miniapps_state() -> dict[str, object]:
        completed = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "list-services"], quiet=True)
        if completed.returncode != 0: return {}
        try:
            services = json.loads(completed.stdout)
            return next((item for item in services if item.get("name") == "mini-apps"), {})
        except (json.JSONDecodeError, TypeError, AttributeError):
            return {}

    def offer_restart(reason: str = "A alteração") -> None:
        if not daemon_running(): return
        note(f"{reason} só entra em vigor no gateway após reiniciar o daemon.")
        answer = prompt("O daemon está em execução; reiniciar agora para efetivar a alteração? [S/n]: ").strip().casefold()
        if answer not in {"", "s", "sim"}: result(False, f"{reason} foi salva, mas o gateway continua com a configuração anterior até o próximo reinício."); return
        if service_installed(): lifecycle("service-restart")
        elif lifecycle("process-stop"): lifecycle("process-start")

    def service_running() -> bool:
        status = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "service-status"], quiet=True)
        text = (status.stdout + "\n" + status.stderr).upper()
        return "RUNNING" in text or "ACTIVE: ACTIVE" in text

    def daemon_wizard() -> None:
        """Configura e valida exclusivamente a plataforma local do daemon."""
        screen("Wizard do Daemon", "Configuração e validação da plataforma local")
        note("Cada etapa confirmada é salva imediatamente. Valores já válidos permanecem disponíveis quando o wizard for executado novamente.")
        main_checks = shikigami_diagnostics(root, lambda message: print("    → " + message))
        print("\n  1. Pré-requisitos do Shikigami")
        print_checks(main_checks)
        if any(check.get("state") != "ok" for check in main_checks):
            result(False, "Corrija os itens do diagnóstico principal antes de configurar o daemon.")
            return
        print("\n  2. Instalação do daemon")
        if not installed():
            if not install_daemon(): return
        elif not runtime_validated():
            print("    " + Ui.text("○", Ui.bold, Ui.amber) + "  Configuração-base encontrada; o runtime ainda não foi validado.")
            if not install_daemon(): return
        else:
            print("    " + Ui.text("✓", Ui.bold, Ui.green) + "  Runtime Python do daemon — instalado e validado.")
            if prompt("  Revisar ou alterar o runtime Python agora? [s/N]: ").strip().casefold() in {"s", "sim"}:
                if not install_daemon(): return
        print("\n  3. Configuração compartilhada do daemon")
        bootstrapped = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "daemon-bootstrap"], quiet=True)
        if bootstrapped.returncode != 0:
            result(False, (bootstrapped.stdout or bootstrapped.stderr).strip() or "Não foi possível preparar a configuração do daemon.")
            return
        result(True, "Configuração-base e instruções do daemon validadas e salvas.")
        try:
            daemon_settings = tomllib.loads((root / "configs" / "daemon" / "daemon.toml").read_text(encoding="utf-8"))
            app_server = daemon_settings.get("app_server", {})
            app_server_enabled = bool(app_server.get("enabled", False))
            idle_default = max(1, int(app_server.get("idle_timeout_seconds") or 1800) // 60)
        except (OSError, ValueError, tomllib.TOMLDecodeError, AttributeError):
            app_server_enabled, idle_default = False, 30
        while True:
            default_choice = "s" if app_server_enabled else "n"
            hint = "S/n" if app_server_enabled else "s/N"
            enabled = prompt(f"\n  4. Habilitar Codex App Server para o daemon? [{hint}]: ").strip().casefold() or default_choice
            if enabled in {"", "s", "sim"}:
                raw_idle = prompt(f"Tempo de inatividade sem turnos, em minutos [{idle_default}]: ").strip() or str(idle_default)
                try: idle = int(raw_idle)
                except ValueError: result(False, "Informe um número inteiro de minutos."); continue
                if idle < 1: result(False, "O tempo mínimo é 1 minuto."); continue
                changed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-app-server", "--enabled", "--idle-timeout", str(idle * 60)], quiet=True)
                result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
                if changed.returncode != 0: return
                break
            if enabled in {"n", "nao", "não"}:
                result(False, "O App Server é necessário para concluir o wizard. Nenhuma alteração foi feita nesta etapa.")
                return
            result(False, "Responda S ou N.")
        print("\n  5. Teste controlado do agente Codex")
        tested = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "test-agent"], quiet=True)
        result(tested.returncode == 0, (tested.stdout or tested.stderr).strip())
        if tested.returncode != 0: return
        print("\n  6. Início da plataforma")
        if daemon_running():
            restart = prompt("O daemon está em execução. Reiniciar agora para aplicar a configuração? [S/n]: ").strip().casefold()
            if restart not in {"", "s", "sim"}:
                result(False, "Configuração salva e testada, mas pendente de aplicação. Reinicie o daemon quando desejar concluir o wizard.")
                return
            if service_installed():
                if not lifecycle("service-restart"): return
            elif lifecycle("process-stop"):
                if not lifecycle("process-start"): return
            else: return
        elif not lifecycle("service-start" if service_installed() else "process-start"):
            return
        if not wait_for_daemon_ready():
            result(False, "O comando de início foi aceito, mas o daemon não respondeu dentro de 10 segundos. Consulte Operação do daemon.")
            return
        print("\n  7. Diagnóstico final")
        if show_daemon_diagnosis(): result(True, "Daemon configurado e em execução.")
        else: result(False, "A plataforma foi configurada, mas o diagnóstico ainda possui pendências.")

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
        def windows_service_account() -> tuple[str, str] | None:
            """Escolhe uma conta compatível com credenciais privadas do Windows."""
            try:
                current = subprocess.run(["whoami"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=5).stdout.strip()
            except (OSError, subprocess.TimeoutExpired): current = ""
            screen("Conta do Serviço Windows", "Define qual perfil do Windows executará o daemon")
            note("LocalSystem é isolado e não enxerga credenciais guardadas no perfil Windows atual. Para Telegram com KeePass, prefira a conta que configurou o cofre.")
            item("1.", "Conta Windows atual", current or "não identificada")
            item("2.", "LocalSystem", "Somente para configurações sem credenciais do perfil do usuário")
            item("3.", "Outra conta Windows", "Domínio\\usuário ou .\\usuário")
            item("X.", "Cancelar")
            while True:
                selected = prompt("Conta [1]: ").strip().casefold() or "1"
                if selected in {"x", "\x1b"}: return None
                if selected == "2": return "LocalSystem", ""
                account = current if selected == "1" else (prompt("Conta Windows: ").strip() if selected == "3" else "")
                if not account:
                    result(False, "Informe uma conta Windows válida."); continue
                password = getpass.getpass("Senha da conta Windows (não será armazenada): ")
                if not password:
                    result(False, "A conta Windows exige senha para executar um serviço."); continue
                return account, password

        def privileged_service_action(action: str) -> bool:
            extras: list[str] = []
            if os.name != "nt":
                try: scope = json.loads((root / "configs" / "daemon" / "service.json").read_text(encoding="utf-8")).get("scope", "system")
                except (OSError, ValueError): scope = "system"
                if scope == "system" and hasattr(os, "geteuid") and os.geteuid() != 0:
                    note("Esta operação exige sudo. A senha será solicitada pelo sistema, sem ser lida ou armazenada pelo Onmyōji.")
                    if prompt("Continuar? [s/N]: ").strip().casefold() not in {"s", "sim"}: result(False, "Operação cancelada."); return False
                    extras.append("--use-sudo")
            return lifecycle(action, extras)

        while True:
            screen("Serviço do sistema operacional", "O daemon será iniciado e controlado pelo sistema operacional")
            if not service_installed():
                if os.name == "nt":
                    note("Escolha a conta do Windows que executará o serviço. LocalSystem é isolado e não acessa credenciais do KeePass guardadas no perfil atual.")
                else:
                    note("Escolha serviço do usuário para usar o login atual do Codex; serviço do sistema exige privilégios e uma conta Linux explícita.")
                item("1.", "Instalar serviço")
                item("X.", "Voltar")
                choice = prompt("Opção: ").strip().casefold()
                if choice in {"x", "\x1b"}: return
                if choice != "1": result(False, "Opção inválida."); continue
                default_name = "Shikigami-" + "".join(part[:1].upper() + part[1:] for part in instance_identity(root).split())
                visible = instance_identity(root)
                name = prompt(f"Nome do serviço [{default_name}]: ").strip() or default_name
                description = prompt(f"Descrição [{f'Shikigami {visible} Daemon'}]: ").strip() or f"Shikigami {visible} Daemon"
                if os.name == "nt":
                    selected_account = windows_service_account()
                    if selected_account is None: result(False, "Instalação do serviço cancelada."); continue
                    account, password = selected_account
                    if prompt(f"Confirmar a instalação do serviço Windows como {account}? [s/N]: ").strip().casefold() not in {"s", "sim"}: result(False, "Instalação do serviço cancelada."); continue
                    extras = ["--name", name, "--description", description, "--scope", "system", "--windows-account", account]
                    if password: extras.append("--password-stdin")
                    if lifecycle("install-service", extras, password if password else None) and prompt("Iniciar agora e validar o registro? [S/n]: ").strip().casefold() in {"", "s", "sim"}:
                        lifecycle("service-start"); lifecycle("service-status")
                    continue
                print()
                item("1.", "Serviço do usuário", "systemd --user · recomendado para o login atual do Codex")
                item("2.", "Serviço do sistema", "systemd · exige sudo e usuário Linux explícito")
                selected = prompt("Escopo [1]: ").strip() or "1"
                if selected == "1": lifecycle("install-service", ["--name", name, "--description", description, "--scope", "user"]); continue
                if selected != "2": result(False, "Escolha 1 ou 2."); continue
                run_as_user = prompt(f"Usuário Linux do serviço [{getpass.getuser()}]: ").strip() or getpass.getuser()
                run_as_group = prompt("Grupo Linux do serviço [padrão do usuário]: ").strip()
                note("O setup solicitará sudo para gravar e habilitar a unit de sistema. A senha não será lida nem armazenada pelo Onmyōji.")
                if prompt("Continuar com a instalação de sistema? [s/N]: ").strip().casefold() not in {"s", "sim"}: result(False, "Instalação do serviço cancelada."); continue
                lifecycle("install-service", ["--name", name, "--description", description, "--scope", "system", "--run-as-user", run_as_user, "--run-as-group", run_as_group, "--use-sudo"]); continue
            item("1.", "Status do serviço")
            item("2.", "Parar serviço" if service_running() else "Iniciar serviço")
            item("3.", "Reiniciar serviço")
            item("4.", "Forçar parada do serviço")
            item("5.", "Remover serviço")
            if os.name == "nt": item("6.", "Configurar conta do serviço", "Necessário para acessar credenciais KeePass do usuário")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "1": lifecycle("service-status")
            elif choice == "2": privileged_service_action("service-stop" if service_running() else "service-start")
            elif choice == "3": privileged_service_action("service-restart")
            elif choice == "4": privileged_service_action("service-force-stop")
            elif choice == "5":
                confirmation = prompt("Digite remover para confirmar: ").strip()
                if confirmation == "remover": privileged_service_action("remove-service")
                else: result(False, "Remoção cancelada; a confirmação deve ser exatamente remover.")
            elif choice == "6" and os.name == "nt":
                selected_account = windows_service_account()
                if selected_account is None: result(False, "Alteração da conta cancelada."); continue
                account, password = selected_account
                extras = ["--account", account]
                if password: extras.append("--password-stdin")
                lifecycle("configure-windows-service-account", extras, password if password else None)
            else: result(False, "Opção inválida.")

    def operation_menu() -> None:
        """Separa a execução temporária do gerenciamento persistente pelo SO."""
        while True:
            screen("Operação do daemon", "Processo local para uso temporário; serviço do SO para operação persistente")
            item("1.", "Processo local", "Executar em segundo plano nesta sessão")
            item("2.", "Serviço do sistema operacional", "Instalar, diagnosticar e gerenciar inicialização persistente")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "1": process_menu()
            elif choice == "2": service_menu()
            else: result(False, "Opção inválida.")

    def telegram_section_menu(section: str) -> None:
        while True:
            telegram_path = root / "shikigami" / "daemon" / "telegram.toml"
            command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "bootstrap"], quiet=True)
            try:
                enabled = bool(json.loads((root / "configs" / "daemon" / "services.json").read_text(encoding="utf-8")).get("telegram", {}).get("enabled", False))
            except (OSError, ValueError): enabled = False
            try:
                telegram_settings = tomllib.loads(telegram_path.read_text(encoding="utf-8")); totp_enabled = bool(telegram_settings.get("totp", {}).get("enabled", False)); app_server_enabled = bool(telegram_settings.get("app_server", {}).get("enabled", False)); app_server_idle = int(telegram_settings.get("app_server", {}).get("idle_timeout_seconds") or 1800); voice_enabled = bool(telegram_settings.get("voice_reply", {}).get("enabled", False)); voice_profile = str(telegram_settings.get("voice_reply", {}).get("eccovox_profile") or ""); voice_auto_off = int(telegram_settings.get("voice_reply", {}).get("auto_off_minutes") or 15); outbound_media = bool(telegram_settings.get("voice_reply", {}).get("agent_outbound_media", False))
            except (OSError, tomllib.TOMLDecodeError): telegram_settings = {}; totp_enabled, app_server_enabled, app_server_idle, voice_enabled, voice_profile, voice_auto_off, outbound_media = False, False, 1800, False, "", 15, False
            try:
                daemon_settings = tomllib.loads((root / "configs" / "daemon" / "daemon.toml").read_text(encoding="utf-8"))
                daemon_app_server = daemon_settings.get("app_server", {})
                app_server_enabled = bool(daemon_app_server.get("enabled", False))
                app_server_idle = int(daemon_app_server.get("idle_timeout_seconds") or 1800)
            except (OSError, tomllib.TOMLDecodeError, AttributeError, TypeError, ValueError): pass
            agent_preferences = telegram_settings.get("agent", {}) if "telegram_settings" in locals() and isinstance(telegram_settings.get("agent", {}), dict) else {}
            owner_preferences = bool(agent_preferences.get("owner_execution_preferences", True)); owner_models = ";".join(str(value) for value in agent_preferences.get("owner_allowed_models", []) if isinstance(value, str)); owner_efforts = ";".join(str(value) for value in agent_preferences.get("owner_allowed_reasoning_efforts", []) if isinstance(value, str))
            instruction_settings = telegram_settings.get("instructions", {}) if isinstance(telegram_settings.get("instructions", {}), dict) else {}
            instruction_file = str(instruction_settings.get("shikigami_file") or "instructions.md")
            title, subtitle = {
                "gateway": ("Gateway Telegram", "Ativação e teste da conexão com o bot"),
                "credential": ("Credencial do bot", "KeePass Vault e autenticação protegida"),
                "app-server": ("Codex App Server", "Conversa persistente e teste controlado do agente"),
                "owners": ("Owners e comandos", "Pareamento, comandos privados e preferências"),
                "optional": ("Recursos opcionais", "TOTP, áudio e instruções particulares"),
            }[section]
            screen(title, subtitle)
            if section == "gateway":
                item("1.", "Desabilitar Telegram" if enabled else "Habilitar Telegram")
                item("2.", "Testar conexão com Telegram")
            elif section == "credential":
                item("1.", "Configurar credencial do bot", "Perfil de vault MCP e referência da entrada")
                item("2.", "Testar conexão com Telegram")
            elif section == "app-server":
                item("1.", "Testar agente Codex", "Execução controlada, sem mensagem ao bot")
                item("2.", "Desabilitar App Server" if app_server_enabled else "Habilitar App Server", "Conversa persistente por Shikigami" if app_server_enabled else "Obrigatório para o Gateway Telegram")
                item("3.", "Tempo de inatividade do App Server", f"{app_server_idle // 60} min · encerra somente sem turnos ativos")
            elif section == "owners":
                item("1.", "Parear owner", "Exige gateway em execução")
                item("2.", "Atualizar comandos privados", "Regrava e confirma comandos de cada owner")
                item("3.", "Preferências de execução do owner", "Habilitadas" if owner_preferences else "Desabilitadas")
            else:
                item("1.", "Desabilitar TOTP" if totp_enabled else "Configurar e habilitar TOTP")
                item("2.", "Configurar respostas em áudio", (f"EccoVox: {voice_profile} · auto-off: {voice_auto_off} min" if voice_enabled else "Desabilitadas"))
                item("3.", "Instruções do Shikigami", instruction_file)
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            actions = {
                "gateway": {"1": "1", "2": "3"},
                "credential": {"1": "2", "2": "3"},
                "app-server": {"1": "7", "2": "9", "3": "10"},
                "owners": {"1": "4", "2": "8", "3": "12"},
                "optional": {"1": "6", "2": "11", "3": "13"},
            }[section]
            if choice not in actions:
                result(False, "Opção inválida."); continue
            choice = actions[choice]
            if choice == "1":
                if enabled:
                    if lifecycle("disable", ["telegram"]):
                        result(True, "Telegram desabilitado; a configuração foi preservada."); offer_restart()
                else:
                    if not show_telegram_diagnosis(): result(False, "Corrija os itens marcados antes de habilitar o gateway."); continue
                    if lifecycle("enable", ["telegram"]):
                        result(True, "Telegram habilitado; ele iniciará junto com o daemon."); offer_restart()
            elif choice == "2":
                listed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "profiles"], quiet=True)
                try: values = json.loads(listed.stdout)
                except json.JSONDecodeError: values = []
                if not values: result(False, "Nenhum perfil de vault disponível. Configure-o primeiro em MCP > Perfis de vault."); continue
                print()
                for index, profile in enumerate(values, 1): item(f"{index}.", profile)
                item("X.", "Voltar")
                selected = prompt("Perfil de vault MCP: ").strip().casefold()
                if selected in {"x", "\x1b"}: continue
                if not selected.isdigit() or not 1 <= int(selected) <= len(values): result(False, "Perfil inválido."); continue
                instance = "".join(part[:1].upper() + part[1:] for part in instance_identity(root).split()) or "Shikigami"
                default_entry = f"APIs/Telegram:{instance}"
                profile = values[int(selected)-1]
                while True:
                    entry = prompt(f"Entrada KeePass [{default_entry}]: ").strip() or default_entry
                    try:
                        current = tomllib.loads(telegram_path.read_text(encoding="utf-8")).get("telegram", {})
                        reference_changed = (str(current.get("keepass_profile") or ""), str(current.get("token_entry") or "")) != (profile, entry)
                    except (OSError, tomllib.TOMLDecodeError, AttributeError): reference_changed = True
                    saved = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-credential", "--profile", profile, "--entry", entry], quiet=True)
                    if saved.returncode != 0: result(False, (saved.stdout or saved.stderr).strip()); break
                    checked = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "check-entry", "--profile", profile, "--entry", entry], quiet=True)
                    try: probe = json.loads(checked.stdout); exists, detail = bool(probe["exists"]), str(probe["message"])
                    except (json.JSONDecodeError, KeyError): exists, detail = False, "Não foi possível verificar a entrada no KeePass."
                    if exists:
                        result(True, detail)
                        action = prompt("Utilizar a entrada existente [U], substituir seu token [S] ou corrigir a chave [C]? [U]: ").strip().casefold() or "u"
                        if action == "u":
                            result(True, "Credencial configurada com a entrada existente.")
                            if reference_changed: offer_restart("A referência da credencial do bot")
                            break
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
                    if written.returncode == 0:
                        offer_restart("A nova credencial do bot")
                        break
            elif choice == "3":
                tested = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "test-telegram"], quiet=True)
                result(tested.returncode == 0, (tested.stdout or tested.stderr).strip())
            elif choice == "4":
                request = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "telegram", "pair-request"], quiet=True)
                if request.returncode != 0: result(False, "Inicie o daemon e habilite o Telegram antes de parear um owner."); continue
                try: pin = json.loads(request.stdout)["pin"]
                except (json.JSONDecodeError, KeyError): result(False, "Não foi possível gerar o código de pareamento."); continue
                result(True, f"Envie /pair {pin} em uma DM para o bot. O código expira em 5 minutos e só pode ser usado uma vez.")
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
                profile = prompt(f"Perfil MCP EccoVox [{voice_profile}]: ").strip() or voice_profile
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

    def configure_wizard_telegram_credential() -> bool:
        """Configura a credencial com os valores existentes, sem desviar ao menu manual."""
        nonlocal telegram_token_changed
        path = root / "shikigami" / "daemon" / "telegram.toml"
        try:
            settings = tomllib.loads(path.read_text(encoding="utf-8")); telegram = settings.get("telegram", {})
            current_profile, current_entry = str(telegram.get("vault_profile") or telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or "")
        except (OSError, tomllib.TOMLDecodeError, AttributeError):
            current_profile, current_entry = "", ""
        listed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "profiles"], quiet=True)
        try: profiles = json.loads(listed.stdout)
        except json.JSONDecodeError: profiles = []
        if not profiles: result(False, "Nenhum perfil de vault MCP disponível."); return False
        default_profile = current_profile if current_profile in profiles else str(profiles[0])
        print("    Perfis de vault MCP disponíveis: " + ", ".join(str(profile) for profile in profiles))
        profile = prompt(f"Perfil de vault MCP [{default_profile}]: ").strip() or default_profile
        if profile not in profiles: result(False, "Perfil de vault MCP inválido."); return False
        default_entry = current_entry or f"APIs/Telegram:{''.join(part[:1].upper() + part[1:] for part in instance_identity(root).split()) or 'Shikigami'}"
        entry = prompt(f"Entrada do vault [{default_entry}]: ").strip() or default_entry
        saved = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-credential", "--profile", profile, "--entry", entry], quiet=True)
        if saved.returncode != 0: result(False, (saved.stdout or saved.stderr).strip()); return False
        checked = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "check-entry", "--profile", profile, "--entry", entry], quiet=True)
        try: probe = json.loads(checked.stdout); exists, detail = bool(probe["exists"]), str(probe["message"])
        except (json.JSONDecodeError, KeyError): exists, detail = False, "Não foi possível verificar a entrada no KeePass."
        if not exists:
            result(False, detail)
            if prompt("Cadastrar o token do bot nesta entrada agora? [S/n]: ").strip().casefold() not in {"", "s", "sim"}: return False
            token = getpass.getpass(Ui.text("› Token do bot (não será exibido): ", Ui.violet, Ui.bold))
            written = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "write-token", "--profile", profile, "--entry", entry], quiet=True, input_value=token)
            if written.returncode != 0: result(False, (written.stdout or written.stderr).strip()); return False
            telegram_token_changed = True
        else: result(True, detail)
        tested = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "test-telegram"], quiet=True)
        result(tested.returncode == 0, (tested.stdout or tested.stderr).strip())
        return tested.returncode == 0

    def telegram_wizard() -> None:
        """Conduz a configuração mínima do gateway, preservando cada etapa válida."""
        nonlocal telegram_token_changed
        telegram_token_changed = False
        screen("Wizard do Telegram", "Configuração guiada do gateway e do primeiro owner")
        note("Cada etapa válida é mantida. O pareamento exige uma ação externa no Telegram e pode ser retomado depois.")
        print("\n  1. Pré-requisitos do Shikigami e daemon")
        main_checks = shikigami_diagnostics(root, lambda message: print("    → " + message)); print_checks(main_checks)
        daemon_checks = validation("daemon-validation-json"); print_checks(daemon_checks)
        if any(check.get("state") == "error" for check in [*main_checks, *daemon_checks]) or not daemon_running():
            result(False, "Corrija os pré-requisitos e inicie o daemon antes de configurar o Telegram."); return

        print("\n  2. Perfis de vault MCP")
        keepass_checks = validation("keepass-validation-json"); print_checks(keepass_checks)
        if any(check.get("state") != "ok" for check in keepass_checks):
            result(False, "Configure ao menos um perfil de vault MCP antes de continuar."); return

        print("\n  3. Preparar a configuração do Telegram")
        prepared = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "bootstrap"], quiet=True)
        if prepared.returncode != 0:
            result(False, (prepared.stdout or prepared.stderr).strip() or "Não foi possível preparar a configuração do Telegram."); return
        result(True, "Configuração-base do Telegram preparada.")

        print("\n  4. Credencial e autenticação do bot")
        if not configure_wizard_telegram_credential(): return

        print("\n  5. Codex App Server")
        if telegram_token_changed:
            note("A nova credencial do bot foi salva. Ela será aplicada quando o wizard reiniciar o daemon no passo 7.")
        try:
            platform = tomllib.loads((root / "configs" / "daemon" / "daemon.toml").read_text(encoding="utf-8"))
            current_idle = max(1, int(platform.get("app_server", {}).get("idle_timeout_seconds") or 1800) // 60)
            app_enabled = bool(platform.get("app_server", {}).get("enabled", False))
        except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
            current_idle, app_enabled = 30, False
        if not app_enabled:
            raw = prompt(f"Habilitar App Server com {current_idle} minutos de inatividade? [S/n]: ").strip().casefold()
            if raw not in {"", "s", "sim"}: result(False, "O Telegram exige o Codex App Server habilitado."); return
            changed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-app-server", "--enabled", "--idle-timeout", str(current_idle * 60)], quiet=True)
            if changed.returncode != 0: result(False, (changed.stdout or changed.stderr).strip()); return
            result(True, "Codex App Server habilitado.")

        print("\n  6. Teste controlado do agente Codex")
        agent = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "test-agent"], quiet=True)
        if agent.returncode != 0: result(False, (agent.stdout or agent.stderr).strip()); return
        result(True, (agent.stdout or "Agente Codex validado.").strip())

        print("\n  7. Habilitar o gateway")
        if not show_telegram_diagnosis(): result(False, "O diagnóstico ainda possui pendências obrigatórias."); return
        if lifecycle("enable", ["telegram"]):
            result(True, "Gateway Telegram habilitado.")
        else: return
        if daemon_running():
            answer = prompt("O daemon está em execução; reiniciar agora para efetivar a alteração? [S/n]: ").strip().casefold()
            if answer not in {"", "s", "sim"}: result(False, "Gateway habilitado, mas pendente de reinício. Execute o wizard novamente após reiniciá-lo."); return
            restarted = lifecycle("service-restart") if service_installed() else (lifecycle("process-stop") and lifecycle("process-start"))
            if not restarted: return
        elif not lifecycle("service-start" if service_installed() else "process-start"):
            return
        if not wait_for_daemon_ready() or not wait_for_telegram_ready():
            result(False, "O daemon iniciou, mas o gateway Telegram não respondeu em até 15 segundos. Consulte o diagnóstico do Telegram."); return
        result(True, "Gateway Telegram em execução e respondendo.")

        print("\n  8. Parear o primeiro owner")
        request = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "telegram", "pair-request"], quiet=True)
        if request.returncode != 0:
            result(False, "Inicie ou reinicie o daemon com o Telegram habilitado antes de parear um owner."); return
        try: pin = json.loads(request.stdout)["pin"]
        except (json.JSONDecodeError, KeyError): result(False, "Não foi possível gerar o código de pareamento."); return
        note(f"Envie /pair {pin} em uma DM para o bot. O código expira em cinco minutos.")
        prompt("Depois de enviar o comando, pressione Enter para verificar o pareamento: ")
        checks = validation()
        owner = next((check for check in checks if check.get("label") == "Owners pareados"), {})
        if owner.get("state") != "ok": result(False, "O owner ainda não foi confirmado. Execute o wizard novamente após concluir o /pair."); return
        result(True, str(owner.get("detail") or "Owner pareado."))

        print("\n  9. Sincronizar comandos privados")
        synced = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "telegram", "sync-commands"], quiet=True)
        if synced.returncode != 0: result(False, (synced.stdout or synced.stderr).strip()); return
        result(True, "Comandos privados sincronizados.")

        print("\n  10. Diagnóstico final")
        ready = show_telegram_diagnosis()
        result(ready, "Telegram configurado e pronto." if ready else "O Telegram foi configurado, mas ainda há pendências no diagnóstico.")

    def telegram_menu() -> None:
        """Central do gateway sem criar arquivos até o usuário iniciar uma configuração."""
        while True:
            screen("Gateway Telegram", "Integração, autenticação e owners do Shikigami")
            item("!.", "Diagnóstico do Telegram", "Plataforma, KeePass Vault e gateway")
            item("W.", "Wizard de configuração do Telegram", "Configura o gateway e o primeiro owner")
            print()
            item("1.", "Gateway Telegram", "Ativação e teste da conexão")
            item("2.", "Credencial do bot", "Perfil de vault MCP e referência do token")
            item("3.", "Codex App Server", "Conversa persistente e teste do agente")
            item("4.", "Owners e comandos", "Pareamento e preferências de execução")
            print()
            item("5.", "Recursos opcionais", "TOTP, áudio e instruções particulares")
            print()
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "!": show_telegram_diagnosis()
            elif choice == "w": telegram_wizard()
            elif choice == "1": telegram_section_menu("gateway")
            elif choice == "2": telegram_section_menu("credential")
            elif choice == "3": telegram_section_menu("app-server")
            elif choice == "4": telegram_section_menu("owners")
            elif choice == "5": telegram_section_menu("optional")
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
            item("1.", "Entender pré-requisitos e configurar", "Domínio, permissões Cloudflare e vault MCP")
            item("2.", "Provisionar Tunnel e DNS", "Cria/altera recursos Cloudflare após confirmação")
            item("3.", "Consultar/iniciar/reiniciar Tunnel", "Em execução" if tunnel.get("running") else "Parado")
            item("4.", "Baixar cloudflared oficial", "Somente se o executável não estiver disponível")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "1":
                screen("Preparar acesso público", "Reúna estes dados antes de criar qualquer recurso na Cloudflare")
                print("  1. Um domínio já adicionado à Cloudflare.")
                print("  2. Um token Cloudflare guardado em um perfil de vault MCP, com permissão Account > Cloudflare Tunnel > Edit e Zone > DNS > Edit.")
                print("  3. Account ID e Zone ID, encontrados no painel Overview da conta e da zona.")
                print("  4. Um hostname livre dentro da zona, por exemplo apps.seudominio.com.")
                print("\n  O token é armazenado somente no KeePass e resolvido em memória pelo serviço interno de credenciais do MCP.")
                if prompt("Digite configurar quando os dados estiverem prontos, ou X para voltar: ").strip().casefold() != "configurar": result(False, "Configuração pública adiada; o Gateway local continua disponível."); continue
                print("\n  Account ID identifica sua conta Cloudflare; copie-o do painel da conta.")
                account_id = prompt("Cloudflare Account ID: ").strip()
                print("\n  Zone ID identifica a zona DNS do domínio; copie-o do Overview do domínio.")
                zone_id = prompt("Cloudflare Zone ID: ").strip()
                print("\n  Hostname é o endereço que será criado no DNS, por exemplo apps.seudominio.com.")
                hostname = prompt("Hostname público: ").strip()
                try:
                    current = tomllib.loads((root / "configs" / "daemon" / "services" / "mini-apps" / "tunnel.toml").read_text(encoding="utf-8"))
                except (OSError, tomllib.TOMLDecodeError):
                    current = {}
                credential = configure_mcp_password_reference(root, "Cloudflare Tunnel", "APIs/Cloudflare/MiniApps", str(current.get("credential_profile") or ""), str(current.get("credential_entry_path") or ""))
                if credential is None:
                    result(False, "Configuração do Tunnel cancelada.")
                    continue
                credential_profile, credential_entry = credential
                print("\n  Pressione Enter se cloudflared estiver no PATH; caso contrário, informe o caminho do executável.")
                executable = prompt("Executável cloudflared [cloudflared]: ").strip() or "cloudflared"
                values = {"account_id": account_id, "zone_id": zone_id, "hostname": hostname, "credential_profile": credential_profile, "credential_entry_path": credential_entry, "cloudflared_executable": executable}
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
            print("  i  Nenhuma Mini App publicada. Crie o código no workspace e publique-o pelo MCP Mini Apps.")
            return
        for app in apps:
            if not isinstance(app, dict): continue
            item(str(app.get("id", ""))[:8] + ".", str(app.get("alias") or "Sem nome"), f"{app.get('state')} · {app.get('health')}")
            print(f"     URL: {app.get('url')}")
            if app.get("last_error"): print("     Atenção: a publicação registrou um erro; consulte os eventos pelo MCP Mini Apps.")

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
        if int(apps.get("total", 0)) == 0: print("\n  Próximo passo local: crie a Mini App dentro do workspace e publique-a pelo MCP Mini Apps.")
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

    def services_menu() -> None:
        """Agrupa integrações que dependem da plataforma, sem configurá-las no wizard."""
        while True:
            screen("Serviços instalados", "Integrações independentes que usam o daemon já configurado")
            item("T.", "Gateway Telegram", "Configuração, teste e pareamento")
            item("M.", "MiniApps", "Gateway HTTPS e Cloudflare Tunnel")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "t": telegram_menu()
            elif choice == "m": miniapps_menu()
            else: result(False, "Opção inválida.")

    def utilities_menu() -> None:
        """Reúne somente ferramentas auxiliares e de manutenção da plataforma."""
        while True:
            screen("Utilitários e manutenção", "Ferramentas técnicas da instância do daemon")
            if installed():
                item("1.", "Reparar ambiente Python", "Dependências privadas do daemon")
                item("2.", "Desinstalar daemon da instância", "Remove configurações e estado locais")
            else:
                note("Instale primeiro o daemon pela opção 1 do menu anterior.")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if installed() and choice == "1": lifecycle("install-runtime")
            elif installed() and choice == "2":
                confirmation = prompt("ATENÇÃO: isto apagará todas as configurações, credenciais referenciadas, pareamentos, conversas e estado do daemon desta instância. Uma reinstalação exigirá configurar tudo novamente. Digite desinstalar para confirmar: ").strip()
                if confirmation == "desinstalar": lifecycle("remove-instance")
                else: result(False, "Desinstalação cancelada; a confirmação deve ser exatamente desinstalar.")
            else: result(False, "Opção inválida.")

    while True:
        status = "CONFIGURAÇÃO-BASE PREPARADA" if installed() else "NÃO CONFIGURADO"
        screen("Daemon", f"Plataforma local e serviços da instância · {status}")
        item("!.", "Diagnóstico do Daemon", "Começa pelo diagnóstico principal do Shikigami")
        item("W.", "Wizard de configuração do Daemon", "Guia de configuração e validação")
        print()
        item("1.", "Preparar e instalar daemon" if not installed() else "Validar runtime do daemon", status)
        item("2.", "Processo/Serviço do Daemon", "Processo local e serviço do sistema operacional")
        item("3.", "Utilitários e manutenção", "Ambiente Python e remoção")
        print()
        item("M.", "Módulos do Daemon", "Telegram, MiniApps e futuras integrações")
        print()
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "!": show_daemon_diagnosis()
        elif choice == "w": daemon_wizard()
        elif choice == "1":
            install_daemon()
        elif choice == "2":
            if not installed(): result(False, "Instale o daemon pela opção 1 antes de operá-lo.")
            else: operation_menu()
        elif choice == "3": utilities_menu()
        elif choice == "m":
            if not installed(): result(False, "Instale o daemon pela opção 1 antes de configurar serviços.")
            else: services_menu()
        else: result(False, "Opção inválida.")


def skill_menu(skill: Skill, root: Path, skills: list[Skill]) -> None:
    while True:
        conflicted = skill.identifier in conflicting_skill_ids(skills)
        enabled = is_skill_enabled(root, skill, skills)
        state = "Desabilitar" if enabled else "Habilitar"
        screen(skill.title, f"Skill do Shikigami · {'CONFLITO' if conflicted else ('ATIVA' if enabled else 'INATIVA')}")
        if skill.description: print(f"  {skill.description}\n")
        if conflicted:
            note(f"O ID {skill.identifier} está duplicado entre as skills do Shikigami. Renomeie uma delas antes de habilitá-la.")
        else:
            item("1.", state)
            if skill.setup_script is not None: item("2.", "Configurar")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return
        if conflicted:
            result(False, "Esta skill possui um ID em conflito e não pode ser alterada.")
        elif choice == "1":
            identifiers = enabled_ids(root, skills)
            (identifiers.discard if enabled else identifiers.add)(skill.identifier)
            ok, message = save_enabled(root, identifiers, skills)
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
        item("D.", "Daemon", "Supervisor local e serviços da instância")
        item("M.", "MCP", "Perfis de vault e integrações MCP")
        conflicts = conflicting_skill_ids(skills)
        enabled = enabled_ids(root, skills)
        def rendered(index: int, skill: Skill, width: int = 50) -> str:
            if skill.identifier in conflicts: state = Ui.badge("CONFLITO", "warning")
            else:
                state = Ui.badge("ATIVA", "active") if skill.identifier in enabled else Ui.badge("inativa", "inactive")
            return skill_item(index, skill, state, width)
        if skills:
            print("\n  SKILLS DO SHIKIGAMI")
            for index, skill in enumerate(skills, 1): print(rendered(index, skill))
        print()
        item("X.", "Sair")
        choice = prompt("Selecione uma opção: ").strip().casefold()
        if choice == "x": return 0
        if choice == "!": diagnostics_menu(root); continue
        if choice == "a": codex_menu(root); continue
        if choice == "d": daemon_menu(root); continue
        if choice == "m": subprocess.run([sys.executable, str(root / "onmyoji-daemon" / "setupMcp.py")], check=False); continue
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
    skills = discover(); conflicts = conflicting_skill_ids(skills); by_id = {skill.identifier: skill for skill in skills if skill.identifier not in conflicts}
    initialized, initialization_message = ensure_skill_state(ROOT, skills)
    if not initialized:
        result(False, initialization_message)
        return 2
    if args.action == "menu": return menu(skills, ROOT)
    if args.action == "list":
        enabled = enabled_ids(ROOT, skills)
        values = [{"id": skill.identifier, "title": skill.title, "enabled": skill.identifier in enabled, "description": skill.description, "conflict": skill.identifier in conflicts} for skill in skills]
        if args.json: print(json.dumps({"ok": True, "skills": values}, ensure_ascii=False))
        else:
            for skill in skills: print(f"{skill.identifier}\t{'enabled' if skill.identifier in enabled else 'disabled'}\t{skill.description}")
        return 0
    if args.action == "status":
        active = enabled_ids(ROOT, skills)
        if args.json: print(json.dumps({"ok": True, "skills": sorted(active)}, ensure_ascii=False))
        else: print("\n".join(sorted(active)))
        return 0
    if args.skill in conflicts: parser.error("--skill identifica skills em conflito entre catálogo e Shikigami; renomeie uma delas")
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
    active = enabled_ids(ROOT, skills)
    (active.add if args.action == "enable" else active.discard)(args.skill)
    ok, message = save_enabled(ROOT, active, skills)
    print(json.dumps({"ok": ok, "skill": args.skill, "enabled": args.action == "enable", "message": message}, ensure_ascii=False) if args.json else message)
    return 0 if ok else 2


if __name__ == "__main__": raise SystemExit(main())
