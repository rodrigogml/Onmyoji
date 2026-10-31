"""Runtime independente usado exclusivamente pelo servidor MCP local."""
from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import subprocess
import sys


MCP_REQUIREMENT = "mcp>=2,<3"
OAUTH_REQUIREMENT = "pykeepass>=4.1,<5"
EMAIL_REQUIREMENT = "IMAPClient>=3,<4"
VALIDATION_REQUIREMENT = "jsonschema>=4,<5"


class McpRuntimeError(RuntimeError):
    """Indica que o ambiente privado do MCP não pôde ser preparado."""


def mcp_runtime_directory(root: Path) -> Path:
    """Retorna o ambiente privado, independente do runtime do daemon."""
    return root.resolve() / "configs" / "mcp" / "venv"


def mcp_runtime_python(root: Path) -> Path:
    """Retorna o interpretador que executa exclusivamente o servidor MCP."""
    environment = mcp_runtime_directory(root)
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _preparation(root: Path) -> tuple[Path, dict[str, str]]:
    """Mantém instalação e caches fora das configurações persistentes."""
    from .document_processing.paths import reject_links
    directory = root.resolve() / "tmp" / "mcp-install"
    reject_links(directory)
    reject_links(mcp_runtime_directory(root))
    directory.mkdir(parents=True, exist_ok=True)
    environment = {key: value for key, value in os.environ.items() if key.upper() not in {
        "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PIP_TARGET", "PIP_PREFIX", "PIP_USER"}}
    environment.update(TEMP=str(directory), TMP=str(directory), TMPDIR=str(directory),
                       PIP_CACHE_DIR=str(directory / "pip-cache"), PIP_CONFIG_FILE=os.devnull,
                       PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    return directory, environment


def _pip_entrypoint() -> str:
    specification = importlib.util.find_spec("pip")
    if specification is None or specification.origin is None:
        raise McpRuntimeError("O instalador pip deste Python está indisponível.")
    path = Path(specification.origin).with_name("__main__.py")
    if not path.is_file():
        raise McpRuntimeError("O instalador pip deste Python está indisponível.")
    return str(path)


def mcp_runtime_ready(root: Path) -> bool:
    """Confirma que o runtime local pode importar a versão suportada do SDK MCP."""
    executable = mcp_runtime_python(root)
    if not executable.is_file():
        return False
    probe = "from importlib.metadata import version; from pykeepass import PyKeePass; from imapclient import IMAPClient; from jsonschema import Draft202012Validator; value=tuple(map(int, version('mcp').split('.')[:2])); assert (2, 0) <= value < (3, 0)"
    try:
        preparation, environment = _preparation(root)
        completed = subprocess.run([str(executable), "-I", "-B", "-c", probe], cwd=str(preparation), env=environment, text=True, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def ensure_mcp_runtime(root: Path) -> str:
    """Cria ou repara o ambiente MCP sem instalar ou iniciar o daemon."""
    if mcp_runtime_ready(root):
        return "Runtime MCP já está pronto."
    environment = mcp_runtime_directory(root)
    executable = mcp_runtime_python(root)
    preparation, install_environment = _preparation(root)
    environment.parent.mkdir(parents=True, exist_ok=True)
    if not executable.is_file():
        try:
            created = subprocess.run([sys.executable, "-I", "-B", "-m", "venv", "--without-pip", str(environment)], cwd=str(preparation), env=install_environment, text=True, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise McpRuntimeError("Não foi possível criar o runtime privado do MCP.") from error
        if created.returncode != 0:
            raise McpRuntimeError("Não foi possível criar o runtime privado do MCP.")
    try:
        installed = subprocess.run([sys.executable, "-I", "-B", _pip_entrypoint(), "--python", str(executable), "install", "--disable-pip-version-check", "--no-input", "--upgrade", MCP_REQUIREMENT, OAUTH_REQUIREMENT, EMAIL_REQUIREMENT, VALIDATION_REQUIREMENT], cwd=str(preparation), env=install_environment, text=True, capture_output=True, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise McpRuntimeError("Não foi possível instalar os componentes do runtime MCP.") from error
    if installed.returncode != 0 or not mcp_runtime_ready(root):
        raise McpRuntimeError("O runtime MCP não pôde ser validado após a instalação.")
    return "Runtime MCP instalado e validado."
