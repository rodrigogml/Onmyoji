"""Runtime Python único, configurado por instância de Shikigami."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib
from collections.abc import Callable


class RuntimeError(RuntimeError):
    """Indica que o runtime Python central não está configurado ou pronto."""


def system_path(root: Path) -> Path:
    return root.resolve() / "configs" / "onmyoji-system.toml"


def venv_path(root: Path) -> Path:
    """Retorna o único ambiente isolado permitido para a instância."""
    return root.resolve() / "venv"


def _legacy(root: Path) -> dict[str, str]:
    """Lê somente para migração a escolha antiga do manifesto do daemon."""
    try:
        data = tomllib.loads((root.resolve() / "configs" / "daemon" / "daemon.toml").read_text(encoding="utf-8"))
        value = data.get("runtime", {})
    except (OSError, tomllib.TOMLDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    mode = str(value.get("mode") or "dedicated")
    if mode == "global":
        return {"mode": "global", "base_python": "", "executable": str(value.get("executable") or "")}
    return {"mode": "isolated", "base_python": str(value.get("base_python") or value.get("executable") or sys.executable), "executable": ""}


def configuration(root: Path) -> dict[str, str]:
    """Lê a seleção global; arquivos antigos recebem um mapeamento transitório."""
    try:
        data = tomllib.loads(system_path(root).read_text(encoding="utf-8"))
        value = data.get("python", {})
    except (OSError, tomllib.TOMLDecodeError):
        value = {}
    if not isinstance(value, dict) or not value:
        return _legacy(root)
    mode = str(value.get("mode") or "isolated")
    if mode not in {"isolated", "global"}:
        raise RuntimeError("Modo de runtime Python inválido. Use isolated ou global.")
    return {"mode": mode, "base_python": str(value.get("base_python") or ""), "executable": str(value.get("executable") or "")}


def python(root: Path) -> Path:
    value = configuration(root)
    if value["mode"] == "isolated":
        return venv_path(root) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = Path(value["executable"]).expanduser()
    if not value["executable"]:
        raise RuntimeError("Selecione o Python global do Shikigami antes de usar ferramentas Python.")
    return executable


def base_python(root: Path) -> Path:
    value = configuration(root)
    candidate = value["base_python"] if value["mode"] == "isolated" else value["executable"]
    if not candidate:
        raise RuntimeError("Selecione um Python 3.11 ou superior para o Shikigami.")
    return Path(candidate).expanduser()


def is_isolated(root: Path) -> bool:
    return configuration(root)["mode"] == "isolated"


def _environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PIP_TARGET", "PIP_PREFIX", "PIP_USER"}}
    environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    return environment


def ensure(root: Path, requirements: list[str], probe: str, progress: Callable[[str], None] | None = None) -> str:
    """Prepara o runtime selecionado e instala requisitos no mesmo interpretador."""
    executable = python(root)
    if is_isolated(root) and not executable.is_file():
        source = base_python(root)
        if not source.is_file():
            raise RuntimeError("O Python-base configurado para criar /venv não existe.")
        if progress:
            progress("Criando o ambiente isolado único do Shikigami.")
        try:
            completed = subprocess.run([str(source), "-I", "-B", "-m", "venv", str(venv_path(root))], env=_environment(), text=True, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("Não foi possível criar o ambiente isolado /venv.") from error
        if completed.returncode:
            raise RuntimeError("O Python-base não conseguiu criar o ambiente isolado /venv.")
    if not executable.is_file():
        raise RuntimeError("O Python global configurado não existe.")
    if requirements:
        if progress:
            progress("Instalando dependências no runtime único do Shikigami.")
        command = [str(executable), "-I", "-B", "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", *requirements]
        try:
            completed = subprocess.run(command, env=_environment(), text=True, capture_output=True, timeout=600, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("Não foi possível instalar dependências no runtime do Shikigami.") from error
        if completed.returncode:
            raise RuntimeError("O instalador de dependências do runtime do Shikigami falhou.")
    try:
        checked = subprocess.run([str(executable), "-I", "-B", "-c", probe], env=_environment(), text=True, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Não foi possível validar o runtime do Shikigami.") from error
    if checked.returncode:
        raise RuntimeError("O runtime do Shikigami não possui as dependências esperadas.")
    return "Runtime Python único do Shikigami pronto."
