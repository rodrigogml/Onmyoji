"""Compatibilidade do MCP com o runtime único da instância."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess

from .. import runtime as shared_runtime


MCP_REQUIREMENT = "mcp>=2,<3"
OAUTH_REQUIREMENT = "pykeepass>=4.1,<5"
EMAIL_REQUIREMENT = "IMAPClient>=3,<4"
VALIDATION_REQUIREMENT = "jsonschema>=4,<5"


class McpRuntimeError(RuntimeError):
    """Indica que o runtime único não pode atender ao MCP."""


def mcp_runtime_directory(root: Path) -> Path:
    """Retorna o local do runtime isolado único, quando selecionado."""
    return shared_runtime.venv_path(root)


def mcp_runtime_python(root: Path) -> Path:
    return shared_runtime.python(root)


def _probe() -> str:
    return "from importlib.metadata import version; from pykeepass import PyKeePass; from imapclient import IMAPClient; from jsonschema import Draft202012Validator; value=tuple(map(int, version('mcp').split('.')[:2])); assert (2, 0) <= value < (3, 0)"


def mcp_runtime_ready(root: Path) -> bool:
    try:
        executable = mcp_runtime_python(root)
        if not executable.is_file():
            return False
        completed = subprocess.run([str(executable), "-I", "-B", "-c", _probe()], text=True, capture_output=True, timeout=10, check=False)
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired, shared_runtime.RuntimeError):
        return False


def ensure_mcp_runtime(root: Path, progress: Callable[[str], None] | None = None) -> str:
    try:
        return shared_runtime.ensure(root, [MCP_REQUIREMENT, OAUTH_REQUIREMENT, EMAIL_REQUIREMENT, VALIDATION_REQUIREMENT], _probe(), progress)
    except shared_runtime.RuntimeError as error:
        raise McpRuntimeError(str(error)) from error
