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
from onmyoji_daemon.codex_capabilities import CapabilitySet, load_profile_capability
from onmyoji_daemon.shikigami_agents import ShikigamiAgentMaterializationError, ShikigamiAgentMaterializer
from onmyoji_daemon.instance import telegram_configuration_path
from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.credentials import CredentialError, KeePassCredentialResolver
from onmyoji_daemon.mcp.eccovox import EccoVoxError, EccoVoxService
from onmyoji_daemon import runtime as python_runtime
from onmyoji_daemon.telegram_stickers import MAX_CONTEXT_LENGTH, STATIC_STICKER_TARGET_BYTES, STICKER_ID, convert_static as convert_static_sticker, discover as discover_stickers, discover_static_sources, load as load_sticker_catalog, save as save_sticker_catalog, sticker_folder, suggested_identifier
from onmyoji_daemon import management as daemon_management
from onmyoji_daemon.linux_service import DEDICATED_SETUP_ENVIRONMENT, ServiceLimits, account_command, account_name as linux_account_name, limits_json, limits_summary, linux_tool, load_execution_profile, provision_account, recommended_limits, save_execution_profile, validate_account
from onmyoji_daemon.publication_diagnostics import diagnose as diagnose_publication
from onmyoji_daemon.cloudflare_tunnel.installer import inspect_cloudflared_directory
from onmyoji_daemon.mcp.runtime import McpRuntimeError, ensure_mcp_runtime, mcp_runtime_python, mcp_runtime_ready
from onmyoji_daemon.mcp.ocr.runtime import DocumentError, install_runtime as install_document_runtime
SYSTEM_BEGIN = "# BEGIN ONMYOJI MANAGED CODEX SETTINGS"
SYSTEM_END = "# END ONMYOJI MANAGED CODEX SETTINGS"
INSTANCE_SKILLS_DIRECTORY = "shikigami-skills"
SHIKIGAMI_SKILLS_STATE = "configs/shikigami/skills.toml"
SANDBOXES = ["read-only", "workspace-write", "danger-full-access"]
APPROVALS = ["on-request", "never"]
PYTHON_COMPONENTS = (
    ("aiohttp", "Daemon", "Daemon, Telegram e Mini Apps"),
    ("cryptography", "Daemon", "Daemon e certificados locais"),
    ("pywin32", "Windows e Excel", "Serviço do Windows e MCP Excel desktop"),
    ("boto3", "Daemon", "MCP AWS"),
    ("paramiko", "Daemon", "MCP SSH"),
    ("setuptools", "Base MCP", "Host MCP e instalação de componentes"),
    ("mcp", "Base MCP", "Host de todas as integrações MCP"),
    ("pykeepass", "Base MCP", "Vault, OAuth e integrações com credenciais"),
    ("IMAPClient", "Base MCP", "MCP E-mail"),
    ("jsonschema", "Base MCP", "Validação de dados e schemas MCP"),
    ("PyMuPDF", "Documentos", "MCP PDF e OCR"),
    ("pypdf", "Documentos", "MCP PDF"),
    ("reportlab", "Documentos", "MCP PDF"),
    ("Pillow", "Documentos", "MCP PDF e OCR"),
    ("openpyxl", "Documentos", "MCP PDF e OCR"),
    ("pyHanko", "Documentos", "MCP PDF e PDF/A"),
    ("ocrmypdf", "OCR básico", "MCP OCR e PDF pesquisável"),
    ("paddleocr", "OCR avançado", "MCP OCR avançado"),
    ("paddlepaddle", "OCR avançado", "MCP OCR avançado CPU ou NVIDIA"),
    ("paddlepaddle-gpu", "OCR avançado", "MCP OCR avançado NVIDIA"),
)
TEXT_LOGO = """⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡀⣚⡀⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣤⣴⡶⠿⠍⠛⠓⣸⡀⠛⠻⠽⠷⣶⣤⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣴⡾⠛⠉⠰⡆⡠⠤⣴⠋⣹⡉⢳⡤⠄⡀⡆⠉⠙⠻⣶⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⠿⠹⡀⠀⠀⠀⠀⠀⠀⠻⣜⣋⣛⡼⠃⠀⠀⠀⠀⠀⠀⡥⠙⢿⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⡀⠀⢀⣞⠝⠁⠀⠀⠙⢦⠀⢀⣠⡴⠶⠚⢊⣿⣇⠚⠲⠦⣤⡀⠀⣠⠎⠀⠀⠀⠙⣷⣄⠀⢀⡀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⡗⢤⣿⠏⠐⠑⠀⠀⠀⢀⡻⠊⠁⠀⠀⠀⣾⣿⣿⡄⠀⠀⠀⠙⠺⣇⠀⠀⠀⠒⠁⠈⢿⣆⣐⡆⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣇⡿⢯⠖⠋⡛⢦⡀⣰⠏⠀⠀⣠⡶⡛⣼⡿⠻⢿⣿⣈⡳⣦⠀⠀⠘⢷⡀⣰⠚⣛⠓⣌⠿⣆⡇⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠈⣿⢹⠹⣜⠾⠛⣱⠧⣭⣤⣬⣭⣭⣭⣵⣿⣥⠤⢤⣭⣧⣭⣬⣥⣭⣬⣬⡥⢧⡚⠛⢓⡼⢹⢻⡧⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⣤⢿⣘⣀⣈⠉⠉⠁⣷⠈⠛⢿⣯⣍⢉⣿⠃⠊⠉⢻⣶⣡⣌⢈⣿⣿⠟⠉⢸⡄⠉⠉⢉⣀⣸⣸⢳⡀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣿⡼⠀⠀⠀⠀⠀⣿⠀⠀⠀⣭⡻⣿⣯⡳⡀⢈⣸⡿⣹⣿⡝⣫⡆⠀⠀⣸⠇⠀⠀⠀⠀⢸⢸⠎⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⣽⣧⠀⢩⠀⠀⠀⠸⣇⠀⠀⠐⣹⣿⠙⠻⣊⣭⡿⠞⠉⢹⣿⡑⠀⠀⢠⡟⠀⠀⠀⢨⠀⢼⣿⡌⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⡧⢿⣆⢸⠀⡀⠀⠀⠘⢧⡀⢰⣿⣿⣴⡾⠟⢯⢞⠿⣷⣤⣿⣧⢀⣰⠟⠀⠀⢀⡀⢸⣄⣿⠃⡇⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠧⠈⢿⣾⠊⠁⠢⣀⡴⠒⠲⢿⠿⢋⠁⠀⠀⢸⠀⠀⠈⢙⠻⣿⠶⢓⠶⣄⠠⠂⠉⣺⣿⠃⠠⠅⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠁⠀⠀⠻⣷⣄⢁⣿⢸⣷⣿⣌⡇⠉⠛⠓⠖⢾⠶⠒⠛⠉⠁⣏⢾⣿⣷⢸⠆⢁⣴⡿⠁⠀⠈⠁⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠻⣿⣍⡓⠶⠖⠋⠄⠀⠀⠀⢀⢸⢀⠀⠀⠀⠀⡌⠲⠶⠖⣫⣴⠿⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠫⣗⣦⣌⣀⠀⠀⠀⢸⢸⢸⠂⠀⠀⢀⣉⣤⣶⠽⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⢀⣤⣶⣦⣤⣄⣀⠀⠀⠀⠀⠀⠀⠀⠈⠙⠓⠺⠯⠥⠄⢸⠠⠬⠭⠿⠒⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⢀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⣤⣾⣿⠿⠛⠻⠛⠻⣿⣷⡄⡀⢀⣀⠀⠀⠀⣠⡄⠀⣠⣄⠀⢹⠁⢠⣤⠀⣤⣤⠄⠀⢠⣤⠀⠀⡋⠭⣭⣍⡃⠀⢠⣤⡄⠀⢤⣤⠄⠀
⠀⣾⣿⡿⠃⠀⠀⡀⠀⠀⠘⣿⣿⡄⣸⣿⣷⣄⠀⢸⠀⠀⢸⢿⣆⠀⣠⢿⣷⠀⠈⠻⣧⣠⠎⠀⣴⠃⠀⠀⠀⠙⣿⡆⠠⣿⡇⠀⢸⣿⠀⠀
⠀⢿⣿⣧⡀⠀⠸⠿⠃⠀⢀⣿⣿⠇⢸⠃⠈⠻⣷⣽⠀⠀⣾⠊⢿⣿⠋⢻⣿⠀⠀⠀⣿⡇⠀⠀⣿⡄⠀⠀⠀⠀⣸⠇⠰⣿⡇⠀⢸⣿⠀⠀
⠀⠈⠻⢿⣷⣀⣀⠀⠀⣠⣾⣿⠏⠀⠾⠦⠀⠀⠈⢿⠀⠰⠿⠄⠈⠃⠀⠼⠿⠆⠀⠀⣿⠧⠀⠀⠈⠻⠷⠤⠤⠒⠁⠀⢠⣿⠁⠀⠼⠿⠄⠀
⠀⠀⠀⠀⠉⠉⣉⣥⣾⡿⠟⣁⣀⣀⣀⣀⣀⣀⣀⣀⣀⡀⣀⠀⠀⠀⠀⠀⢀⡀⣀⣈⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⠀⠔⠋⠁⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠨⠙⠛⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠀⠀⠀⠀⠀⠈⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀"""


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


def publication_test_cancelled() -> bool:
    """Consulta X sem deixar uma thread de leitura pendente após o teste público."""
    if not sys.stdin.isatty():
        return False
    if os.name == "nt":
        import msvcrt
        while msvcrt.kbhit():
            if msvcrt.getwch().casefold() in {"x", "\x1b"}:
                return True
        return False
    import select
    ready, _, _ = select.select([sys.stdin], [], [], 0)
    return bool(ready) and sys.stdin.readline().strip().casefold() in {"x", "\x1b"}


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


def vault_entry_path(root: Path, layer: str, integration: str, profile: str = "", definition: str = "") -> str:
    """Monta a referência padrão de uma entrada KeePass da instância.

    A hierarquia de grupos permanece limitada ao Shikigami e à camada. O
    restante identifica a entrada pelo título, separado por dois-pontos.
    """
    title = ":".join(value for value in (integration, profile, definition) if value)
    return f"{instance_identity(root)}/{layer}/{title}"


def default_system() -> dict[str, object]:
    return {
        "shikigami_identity": "",
        "shikigami_installed": False,
        "python_mode": "isolated",
        "python_base_executable": "",
        "python_executable": "",
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


def install_codex_cli(root: Path) -> str | None:
    """Instala o pacote oficial no prefixo local da identidade em execução."""
    npm = shutil.which("npm")
    if not npm and os.name != "nt" and not dedicated_child_profile(root):
        installed = install_linux_packages("Node.js/npm para instalar o Codex CLI", {
            "apt-get": ["nodejs", "npm"], "dnf": ["nodejs", "npm"], "yum": ["nodejs", "npm"],
            "zypper": ["nodejs", "npm"], "pacman": ["nodejs", "npm"],
        })
        if installed:
            npm = shutil.which("npm") or linux_tool("npm")
    if not npm:
        result(False, "O npm não está disponível nesta identidade. Volte e aceite a preparação de Node.js/npm ou informe um Codex já instalado.")
        return None
    home, prefix = Path.home().resolve(), Path.home().resolve() / ".local"
    flow_heading("Instalação assistida", "Codex CLI para a identidade em execução")
    note("Será instalado @openai/codex@latest pelo npm, conforme a documentação oficial da OpenAI.")
    print(f"  Conta:   {linux_current_user() if os.name != 'nt' else getpass.getuser()}")
    print(f"  Destino: {prefix}")
    print(f"  Binário: {prefix / 'bin' / 'codex'}")
    if prompt("Instalar ou atualizar o Codex CLI nesse destino? [S/n]: ").strip().casefold() in {"n", "nao", "não", "x", "\x1b"}:
        result(False, "Instalação do Codex CLI cancelada.")
        return None
    try:
        prefix.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ); environment.update({"HOME": str(home), "npm_config_prefix": str(prefix), "PATH": str(prefix / "bin") + os.pathsep + os.environ.get("PATH", dedicated_path(home))})
        completed = subprocess.run([npm, "install", "--global", "--prefix", str(prefix), "@openai/codex@latest"], cwd=str(codex_setup_directory(root)), env=environment, check=False)
    except OSError as error:
        result(False, f"Não foi possível executar o npm: {error}")
        return None
    executable = prefix / "bin" / "codex"
    if completed.returncode != 0:
        result(False, f"O npm terminou com código {completed.returncode}; o Codex CLI não foi certificado.")
        return None
    valid, detail = validate_codex_executable(str(executable))
    result(valid, detail)
    return str(executable.absolute()) if valid else None


def choose_codex_executable(current: str, flow: bool = False, root: Path | None = None) -> str | None:
    """Oferece executáveis encontrados, instalação assistida e caminho completo."""
    found = discover_codex_executables()
    install = "Instalar ou atualizar o Codex CLI nesta conta"
    manual = "Informar caminho completo"
    if not found:
        note("Nenhum executável Codex foi encontrado no PATH atual.")
    chooser = flow_choice if flow else choose
    options = [*found, *([install] if root is not None and os.name != "nt" else []), manual]
    default = current if current in found else (install if install in options else manual)
    selected = chooser("Executável Codex", options, default)
    if selected is None:
        return None
    if selected == install and root is not None:
        return install_codex_cli(root)
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


def choose_codex_model(catalog: list[dict[str, object]], current: str, flow: bool = False) -> dict[str, object] | None:
    """Permite selecionar exclusivamente modelos disponíveis para a conta atual."""
    options = [model_identifier(model) for model in catalog]
    selected = (flow_choice if flow else choose)("Modelo", options, current if current in options else "")
    return catalog_model(catalog, selected) if selected else None


def choose_reasoning_effort(model: dict[str, object], current: str, reset_to_model_default: bool = False, flow: bool = False) -> str | None:
    """Seleciona somente esforços declarados pelo catálogo para o modelo escolhido."""
    values = reasoning_efforts_for_model(model)
    selected = preferred_reasoning_effort(model) if reset_to_model_default or current not in values else current
    return (flow_choice if flow else choose)("Esforço de raciocínio", values, selected) if values else None


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


def codex_setup_directory(root: Path) -> Path:
    """Retorna a área temporária para comandos auxiliares do Codex-CLI."""
    directory = root.resolve() / "tmp" / "codex-cli"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def validate_codex_login(root: Path, data: dict[str, object]) -> tuple[bool, str]:
    """Confirma a autenticação do CODEX_HOME da instância sem revelar credenciais."""
    executable_ok, executable_detail = validate_codex_executable(str(data["executable"]))
    if not executable_ok:
        return False, executable_detail
    environment = dict(os.environ); environment["CODEX_HOME"] = str(root)
    try:
        completed = subprocess.run([str(data["executable"]), "login", "status"], cwd=str(codex_setup_directory(root)), env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=20)
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
        process = subprocess.Popen([executable, "app-server"], cwd=str(codex_setup_directory(root)), env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
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
    temporary = codex_setup_directory(root)
    command = [executable, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--model", model, "--config", f"model_reasoning_effort={json.dumps(effort)}", "Responda exatamente: OK"]
    try:
        completed = subprocess.run(command, cwd=str(temporary), env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=120)
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
    python = raw.get("python", {})
    if isinstance(python, dict):
        if python.get("mode") in {"isolated", "global"}: values["python_mode"] = python["mode"]
        if isinstance(python.get("base_python"), str): values["python_base_executable"] = python["base_python"]
        if isinstance(python.get("executable"), str): values["python_executable"] = python["executable"]
    else:
        # Migração sem remoção: o próximo salvamento centraliza a escolha antiga.
        try:
            legacy = tomllib.loads((root / "configs" / "daemon" / "daemon.toml").read_text(encoding="utf-8")).get("runtime", {})
        except (OSError, tomllib.TOMLDecodeError):
            legacy = {}
        if isinstance(legacy, dict):
            legacy_mode = str(legacy.get("mode") or "dedicated")
            if legacy_mode == "global":
                values["python_mode"] = "global"; values["python_executable"] = str(legacy.get("executable") or "")
            else:
                values["python_mode"] = "isolated"; values["python_base_executable"] = str(legacy.get("base_python") or legacy.get("executable") or sys.executable)
    for key, value in raw.get("codex", {}).items():
        if key in values and isinstance(value, str): values[key] = value
        if key == "additional_writable_directories" and isinstance(value, list) and all(isinstance(item, str) for item in value): values[key] = value
    return values


def render_system(data: dict[str, object]) -> str:
    codex_keys = tuple(key for key in default_system() if not key.startswith(("shikigami_", "python_")))
    mode = data.get("python_mode", "isolated")
    base = data.get("python_base_executable", "") if mode == "isolated" else ""
    executable = data.get("python_executable", "") if mode == "global" else ""
    return "\n".join(["schema_version = 2", "", "[shikigami]", f"identity = {json.dumps(data['shikigami_identity'], ensure_ascii=False)}", f"installed = {str(bool(data['shikigami_installed'])).lower()}", "", "[python]", f"mode = {json.dumps(mode)}", f"base_python = {json.dumps(base)}", f"executable = {json.dumps(executable)}", "", "[codex]", *[f"{key} = {json.dumps(data[key], ensure_ascii=False)}" for key in codex_keys]]) + "\n"


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
    needs_save = not isinstance(section, dict) or not isinstance(section.get("identity"), str) or not isinstance(section.get("installed"), bool) or not isinstance(raw.get("python") if isinstance(raw, dict) else None, dict)
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


def choose_sandbox(root: Path, workspace: Path, current: str, flow: bool = False) -> str | None:
    """Explica os limites de cada sandbox antes de permitir sua seleção."""
    root, workspace = root.resolve(), workspace.resolve()
    (flow_heading if flow else screen)("Sandbox", "Limites técnicos dos comandos gerados pelo Codex")
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
    default_index = SANDBOXES.index(current) + 1 if current in SANDBOXES else 1
    while True:
        choice = prompt(f"Opção [{default_index}]: ").strip().casefold()
        if choice in {"x", "\x1b"}: return None
        if not choice: return SANDBOXES[default_index - 1]
        if choice.isdigit() and 1 <= int(choice) <= len(SANDBOXES): return SANDBOXES[int(choice) - 1]
        result(False, "Opção inválida.")


def choose_approval_policy(root: Path, workspace: Path, sandbox: str, current: str, flow: bool = False) -> str | None:
    """Explica quando o Codex interrompe a execução para pedir autorização local."""
    root, workspace = root.resolve(), workspace.resolve()
    (flow_heading if flow else screen)("Política de aprovação", "Quando o Codex-CLI deve interromper a execução para pedir confirmação")
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
    default_index = APPROVALS.index(current) + 1 if current in APPROVALS else 1
    while True:
        choice = prompt(f"Opção [{default_index}]: ").strip().casefold()
        if choice in {"x", "\x1b"}: return None
        if not choice: return APPROVALS[default_index - 1]
        if choice.isdigit() and 1 <= int(choice) <= len(APPROVALS): return APPROVALS[int(choice) - 1]
        result(False, "Opção inválida.")


def configure_initial_codex(root: Path, data: dict[str, object]) -> dict[str, object] | None:
    """Configura executável, login e seleção dinâmica de modelo antes da instalação."""
    while True:
        flow_heading("Instalação inicial", "Codex-CLI")
        note("Primeiro confirme o executável e o login; depois escolha um modelo da conta autenticada.")
        executable = choose_codex_executable(str(data["executable"]), flow=True, root=root)
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
            if not configure_codex_login(root, candidate, flow=True):
                return None
            logged_in, login_detail = validate_codex_login(root, candidate)
            result(logged_in, login_detail)
            if not logged_in:
                continue
        catalog, catalog_detail = codex_model_catalog(root, candidate)
        result(catalog is not None, catalog_detail)
        if catalog is None:
            continue
        model = choose_codex_model(catalog, str(candidate["model"]), flow=True)
        if model is None:
            return None
        candidate["model"] = model_identifier(model)
        effort = choose_reasoning_effort(model, str(candidate["model_reasoning_effort"]), reset_to_model_default=True, flow=True)
        if effort is None:
            result(False, "O catálogo não informou esforços compatíveis para o modelo selecionado.")
            continue
        candidate["model_reasoning_effort"] = effort
        model_ok, model_detail = validate_codex_model(root, candidate)
        result(model_ok, model_detail)
        if model_ok:
            return candidate


def linux_current_user() -> str:
    try:
        completed = subprocess.run(["id", "-un"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=5, check=False)
        return completed.stdout.strip() or getpass.getuser()
    except (OSError, subprocess.TimeoutExpired):
        return getpass.getuser()


def install_linux_packages(purpose: str, packages: dict[str, list[str]]) -> bool:
    """Oferece uma correção explícita para pré-requisitos do sistema operacional."""
    managers = (
        ("apt-get", ["update"], ["install", "-y"]),
        ("dnf", [], ["install", "-y"]),
        ("yum", [], ["install", "-y"]),
        ("zypper", [], ["--non-interactive", "install"]),
        ("pacman", [], ["--sync", "--refresh", "--needed", "--noconfirm"]),
    )
    selected = next(((name, linux_tool(name), refresh, install) for name, refresh, install in managers if linux_tool(name) and packages.get(name)), None)
    if selected is None:
        result(False, f"Não há um gerenciador de pacotes compatível para preparar {purpose} automaticamente.")
        return False
    name, executable, refresh_arguments, install_arguments = selected
    requested = packages[name]
    flow_heading("Preparação de pré-requisitos", purpose)
    note(f"Gerenciador detectado: {name}. Pacotes propostos: {', '.join(requested)}.")
    note("O setup mostrará a saída do gerenciador e só continuará se a instalação terminar sem erro.")
    if prompt("Autorizar a instalação desses pacotes com privilégios administrativos? [S/n]: ").strip().casefold() in {"n", "nao", "não", "x", "\x1b"}:
        result(False, "Instalação de pré-requisitos não autorizada.")
        return False
    elevated = getattr(os, "geteuid", lambda: 0)() != 0
    sudo = linux_tool("sudo") if elevated else None
    if elevated and not sudo:
        result(False, "sudo não está disponível para instalar os pré-requisitos.")
        return False
    commands = []
    if refresh_arguments: commands.append([str(executable), *refresh_arguments])
    commands.append([str(executable), *install_arguments, *requested])
    prefix = [str(sudo), "--"] if elevated else []
    for command in commands:
        try: completed = subprocess.run([*prefix, *command], check=False)
        except OSError as error: result(False, f"Não foi possível executar {name}: {error}"); return False
        if completed.returncode != 0:
            result(False, f"{name} terminou com código {completed.returncode}.")
            return False
    result(True, f"Pré-requisitos de {purpose} instalados.")
    return True


def dedicated_command_check(root: Path, profile: dict[str, object], arguments: list[str]) -> tuple[bool, str]:
    """Executa uma verificação com HOME e PATH da conta dedicada."""
    account, home = str(profile["account"]), Path(str(profile["home"]))
    environment = {"CODEX_HOME": str(root.resolve()), "PATH": dedicated_path(home)}
    try:
        delegated = account_command(account, home, ["/usr/bin/env", *arguments], environment)
        completed = subprocess.run(delegated, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=30, check=False)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    return completed.returncode == 0, diagnostic_detail(completed)


def dedicated_command_version(root: Path, profile: dict[str, object], command: str) -> tuple[bool, str]:
    return dedicated_command_check(root, profile, [command, "--version"])


def prepare_dedicated_prerequisites(root: Path, profile: dict[str, object]) -> None:
    """Prepara dependências instaláveis antes de entregar o roteiro à conta sem sudo."""
    codex_ready, _detail = dedicated_command_version(root, profile, "codex")
    npm_ready, _detail = dedicated_command_version(root, profile, "npm")
    if not codex_ready and not npm_ready:
        installed = install_linux_packages("Node.js/npm para instalar o Codex CLI na conta dedicada", {
            "apt-get": ["nodejs", "npm"], "dnf": ["nodejs", "npm"], "yum": ["nodejs", "npm"],
            "zypper": ["nodejs", "npm"], "pacman": ["nodejs", "npm"],
        })
        if installed:
            npm_ready, detail = dedicated_command_version(root, profile, "npm")
            result(npm_ready, detail or "npm acessível à conta dedicada.")
    python_ready, _detail = dedicated_command_check(root, profile, ["python3", "-I", "-B", "-c", "import sys, venv; assert sys.version_info >= (3, 11)"])
    if not python_ready:
        installed = install_linux_packages("Python 3 para executar o setup e criar o runtime", {
            "apt-get": ["python3", "python3-venv"], "dnf": ["python3"], "yum": ["python3"],
            "zypper": ["python3"], "pacman": ["python"],
        })
        if installed:
            python_ready, detail = dedicated_command_check(root, profile, ["python3", "-I", "-B", "-c", "import sys, venv; assert sys.version_info >= (3, 11)"])
            result(python_ready, detail or "Python 3.11+ com venv acessível à conta dedicada.")


def dedicated_path(home: Path) -> str:
    values = [str(home / ".local" / "bin"), str(home / "bin"), "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]
    return os.pathsep.join(values)


def dedicated_child_profile(root: Path) -> dict[str, object]:
    if os.environ.get(DEDICATED_SETUP_ENVIRONMENT) != "1": return {}
    profile = load_execution_profile(root)
    if profile.get("mode") != "dedicated": return {}
    account = str(profile.get("account") or "")
    return profile if account and linux_current_user() == account else {}


def configure_installer_git_access(root: Path) -> tuple[bool, str]:
    """Autoriza somente esta instância no Git global do instalador após a troca de proprietário."""
    if not shutil.which("git"): return False, "Git não está disponível para configurar o acesso do instalador."
    current = subprocess.run(["git", "config", "--global", "--get-all", "safe.directory"], text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    resolved = str(root.resolve())
    if resolved in {line.strip() for line in current.stdout.splitlines()}:
        return True, "Instância já autorizada no Git do instalador."
    changed = subprocess.run(["git", "config", "--global", "--add", "safe.directory", resolved], text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    return changed.returncode == 0, (changed.stderr or changed.stdout).strip() or "Instância autorizada no Git do instalador."


def validate_dedicated_environment(root: Path, data: dict[str, object]) -> bool:
    """Exercita o ambiente efetivo que será herdado pelo daemon e pelo Codex."""
    profile = dedicated_child_profile(root)
    if not profile:
        result(False, "A fase dedicada não corresponde à conta registrada."); return False
    account, home = str(profile["account"]), Path(str(profile["home"]))
    workspace = Path(str(profile["workspace"])).resolve()
    flow_heading("Validação da identidade dedicada", f"Conta {account} · home {home}")
    checks: list[tuple[bool, str]] = [
        (Path(os.environ.get("HOME", "")).resolve() == home.resolve(), "HOME da conta dedicada"),
        (os.access(root, os.R_OK | os.W_OK | os.X_OK), "Acesso completo à instância"),
        (workspace.is_dir() and os.access(workspace, os.R_OK | os.W_OK | os.X_OK), "Acesso completo ao workspace"),
    ]
    python_value = str(data.get("python_base_executable") if data.get("python_mode") == "isolated" else data.get("python_executable") or "")
    python_path = Path(python_value).expanduser()
    checks.append((python_path.is_file() and os.access(python_path, os.X_OK), "Python selecionado acessível"))
    codex_path = Path(str(data.get("executable") or "")).expanduser()
    checks.append((codex_path.is_file() and os.access(codex_path, os.X_OK), "Codex-CLI acessível"))
    git_environment = dict(os.environ); git_environment.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    try:
        status = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=no"], cwd=str(root), env=git_environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=20, check=False)
        checks.append((status.returncode == 0, "Repositório Git acessível sem conflito de proprietário"))
        remotes = subprocess.run(["git", "remote"], cwd=str(root), env=git_environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10, check=False)
        remote_names = remotes.stdout.split() if remotes.returncode == 0 else []
        for remote in (name for name in ("upstream", "origin") if name in remote_names):
            reachable = subprocess.run(["git", "ls-remote", remote], cwd=str(root), env=git_environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60, check=False)
            checks.append((reachable.returncode == 0, f"Acesso ao remoto Git {remote}"))
    except (OSError, subprocess.TimeoutExpired):
        checks.append((False, "Execução e remotos do Git"))
    for ready, label in checks: result(ready, label)
    return all(ready for ready, _label in checks)


def launch_dedicated_setup(root: Path, profile: dict[str, object]) -> bool:
    account, home = str(profile["account"]), Path(str(profile["home"]))
    environment = {"CODEX_HOME": str(root.resolve()), DEDICATED_SETUP_ENVIRONMENT: "1", "PYTHONDONTWRITEBYTECODE": "1", "PATH": dedicated_path(home)}
    candidates = [Path(sys.executable), Path("/usr/bin/python3")]
    discovered = shutil.which("python3")
    if discovered: candidates.append(Path(discovered))
    selected: Path | None = None
    for candidate in dict.fromkeys(path.resolve() for path in candidates if path.is_file()):
        try: probe_command = account_command(account, home, [str(candidate), "-B", "-c", "import sys; assert sys.version_info >= (3, 11)"], environment)
        except (RuntimeError, ValueError) as error: result(False, str(error)); return False
        try:
            probe = subprocess.run(probe_command, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0: selected = candidate; break
    if selected is None:
        result(False, "Nenhum Python 3.11+ acessível à conta dedicada foi encontrado fora do home do instalador."); return False
    command = account_command(account, home, [str(selected), "-B", str(root / "setupOnmyoji.py"), "--dedicated-phase"], environment)
    try: completed = subprocess.run(command, check=False)
    except OSError as error: result(False, f"Não foi possível iniciar a fase dedicada: {error}"); return False
    if completed.returncode != 0:
        result(False, f"A fase dedicada terminou com código {completed.returncode}."); return False
    if not bool(profile.get("installer_access", True)): return True
    validated = load_execution_profile(root)
    if validated.get("validated") is not True:
        result(False, "A fase dedicada terminou sem certificar o ambiente."); return False
    return True


def run_dedicated_menu(root: Path, destination: str) -> bool:
    profile = load_execution_profile(root)
    if profile.get("mode") != "dedicated" or profile.get("validated") is not True:
        return False
    account, home = str(profile.get("account") or ""), Path(str(profile.get("home") or ""))
    if linux_current_user() == account:
        return False
    python = Path(str(profile.get("setup_python") or "/usr/bin/python3"))
    environment = {"CODEX_HOME": str(root.resolve()), DEDICATED_SETUP_ENVIRONMENT: "1", "PYTHONDONTWRITEBYTECODE": "1", "PATH": dedicated_path(home)}
    try: command = account_command(account, home, [str(python), "-B", str(root / "setupOnmyoji.py"), "--dedicated-menu", destination], environment)
    except (RuntimeError, ValueError) as error: result(False, str(error)); return True
    try: completed = subprocess.run(command, check=False)
    except OSError as error: result(False, f"Não foi possível abrir o menu sob {account}: {error}"); return True
    if completed.returncode: result(False, f"O menu dedicado terminou com código {completed.returncode}.")
    return True


def launch_mcp_menu(root: Path) -> bool:
    """Prepara o runtime único e abre o setup MCP com seu próprio Python."""
    if not mcp_runtime_ready(root):
        flow_heading("Preparação do MCP", "Componentes necessários no runtime Python da instância")
        note("O runtime MCP está ausente ou incompleto; ele precisa ser preparado antes de abrir as integrações.")
        if prompt("Instalar ou reparar os componentes MCP agora? [S/n]: ").strip().casefold() not in {"", "s", "sim"}:
            result(False, "A abertura do MCP foi cancelada sem alterar o runtime.")
            return False
        try:
            note(ensure_mcp_runtime(root, progress=note))
        except McpRuntimeError as error:
            result(False, f"Não foi possível preparar o runtime MCP: {error}")
            return False
    try:
        executable = mcp_runtime_python(root)
    except (OSError, python_runtime.RuntimeError) as error:
        result(False, f"Não foi possível localizar o Python do runtime MCP: {error}")
        return False
    if not executable.is_file():
        result(False, f"O Python do runtime MCP não foi encontrado em {executable}.")
        return False
    try:
        completed = subprocess.run([str(executable), "-B", str(root / "onmyoji-daemon" / "setupMcp.py")], check=False)
    except OSError as error:
        result(False, f"Não foi possível abrir o setup MCP: {error}")
        return False
    if completed.returncode:
        result(False, f"O setup MCP terminou com código {completed.returncode}.")
        return False
    return True


def execution_diagnostics(root: Path, progress=None) -> list[dict[str, str]]:
    """Executa os diagnósticos sob a identidade que efetivamente executará o Shikigami."""
    profile = load_execution_profile(root)
    if profile.get("mode") != "dedicated" or profile.get("validated") is not True or linux_current_user() == profile.get("account"):
        return shikigami_diagnostics(root, progress)
    if progress:
        progress(f"Executando verificações como {profile.get('account')}...")
    account, home = str(profile.get("account") or ""), Path(str(profile.get("home") or ""))
    python = Path(str(profile.get("setup_python") or "/usr/bin/python3"))
    environment = {"CODEX_HOME": str(root.resolve()), DEDICATED_SETUP_ENVIRONMENT: "1", "PYTHONDONTWRITEBYTECODE": "1", "PATH": dedicated_path(home)}
    try:
        command = account_command(account, home, [str(python), "-B", str(root / "setupOnmyoji.py"), "--dedicated-checks"], environment)
        completed = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=180, check=False)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        return [{"state": "error", "label": "Diagnóstico sob identidade dedicada", "detail": str(error)}]
    try:
        diagnostics = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        diagnostics = None
    if completed.returncode or not isinstance(diagnostics, list):
        detail = diagnostic_detail(completed) or f"O diagnóstico terminou com código {completed.returncode}."
        return [{"state": "error", "label": "Diagnóstico sob identidade dedicada", "detail": detail}]
    return diagnostics


def prepare_dedicated_environment(root: Path, identity: str, workspace: Path, account: str, installer: str, installer_access: bool) -> bool:
    """Provisiona a identidade e delega toda configuração sensível ao seu processo filho."""
    home = Path("/home") / account
    profile = {"mode": "dedicated", "identity": identity, "account": account, "group": account, "home": str(home), "installer_user": installer, "installer_access": installer_access, "workspace": str(workspace.resolve()), "validated": False}
    try: save_execution_profile(root, profile)
    except OSError as error: result(False, f"Não foi possível registrar a identidade dedicada: {error}"); return False
    if installer_access:
        configured, detail = configure_installer_git_access(root); result(configured, detail)
        if not configured: return False
    prepared, detail, _home = provision_account(account, installer, root, workspace, True, lambda message: print("  → " + message), share_with_installer=installer_access)
    if not prepared and detail.startswith("Ferramentas Linux ausentes:"):
        repaired = install_linux_packages("conta Linux isolada e permissões herdáveis", {
            "apt-get": ["passwd", "acl"], "dnf": ["shadow-utils", "acl"], "yum": ["shadow-utils", "acl"],
            "zypper": ["shadow", "acl"], "pacman": ["shadow", "acl"],
        })
        if repaired:
            prepared, detail, _home = provision_account(account, installer, root, workspace, True, lambda message: print("  → " + message), share_with_installer=installer_access)
    result(prepared, detail)
    if not prepared: return False
    prepare_dedicated_prerequisites(root, profile)
    return launch_dedicated_setup(root, profile)


def validate_setup_python(executable: Path, isolated: bool) -> tuple[bool, str, bool]:
    """Valida versão e, quando necessário, a capacidade de criar ambientes venv."""
    try:
        version = subprocess.run([str(executable), "-I", "-B", "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'); assert sys.version_info >= (3, 11)"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"Não foi possível validar o Python: {error}", False
    if version.returncode != 0:
        return False, diagnostic_detail(version) or "O interpretador precisa ser Python 3.11 ou superior.", False
    label = (version.stdout or "").strip()
    if not isolated:
        return True, f"Python {label} validado.", False
    try:
        venv = subprocess.run([str(executable), "-I", "-B", "-c", "import venv; assert hasattr(venv, 'EnvBuilder')"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"Não foi possível validar o módulo venv: {error}", True
    if venv.returncode != 0:
        return False, diagnostic_detail(venv) or f"Python {label} não possui o módulo venv funcional.", True
    return True, f"Python {label} e módulo venv validados.", False


def configure_python_runtime(root: Path, data: dict[str, object]) -> dict[str, object] | None:
    while True:
        flow_heading("Instalação inicial", "Runtime Python do Shikigami")
        print("  1. Ambiente isolado — cria /venv na raiz da instância.")
        print("  2. Python global — instala dependências no interpretador selecionado.")
        default_mode = "2" if data.get("python_mode") == "global" else "1"
        mode = prompt(f"Opção [{default_mode}]: ").strip().casefold() or default_mode
        if mode in {"x", "\x1b"}: return None
        if mode not in {"1", "2"}:
            result(False, "Escolha 1 ou 2.")
            continue
        configured = str(data.get("python_base_executable") if mode == "1" else data.get("python_executable") or "")
        selected_python = prompt(f"Python [{configured or sys.executable}]: ").strip() or configured or sys.executable
        selected_path = Path(selected_python).expanduser()
        if not selected_path.is_file():
            result(False, "Informe um executável Python existente.")
            continue
        valid, detail, missing_venv = validate_setup_python(selected_path, mode == "1")
        result(valid, detail)
        if not valid:
            if missing_venv and os.name != "nt" and not dedicated_child_profile(root):
                executable_name = selected_path.name
                apt_package = f"{executable_name}-venv" if re.fullmatch(r"python3\.\d+", executable_name) else "python3-venv"
                if install_linux_packages("módulo venv do Python selecionado", {
                    "apt-get": [apt_package], "dnf": ["python3"], "yum": ["python3"],
                    "zypper": ["python3"], "pacman": ["python"],
                }):
                    valid, detail, _missing = validate_setup_python(selected_path, True); result(valid, detail)
            if not valid:
                continue
        candidate = dict(data); candidate["python_mode"] = "isolated" if mode == "1" else "global"
        candidate["python_base_executable"] = str(selected_path) if mode == "1" else ""
        candidate["python_executable"] = str(selected_path) if mode == "2" else ""
        return candidate


def initial_installation_wizard(root: Path, data: dict[str, object]) -> dict[str, object] | None:
    """Coleta a identidade e confirma um Codex-CLI funcional antes de instalar o Shikigami."""
    while True:
        child_profile = dedicated_child_profile(root)
        if child_profile:
            candidate = dict(data); candidate["shikigami_identity"] = str(child_profile.get("identity") or data.get("shikigami_identity") or "Shikigami"); candidate["project_directory"] = str(child_profile["workspace"])
        else:
            flow_heading("Instalação inicial", "Identidade do Shikigami")
            note("Informe o nome exibido pelo Shikigami. Digite X para cancelar.")
            suggested = " ".join(str(data.get("shikigami_identity") or "").split())
            label = f"Nome do Shikigami [{suggested}]: " if suggested else "Nome do Shikigami: "
            raw = prompt(label).strip()
            if raw.casefold() in {"x", "\x1b"}: return None
            identity = " ".join(raw.split()) or suggested
            if not identity: result(False, "Informe um nome não vazio."); continue
            candidate = dict(data); candidate["shikigami_identity"] = identity
            project_directory = choose_project_directory(root, str(candidate["project_directory"]))
            if project_directory is None: return None
            candidate["project_directory"] = project_directory
            if os.name != "nt":
                execution = flow_choice("Identidade de execução", ["Usuário atual", "Usuário Linux exclusivo"], "Usuário Linux exclusivo")
                if execution is None: return None
                installer = linux_current_user()
                if execution == "Usuário Linux exclusivo":
                    default_account = linux_account_name(identity)
                    raw_account = prompt(f"Usuário Linux exclusivo [{default_account}]: ").strip() or default_account
                    try: account = validate_account(raw_account)
                    except ValueError as error: result(False, str(error)); continue
                    share = prompt(f"Permitir ao usuário {installer} acesso completo à instância e ao workspace? [S/n]: ").strip().casefold() not in {"n", "nao", "não"}
                    candidate_saved, detail = save_system(root, candidate, validate=False); result(candidate_saved, detail)
                    if not candidate_saved: return None
                    if not prepare_dedicated_environment(root, identity, Path(project_directory), account, installer, share): return None
                    if not share: raise SystemExit(0)
                    installed = load_system(root)
                    return installed if bool(installed.get("shikigami_installed")) else None
                save_execution_profile(root, {"mode": "current", "identity": identity, "account": installer, "home": str(Path.home()), "workspace": project_directory, "installer_user": installer, "installer_access": True, "validated": True})
        candidate = configure_python_runtime(root, candidate)
        if candidate is None: return None
        candidate = configure_initial_codex(root, candidate)
        if candidate is None:
            return None
        project_directory = str(candidate["project_directory"])
        sandbox = choose_sandbox(root, Path(project_directory), str(candidate["sandbox_mode"]), flow=True)
        if sandbox is None:
            return None
        candidate["sandbox_mode"] = sandbox
        approval = choose_approval_policy(root, Path(project_directory), sandbox, str(candidate["approval_policy"]), flow=True)
        if approval is None:
            return None
        candidate["approval_policy"] = approval
        if child_profile and not validate_dedicated_environment(root, candidate): return None
        candidate["shikigami_installed"] = True
        ok, message = save_system(root, candidate)
        if ok:
            if child_profile:
                child_profile["validated"] = True; child_profile["codex_executable"] = str(candidate["executable"]); child_profile["python_mode"] = str(candidate["python_mode"]); child_profile["setup_python"] = sys.executable
                save_execution_profile(root, child_profile)
            return candidate
        result(False, message)


def dedicated_migration_wizard(root: Path, data: dict[str, object]) -> dict[str, object] | None:
    """Revalida uma instalação existente inteiramente sob a nova identidade Linux."""
    profile = dedicated_child_profile(root)
    if not profile:
        result(False, "A migração não está sendo executada pela conta Linux registrada."); return None
    candidate = dict(data); candidate["project_directory"] = str(profile["workspace"])
    candidate = configure_python_runtime(root, candidate)
    if candidate is None: return None
    candidate = configure_initial_codex(root, candidate)
    if candidate is None: return None
    if not validate_dedicated_environment(root, candidate): return None
    ok, message = save_system(root, candidate); result(ok, message)
    if not ok: return None
    profile["validated"] = True; profile["codex_executable"] = str(candidate["executable"]); profile["python_mode"] = str(candidate["python_mode"]); profile["setup_python"] = sys.executable
    try: save_execution_profile(root, profile)
    except OSError as error: result(False, f"Não foi possível certificar a identidade dedicada: {error}"); return None
    result(True, "Python, Codex, workspace e Git foram certificados sob a conta dedicada.")
    return candidate


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
        lines.extend([f"[skills.{json.dumps(skill.identifier, ensure_ascii=False)}]", f"enabled = {'true' if skill.identifier in identifiers else 'false'}", 'mode = "managed_copy"', f"source = {json.dumps(skill.source.name, ensure_ascii=False)}", ""])
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
                if not isinstance(identifier, str) or not isinstance(state, dict) or type(state.get("enabled")) is not bool or state.get("mode") != "managed_copy" or not isinstance(state.get("source", identifier), str): raise ValueError("registro inválido")
        except (OSError, tomllib.TOMLDecodeError, ValueError) as error: return False, f"O estado das skills é inválido: {error}"
        return True, "Estado das skills válido."
    return save_enabled(root, set(), skills)


def invoke(skill: Skill, root: Path) -> tuple[bool, str]:
    if skill.setup_script is None:
        return False, "Esta skill não possui rotina de configuração."
    subprocess.run([sys.executable, str(skill.setup_script), "--onmyoji-root", str(root), "--action", "configure"], check=False)
    return True, "Configuração concluída."


def terminal_width() -> int:
    """Retorna a largura visual comum às telas interativas do setup."""
    return max(56, min(shutil.get_terminal_size((78, 24)).columns, 96))


def opening_logo() -> None:
    """Exibe o logo textual centralizado sem redimensionar suas linhas."""
    width = terminal_width()
    for line in TEXT_LOGO.splitlines():
        print(line.center(width))


def screen(title: str, subtitle: str = "") -> None:
    width = terminal_width()
    print("\n" + Ui.text("╭" + "─" * (width - 2) + "╮", Ui.violet))
    heading = f" ONMYŌJI  /  {title.upper()}"
    print("│" + Ui.text(heading.ljust(width - 2), Ui.bold, Ui.violet) + "│")
    if subtitle: print("│ " + Ui.text(subtitle[:width - 4].ljust(width - 4), Ui.slate) + " │")
    print(Ui.text("╰" + "─" * (width - 2) + "╯", Ui.violet))


def flow_heading(title: str, subtitle: str = "") -> None:
    """Identifica um roteiro sem simular uma tela navegável."""
    print("\n  " + Ui.text(f"ONMYŌJI / {title.upper()}", Ui.bold, Ui.violet))
    if subtitle:
        print("  " + Ui.text(subtitle, Ui.slate))


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
    """Abre um submenu navegável de opções enumeradas."""
    screen(label, "Escolha uma opção ou pressione X para voltar")
    for index, value in enumerate(values, 1): item(f"{index}.", value)
    item("X.", "Voltar")
    default_index = values.index(current) + 1 if current in values else 1
    while True:
        choice = prompt(f"Opção [{default_index}]: ").strip().casefold()
        if choice in {"x", "\x1b"}: return None
        if not choice: return values[default_index - 1]
        if choice.isdigit() and 1 <= int(choice) <= len(values): return values[int(choice) - 1]
        result(False, "Opção inválida.")


def flow_choice(label: str, values: list[str], current: str = "", cancel_label: str = "Cancelar") -> str | None:
    """Coleta uma opção de um roteiro sem abrir um novo menu visual.

    A opção padrão sempre é apresentada pelo número que a seleciona, nunca
    pelo texto descritivo. ``X`` interrompe somente a etapa atual.
    """
    if not values:
        result(False, "Não há opções disponíveis.")
        return None
    print(f"\n  {label}")
    for index, value in enumerate(values, 1): item(f"{index}.", value)
    item("X.", cancel_label)
    default_index = values.index(current) + 1 if current in values else 1
    while True:
        choice = prompt(f"Opção [{default_index}]: ").strip().casefold()
        if choice in {"x", "\x1b"}: return None
        if not choice: return values[default_index - 1]
        if choice.isdigit() and 1 <= int(choice) <= len(values): return values[int(choice) - 1]
        result(False, "Opção inválida.")


def choose_eccovox_tts_capability(root: Path, profile: str, key: str, title: str, current: str = "") -> str | None:
    """Consulta o catálogo do EccoVox e evita gravar identificadores livres."""
    try:
        tts = EccoVoxService(root).capabilities(profile)["capabilities"]["tts"]
        if tts.get("status") not in {"ready", "degraded"}:
            raise EccoVoxError("runtime_unavailable", "A síntese de voz não está disponível no EccoVox.")
        values = tts[key]
        if not isinstance(values, list):
            raise ValueError
        options = [(str(value["id"]).strip(), str(value.get("label") or value["id"]).strip()) for value in values if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"].strip()]
    except (EccoVoxError, KeyError, TypeError, ValueError) as error:
        result(False, f"Não foi possível consultar as capacidades do EccoVox: {error}")
        return None
    default = "Herdar o perfil MCP EccoVox"
    labels = [default, *(f"{label} ({identifier})" for identifier, label in options)]
    selected = next((f"{label} ({identifier})" for identifier, label in options if identifier == current), default)
    choice = flow_choice(title, labels, selected)
    if choice is None:
        return None
    return "" if choice == default else next(identifier for identifier, label in options if choice == f"{label} ({identifier})")


def configure_mcp_password_reference(root: Path, integration: str, default_entry: str, current_profile: str = "", current_entry: str = "") -> tuple[str, str] | None:
    """Seleciona um perfil MCP e garante uma senha na entrada escolhida."""
    profiles = McpConfiguration(root).vault_profile_names()
    if not profiles:
        result(False, "Nenhum perfil de vault MCP disponível. Configure-o primeiro no menu MCP.")
        return None
    selected = current_profile if current_profile in profiles else profiles[0]
    profile = flow_choice(f"{integration} · Perfil de vault MCP", profiles, selected)
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
            action = flow_choice(f"Credencial · {integration}", ["Usar a chave já salva", "Informar nova chave e substituir", "Reinformar entrada KeePass"], "Usar a chave já salva")
            if action is None: return None
            if action.startswith("Usar"): return profile, entry
            if action.startswith("Reinformar"): continue
        else:
            verb = "salvar nesta entrada" if exists else "criar esta entrada"
            action = flow_choice(f"Credencial · {integration}", [f"Informar a chave e {verb}", "Reinformar entrada KeePass"], f"Informar a chave e {verb}")
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


def configure_totp_password(root: Path, profile: str, label: str, default_entry: str, current_entry: str = "") -> str | None:
    """Garante a senha TOTP indicada no mesmo vault usado pelo gateway."""
    resolver = KeePassCredentialResolver(root)
    entry = current_entry or default_entry
    while True:
        informed = prompt(f"Entrada KeePass da senha TOTP {label} [{entry}]: ").strip()
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
            action = flow_choice(f"Senha TOTP {label}", ["Usar a senha já salva", "Informar nova senha e substituir", "Reinformar entrada KeePass"], "Usar a senha já salva")
            if action is None:
                return None
            if action.startswith("Usar"):
                return entry
            if action.startswith("Reinformar"):
                continue
        else:
            verb = "salvar nesta entrada" if exists else "criar esta entrada"
            action = flow_choice(f"Senha TOTP {label}", [f"Informar a senha e {verb}", "Reinformar entrada KeePass"], f"Informar a senha e {verb}")
            if action is None:
                return None
            if action.startswith("Reinformar"):
                continue
        secret = getpass.getpass(Ui.text(f"› Senha TOTP {label} (não será exibida): ", Ui.violet, Ui.bold)).strip()
        if not secret:
            result(False, "A senha TOTP é obrigatória.")
            continue
        try:
            created = resolver.save_password(profile, entry, secret)
        except CredentialError as error:
            result(False, str(error))
            return None
        result(True, "Entrada criada no cofre MCP." if created else "Senha atualizada no cofre MCP.")
        return entry


def telegram_token_entry(root: Path) -> str:
    """Retorna a entrada padrão do token vinculada à identidade da instância."""
    return vault_entry_path(root, "Serviço", "Telegram", "", "BotToken")


def telegram_totp_entry(root: Path, kind: str) -> str:
    """Retorna a entrada padrão da senha TOTP real ou falsa do Telegram."""
    return vault_entry_path(root, "Serviço", "Telegram", "TOTP", kind.title())


def configured_telegram_token_entry(root: Path, current_entry: str = "") -> str:
    """Preserva escolha explícita, migrando somente padrões legados conhecidos."""
    legacy_entries = {
        "APIs/Telegram:Shikigami",
        vault_entry_path(root, "Serviço", "Telegram", "default", "Bot"),
    }
    return current_entry if current_entry and current_entry not in legacy_entries else telegram_token_entry(root)


def configured_telegram_totp_entry(root: Path, kind: str, current_entry: str = "") -> str:
    """Preserva TOTP personalizado e migra referências geradas pelo padrão legado."""
    normalized_kind = kind.title()
    legacy_entries = {
        f"APIs/TelegramTOTP:{instance_identity(root)}:{normalized_kind}",
        vault_entry_path(root, "Serviço", "Telegram", "default", f"TOTP:{normalized_kind}"),
    }
    return current_entry if current_entry and current_entry not in legacy_entries else telegram_totp_entry(root, kind)


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


def configure_codex_login(root: Path, data: dict[str, object], device_auth: bool | None = None, flow: bool = False) -> bool:
    """Abre o fluxo local de login e confirma seu resultado antes de prosseguir."""
    if device_auth is None:
        method = (flow_choice if flow else choose)("Login no Codex-CLI", ["Autenticação por dispositivo (recomendado para SSH/headless)", "Navegador local (callback em localhost)"], "Autenticação por dispositivo (recomendado para SSH/headless)")
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
        completed = subprocess.run(command, cwd=str(codex_setup_directory(root)), env=environment, check=False)
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
    checking("Verificando o ambiente Python do Shikigami...")
    try:
        configuration = python_runtime.configuration(root)
        runtime = python_runtime.python(root)
        base = python_runtime.base_python(root)
        executable = runtime if runtime.is_file() else base
        checked = subprocess.run([str(executable), "-I", "-B", "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10, check=False)
        version = (checked.stdout or "").strip()
        valid = checked.returncode == 0 and tuple(int(value) for value in version.split(".")[:2]) >= (3, 11)
        if not valid:
            raise ValueError("O interpretador selecionado não é um Python 3.11 ou superior funcional.")
        if configuration["mode"] == "isolated" and not runtime.is_file():
            diagnostics.append({"state": "pending", "label": "Ambiente Python", "detail": f"Python-base {version} pronto; /venv será criado quando um componente for instalado."})
        else:
            diagnostics.append({"state": "ok", "label": "Ambiente Python", "detail": f"{configuration['mode']} · Python {version} · {runtime}"})
    except (OSError, ValueError, subprocess.TimeoutExpired, python_runtime.RuntimeError) as error:
        diagnostics.append({"state": "error", "label": "Ambiente Python", "detail": str(error)})
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
    flow_heading("Diagnóstico", "Estado da instalação e do Codex-CLI")
    diagnostics = execution_diagnostics(root, lambda message: print("  → " + message))
    print()
    for diagnostic in diagnostics:
        state = diagnostic["state"]
        symbol, color = {"ok": ("✓", Ui.green), "pending": ("○", Ui.amber), "error": ("✗", Ui.red)}[state]
        print("  " + Ui.text(symbol, Ui.bold, color) + "  " + diagnostic["label"] + " — " + Ui.text(diagnostic["detail"], color))
    prompt("Pressione Enter para voltar: ")


def _runtime_versions(root: Path) -> dict[str, str | None]:
    """Consulta versões no único interpretador selecionado, sem usar o Python do setup."""
    executable = python_runtime.python(root)
    names = [component[0] for component in PYTHON_COMPONENTS]
    program = (
        "import importlib.metadata as metadata, json; names=" + repr(names) + "; result={}\n"
        "for name in names:\n"
        " try: result[name]=metadata.version(name)\n"
        " except metadata.PackageNotFoundError: result[name]=None\n"
        "print(json.dumps(result))"
    )
    completed = subprocess.run([str(executable), "-I", "-B", "-c", program], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=15, check=False)
    if completed.returncode:
        raise RuntimeError("Não foi possível consultar os componentes do runtime Python selecionado.")
    values = json.loads(completed.stdout)
    if not isinstance(values, dict):
        raise RuntimeError("O runtime Python retornou uma lista de componentes inválida.")
    return {name: value if isinstance(value, str) else None for name, value in values.items()}


def _show_python_components(root: Path) -> None:
    """Exibe pacotes Python e seus consumidores lógicos sem instalar nada."""
    flow_heading("Python e bibliotecas", "Componentes instalados e seus consumidores")
    try:
        configuration = python_runtime.configuration(root)
        executable = python_runtime.python(root)
        print(f"  Runtime: {configuration['mode']} · {executable}")
        versions = _runtime_versions(root)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired, python_runtime.RuntimeError, RuntimeError) as error:
        result(False, str(error))
        return
    for package, group, consumers in PYTHON_COMPONENTS:
        if package == "pywin32" and os.name != "nt":
            continue
        version = versions.get(package)
        item("·", package, f"{version or 'não instalado'} · {group} · {consumers}")


def _configure_python_runtime(root: Path, data: dict[str, object]) -> dict[str, object]:
    """Atualiza a seleção central do runtime, sem instalar componentes."""
    flow_heading("Python e bibliotecas", "Runtime único do Shikigami")
    print("  1. Ambiente isolado — cria e usa /venv na raiz da instância.")
    print("  2. Python global — instala e usa componentes no interpretador escolhido.")
    default = "1" if data["python_mode"] == "isolated" else "2"
    mode = prompt(f"Modo [{default}]: ").strip().casefold() or default
    if mode in {"x", "\x1b"}:
        return data
    if mode not in {"1", "2"}:
        result(False, "Escolha 1 ou 2.")
        return data
    default_python = str(data["python_base_executable"] if mode == "1" else data["python_executable"]) or sys.executable
    executable = prompt(f"Python [{default_python}]: ").strip() or default_python
    candidate_path = Path(executable).expanduser()
    if not candidate_path.is_file():
        result(False, "Informe um executável Python existente.")
        return data
    try:
        checked = subprocess.run([str(candidate_path), "-I", "-B", "-c", "import sys; assert sys.version_info >= (3, 11)"], text=True, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        checked = None
    if checked is None or checked.returncode:
        result(False, "O executável deve ser um Python funcional na versão 3.11 ou superior.")
        return data
    candidate = dict(data)
    candidate["python_mode"] = "isolated" if mode == "1" else "global"
    candidate["python_base_executable"] = str(candidate_path) if mode == "1" else ""
    candidate["python_executable"] = str(candidate_path) if mode == "2" else ""
    ok, message = save_system(root, candidate)
    result(ok, message)
    return candidate if ok else data


def python_menu(root: Path) -> None:
    """Centraliza o runtime único e a manutenção explícita de bibliotecas."""
    data = load_system(root)
    while True:
        configuration = python_runtime.configuration(root)
        executable = python_runtime.python(root)
        location = "/venv" if configuration["mode"] == "isolated" else str(executable)
        screen("Python e bibliotecas", "Runtime compartilhado pelo daemon, MCPs e serviços")
        item("1.", "Selecionar runtime Python", f"{configuration['mode']} · {location}")
        item("2.", "Listar componentes instalados", "Versões, grupos e consumidores")
        print()
        item("3.", "Instalar ou reparar componentes do daemon", "Daemon, Telegram, Mini Apps, AWS e SSH")
        item("4.", "Instalar ou reparar componentes MCP", "Host MCP, vault/OAuth, E-mail e schemas")
        item("5.", "Instalar ou reparar PDF e OCR básico", "PDF, OCRmyPDF e bibliotecas documentais")
        item("6.", "Preparar OCR avançado", "CPU ou GPU NVIDIA; também prepara modelos")
        item("7.", "Instalar ou reparar todos os componentes básicos", "Daemon, MCP, PDF e OCR básico")
        item("8.", "Diagnóstico de runtime", "Interpretador e componentes Python instalados")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        if choice == "1":
            data = _configure_python_runtime(root, data)
        elif choice in {"2", "8"}:
            _show_python_components(root)
        elif choice == "3":
            try:
                ok, message = daemon_management.ensure_runtime(root, show_progress=True)
                result(ok, message)
            except (OSError, ValueError, python_runtime.RuntimeError) as error:
                result(False, str(error))
        elif choice == "4":
            try:
                note(ensure_mcp_runtime(root, progress=note))
                result(True, "Componentes MCP preparados no runtime único.")
            except McpRuntimeError as error:
                result(False, str(error))
        elif choice in {"5", "6"}:
            if choice == "6":
                variant = flow_choice("OCR avançado", ["CPU", "GPU NVIDIA"], "CPU")
                if variant is None:
                    continue
                tier, gpu = "advanced", variant == "GPU NVIDIA"
            else:
                tier, gpu = "base", False
            try:
                install_document_runtime(root, tier=tier, gpu=gpu, progress=note)
                result(True, "Componentes documentais preparados no runtime único.")
            except (DocumentError, McpRuntimeError, OSError, ValueError) as error:
                result(False, str(error))
        elif choice == "7":
            try:
                ok, message = daemon_management.ensure_runtime(root, show_progress=True)
                if not ok:
                    result(False, message)
                    continue
                note(ensure_mcp_runtime(root, progress=note))
                install_document_runtime(root, tier="base", progress=note)
                result(True, "Todos os componentes básicos foram preparados no runtime único.")
            except (DocumentError, McpRuntimeError, OSError, ValueError, python_runtime.RuntimeError) as error:
                result(False, str(error))
        else:
            result(False, "Opção inválida.")


def agent_profiles_menu(root: Path) -> None:
    """Administra somente fontes locais válidas e habilitações opcionais."""
    materializer = ShikigamiAgentMaterializer(root)
    while True:
        try:
            profiles = materializer.discover()
            configured = materializer.configuration()
            launch = CodexLaunch.from_data(root, load_system(root))
            capability = load_profile_capability(root, launch.executable, launch.workspace)
        except CodexExecutionError:
            capability = {"supported": False, "reason": "codex_configuration_missing"}
        except (OSError, ShikigamiAgentMaterializationError) as error:
            result(False, str(error)); return
        screen("Perfis de agentes do Shikigami", "Fontes TOML opcionais; nenhuma especialização é criada pelo setup")
        note("Fonte: shikigami-agents/<id>.toml · cópias: workspace/.codex/agents/<id>.toml.")
        reason = str(capability.get("reason") or "g4_unverified")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", reason): reason = "g4_unverified"
        note("Distribuição disponível para os três clientes." if capability.get("supported") is True else "Distribuição indisponível: " + reason + ". O gate G4 precisa ser comprovado antes de habilitar perfis.")
        identifiers = sorted(profiles)
        if not identifiers:
            note("Nenhum perfil específico encontrado; a delegação nativa continua disponível conforme o runtime.")
        for index, identifier in enumerate(identifiers, 1):
            enabled = configured.get(identifier, {}).get("enabled") is True
            item(f"{index}.", identifier, ("Habilitado" if enabled else "Desabilitado") + " · " + profiles[identifier].description)
        item("X.", "Voltar")
        selected = prompt("Opção: ").strip().casefold()
        if selected in {"x", "\x1b"}: return
        if not selected.isdigit() or not 1 <= int(selected) <= len(identifiers):
            result(False, "Opção inválida."); continue
        identifier = identifiers[int(selected) - 1]
        enable = configured.get(identifier, {}).get("enabled") is not True
        if enable and capability.get("supported") is not True:
            result(False, "O perfil não foi habilitado: descoberta e snapshot de runtime ainda não foram comprovados."); continue
        answer = prompt(f"{'Habilitar' if enable else 'Desabilitar'} o perfil {identifier}? [s/N]: ").strip().casefold()
        if answer in {"x", "\x1b", "", "n", "não", "nao"}: continue
        if answer not in {"s", "sim"}:
            result(False, "Responda S ou N; X cancela."); continue
        try:
            materializer.set_enabled(identifier, enable)
            result(True, "Habilitação local salva. A próxima abertura gerenciada sincronizará as cópias em ponto seguro.")
        except (OSError, ShikigamiAgentMaterializationError) as error:
            result(False, str(error))


def configure_gateway_execution(root: Path) -> bool:
    """Configura a intenção local e informa o fallback efetivo das capacidades."""
    flow_heading("Entradas durante execução", "Fila, direcionamento e acompanhamento do coordenador")
    try:
        settings = tomllib.loads(telegram_configuration_path(root).read_text(encoding="utf-8"))
        if type(settings.get("schema_version")) is not int or settings["schema_version"] not in {1, 2}:
            raise ValueError("Versão de configuração não suportada.")
        agent, limits = settings.get("agent", {}), settings.get("limits", {})
        policy = str(agent.get("busy_input_policy") or "queue")
        workers = min(16, max(1, int(limits.get("preparation_workers", 4))))
        grace = min(60, max(1, int(agent.get("interrupt_grace_seconds", 15))))
        post_turn = agent.get("post_turn_results") is True
    except FileNotFoundError:
        policy, workers, grace, post_turn = "queue", 4, 15, False
    except (OSError, ValueError, TypeError, AttributeError):
        result(False, "A configuração do Gateway está inválida; corrija-a antes de salvar."); return False
    capabilities = CapabilitySet.from_instance(root)
    effective = "steer" if policy == "steer" and capabilities.supports("steering") else "queue"
    note(f"Política configurada: {policy if policy in {'queue', 'steer'} else 'queue'} · efetiva: {effective}.")
    if not capabilities.supports("steering"):
        note("Direcionamento indisponível: " + capabilities.reason("steering") + "; mensagens serão enfileiradas.")
    if not capabilities.supports("post_turn"):
        note("Entrega pós-turno indisponível: " + capabilities.reason("post_turn") + "; o principal acompanha filhos dentro do turno.")
    choices = ["Enfileirar mensagens", "Direcionar o turno ativo quando suportado"]
    selected = flow_choice("Mensagens recebidas durante trabalho", choices, choices[1] if policy == "steer" else choices[0])
    if selected is None: return False
    policy = "steer" if selected == choices[1] else "queue"
    for field, label, current, minimum, maximum in (("preparation_workers", "Preparações de anexos e voz em paralelo", workers, 1, 16), ("interrupt_grace_seconds", "Graça da interrupção, em segundos", grace, 1, 60)):
        raw = prompt(f"{label} [{current}] ({minimum} a {maximum}): ").strip()
        if raw.casefold() in {"x", "\x1b"}: return False
        try: value = int(raw) if raw else current
        except ValueError: result(False, "Informe um número inteiro válido."); return False
        if not minimum <= value <= maximum:
            result(False, f"Informe um número entre {minimum} e {maximum}."); return False
        if field == "preparation_workers": workers = value
        else: grace = value
    answer = prompt(f"Solicitar entrega pós-turno quando houver capacidade comprovada? [{'S/n' if post_turn else 's/N'}]: ").strip().casefold()
    if answer in {"x", "\x1b"}: return False
    if answer not in {"", "s", "sim", "n", "não", "nao"}:
        result(False, "Responda S ou N; X cancela."); return False
    requested_post_turn = post_turn if not answer else answer in {"s", "sim"}
    arguments = [sys.executable, str(root / "onmyoji-daemon" / "setupDaemon.py"), "--onmyoji-root", str(root), "--action", "set-gateway-execution", "--busy-input-policy", policy, "--preparation-workers", str(workers), "--interrupt-grace-seconds", str(grace), "--post-turn-results", str(requested_post_turn).lower()]
    try:
        changed = subprocess.run(arguments, cwd=str(root / "onmyoji-daemon"), text=True, encoding="utf-8", errors="replace", capture_output=True)
    except OSError:
        result(False, "Não foi possível salvar a configuração local do Gateway."); return False
    if changed.returncode != 0:
        result(False, "Não foi possível salvar a configuração local do Gateway."); return False
    result(True, "Preferências locais salvas; reinicie o Gateway para aplicar.")
    if policy == "steer" and not capabilities.supports("steering"):
        note("A intenção de direcionar foi salva; a política efetiva permanece fila enquanto G1 não estiver comprovado.")
    if requested_post_turn and not capabilities.supports("post_turn"):
        note("A entrega pós-turno permanece desativada efetivamente até comprovar seus gates de runtime.")
    return True


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
        item("9.", "Perfis de agentes do Shikigami", "Fontes TOML opcionais e compatibilidade de distribuição")
        print()
        item("C.", "Executar Codex-CLI", "CLI nativo com modelo, sandbox e diretórios configurados")
        item("I.", "Console interativo Onmyōji", "App Server com developer instructions compostas")
        print(f"\n  Estado: {'✓ PRONTO' if ready else '○ ' + message}")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            value = choose_codex_executable(str(data["executable"]), root=root)
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
        elif choice == "9": agent_profiles_menu(root)
        elif choice == "c": launch_codex(root, data)
        elif choice == "i": launch_onmyoji_interactive(root, data)
        else: result(False, "Opção inválida.")


def local_daemon_rpc(root: Path, method: str, params: dict | None = None):
    from onmyoji_daemon.rpc import call
    from onmyoji_daemon.supervisor import endpoint
    return call(*endpoint(root), method, params or {}, timeout=25 if method in {"start", "stop", "restart"} else 5)


def save_notification_configuration(root: Path, changes: dict) -> dict:
    from onmyoji_daemon.agent_notification.configuration import NotificationConfiguration
    from onmyoji_daemon.supervisor import endpoint
    try:
        endpoint(root)
    except FileNotFoundError:
        return NotificationConfiguration(root).configure(changes)
    state = local_daemon_rpc(root, "status", {"service": "telegram"})
    if state.get("state") == "running":
        return local_daemon_rpc(root, "notification.configure", changes)
    return NotificationConfiguration(root).configure(changes)


def notification_menu(root: Path) -> None:
    from onmyoji_daemon.agent_notification.configuration import NotificationConfiguration
    from onmyoji_daemon.rpc import RpcError
    configuration = NotificationConfiguration(root)
    while True:
        try:
            current = configuration.load()
        except (OSError, ValueError, RpcError) as error:
            result(False, str(error)); return
        screen("Notificações ao agente", "Ingresso privado/local · " + ("HABILITADO" if current["enabled"] else "DESABILITADO"))
        item("1.", "Desabilitar ingresso" if current["enabled"] else "Habilitar ingresso")
        item("2.", "Diagnóstico")
        item("3.", "Modo de recepção", "Telegram e eventos" if current["telegram_receiver_enabled"] else "Somente eventos")
        item("4.", "Conta SMTP padrão", current["smtp_account"] or "Não selecionada")
        item("5.", "Canal global de incidentes", str(current["incident"] or "Desabilitado"))
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        try:
            changes = None
            if choice == "1":
                changes = {"enabled": not current["enabled"]}
                path = telegram_configuration_path(root)
                telegram = tomllib.loads(path.read_text(encoding="utf-8")).get("telegram", {}) if path.exists() else {}
                if changes["enabled"] and not telegram.get("token_entry"):
                    changes["telegram_receiver_enabled"] = False
                save_notification_configuration(root, changes)
                if changes["enabled"]:
                    local_daemon_rpc(root, "enable", {"service": "telegram"})
                    state = local_daemon_rpc(root, "start", {"service": "telegram"})
                    result(state.get("state") == "running", "Ingresso configurado; confira o diagnóstico." if state.get("state") == "running" else str(state.get("last_error") or "Não foi possível iniciar o gateway."))
                else:
                    result(True, "Ingresso desabilitado.")
                continue
            if choice == "2":
                flow_heading("Diagnóstico de notificações", "Configuração local e execução compartilhada")
                diagnostic = local_daemon_rpc(root, "notification.diagnose")
                for label, ready in diagnostic["checks"].items():
                    result(bool(ready), label.replace("_", " "))
                note("Execuções: " + json.dumps(diagnostic["counts"], ensure_ascii=False))
                continue
            if choice == "3":
                selected = flow_choice("Modo de recepção", ["Somente eventos", "Telegram e eventos"], "Telegram e eventos" if current["telegram_receiver_enabled"] else "Somente eventos")
                if selected is None:
                    continue
                changes = {"telegram_receiver_enabled": selected == "Telegram e eventos"}
            elif choice == "4":
                accounts = McpConfiguration(root).email().get("accounts", {})
                names = ["Desabilitar e-mail", *sorted(name for name, account in accounts.items() if account.get("enabled") and account.get("smtp"))]
                selected = flow_choice("Conta SMTP padrão", names, current["smtp_account"] or "Desabilitar e-mail")
                if selected is None:
                    continue
                changes = {"smtp_account": "" if selected == "Desabilitar e-mail" else selected}
            elif choice == "5":
                kind = flow_choice("Canal de incidentes", ["Desabilitado", "Telegram", "E-mail"], {"telegram": "Telegram", "email": "E-mail"}.get((current["incident"] or {}).get("type"), "Desabilitado"))
                if kind is None:
                    continue
                if kind == "Desabilitado":
                    changes = {"incident": None}
                else:
                    previous = (current["incident"] or {}).get("value", "")
                    destination = prompt(f"Destino [{previous or 'vazio = cancelar'}]: ").strip()
                    if destination.casefold() in {"x", "\x1b"}:
                        continue
                    destination = destination or previous
                    if not destination:
                        continue
                    changes = {"incident": {"type": "telegram" if kind == "Telegram" else "email", "value": destination}}
            else:
                result(False, "Opção inválida."); continue
            saved = save_notification_configuration(root, changes)
            result(True, "Configuração salva.")
            if saved.get("restart_required"):
                note("Reinicie o processo do daemon para aplicar limites e o modo de recepção.")
        except (OSError, ValueError, RpcError) as error:
            result(False, str(error))


def scheduler_menu(root: Path) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from onmyoji_daemon.rpc import RpcError
    from onmyoji_daemon.task_scheduler.repository import SORT_FIELDS
    sort, descending, offset = "next_run_at", False, 0
    labels = ("Nome", "Estado", "Próxima execução", "Criação", "Ação", "Canal", "Destino", "Thread")
    while True:
        screen("Agendador", "Consulta de definições · CLI/RPC administram os agendamentos")
        listing = {"items": [], "total": 0}
        try:
            state = local_daemon_rpc(root, "status", {"service": "task-scheduler"})
            note("Serviço: " + state.get("state", "desconhecido"))
            if state.get("state") == "running":
                listing = local_daemon_rpc(root, "task.list", {"sort": sort, "descending": descending, "offset": offset, "limit": 20})
            print("  " + " | ".join(f"{index}. {label}" for index, label in enumerate(labels, 1)))
            for value in listing["items"]:
                zone = ZoneInfo(value.get("rule", {}).get("timezone", "America/Sao_Paulo"))
                def local_time(raw):
                    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(zone).strftime("%Y-%m-%d %H:%M %z") if raw else None
                fields = [value["name"], value["state"], local_time(value["next_run_at"]), local_time(value["created_at"]), value["action_type"], value["channel"], value["destination"], value["thread_policy"]]
                widths = (16, 9, 24, 24, 12, 8, 18, 6)
                rendered = [str(field or "—")[:width] for field, width in zip(fields, widths)]
                print("  " + " | ".join(rendered))
            note(f"{listing['total']} definição(ões) · página {offset // 20 + 1} · ordem {labels[SORT_FIELDS.index(sort)]} {'decrescente' if descending else 'crescente'}")
        except (OSError, ValueError, RpcError) as error:
            result(False, str(error))
        item("1–8.", "Ordenar por coluna; repetir inverte a direção")
        item("P.", "Página anterior"); item("N.", "Próxima página"); item("R.", "Atualizar")
        item("E.", "Habilitar e iniciar serviço"); item("D.", "Desabilitar serviço"); item("H.", "Diagnóstico")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        if choice in {str(index) for index in range(1, 9)}:
            selected = SORT_FIELDS[int(choice) - 1]
            descending = not descending if sort == selected else False
            sort, offset = selected, 0
        elif choice == "p": offset = max(0, offset - 20)
        elif choice == "n":
            if offset + 20 < listing["total"]: offset += 20
        elif choice == "r": pass
        elif choice in {"e", "d", "h"}:
            try:
                if choice == "h":
                    flow_heading("Diagnóstico do agendador", "Capacidade e ocorrências")
                    note(json.dumps(local_daemon_rpc(root, "task.status"), ensure_ascii=False))
                else:
                    local_daemon_rpc(root, "enable" if choice == "e" else "disable", {"service": "task-scheduler"})
                    if choice == "e":
                        started = local_daemon_rpc(root, "start", {"service": "task-scheduler"})
                        result(started.get("state") == "running", "Serviço em execução." if started.get("state") == "running" else str(started.get("last_error") or "Falha ao iniciar."))
            except (OSError, ValueError, RpcError) as error:
                result(False, str(error))
        else: result(False, "Opção inválida.")


def daemon_menu(root: Path) -> None:
    project = root / "onmyoji-daemon"
    script = project / "setupDaemon.py"
    if not script.is_file():
        result(False, "Projeto do daemon não encontrado.")
        return
    environment = dict(os.environ); source = str(project / "src")
    environment["PYTHONPATH"] = source + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    execution_profile = load_execution_profile(root)
    dedicated_execution = execution_profile.get("mode") == "dedicated" and execution_profile.get("validated") is True and linux_current_user() != execution_profile.get("account")
    telegram_token_changed = False

    def windows_service_administrator() -> bool:
        """Confirma privilégio necessário antes de alterar o Service Control Manager."""
        if os.name != "nt": return True
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

    def command(arguments: list[str], quiet: bool = False, input_value: str | None = None, capture_stdout: bool = False, administrative: bool = False) -> subprocess.CompletedProcess[str]:
        output = {"stdout": subprocess.PIPE} if capture_stdout else {"capture_output": quiet}
        effective = list(arguments)
        if dedicated_execution and not administrative:
            account, home = str(execution_profile["account"]), Path(str(execution_profile["home"]))
            if effective and Path(effective[0]).resolve() == Path(sys.executable).resolve(): effective[0] = str(execution_profile.get("setup_python") or "/usr/bin/python3")
            delegated_environment = {"CODEX_HOME": str(root.resolve()), "PYTHONPATH": environment["PYTHONPATH"], "PYTHONUTF8": "1", "PATH": dedicated_path(home)}
            effective = account_command(account, home, effective, delegated_environment)
        return subprocess.run(effective, cwd=str(project), env=environment, text=True, encoding="utf-8", errors="replace", input=input_value, **output)

    def lifecycle(action: str, extra: list[str] | None = None, input_value: str | None = None) -> bool:
        visible = action in {"install-service", "configure-windows-service-account", "configure-service-resources", "repair-service-permissions", "service-security", "remove-service", "service-start", "service-stop", "service-restart", "service-force-stop"}
        try:
            completed = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), action, *(extra or [])], quiet=not visible, input_value=input_value, administrative=visible)
        except KeyboardInterrupt:
            result(False, "Operação interrompida pelo usuário."); return False
        detail = (completed.stdout or completed.stderr or ("Operação concluída." if completed.returncode == 0 else f"O comando terminou com código {completed.returncode}; consulte a saída exibida acima.")).strip()
        result(completed.returncode == 0, detail)
        return completed.returncode == 0

    def install_daemon() -> bool:
        """Prepara, configura e valida o runtime escolhido pelo usuário."""
        print()
        print("  [1/4] Preparando a instância do daemon...")
        prepared = command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "prepare-instance"], quiet=True)
        result(prepared.returncode == 0, (prepared.stdout or prepared.stderr).strip())
        if prepared.returncode != 0: return False
        print("\n  [2/4] Runtime Python do Shikigami")
        note("O daemon usará a escolha central configurada em Python e bibliotecas.")
        print("\n  [3/4] Componentes necessários")
        print("    • aiohttp — comunicação HTTP do daemon e seus gateways.")
        print("    • cryptography — recursos criptográficos e certificados locais.")
        if os.name == "nt": print("    • pywin32 — integração opcional com Serviço do Windows.")
        print("\n  [4/4] Preparando o runtime e os componentes")
        note("O daemon instala automaticamente os componentes necessários no runtime único; a manutenção manual fica em Python e bibliotecas.")
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
            if check.get("action"): print("       Próximo passo: " + check["action"])

    def show_validation() -> bool:
        checks = validation(); print()
        print_checks(checks)
        required = {"Daemon da instância", "Estrutura da configuração Telegram", "Perfil de vault MCP", "Referência do token", "Executável Codex para serviço", "Workspace do Shikigami", "Execução do agente Codex", "Conexão Telegram"}
        return bool(checks) and all(check.get("state") == "ok" for check in checks if check.get("label") in required)

    def show_daemon_diagnosis() -> bool:
        """Mostra primeiro os pré-requisitos do Shikigami e depois a plataforma."""
        flow_heading("Diagnóstico do Daemon", "Pré-requisitos do Shikigami e estado da plataforma; serviços não são avaliados aqui")
        print("\n  Diagnóstico principal do Shikigami")
        main_checks = execution_diagnostics(root, lambda message: print("    → " + message)); print_checks(main_checks)
        print("\n  Diagnóstico da plataforma do daemon")
        daemon_checks = validation("daemon-validation-json"); print_checks(daemon_checks)
        return all(check.get("state") == "ok" for check in [*main_checks, *daemon_checks])

    def show_telegram_diagnosis() -> bool:
        """Exibe dependências da plataforma antes das verificações do gateway."""
        flow_heading("Diagnóstico do Telegram", "Plataforma, vault MCP e estado do gateway")
        print("\n  Diagnóstico principal do Shikigami")
        main_checks = execution_diagnostics(root, lambda message: print("    → " + message)); print_checks(main_checks)
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
        flow_heading("Wizard do Daemon", "Configuração e validação da plataforma local")
        note("Cada etapa confirmada é salva imediatamente. Valores já válidos permanecem disponíveis quando o wizard for executado novamente.")
        main_checks = execution_diagnostics(root, lambda message: print("    → " + message))
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
            if prompt("  Reinstalar ou validar componentes do daemon agora? [s/N]: ").strip().casefold() in {"s", "sim"}:
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

        def choose_service_limits(current: dict[str, object] | None = None) -> ServiceLimits | None:
            """Seleciona limites adaptativos ou valores explícitos para o drop-in systemd."""
            recommended = recommended_limits()
            flow_heading("Limites do serviço", "Recursos do daemon e de todos os processos iniciados por ele")
            note("Os valores recomendados foram calculados uma vez para a CPU, memória e swap disponíveis nesta máquina.")
            print()
            for line in limits_summary(recommended): print("  " + line)
            choices = ["Recomendados para esta máquina", "Personalizar", "Sem limites adicionais"]
            selected = flow_choice("Perfil de recursos", choices, choices[1] if current else choices[0])
            if selected is None: return None
            if selected == choices[0]: return recommended
            if selected == choices[2]: return ServiceLimits()
            try: base = ServiceLimits.from_mapping(current) if current else recommended
            except (TypeError, ValueError): base = recommended

            def numeric(label: str, value: int, divisor: int = 1, maximum: int | None = None) -> int | None:
                shown = value // divisor if divisor > 1 else value
                while True:
                    raw = prompt(f"{label} [{shown}]: ").strip().casefold()
                    if raw in {"x", "\x1b"}: return None
                    try: selected_value = int(raw) if raw else shown
                    except ValueError: result(False, "Informe um número inteiro ou X para cancelar."); continue
                    if selected_value < 0 or (maximum is not None and selected_value > maximum): result(False, f"Use um valor entre 0 e {maximum}."); continue
                    return selected_value * divisor

            mib = 1024 * 1024
            cpu = numeric("CPUQuota em porcentagem; 100 equivale a uma CPU, 0 omite", base.cpu_quota_percent)
            if cpu is None: return None
            high = numeric("MemoryHigh em MiB; 0 omite", base.memory_high_bytes, mib)
            if high is None: return None
            maximum = numeric("MemoryMax em MiB; 0 omite", base.memory_max_bytes, mib)
            if maximum is None: return None
            swap = numeric("MemorySwapMax em MiB; 0 omite", base.memory_swap_max_bytes, mib)
            if swap is None: return None
            tasks = numeric("TasksMax; 0 omite", base.tasks_max)
            if tasks is None: return None
            weight = numeric("IOWeight; 0 usa o padrão", base.io_weight, maximum=10000)
            if weight is None: return None
            devices_default = "s" if base.private_devices else "n"
            devices_hint = "S/n" if base.private_devices else "s/N"
            while True:
                raw = prompt(f"Ocultar dispositivos físicos do serviço? [{devices_hint}]: ").strip().casefold() or devices_default
                if raw in {"x", "\x1b"}: return None
                if raw in {"s", "sim", "n", "nao", "não"}: break
                result(False, "Responda S, N ou X.")
            try:
                return ServiceLimits.from_mapping({"cpu_quota_percent": cpu, "memory_high_bytes": high, "memory_max_bytes": maximum, "memory_swap_max_bytes": swap, "tasks_max": tasks, "io_weight": weight, "private_devices": raw in {"s", "sim"}})
            except ValueError as error:
                result(False, str(error)); return None

        def privileged_service_action(action: str, extra: list[str] | None = None) -> bool:
            extras: list[str] = list(extra or [])
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
                default_name = daemon_management.default_service_name(root)
                visible = instance_identity(root)
                name = prompt(f"Nome do serviço [{default_name}]: ").strip() or default_name
                description = prompt(f"Descrição [{f'Shikigami {visible} Daemon'}]: ").strip() or f"Shikigami {visible} Daemon"
                if os.name == "nt":
                    if not windows_service_administrator():
                        result(False, "A instalação do Serviço do Windows exige um terminal executado como Administrador. Feche este setup, abra o PowerShell como Administrador e execute-o novamente."); continue
                    selected_account = windows_service_account()
                    if selected_account is None: result(False, "Instalação do serviço cancelada."); continue
                    account, password = selected_account
                    if prompt(f"Confirmar a instalação do serviço Windows como {account}? [s/N]: ").strip().casefold() not in {"s", "sim"}: result(False, "Instalação do serviço cancelada."); continue
                    extras = ["--name", name, "--description", description, "--scope", "system", "--windows-account", account]
                    if password: extras.append("--password-stdin")
                    if lifecycle("install-service", extras, password if password else None) and prompt("Iniciar agora e validar o registro? [S/n]: ").strip().casefold() in {"", "s", "sim"}:
                        lifecycle("service-start"); lifecycle("service-status")
                    continue
                limits = choose_service_limits()
                if limits is None: result(False, "Instalação do serviço cancelada."); continue
                execution_profile = load_execution_profile(root)
                if execution_profile.get("mode") == "dedicated":
                    selected = "Usuário exclusivo — serviço isolado do sistema"
                    note(f"Identidade preparada: {execution_profile.get('account')} · validada: {'sim' if execution_profile.get('validated') else 'não'}.")
                else:
                    default_execution = "Usuário atual — systemd --user" if execution_profile.get("mode") == "current" else "Usuário exclusivo — serviço isolado do sistema"
                    selected = flow_choice("Identidade de execução", ["Usuário atual — systemd --user", "Usuário exclusivo — serviço isolado do sistema"], default_execution)
                if selected is None: result(False, "Instalação do serviço cancelada."); continue
                common = ["--name", name, "--description", description, "--resource-limits", limits_json(limits)]
                if selected.startswith("Usuário atual"):
                    lifecycle("install-service", [*common, "--scope", "user"]); continue
                data = load_system(root)
                workspace = Path(str(data.get("project_directory") or suggested_project_directory(root))).expanduser().resolve()
                if not workspace.is_dir(): result(False, f"O workspace precisa existir antes do isolamento: {workspace}"); continue
                if execution_profile.get("mode") == "dedicated":
                    run_as_user = str(execution_profile.get("account") or ""); installer = str(execution_profile.get("installer_user") or ""); share = bool(execution_profile.get("installer_access", True))
                else:
                    installer = linux_current_user(); default_account = linux_account_name(visible)
                    run_as_user = prompt(f"Usuário Linux exclusivo [{default_account}]: ").strip() or default_account
                    try: run_as_user = validate_account(run_as_user)
                    except ValueError as error: result(False, str(error)); continue
                    share = prompt(f"Permitir ao usuário {installer} acesso completo à instância e ao workspace? [S/n]: ").strip().casefold() not in {"n", "nao", "não"}
                if execution_profile.get("validated") is not True:
                    note(f"O ambiente será migrado e validado como {run_as_user} antes da criação da unit.")
                    if not prepare_dedicated_environment(root, visible, workspace, run_as_user, installer, share): continue
                    if not share:
                        result(True, f"Ambiente dedicado preparado. Execute o setup como root para instalar o serviço de sistema.")
                        raise SystemExit(0)
                    execution_profile = load_execution_profile(root)
                    if execution_profile.get("validated") is not True: result(False, "A identidade dedicada não foi certificada."); continue
                note(f"Home da conta: /home/{run_as_user}. A instância continuará em {root} e o workspace em {workspace}.")
                if prompt("Continuar com a instalação isolada? [s/N]: ").strip().casefold() not in {"s", "sim"}: result(False, "Instalação do serviço cancelada."); continue
                extras = [*common, "--scope", "system", "--run-as-user", run_as_user, "--run-as-group", run_as_user, "--isolate-linux", "--installer-user", installer, "--workspace", str(workspace), "--use-sudo"]
                if share: extras.append("--installer-access")
                lifecycle("install-service", extras)
                continue
            item("1.", "Status do serviço")
            item("2.", "Parar serviço" if service_running() else "Iniciar serviço")
            item("3.", "Reiniciar serviço")
            item("4.", "Forçar parada do serviço")
            item("5.", "Remover serviço")
            if os.name == "nt": item("6.", "Configurar conta do serviço", "Necessário para acessar credenciais KeePass do usuário")
            else:
                try: service_data = json.loads((root / "configs" / "daemon" / "service.json").read_text(encoding="utf-8"))
                except (OSError, ValueError): service_data = {}
                item("6.", "Limites de recursos", "CPU, memória, swap, processos, I/O e dispositivos")
                if service_data.get("isolated"): item("7.", "Reparar permissões", "Reaplica grupo, setgid e ACLs herdáveis")
                item("8.", "Avaliar isolamento", "Relatório systemd-analyze security")
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
                if not windows_service_administrator():
                    result(False, "Alterar a conta do Serviço do Windows exige um terminal executado como Administrador. Feche este setup, abra o PowerShell como Administrador e execute-o novamente."); continue
                selected_account = windows_service_account()
                if selected_account is None: result(False, "Alteração da conta cancelada."); continue
                account, password = selected_account
                extras = ["--account", account]
                if password: extras.append("--password-stdin")
                lifecycle("configure-windows-service-account", extras, password if password else None)
            elif choice == "6" and os.name != "nt":
                limits = choose_service_limits(service_data.get("resource_limits") if isinstance(service_data.get("resource_limits"), dict) else None)
                if limits is not None: privileged_service_action("configure-service-resources", ["--values", limits_json(limits)])
            elif choice == "7" and os.name != "nt" and service_data.get("isolated"):
                privileged_service_action("repair-service-permissions")
            elif choice == "8" and os.name != "nt":
                privileged_service_action("service-security")
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

    def configure_voice_replies() -> None:
        """Configura áudio do Telegram diretamente, sem um submenu de opção única."""
        telegram_path = telegram_configuration_path(root)
        command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "bootstrap"], quiet=True)
        try:
            settings = tomllib.loads(telegram_path.read_text(encoding="utf-8")); voice = settings.get("voice_reply", {})
            enabled, profile = bool(voice.get("enabled", False)), str(voice.get("eccovox_profile") or "")
            name, language, auto_off = str(voice.get("voice") or ""), str(voice.get("language") or ""), int(voice.get("auto_off_minutes") or 15)
        except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
            enabled, profile, name, language, auto_off = False, "", "", "", 15
        active = prompt("Habilitar respostas em áudio? [S/n]: ").strip().casefold() not in {"n", "x", "\x1b"}
        if active:
            try: profiles = sorted(key for key, value in McpConfiguration(root).eccovox()["profiles"].items() if isinstance(value, dict) and value.get("enabled") is True)
            except Exception: profiles = []
            if not profiles:
                result(False, "Nenhum perfil EccoVox habilitado. Configure-o primeiro em MCP > Integrações MCP > EccoVox."); return
            profile = flow_choice("Perfil MCP EccoVox", profiles, profile if profile in profiles else profiles[0])
            if profile is None: return
            name = choose_eccovox_tts_capability(root, profile, "voices", "Voz das respostas Telegram", name)
            if name is None: return
            language = choose_eccovox_tts_capability(root, profile, "languages", "Idioma das respostas Telegram", language)
            if language is None: return
        raw = prompt(f"Desligar áudio após minutos sem interação [{auto_off}]: ").strip()
        try: minutes = int(raw) if raw else auto_off
        except ValueError: result(False, "Informe minutos inteiros."); return
        arguments = [sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-voice-reply", "--profile", profile, "--voice", name, "--language", language, "--auto-off-minutes", str(minutes)] + (["--enabled"] if active else [])
        changed = command(arguments, quiet=True); result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
        if changed.returncode == 0: offer_restart()

    def configure_default_thoughts() -> None:
        """Define o comportamento inicial de pensamentos para novas conversas."""
        telegram_path = telegram_configuration_path(root)
        command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "bootstrap"], quiet=True)
        try:
            telegram = tomllib.loads(telegram_path.read_text(encoding="utf-8")).get("telegram", {})
            share, delete = bool(telegram.get("default_share_thoughts", True)), bool(telegram.get("default_delete_thoughts", True))
        except (OSError, tomllib.TOMLDecodeError, TypeError):
            share, delete = True, True
        sharing = flow_choice("Pensamentos nas novas conversas", ["Compartilhar durante o turno", "Não compartilhar"], "Compartilhar durante o turno" if share else "Não compartilhar")
        if sharing is None:
            return
        share = sharing.startswith("Compartilhar")
        if share:
            retention = flow_choice("Após concluir o turno", ["Excluir pensamentos compartilhados", "Manter pensamentos compartilhados"], "Excluir pensamentos compartilhados" if delete else "Manter pensamentos compartilhados")
            if retention is None:
                return
            delete = retention.startswith("Excluir")
        else:
            delete = True
            note("Os pensamentos não serão enviados ao Telegram; não há conteúdo para manter após o turno.")
        changed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-default-thought-preferences", "--share-thoughts", str(share).lower(), "--delete-thoughts", str(delete).lower()], quiet=True)
        result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
        if changed.returncode == 0:
            note("O padrão será aplicado em novas conversas e ao usar /new; a conversa atual preserva sua escolha própria.")
            offer_restart()

    def configure_parallel_responses() -> None:
        """Define o limite global de turnos simultâneos em conversas distintas."""
        telegram_path = telegram_configuration_path(root)
        command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "bootstrap"], quiet=True)
        try:
            agent = tomllib.loads(telegram_path.read_text(encoding="utf-8")).get("agent", {})
            current = int(agent.get("max_parallel_conversations", 1))
            current = min(16, max(1, current))
        except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
            current = 1
        note("O limite distribui processamento entre conversas diferentes. Mensagens da mesma conversa continuam em ordem.")
        raw = prompt(f"Máximo de conversas processadas em paralelo [{current}] (1 a 16): ").strip()
        if raw.casefold() in {"x", "\x1b"}:
            return
        try:
            maximum = int(raw) if raw else current
        except ValueError:
            result(False, "Informe um número inteiro entre 1 e 16.")
            return
        if not 1 <= maximum <= 16:
            result(False, "Informe um número entre 1 e 16.")
            return
        changed = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-max-parallel-conversations", "--max-parallel-conversations", str(maximum)], quiet=True)
        result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
        if changed.returncode == 0:
            note("O novo limite será usado após reiniciar o gateway Telegram.")
            offer_restart()

    def convert_stickers_menu() -> None:
        """Converte fontes estáticas sem alterar os originais nem sobrescrever saídas."""
        while True:
            sources = [
                path for path in discover_static_sources(root)
                if not (path.suffix.casefold() == ".webp" and path.stem.casefold().endswith("-telegram"))
            ]
            screen("Converter figurinhas estáticas", "Cria cópias WebP 512×512; os originais serão preservados")
            note(f"Pasta: {sticker_folder(root)}")
            if not sources:
                result(False, "Nenhuma imagem PNG, JPG, JPEG ou WebP aguardando conversão foi encontrada.")
                item("X.", "Voltar")
                if prompt("Opção: ").strip().casefold() in {"x", "\x1b", ""}:
                    return
                result(False, "Opção inválida.")
                continue
            for index, source in enumerate(sources, 1):
                item(f"{index}.", source.name, f"{source.stat().st_size / 1024:.1f} KiB")
            print()
            item("T.", "Converter todas", f"{len(sources)} imagem(ns)")
            item("X.", "Voltar")
            choice = prompt("Opção [1]: ").strip().casefold() or "1"
            if choice in {"x", "\x1b"}:
                return
            if choice == "t":
                selected = sources
            elif choice.isdigit() and 1 <= int(choice) <= len(sources):
                selected = [sources[int(choice) - 1]]
            else:
                result(False, "Opção inválida.")
                continue
            for source in selected:
                note(f"Convertendo {source.name}; criando uma cópia WebP 512×512.")
                try:
                    converted = convert_static_sticker(source)
                    detail = f"{converted.output.name} · {converted.output_bytes / 1024:.1f} KiB · qualidade {converted.quality}"
                    result(True, f"Figurinha convertida: {detail}.")
                    if not converted.reached_target:
                        note(f"A saída não atingiu o alvo de {STATIC_STICKER_TARGET_BYTES // 1024} KiB; ainda pode ser cadastrada se estiver dentro do limite de envio do gateway.")
                except FileExistsError:
                    note(f"{source.name}: saída já existente; preservada sem alteração.")
                except (ValueError, RuntimeError) as error:
                    result(False, f"{source.name}: {error}")
            note("Use 'Cadastrar arquivos encontrados' para informar o contexto e disponibilizar as saídas ao agente.")

    def configure_stickers() -> None:
        """Administra os ativos versionáveis de figurinha sem expor caminhos ao agente."""
        folder = sticker_folder(root)
        while True:
            catalog = load_sticker_catalog(root, 20 * 1024 * 1024)
            registered = {value["file"] for value in catalog.raw_entries.values()}
            pending = [path for path in discover_stickers(root) if path.name not in registered]
            sources = discover_static_sources(root)
            screen("Figurinhas do agente", "Catálogo versionável de mídia permitida para a conversa Telegram ativa")
            note(f"Pasta: {folder}")
            item("1.", "Cadastrar arquivos encontrados", f"{len(pending)} sem explicação")
            item("2.", "Editar explicação de uma figurinha", f"{len(catalog.raw_entries)} cadastrada(s)")
            item("3.", "Exibir catálogo e pendências", f"{len(catalog.stickers)} disponível(is) ao agente")
            item("4.", "Converter e otimizar imagens estáticas", f"{len(sources)} PNG, JPG ou WebP encontrada(s)")
            item("5.", "Abrir pasta de figurinhas", "Explorer no Windows")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}:
                return
            if choice == "1":
                if not pending:
                    result(True, "Todos os arquivos suportados já possuem cadastro.")
                    continue
                selected = flow_choice("Arquivo para cadastrar", [path.name for path in pending], pending[0].name)
                if selected is None:
                    note("Cadastro cancelado; o catálogo não foi alterado.")
                    continue
                path = next(path for path in pending if path.name == selected)
                identifier = prompt(f"ID técnico para {path.name} [{suggested_identifier(path)}] (não é a explicação): ").strip() or suggested_identifier(path)
                if identifier.casefold() in {"x", "\x1b"}:
                    note("Cadastro cancelado; o catálogo não foi alterado.")
                    continue
                if not STICKER_ID.fullmatch(identifier) or identifier in catalog.raw_entries:
                    result(False, "ID inválido ou já utilizado; use de 1 a 64 letras minúsculas, números, _ ou -, começando por letra ou número.")
                    continue
                context = prompt(f"Quando usar a figurinha {identifier} [até {MAX_CONTEXT_LENGTH} caracteres]: ").strip()
                if context.casefold() in {"x", "\x1b"}:
                    note("Cadastro cancelado; o catálogo não foi alterado.")
                    continue
                if not 1 <= len(context) <= MAX_CONTEXT_LENGTH:
                    result(False, f"A explicação deve ter entre 1 e {MAX_CONTEXT_LENGTH} caracteres; nada foi gravado.")
                    continue
                if prompt(f"Cadastrar '{identifier}' com esta explicação? [s/N]: ").strip().casefold() not in {"s", "sim"}:
                    note("Cadastro não confirmado; o catálogo não foi alterado.")
                    continue
                entries = dict(catalog.raw_entries); entries[identifier] = {"file": path.name, "context": context}
                try:
                    save_sticker_catalog(root, entries); result(True, "Figurinha cadastrada e catálogo gravado.")
                except ValueError as error:
                    result(False, f"Nada foi gravado: {error}")
            elif choice == "2":
                names = sorted(catalog.raw_entries)
                labels = [f"{identifier} · {catalog.raw_entries[identifier]['file']}" for identifier in names]
                selected = flow_choice("Figurinha para editar", labels, labels[0] if labels else "") if labels else None
                if selected is None:
                    result(False, "Nenhuma figurinha cadastrada." if not labels else "Edição cancelada.")
                    continue
                identifier = names[labels.index(selected)]
                current = catalog.raw_entries[identifier]
                context = prompt(f"Quando usar a figurinha {identifier} [{current['context']}] (até {MAX_CONTEXT_LENGTH} caracteres): ").strip() or current["context"]
                if context.casefold() in {"x", "\x1b"}:
                    continue
                if not 1 <= len(context) <= MAX_CONTEXT_LENGTH:
                    result(False, f"A explicação deve ter entre 1 e {MAX_CONTEXT_LENGTH} caracteres; nada foi gravado.")
                    continue
                entries = dict(catalog.raw_entries); entries[identifier] = {"file": current["file"], "context": context}
                try:
                    save_sticker_catalog(root, entries); result(True, "Explicação atualizada e catálogo gravado.")
                except ValueError as error:
                    result(False, f"Nada foi gravado: {error}")
            elif choice == "3":
                if catalog.stickers:
                    for sticker in catalog.stickers: print(f"    {sticker.identifier:<20} {sticker.path.name} · {sticker.context}")
                else:
                    note("Nenhuma figurinha disponível ao agente.")
                for issue in catalog.issues: result(False, issue)
                for path in pending: note(f"{path.name}: sem explicação de uso.")
                unsupported = [path for path in sources if path.suffix.casefold() != ".webp"]
                if unsupported:
                    note("PNG e JPG não podem ser enviados como figurinha; escolha a opção 4 para criar versões WebP.")
                    for path in unsupported: note(f"{path.name}: precisa de conversão para WebP.")
            elif choice == "4":
                convert_stickers_menu()
            elif choice == "5":
                folder.mkdir(parents=True, exist_ok=True)
                try:
                    if os.name == "nt": os.startfile(str(folder))
                    else: note(f"Coloque as figurinhas em: {folder}")
                except OSError as error:
                    result(False, f"Não foi possível abrir a pasta: {error}")
            else:
                result(False, "Opção inválida.")

    def telegram_section_menu(section: str) -> None:
        while True:
            telegram_path = telegram_configuration_path(root)
            command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "bootstrap"], quiet=True)
            try:
                enabled = bool(json.loads((root / "configs" / "daemon" / "services.json").read_text(encoding="utf-8")).get("telegram", {}).get("enabled", False))
            except (OSError, ValueError): enabled = False
            try:
                telegram_settings = tomllib.loads(telegram_path.read_text(encoding="utf-8"))
                totp_enabled = bool(telegram_settings.get("totp", {}).get("enabled", False))
                voice_enabled = bool(telegram_settings.get("voice_reply", {}).get("enabled", False))
                voice_profile = str(telegram_settings.get("voice_reply", {}).get("eccovox_profile") or "")
                voice_name = str(telegram_settings.get("voice_reply", {}).get("voice") or "")
                voice_language = str(telegram_settings.get("voice_reply", {}).get("language") or "")
                voice_auto_off = int(telegram_settings.get("voice_reply", {}).get("auto_off_minutes") or 15)
                outbound_media = bool(telegram_settings.get("telegram", {}).get("agent_outbound_media", telegram_settings.get("voice_reply", {}).get("agent_outbound_media", False)))
            except (OSError, tomllib.TOMLDecodeError):
                telegram_settings = {}; totp_enabled, voice_enabled, voice_profile, voice_name, voice_language, voice_auto_off, outbound_media = False, False, "", "", "", 15, False
            try:
                daemon_settings = tomllib.loads((root / "configs" / "daemon" / "daemon.toml").read_text(encoding="utf-8"))
                daemon_app_server = daemon_settings.get("app_server", {})
                daemon_instructions = daemon_settings.get("instructions", {})
                app_server_enabled = bool(daemon_app_server.get("enabled", False))
                app_server_idle = int(daemon_app_server.get("idle_timeout_seconds") or 1800)
                instruction_file = str(daemon_instructions.get("shikigami_file") or "instructions.md")
            except (OSError, tomllib.TOMLDecodeError, AttributeError, TypeError, ValueError):
                app_server_enabled, app_server_idle, instruction_file = False, 1800, "instructions.md"
            agent_preferences = telegram_settings.get("agent", {}) if "telegram_settings" in locals() and isinstance(telegram_settings.get("agent", {}), dict) else {}
            owner_preferences = bool(agent_preferences.get("owner_execution_preferences", True)); owner_models = ";".join(str(value) for value in agent_preferences.get("owner_allowed_models", []) if isinstance(value, str)); owner_efforts = ";".join(str(value) for value in agent_preferences.get("owner_allowed_reasoning_efforts", []) if isinstance(value, str))
            title, subtitle = {
                "gateway": ("Gateway Telegram", "Ativação e teste da conexão com o bot"),
                "credential": ("Credencial do bot", "KeePass Vault e autenticação protegida"),
                "app-server": ("Codex App Server", "Conversa persistente e teste controlado do agente"),
                "owners": ("Owners e comandos", "Pareamento, comandos privados e preferências"),
                "security": ("Segurança e instruções", "TOTP e instruções particulares do Shikigami"),
                "media": ("Arquivos e fotos do agente", "Permissão do gateway Telegram para mídia de saída"),
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
            elif section == "security":
                item("1.", "Desabilitar TOTP" if totp_enabled else "Configurar e habilitar TOTP")
                item("2.", "Instruções do Shikigami", instruction_file)
            else:
                item("1.", "Desabilitar envio pelo agente" if outbound_media else "Habilitar envio pelo agente", "Arquivos, fotos e áudio na conversa Telegram ativa")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            actions = {
                "gateway": {"1": "1", "2": "3"},
                "credential": {"1": "2", "2": "3"},
                "app-server": {"1": "7", "2": "9", "3": "10"},
                "owners": {"1": "4", "2": "8", "3": "12"},
                "security": {"1": "6", "2": "13"},
                "media": {"1": "14"},
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
                default_entry = telegram_token_entry(root)
                profile = values[int(selected)-1]
                while True:
                    entry = prompt(f"Entrada KeePass [{default_entry}]: ").strip() or default_entry
                    try:
                        current = tomllib.loads(telegram_path.read_text(encoding="utf-8")).get("telegram", {})
                        reference_changed = (str(current.get("vault_profile") or ""), str(current.get("token_entry") or "")) != (profile, entry)
                    except (OSError, tomllib.TOMLDecodeError, AttributeError): reference_changed = True
                    saved = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-credential", "--profile", profile, "--entry", entry], quiet=True)
                    if saved.returncode != 0: result(False, (saved.stdout or saved.stderr).strip()); break
                    checked = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "check-entry", "--profile", profile, "--entry", entry], quiet=True)
                    try: probe = json.loads(checked.stdout); exists, detail = bool(probe["exists"]), str(probe["message"])
                    except (json.JSONDecodeError, KeyError): exists, detail = False, "Não foi possível verificar a entrada no KeePass."
                    if exists:
                        result(True, detail)
                        action = flow_choice("Credencial do bot", ["Usar a entrada existente", "Substituir o token", "Reinformar a entrada KeePass"], "Usar a entrada existente")
                        if action is None:
                            continue
                        if action.startswith("Usar"):
                            result(True, "Credencial configurada com a entrada existente.")
                            if reference_changed: offer_restart("A referência da credencial do bot")
                            break
                        if action.startswith("Reinformar"):
                            continue
                    else:
                        result(False, f"A entrada não foi encontrada ou não pôde ser lida: {detail}")
                        action = flow_choice("Credencial do bot", ["Criar o token nesta entrada", "Reinformar a entrada KeePass", "Configurar a referência e incluir o token depois"], "Configurar a referência e incluir o token depois")
                        if action is None or action.startswith("Reinformar"):
                            continue
                        if action.startswith("Configurar"):
                            result(True, "Referência salva; inclua o token no KeePass antes de habilitar o gateway.")
                            break
                    token = getpass.getpass(Ui.text("› Token do bot (não será exibido): ", Ui.violet, Ui.bold)).strip()
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
                    profile = str(telegram_settings.get("telegram", {}).get("vault_profile") or "")
                    if not profile:
                        result(False, "Configure primeiro a credencial do bot e seu perfil de vault MCP.")
                        continue
                    totp = telegram_settings.get("totp", {})
                    real_entry = configured_telegram_totp_entry(root, "real", str(totp.get("real_password_entry") or ""))
                    real = configure_totp_password(root, profile, "real", telegram_totp_entry(root, "real"), real_entry)
                    if real is None:
                        continue
                    fake_entry = configured_telegram_totp_entry(root, "fake", str(totp.get("fake_password_entry") or ""))
                    fake = configure_totp_password(root, profile, "falsa", telegram_totp_entry(root, "fake"), fake_entry)
                    if fake is None:
                        continue
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
                        if checked.returncode == 0: note("A alteração será reaplicada ao retomar a mesma thread na próxima mensagem do Telegram.")
                    except (OSError, ValueError) as error: result(False, f"Não foi possível abrir o editor: {error}")
            elif choice == "14":
                enabled_media = prompt(f"Habilitar o envio de arquivos, fotos e áudio pelo agente? [{'S' if outbound_media else 'n'}]: ").strip().casefold() or ("s" if outbound_media else "n")
                arguments = [sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-agent-outbound-media"] + (["--enabled"] if enabled_media == "s" else ["--disabled"])
                changed = command(arguments, quiet=True); result(changed.returncode == 0, (changed.stdout or changed.stderr).strip())
                if changed.returncode == 0: offer_restart()
            else: result(False, "Opção inválida.")

    def configure_wizard_telegram_credential() -> bool:
        """Configura a credencial com os valores existentes, sem desviar ao menu manual."""
        nonlocal telegram_token_changed
        path = telegram_configuration_path(root)
        try:
            settings = tomllib.loads(path.read_text(encoding="utf-8")); telegram = settings.get("telegram", {})
            current_profile, current_entry = str(telegram.get("vault_profile") or ""), str(telegram.get("token_entry") or "")
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
        default_entry = configured_telegram_token_entry(root, current_entry)
        entry = prompt(f"Entrada do vault [{default_entry}]: ").strip() or default_entry
        saved = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "set-credential", "--profile", profile, "--entry", entry], quiet=True)
        if saved.returncode != 0: result(False, (saved.stdout or saved.stderr).strip()); return False
        checked = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "check-entry", "--profile", profile, "--entry", entry], quiet=True)
        try: probe = json.loads(checked.stdout); exists, detail = bool(probe["exists"]), str(probe["message"])
        except (json.JSONDecodeError, KeyError): exists, detail = False, "Não foi possível verificar a entrada no KeePass."
        def replace_token() -> bool:
            nonlocal telegram_token_changed
            token = getpass.getpass(Ui.text("› Token do bot (não será exibido): ", Ui.violet, Ui.bold)).strip()
            if not token:
                result(False, "O token do bot é obrigatório.")
                return False
            written = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "write-token", "--profile", profile, "--entry", entry], quiet=True, input_value=token)
            if written.returncode != 0:
                result(False, (written.stdout or written.stderr).strip())
                return False
            result(True, "Token gravado com segurança no cofre MCP.")
            telegram_token_changed = True
            return True
        if not exists:
            result(False, detail)
            if prompt("Cadastrar o token do bot nesta entrada agora? [S/n]: ").strip().casefold() not in {"", "s", "sim"}: return False
            if not replace_token(): return False
        else:
            result(True, detail)
            action = flow_choice("Credencial do bot", ["Usar o token salvo", "Substituir o token"], "Usar o token salvo")
            if action is None: return False
            if action.startswith("Substituir") and not replace_token(): return False
        while True:
            tested = command([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "test-telegram"], quiet=True)
            result(tested.returncode == 0, (tested.stdout or tested.stderr).strip())
            if tested.returncode == 0: return True
            if prompt("Substituir o token do bot e testar novamente? [S/n]: ").strip().casefold() not in {"", "s", "sim"}: return False
            if not replace_token(): return False

    def telegram_wizard() -> None:
        """Conduz a configuração mínima do gateway, preservando cada etapa válida."""
        nonlocal telegram_token_changed
        telegram_token_changed = False
        flow_heading("Wizard do Telegram", "Configuração guiada do gateway e do primeiro owner")
        note("Cada etapa válida é mantida. O pareamento exige uma ação externa no Telegram e pode ser retomado depois.")
        print("\n  1. Pré-requisitos do Shikigami e daemon")
        main_checks = execution_diagnostics(root, lambda message: print("    → " + message)); print_checks(main_checks)
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
            print()
            item("W.", "Wizard de configuração do Telegram", "Configura o gateway e o primeiro owner")
            print()
            item("1.", "Gateway Telegram", "Ativação e teste da conexão")
            item("2.", "Credencial do bot", "Perfil de vault MCP e referência do token")
            item("3.", "Codex App Server", "Conversa persistente e teste do agente")
            item("4.", "Owners e comandos", "Pareamento e preferências de execução")
            print()
            item("5.", "Segurança e instruções", "TOTP e instruções particulares")
            item("6.", "Respostas em áudio", "Perfil EccoVox e duração da resposta por voz")
            item("7.", "Arquivos e fotos do agente", "Permissão de mídia enviada pelo gateway Telegram")
            item("8.", "Padrão de pensamentos", "Comportamento inicial de novas conversas e /new")
            item("9.", "Figurinhas do agente", "Catálogo e explicações de uso")
            item("10.", "Paralelismo de respostas", "Máximo de conversas processadas simultaneamente")
            item("11.", "Entradas durante execução", "Fila, direcionamento e entrega pós-turno condicionada")
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
            elif choice == "5": telegram_section_menu("security")
            elif choice == "6": configure_voice_replies()
            elif choice == "7": telegram_section_menu("media")
            elif choice == "8": configure_default_thoughts()
            elif choice == "9": configure_stickers()
            elif choice == "10": configure_parallel_responses()
            elif choice == "11":
                if configure_gateway_execution(root): offer_restart()
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
        flow_heading("Diagnóstico Mini Apps", f"Local: {local} · Público: {public}")
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
                if prompt("Instalar a CA no computador? [s/N]: ").strip().casefold() not in {"s", "sim"}: result(False, "Cancelado."); continue
                completed = miniapps_command("tls", "install-trust", "--confirm")
            else: result(False, "Opção inválida."); continue
            result(completed.returncode == 0, (completed.stdout or completed.stderr).strip())

    def publication_command(*arguments: str, quiet: bool = True, read_result: bool = False) -> subprocess.CompletedProcess[str]:
        return command([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), *arguments], quiet=quiet, capture_stdout=read_result)

    def tunnel_settings() -> dict[str, object]:
        path = root / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "tunnel.toml"
        try: return tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError): return {}

    def show_publication_diagnosis() -> bool:
        flow_heading("Diagnóstico da publicação externa", "Daemon, KeePass, Gateway HTTPS, cloudflared e conexão do Tunnel")
        checks = diagnose_publication(root, progress=lambda message: print("    → " + message, flush=True))
        print()
        print_checks(checks)
        required = [check for check in checks if check.get("label") != "Mini Apps (opcional)"]
        ready = bool(required) and all(check.get("state") == "ok" for check in required)
        result(ready, "Publicação externa pronta; Gateway e conexão do Tunnel confirmados." if ready else "A publicação externa ainda não está pronta. Siga os próximos passos indicados ou execute W.")
        return ready

    def test_publication() -> bool:
        """Confirma acesso ao link público temporário, com prazo e cancelamento local."""
        flow_heading("Teste de acesso público", "Confirmação pelo navegador, independente de Mini Apps")

        def response(*arguments: str) -> dict[str, object]:
            completed = publication_command(*arguments)
            if completed.returncode != 0:
                raise ValueError("publication_test_unavailable")
            value = json.loads(completed.stdout or "{}")
            if not isinstance(value, dict):
                raise ValueError("invalid_publication_test_response")
            return value

        test_id, state = "", "waiting"
        try:
            gateway = response("gateway", "status")
            tunnel = response("cloudflare-tunnel", "status")
            if gateway.get("ready") is not True or not str(gateway.get("public_origin") or "").startswith("https://"):
                result(False, "O teste exige o Gateway HTTPS pronto e um hostname público configurado. Prepare a publicação na opção 4 e repita em T. Testar acesso público.")
                return False
            if any(tunnel.get(key) is not True for key in ("configured", "provisioned", "running", "connected", "ingress_ready")):
                result(False, "O teste exige o Tunnel provisionado, conectado e com ingress confirmado para o Gateway atual. Consulte o diagnóstico (!) e repita em T. Testar acesso público.")
                return False
            started = response("gateway", "publication-test", "start", "--confirm")
            identifier = started.get("test_id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise ValueError("invalid_publication_test_response")
            test_id = identifier
            url, duration = started.get("url"), started.get("expires_in_seconds")
            if started.get("state") != "waiting" or not isinstance(url, str) or not url.startswith("https://") or any(character.isspace() for character in url) or type(duration) is not int or not 1 <= duration <= 120:
                raise ValueError("invalid_publication_test_response")
            note("Abra este link em um navegador. Para conferir o acesso externo, você pode usar o celular fora da rede local:")
            print(f"\n    {url}\n", flush=True)
            note(f"Aguardando a abertura do link por até {duration} segundos. X cancela" + ("." if os.name == "nt" else " (pressione Enter após X)."))
            deadline = time.monotonic() + duration
            next_poll, next_notice = 0.0, time.monotonic() + 10
            while True:
                if publication_test_cancelled():
                    result(False, "Teste de acesso público cancelado.")
                    return False
                now = time.monotonic()
                if now >= next_poll or now >= deadline:
                    checked = response("gateway", "publication-test", "status", test_id)
                    state = str(checked.get("state") or "")
                    if state == "confirmed":
                        result(True, "Acesso público confirmado: o Gateway recebeu a abertura do link HTTPS de teste.")
                        return True
                    if state in {"expired", "cancelled"}:
                        result(False, "O link de teste expirou sem confirmação. Repita em T. Testar acesso público." if state == "expired" else "Teste de acesso público cancelado.")
                        return False
                    if state != "waiting":
                        raise ValueError("invalid_publication_test_response")
                    next_poll = now + 1
                if now >= deadline:
                    break
                if now >= next_notice:
                    note("Ainda aguardando a abertura do link; X cancela.")
                    next_notice = now + 10
                time.sleep(0.2)
            result(False, "O link de teste expirou sem confirmação. Repita em T. Testar acesso público.")
            return False
        except KeyboardInterrupt:
            result(False, "Teste de acesso público cancelado.")
            return False
        except (OSError, ValueError, subprocess.SubprocessError):
            result(False, "Não foi possível confirmar o teste público. Confira o diagnóstico (!) e se o Gateway e o Daemon estão atualizados; repita em T. Testar acesso público.")
            return False
        finally:
            if test_id and state not in {"confirmed", "expired", "cancelled"}:
                try:
                    publication_command("gateway", "publication-test", "cancel", test_id)
                except (OSError, subprocess.SubprocessError):
                    pass

    def cloudflared_installed(executable: str) -> bool:
        selected = shutil.which(executable) or (executable if Path(executable).is_file() else None)
        if not selected: return False
        try:
            checked = subprocess.run([selected, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
        except (OSError, subprocess.SubprocessError): return False
        return checked.returncode == 0 and "cloudflared" in checked.stdout.lower()

    def start_publication_service(service: str) -> bool:
        if not daemon_running():
            result(False, "Inicie o daemon em Processo/Serviço do Daemon antes de executar esta ação.")
            return False
        completed = publication_command("start", service)
        if completed.returncode != 0:
            result(False, (completed.stderr or completed.stdout or f"Não foi possível iniciar {service}. Reinicie o daemon e consulte !.").strip()); return False
        try: state = json.loads(completed.stdout or "{}")
        except ValueError: state = {}
        if not isinstance(state, dict) or state.get("state") != "running":
            detail = str(state.get("last_error") or "O serviço não confirmou o estado running.") if isinstance(state, dict) else "Resposta de estado inválida."
            result(False, f"Falha ao iniciar {service}: {detail} Consulte o diagnóstico (!)."); return False
        result(True, f"Serviço {service} em execução.")
        return True

    def install_publication_binary(update: bool = False) -> bool:
        flow_heading("Verificar ou atualizar cloudflared" if update else "Localizar ou instalar cloudflared", "Detecta instalação existente e consulta a versão oficial antes de decidir")
        current = tunnel_settings()
        note("Informe a pasta onde o cloudflared já está instalado ou o destino de uma nova instalação.")
        note("A verificação não exige conta, domínio ou token. Um executável existente pode ser reutilizado sem download.")
        configured = str(current.get("cloudflared_executable") or "cloudflared")
        existing_parent = Path(configured).parent if Path(configured).is_absolute() else None
        suggested = str(current.get("install_directory") or existing_parent or root / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "bin")
        destination = prompt(f"Diretório de instalação [{suggested}]: ").strip() or suggested
        if destination.casefold() in {"x", "\x1b"}: return False
        if not Path(destination).is_absolute():
            result(False, "Informe um diretório absoluto acessível ao usuário do daemon.")
            return False
        from onmyoji_daemon.cloudflare_tunnel.exception import TunnelError
        try:
            inspected = inspect_cloudflared_directory(destination, configured, progress=note)
        except (TunnelError, OSError) as error:
            result(False, f"Não foi possível verificar o diretório: {error}"); return False
        candidates = inspected["candidates"]
        for error in inspected.get("errors", []):
            result(False, f"{error['executable']}: {error['message']}")
        latest = inspected.get("latest_version")
        if latest:
            note(f"Versão oficial estável mais recente: {latest}.")
        else:
            note("Não foi possível confirmar a versão oficial mais recente: " + str(inspected.get("latest_error") or "consulta indisponível") + ". A reutilização de um binário local validado continua disponível.")
        if candidates:
            selected = candidates[0]
            if len(candidates) > 1:
                labels = [f"{entry['executable']} · versão {entry['version']}" for entry in candidates]
                choice = flow_choice("Executável encontrado para validar e utilizar", labels, labels[0])
                if choice is None: return False
                selected = candidates[labels.index(choice)]
            result(True, f"Executável encontrado: {selected['executable']} · versão {selected['version']}.")
            def version_key(value: str) -> tuple[int, ...]:
                match = re.fullmatch(r"v?(\d{4})\.(\d+)\.(\d+)", value)
                return tuple(int(part) for part in match.groups()) if match else ()
            newer = bool(latest and version_key(str(latest)) > version_key(selected["version"]))
            if newer:
                note(f"Há atualização disponível: {selected['version']} → {latest}.")
            elif latest:
                note("A versão instalada já é a mais recente ou é posterior à release estável consultada.")
            install_choice = f"Instalar nova versão {latest}" if newer else "Instalar cópia gerenciada"
            choice = flow_choice("O que fazer com o cloudflared", ["Usar o executável existente", install_choice], "Usar o executável existente")
            if choice is None: return False
            if choice == "Usar o executável existente":
                if not start_publication_service("cloudflare-tunnel"): return False
                completed = publication_command("cloudflare-tunnel", "use-cloudflared", "--executable", selected["executable"], "--confirm", "--wait", quiet=False)
                result(completed.returncode == 0, "Executável existente validado e selecionado. Nenhum arquivo foi baixado ou substituído." if completed.returncode == 0 else "Não foi possível selecionar o executável existente; confira o diagnóstico acima.")
                return completed.returncode == 0
        else:
            note("Nenhum executável cloudflared válido foi encontrado no diretório informado.")
        base = str(Path(destination).parent) if Path(destination).is_file() else destination
        note("Uma instalação gerenciada grava a nova versão em uma subpasta versions/ e preserva os executáveis existentes.")
        arguments = ["cloudflare-tunnel", "update-cloudflared" if update else "install-cloudflared", "--install-directory", base, "--confirm", "--wait"]
        if latest: arguments.extend(["--version", str(latest)])
        external = configured not in {"", "cloudflared"} and not current.get("managed_version")
        if external:
            note("Ao confirmar, a instância passará a usar a cópia gerenciada; o executável externo será preservado.")
            arguments.append("--adopt")
        if prompt("Baixar e instalar a versão oficial neste diretório? [s/N]: ").strip().casefold() not in {"s", "sim"}: return False
        if not start_publication_service("cloudflare-tunnel"): return False
        completed = publication_command(*arguments, quiet=False)
        result(completed.returncode == 0, "cloudflared instalado e validado." if completed.returncode == 0 else "A instalação não foi concluída; consulte o resultado acima.")
        return completed.returncode == 0

    def configure_publication() -> bool:
        flow_heading("Configurar Cloudflare e credencial", "Identificadores locais; API Token protegido pelo KeePass")
        current = tunnel_settings()
        legacy = root / "configs" / "daemon" / "services" / "mini-apps" / "tunnel.toml"
        if not current.get("account_id") and legacy.is_file():
            result(False, "Existe uma configuração legada. Use 8. Migrar configuração legada para preservar o Tunnel e suas URLs.")
            return False
        note("1. API Token: autoriza o acesso à Cloudflare e será salvo no campo senha do KeePass.")
        note("Crie um API Token com Account > Cloudflare Tunnel > Edit e Zone > DNS > Edit, limitado à conta e à zona utilizadas.")
        note("Onde criar: https://dash.cloudflare.com/profile/api-tokens")
        credential = configure_mcp_password_reference(root, "Cloudflare Tunnel", vault_entry_path(root, "Serviço", "CloudflareTunnel", definition="APIToken"), str(current.get("credential_profile") or ""), str(current.get("credential_entry_path") or ""))
        if credential is None: return False
        note("2. Account ID e Zone ID identificam sua conta e o domínio; não são tokens e ficam em tunnel.toml.")
        note("No painel Cloudflare, abra o domínio e copie Account ID e Zone ID na seção API da página Overview.")
        note("Ajuda: https://developers.cloudflare.com/fundamentals/account/find-account-and-zone-ids/")
        values: dict[str, str] = {}
        for key, label in (("account_id", "Account ID da conta Cloudflare"), ("zone_id", "Zone ID do domínio Cloudflare")):
            default = str(current.get(key) or "")
            while True:
                value = prompt(label + (f" [{default}]" if default else " [X cancela]") + ": ").strip() or default
                if value.casefold() in {"x", "\x1b"}: return False
                if re.fullmatch(r"[0-9a-fA-F]{32}", value):
                    values[key] = value.lower(); break
                result(False, f"{label} deve ter 32 caracteres hexadecimais, sem espaços. Copie o ID do painel; a credencial já foi tratada no KeePass.")
        note("3. Hostname: endereço público completo deste Shikigami, como tsuki.seudominio.com, sem https:// nem caminho.")
        note("O cloudflared conecta este computador à Cloudflare; não é necessário informar o IP público do computador.")
        from onmyoji_daemon.cloudflare_tunnel.configuration import TunnelDefinition
        from onmyoji_daemon.cloudflare_tunnel.exception import TunnelError
        hostname_default = str(current.get("hostname") or "")
        while True:
            hostname = prompt("Hostname público" + (f" [{hostname_default}]" if hostname_default else " [X cancela]") + ": ").strip() or hostname_default
            if hostname.casefold() in {"x", "\x1b"}: return False
            try:
                TunnelDefinition(values["account_id"], values["zone_id"], hostname.lower()).validate()
            except TunnelError as error:
                result(False, str(error)); continue
            values["hostname"] = hostname.lower(); break
        values["credential_profile"], values["credential_entry_path"] = credential
        note(f"Os identificadores e a referência KeePass serão gravados em {root / 'configs/daemon/services/cloudflare-tunnel/tunnel.toml'}.")
        note("Esta etapa salva a configuração local; o provisionamento remoto será confirmado separadamente.")
        if prompt("Salvar a configuração Cloudflare e a referência KeePass? [S/n]: ").strip().casefold() not in {"", "s", "sim"}: return False
        completed = publication_command("cloudflare-tunnel", "configure", "--values", json.dumps(values))
        if completed.returncode != 0:
            result(False, (completed.stderr or completed.stdout or "A configuração não pôde ser salva.").strip()); return False
        result(True, "Configuração Cloudflare e referência KeePass salvas. O token permanece no cofre.")
        return True

    def provision_publication() -> bool:
        current = tunnel_settings()
        if not all(current.get(key) for key in ("account_id", "zone_id", "hostname", "credential_profile", "credential_entry_path")):
            result(False, "Configure Cloudflare e a credencial primeiro: opção 1 ou W. A instalação do binário, opção 3, é independente."); return False
        executable = str(current.get("cloudflared_executable") or "cloudflared")
        if not cloudflared_installed(executable) and not install_publication_binary(): return False
        flow_heading("Verificar e preparar a publicação Cloudflare", "Recupera publicações existentes sem depender da instalação anterior")
        note("O Gateway e o serviço administrativo serão preparados localmente. A consulta remota não altera Tunnel, ingress nem DNS.")
        if not start_publication_service("gateway") or not start_publication_service("cloudflare-tunnel"): return False
        note("Aguardando o Supervisor confirmar a origem HTTPS e a CA do Gateway no serviço Tunnel.")
        deadline, origin = time.monotonic() + 10, {}
        while time.monotonic() < deadline:
            checked = publication_command("cloudflare-tunnel", "status")
            try: origin = json.loads(checked.stdout or "{}") if checked.returncode == 0 else {}
            except ValueError: origin = {}
            if isinstance(origin, dict) and origin.get("activated") is True and origin.get("ca_file"): break
            time.sleep(0.25)
        else:
            result(False, "O Supervisor não confirmou a origem HTTPS em 10 segundos. Nenhuma solicitação de provisionamento foi enviada; consulte o diagnóstico (!)."); return False
        note("Consultando o DNS, a conta e a configuração remota com o API Token do KeePass.")
        try:
            inspected = publication_command("cloudflare-tunnel", "inspect-publication", "--wait", quiet=False, read_result=True)
        except (OSError, subprocess.SubprocessError) as error:
            result(False, f"Não foi possível executar a consulta Cloudflare: {error}"); return False
        try: inspection = json.loads(inspected.stdout or "{}")
        except ValueError: inspection = {}
        if inspected.returncode != 0:
            detail = inspection.get("error") if isinstance(inspection, dict) else None
            stage = inspection.get("progress") if isinstance(inspection, dict) else None
            code = inspection.get("error_code") if isinstance(inspection, dict) else None
            result(False, "A consulta Cloudflare falhou. " + (f"Etapa: {stage}. " if stage else "") + (f"Código: {code}. " if code else "") + str(detail or "Confira o diagnóstico acima; nenhuma substituição foi solicitada."))
            return False
        preview = inspection.get("result") if isinstance(inspection, dict) and inspection.get("state") == "succeeded" else None
        if not isinstance(preview, dict) or preview.get("status") not in {"new", "existing"}:
            result(False, "A consulta não retornou uma prévia válida. Reinicie o serviço Cloudflare Tunnel para carregar o código atualizado e tente novamente."); return False
        if preview["status"] == "existing":
            preview_id = preview.get("preview_id")
            if not isinstance(preview_id, str) or not re.fullmatch(r"[a-f0-9]{32}", preview_id):
                result(False, "A consulta não retornou um identificador de prévia válido. Execute a consulta novamente."); return False
            note(f"Publicação existente encontrada: {preview.get('hostname')}")
            note(f"Tunnel: {preview.get('tunnel_name')} · ID {preview.get('tunnel_id')}")
            if preview.get("configured_tunnel_id") and preview["configured_tunnel_id"] != preview.get("tunnel_id"):
                note(f"O ID local {preview['configured_tunnel_id']} difere do DNS. Ao confirmar, o ID local será substituído pelo Tunnel encontrado acima.")
            note(f"Origem atual: {preview.get('current_origin') or 'sem origem cadastrada'}")
            note(f"Nova origem: {preview.get('new_origin')}")
            note(str(preview.get("message") or "O Tunnel e seu DNS serão reutilizados; a origem e a validação TLS desta publicação serão atualizadas."))
            note("DNS: o CNAME existente será preservado." if preview.get("dns_action") == "reuse" else "DNS: será criado o CNAME ausente para este Tunnel.")
            note(f"Identificador da prévia e do backup: {preview_id}.")
            note("Uma cópia da configuração anterior será guardada para restauração. As permissões de escrita são verificadas pela Cloudflare ao aplicar a alteração.")
            if prompt("Reutilizar este Tunnel e substituir a configuração desta publicação? [s/N]: ").strip().casefold() not in {"s", "sim"}:
                note("Reutilização cancelada; nenhuma substituição foi solicitada à Cloudflare."); return False
            arguments = ["cloudflare-tunnel", "adopt-publication", "--preview-id", preview_id, "--confirm", "--wait"]
        else:
            note(f"Nenhuma publicação anterior foi encontrada para https://{current['hostname']}. Será criada a publicação para {preview.get('new_origin')}.")
            if prompt("Provisionar hostname, ingress e DNS na Cloudflare? [s/N]: ").strip().casefold() not in {"s", "sim"}: return False
            arguments = ["cloudflare-tunnel", "provision", "--confirm", "--wait"]
        completed = publication_command(*arguments, quiet=False)
        result(completed.returncode == 0, "Provisionamento concluído; confira a conexão no diagnóstico (!)." if completed.returncode == 0 else "Provisionamento não concluído; confira a etapa, o código de erro e o resultado acima.")
        return completed.returncode == 0

    def configure_publication_gateway() -> bool:
        if not start_publication_service("gateway"): return False
        status = publication_command("gateway", "status")
        try: current = json.loads(status.stdout or "{}") if status.returncode == 0 else {}
        except ValueError: current = {}
        if not isinstance(current, dict) or not isinstance(current.get("http_port"), int):
            result(False, (status.stderr or status.stdout or "Não foi possível consultar a porta do Gateway. Consulte o diagnóstico (!).").strip()); return False
        port = current["http_port"]
        while True:
            raw = prompt(f"Porta HTTPS local do Gateway [{port}]: ").strip() or str(port)
            if raw.casefold() in {"x", "\x1b"}: return False
            try: selected = int(raw)
            except ValueError: selected = 0
            if 1 <= selected <= 65535: break
            result(False, "Informe uma porta numérica entre 1 e 65535.")
        if selected == port:
            result(True, f"Gateway HTTPS preparado na porta {port}."); return True
        note("A alteração reinicia o Gateway; um ingress já provisionado precisará ser atualizado na opção 4.")
        if prompt("Salvar a porta e reiniciar o Gateway agora? [S/n]: ").strip().casefold() not in {"", "s", "sim"}: return False
        completed = publication_command("gateway", "configure", "--values", json.dumps({"http_port": selected}), "--confirm")
        if completed.returncode != 0:
            result(False, (completed.stderr or completed.stdout or "A porta não pôde ser salva.").strip()); return False
        return lifecycle("restart", ["gateway"])

    def publication_wizard() -> None:
        flow_heading("Assistente de publicação externa", "Gateway HTTPS e Tunnel Cloudflare, passo a passo")
        note("O Gateway encaminha as requisições aos serviços locais; o Tunnel publica o Gateway no hostname Cloudflare.")
        note("Mini Apps é um consumidor opcional e possui seu próprio wizard. X cancela a etapa atual; etapas já confirmadas permanecem salvas.")
        print("\n  1. Verificar o daemon da instância")
        if not daemon_running():
            if not installed():
                result(False, "Prepare o daemon em 'Validar runtime do daemon' e execute este wizard novamente."); return
            if prompt("Iniciar o daemon agora para configurar a publicação? [S/n]: ").strip().casefold() not in {"", "s", "sim"}: return
            if not lifecycle("service-start" if service_installed() else "process-start") or not wait_for_daemon_ready():
                result(False, "O daemon não respondeu. Consulte o diagnóstico (!)."); return
        registered = publication_command("list-services")
        try: names = {item["name"] for item in json.loads(registered.stdout or "[]") if isinstance(item, dict) and "name" in item}
        except (ValueError, TypeError): names = set()
        if not {"gateway", "cloudflare-tunnel"} <= names:
            result(False, "O daemon em memória ainda não reconhece Gateway e Tunnel. Reinicie-o em Processo/Serviço do Daemon e reabra W."); return
        result(True, "Daemon respondendo com os serviços Gateway e Tunnel disponíveis.")
        print("\n  2. Configurar token KeePass, conta Cloudflare e hostname")
        if not configure_publication(): return
        print("\n  3. Validar ou instalar o executável cloudflared")
        executable = str(tunnel_settings().get("cloudflared_executable") or "cloudflared")
        if cloudflared_installed(executable): result(True, "Executável cloudflared encontrado e validado.")
        elif not install_publication_binary(): return
        print("\n  4. Preparar o Gateway HTTPS local")
        note("O certificado e a origem HTTPS são locais; confirme a porta que será usada pelo Tunnel.")
        if not configure_publication_gateway(): return
        print("\n  5. Descobrir, reutilizar ou criar a publicação e iniciar o Tunnel")
        if not provision_publication(): return
        print("\n  6. Retomar Gateway e Tunnel após reiniciar o daemon")
        if prompt("Habilitar os dois serviços para início automático com o daemon? [S/n]: ").strip().casefold() in {"", "s", "sim"}:
            if not lifecycle("enable", ["gateway"]) or not lifecycle("enable", ["cloudflare-tunnel"]): return
        print("\n  7. Diagnóstico final")
        show_publication_diagnosis()
        print("\n  8. Confirmar o acesso pelo link público")
        test_publication()
        note("Para publicar Mini Apps e OAuth, abra Daemon > M. Mini Apps > W. O acesso local pode ser validado separadamente.")

    def publication_listeners() -> None:
        if not start_publication_service("gateway"): return
        completed = publication_command("gateway", "listeners", "list")
        flow_heading("Listeners públicos", "Cada primeiro segmento possui um proprietário exclusivo")
        result(completed.returncode == 0, (completed.stdout or completed.stderr).strip())
        if completed.returncode != 0: return
        selected = flow_choice("Ação sobre um listener", ["Somente consultar", "Habilitar", "Desabilitar", "Remover", "Restaurar"], "Somente consultar")
        if selected in {None, "Somente consultar"}: return
        prefix = prompt("Primeiro segmento do path: ").strip()
        if not prefix or prefix.casefold() == "x": return
        owner = prompt("Serviço proprietário: ").strip()
        if not owner or owner.casefold() == "x": return
        actions = {"Habilitar": "enable", "Desabilitar": "disable", "Remover": "remove", "Restaurar": "restore"}
        if prompt(f"{selected} o listener {prefix}? [s/N]: ").strip().casefold() not in {"s", "sim"}: return
        completed = publication_command("gateway", "listeners", actions[selected], prefix, "--owner", owner, "--confirm")
        result(completed.returncode == 0, (completed.stdout or completed.stderr).strip())

    def publication_migration() -> None:
        flow_heading("Migrar configuração legada", "Usa o arquivo antigo de Mini Apps e mantém hostname, Tunnel ID e referências KeePass")
        inspection = publication_command("publication-migrate", "inspect")
        result(inspection.returncode == 0, (inspection.stdout or inspection.stderr).strip())
        if inspection.returncode != 0: return
        action = flow_choice("Operação de migração", ["Somente consultar", "Migrar", "Rollback"], "Somente consultar")
        if action in {None, "Somente consultar"}: return
        note("A transição exige uma janela de manutenção. O rollback da infraestrutura exige reinstalar a versão anterior para reativar o controlador legado.")
        if prompt(f"{'Migrar' if action == 'Migrar' else 'Restaurar infraestrutura anterior'} agora? [s/N]: ").strip().casefold() not in {"s", "sim"}: return
        completed = publication_command("publication-migrate", "apply" if action == "Migrar" else "rollback", "--confirm", quiet=False)
        result(completed.returncode == 0, "Operação concluída; confira o estado informado." if completed.returncode == 0 else "Operação interrompida; consulte o diagnóstico e o manifesto de migração.")

    def restore_publication() -> None:
        flow_heading("Restaurar publicação anterior", "Usa o backup de uma reutilização confirmada neste setup")
        directory = root / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "publication-recovery"
        backups = []
        for path in sorted(directory.glob("*.json"), reverse=True):
            if not re.fullmatch(r"[a-f0-9]{32}", path.stem): continue
            try: saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError): continue
            if isinstance(saved, dict) and saved.get("state") in {"applied", "applying", "failed", "rolling_back"}:
                backups.append((path.stem, str(saved["state"])))
        if not backups:
            result(False, "Não há backup de substituição aplicada disponível para restaurar. As prévias apenas consultadas não alteraram a Cloudflare."); return
        labels = [f"{identifier} · {state}" for identifier, state in backups]
        selected = flow_choice("Backup da publicação a restaurar", labels, labels[0])
        if selected is None: return
        identifier = backups[labels.index(selected)][0]
        note("A restauração recupera a origem e o TLS anteriores. Um CNAME anterior é preservado; somente um CNAME criado pela substituição pode ser removido.")
        note("A origem antiga precisa estar em execução para atender requisições. Alterações posteriores ao backup impedem a restauração automática.")
        if prompt("Restaurar a configuração anterior desta publicação? [s/N]: ").strip().casefold() not in {"s", "sim"}: return
        if not start_publication_service("cloudflare-tunnel"): return
        completed = publication_command("cloudflare-tunnel", "rollback-publication", "--preview-id", identifier, "--confirm", "--wait", quiet=False)
        result(completed.returncode == 0, "Configuração anterior restaurada; confira a disponibilidade da origem antiga." if completed.returncode == 0 else "A restauração não foi concluída. Confira a etapa e o erro acima; o backup foi preservado.")

    def publication_menu() -> None:
        while True:
            screen("Publicação externa", "Gateway HTTP e Cloudflare Tunnel independentes de Mini Apps")
            item("!.", "Diagnóstico da publicação externa", "Pré-requisitos, KeePass, Gateway, binário e conexão")
            print()
            item("W.", "Wizard de publicação externa", "Configura e prepara Gateway e Tunnel passo a passo")
            item("T.", "Testar acesso público", "Link temporário e confirmação pelo navegador")
            print()
            item("1.", "Configurar Cloudflare e credencial", "Token no KeePass, Account ID, Zone ID e hostname")
            item("2.", "Estado e diagnóstico", "Disponível mesmo sem configuração ou serviços iniciados")
            item("3.", "Instalar ou atualizar cloudflared")
            item("4.", "Preparar ou recuperar publicação", "Detecta e permite reutilizar Tunnel, ingress e DNS existentes")
            item("5.", "Administrar serviços", "Iniciar, parar, reiniciar, habilitar ou desabilitar")
            item("6.", "Listeners públicos")
            item("7.", "Porta HTTPS do Gateway")
            item("8.", "Migrar configuração legada", "Quando a instalação conserva o arquivo antigo de Mini Apps")
            item("9.", "Restaurar publicação anterior", "Backup de uma reutilização confirmada neste setup")
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice in {"!", "2"}: show_publication_diagnosis()
            elif choice == "w": publication_wizard()
            elif choice == "t": test_publication()
            elif choice == "1":
                if configure_publication(): test_publication()
            elif choice == "3":
                action = flow_choice("Executável cloudflared", ["Instalar", "Atualizar", "Restaurar versão anterior"], "Instalar")
                if action == "Restaurar versão anterior":
                    if prompt("Restaurar a versão anterior preservada do cloudflared? [s/N]: ").strip().casefold() not in {"s", "sim"}: continue
                    if not start_publication_service("cloudflare-tunnel"): continue
                    completed = publication_command("cloudflare-tunnel", "rollback-cloudflared", "--confirm", "--wait", quiet=False)
                    result(completed.returncode == 0, "Versão anterior restaurada." if completed.returncode == 0 else "A versão anterior não pôde ser restaurada.")
                elif action is not None: install_publication_binary(action == "Atualizar")
            elif choice == "4":
                if provision_publication(): test_publication()
            elif choice == "5":
                service = flow_choice("Serviço", ["Gateway HTTP", "Cloudflare Tunnel"], "Gateway HTTP")
                if service is None: continue
                action = flow_choice("Ação", ["Iniciar", "Parar", "Reiniciar", "Habilitar", "Desabilitar"], "Iniciar")
                if action is not None: lifecycle({"Iniciar": "start", "Parar": "stop", "Reiniciar": "restart", "Habilitar": "enable", "Desabilitar": "disable"}[action], ["gateway" if service == "Gateway HTTP" else "cloudflare-tunnel"])
            elif choice == "6": publication_listeners()
            elif choice == "7": configure_publication_gateway()
            elif choice == "8": publication_migration()
            elif choice == "9": restore_publication()
            else: result(False, "Opção inválida.")

    def miniapps_publications() -> None:
        if not require_miniapps_running(): return
        completed = miniapps_command("list")
        try: apps = json.loads(completed.stdout) if completed.returncode == 0 else None
        except json.JSONDecodeError: apps = None
        if not isinstance(apps, list): result(False, (completed.stdout or completed.stderr).strip()); return
        flow_heading("Publicações Mini App", "Os arquivos permanecem no workspace; o Gateway mantém somente o registro")
        if not apps:
            print("  i  Nenhuma Mini App publicada. Crie o código no workspace e publique-o pelo MCP Mini Apps.")
            return
        for app in apps:
            if not isinstance(app, dict): continue
            item(str(app.get("id", ""))[:8] + ".", str(app.get("alias") or "Sem nome"), f"{app.get('state')} · {app.get('health')}")
            print(f"     URL: {app.get('url')}")
            if app.get("last_error"): print("     Atenção: a publicação registrou um erro; consulte os eventos pelo MCP Mini Apps.")

    def miniapps_wizard() -> None:
        flow_heading("Assistente Mini Apps", "O acesso público é opcional; comece pelo objetivo de uso")
        choice = flow_choice("Objetivo de uso", ["Somente neste computador", "Também em domínio público"], "Somente neste computador")
        if choice is None: return
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
        if choice == "Também em domínio público":
            print("\n  Agora você verá os pré-requisitos do domínio. Se ainda não tiver domínio, token ou IDs Cloudflare, escolha voltar: nenhuma configuração pública é obrigatória neste momento.")
            publication_menu()

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
            item("5.", "Publicação externa compartilhada", "Gateway HTTP e Cloudflare Tunnel")
            print()
            item("X.", "Voltar")
            choice = prompt("Opção: ").strip().casefold()
            if choice in {"x", "\x1b"}: return
            if choice == "1": miniapps_wizard()
            elif choice == "2": show_miniapps_diagnosis()
            elif choice == "3": miniapps_local_menu()
            elif choice == "4": miniapps_publications()
            elif choice == "5": publication_menu()
            else: result(False, "Opção inválida.")

    def uninstall_instance() -> None:
        """Confirma a remoção sem abrir um submenu de opção única."""
        if not installed():
            result(False, "Instale o daemon pela opção 1 antes de desinstalá-lo.")
            return
        confirmation = prompt("ATENÇÃO: isto apagará todas as configurações, credenciais referenciadas, pareamentos, conversas e estado do daemon desta instância. Uma reinstalação exigirá configurar tudo novamente. Digite desinstalar para confirmar: ").strip()
        if confirmation == "desinstalar": lifecycle("remove-instance")
        else: result(False, "Desinstalação cancelada; a confirmação deve ser exatamente desinstalar.")

    while True:
        status = "CONFIGURAÇÃO-BASE PREPARADA" if installed() else "NÃO CONFIGURADO"
        screen("Daemon", f"Plataforma local e serviços da instância · {status}")
        item("!.", "Diagnóstico do Daemon", "Começa pelo diagnóstico principal do Shikigami")
        print()
        item("W.", "Wizard de configuração do Daemon", "Guia de configuração e validação")
        print()
        item("1.", "Preparar e instalar daemon" if not installed() else "Validar runtime do daemon", status)
        item("2.", "Processo/Serviço do Daemon", "Processo local e serviço do sistema operacional")
        item("3.", "Desinstalar daemon da instância", "Remove configurações e estado locais")
        print()
        item("T.", "Telegram", "Configuração, teste e pareamento")
        item("M.", "MiniApps", "Aplicações, HTTPS local e OAuth")
        item("P.", "Publicação externa", "Gateway HTTP e Cloudflare Tunnel compartilhados")
        item("N.", "Notificações ao agente", "Ingresso privado/local e canais operacionais")
        item("A.", "Agendador", "Consulta e ordenação de definições")
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
        elif choice == "3": uninstall_instance()
        elif choice in {"t", "m", "p", "n", "a"}:
            if not installed(): result(False, "Instale o daemon pela opção 1 antes de configurar serviços.")
            elif choice == "t": telegram_menu()
            elif choice == "m": miniapps_menu()
            elif choice == "n": notification_menu(root)
            elif choice == "a": scheduler_menu(root)
            else: publication_menu()
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
        print()
        item("P.", "Python e bibliotecas", "Runtime único e componentes compartilhados")
        item("C.", "Codex-CLI", "Modelo, projeto, sandbox e permissões")
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
        if choice == "!":
            if not run_dedicated_menu(root, "diagnostics"): diagnostics_menu(root)
            continue
        if choice == "p":
            if not run_dedicated_menu(root, "python"): python_menu(root)
            continue
        if choice == "c":
            if not run_dedicated_menu(root, "codex"): codex_menu(root)
            continue
        if choice == "d": daemon_menu(root); continue
        if choice == "m":
            if not run_dedicated_menu(root, "mcp"): launch_mcp_menu(root)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(skills): skill_menu(skills[int(choice) - 1], root, skills)
        else: result(False, "Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--action", choices=["menu", "list", "enable", "disable", "configure", "status"], default="menu"); parser.add_argument("--skill"); parser.add_argument("--json", action="store_true"); parser.add_argument("--dedicated-phase", action="store_true", help=argparse.SUPPRESS); parser.add_argument("--dedicated-menu", choices=["diagnostics", "python", "codex", "mcp"], help=argparse.SUPPRESS); parser.add_argument("--dedicated-checks", action="store_true", help=argparse.SUPPRESS); args = parser.parse_args()
    if args.action == "menu" and not (args.dedicated_phase or args.dedicated_menu or args.dedicated_checks): opening_logo()
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
        if system is None: return 2 if args.dedicated_phase else 0
    elif args.dedicated_phase:
        system = dedicated_migration_wizard(ROOT, system)
        if system is None: return 2
    if args.dedicated_phase: return 0
    if args.dedicated_checks:
        if not dedicated_child_profile(ROOT):
            print(json.dumps([{"state": "error", "label": "Identidade dedicada", "detail": "A execução não corresponde à conta Linux registrada."}], ensure_ascii=False))
            return 2
        print(json.dumps(shikigami_diagnostics(ROOT), ensure_ascii=False))
        return 0
    if args.dedicated_menu:
        if not dedicated_child_profile(ROOT): result(False, "O menu dedicado exige a conta Linux registrada."); return 2
        if args.dedicated_menu == "diagnostics": diagnostics_menu(ROOT)
        elif args.dedicated_menu == "python": python_menu(ROOT)
        elif args.dedicated_menu == "codex": codex_menu(ROOT)
        else: launch_mcp_menu(ROOT)
        return 0
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
