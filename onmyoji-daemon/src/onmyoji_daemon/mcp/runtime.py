"""Runtime independente usado exclusivamente pelo servidor MCP local."""
from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import threading
from collections.abc import Callable


MCP_REQUIREMENT = "mcp>=2,<3"
OAUTH_REQUIREMENT = "pykeepass>=4.1,<5"
EMAIL_REQUIREMENT = "IMAPClient>=3,<4"
VALIDATION_REQUIREMENT = "jsonschema>=4,<5"


class McpRuntimeError(RuntimeError):
    """Indica que o ambiente privado do MCP não pôde ser preparado."""


def _setup_error_detail(completed: subprocess.CompletedProcess[str]) -> str:
    """Retorna a última linha útil do instalador sem propagar segredos acidentais."""
    lines = (completed.stderr or completed.stdout).strip().splitlines()
    if not lines:
        return ""
    return re.sub(r"(?i)\b(token|password|secret|credential)\b(\s*[:=]\s*)\S+", r"\1\2[redacted]", lines[-1])


def _run_setup_command(command: list[str], *, cwd: Path, environment: dict[str, str], timeout: int, stage: str, progress: Callable[[str], None] | None) -> subprocess.CompletedProcess[str]:
    """Executa uma etapa do setup exibindo andamento e preservando erro sanitizado."""
    if progress:
        progress(stage)
    finished = threading.Event()
    def heartbeat() -> None:
        while not finished.wait(15):
            if progress:
                progress(f"{stage} Ainda em andamento; esta etapa pode levar alguns minutos.")
    reporter = threading.Thread(target=heartbeat, daemon=True)
    reporter.start()
    try:
        completed = subprocess.run(command, cwd=str(cwd), env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise McpRuntimeError(f"{stage} Não foi possível concluir a etapa.") from error
    finally:
        finished.set(); reporter.join(timeout=1)
    if completed.returncode:
        detail = _setup_error_detail(completed)
        suffix = f" Detalhe: {detail}" if detail else ""
        raise McpRuntimeError(f"{stage} O comando retornou código {completed.returncode}.{suffix}")
    if progress:
        progress(f"{stage} Concluída.")
    return completed


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


def ensure_mcp_runtime(root: Path, progress: Callable[[str], None] | None = None) -> str:
    """Cria ou repara o ambiente MCP, relatando etapas ao chamador quando solicitado."""
    if progress:
        progress("Verificando o runtime MCP privado.")
    if mcp_runtime_ready(root):
        if progress:
            progress("Runtime MCP privado já está pronto.")
        return "Runtime MCP já está pronto."
    environment = mcp_runtime_directory(root)
    executable = mcp_runtime_python(root)
    preparation, install_environment = _preparation(root)
    environment.parent.mkdir(parents=True, exist_ok=True)
    if not executable.is_file():
        _run_setup_command([sys.executable, "-I", "-B", "-m", "venv", "--without-pip", str(environment)], cwd=preparation, environment=install_environment, timeout=120, stage="Criando o runtime MCP privado.", progress=progress)
    _run_setup_command([sys.executable, "-I", "-B", _pip_entrypoint(), "--python", str(executable), "install", "--disable-pip-version-check", "--no-input", "--upgrade", MCP_REQUIREMENT, OAUTH_REQUIREMENT, EMAIL_REQUIREMENT, VALIDATION_REQUIREMENT], cwd=preparation, environment=install_environment, timeout=600, stage="Instalando dependências básicas do runtime MCP.", progress=progress)
    if progress:
        progress("Validando o runtime MCP privado.")
    if not mcp_runtime_ready(root):
        raise McpRuntimeError("O runtime MCP não pôde ser validado após a instalação.")
    if progress:
        progress("Runtime MCP instalado e validado.")
    return "Runtime MCP instalado e validado."
