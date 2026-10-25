"""Runtime independente usado exclusivamente pelo servidor MCP local."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


MCP_REQUIREMENT = "mcp>=2,<3"


class McpRuntimeError(RuntimeError):
    """Indica que o ambiente privado do MCP não pôde ser preparado."""


def mcp_runtime_directory(root: Path) -> Path:
    """Retorna o ambiente privado, independente do runtime do daemon."""
    return root.resolve() / "configs" / "mcp" / "venv"


def mcp_runtime_python(root: Path) -> Path:
    """Retorna o interpretador que executa exclusivamente o servidor MCP."""
    environment = mcp_runtime_directory(root)
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def mcp_runtime_ready(root: Path) -> bool:
    """Confirma que o runtime local pode importar a versão suportada do SDK MCP."""
    executable = mcp_runtime_python(root)
    probe = "from importlib.metadata import version; value=tuple(map(int, version('mcp').split('.')[:2])); assert (2, 0) <= value < (3, 0)"
    try:
        completed = subprocess.run([str(executable), "-c", probe], text=True, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def ensure_mcp_runtime(root: Path) -> str:
    """Cria ou repara o ambiente MCP sem instalar ou iniciar o daemon."""
    if mcp_runtime_ready(root):
        return "Runtime MCP já está pronto."
    environment = mcp_runtime_directory(root)
    executable = mcp_runtime_python(root)
    environment.parent.mkdir(parents=True, exist_ok=True)
    if not executable.is_file():
        try:
            created = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], text=True, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise McpRuntimeError("Não foi possível criar o runtime privado do MCP.") from error
        if created.returncode != 0:
            raise McpRuntimeError("Não foi possível criar o runtime privado do MCP.")
    target = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    try:
        installed = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "--target", str(target), MCP_REQUIREMENT], text=True, capture_output=True, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise McpRuntimeError("Não foi possível instalar os componentes do runtime MCP.") from error
    if installed.returncode != 0 or not mcp_runtime_ready(root):
        raise McpRuntimeError("O runtime MCP não pôde ser validado após a instalação.")
    return "Runtime MCP instalado e validado."
