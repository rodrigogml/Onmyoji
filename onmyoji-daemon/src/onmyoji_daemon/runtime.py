"""Runtime Python único, configurado por instância de Shikigami."""
from __future__ import annotations

import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tomllib
from time import monotonic
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


def _safe_output(value: str) -> str:
    """Remove segredos de uma linha devolvida por ferramenta externa."""
    value = re.sub(r"(?i)\b(token|password|secret|credential|api[_-]?key)\b(\s*[:=]\s*)\S+", r"\1\2[redacted]", value.strip())
    value = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [redacted]", value)
    return value[:500]


def _python_code(executable: Path, code: str) -> list[str]:
    """Evita a consulta WMI do Python 3.12 ao importar ferramentas no Windows."""
    prefix = "import platform; platform.system = lambda: 'Windows'; " if os.name == "nt" else ""
    return [str(executable), "-I", "-B", "-c", prefix + code]


def _pip_command(executable: Path, arguments: list[str]) -> list[str]:
    if os.name != "nt":
        return [str(executable), "-I", "-B", "-m", "pip", *arguments]
    code = "import platform, runpy; platform.system = lambda: 'Windows'; runpy.run_module('pip', run_name='__main__')"
    return [str(executable), "-I", "-B", "-c", code, *arguments]


def run_with_progress(command: list[str], *, environment: dict[str, str], timeout: int, stage: str, progress: Callable[[str], None] | None, cwd: Path | None = None) -> tuple[int, str]:
    """Executa ferramenta externa exibindo saída sanitizada e batimentos periódicos."""
    try:
        process = subprocess.Popen(command, env=environment, cwd=str(cwd) if cwd else None, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
    except OSError as error:
        raise RuntimeError(f"Não foi possível iniciar {stage.lower()}.") from error
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()
    def read_output() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)
    threading.Thread(target=read_output, daemon=True).start()
    deadline = monotonic() + timeout; detail = ""; closed = False
    while not closed:
        remaining = deadline - monotonic()
        if remaining <= 0:
            process.kill(); process.wait()
            raise RuntimeError(f"{stage} excedeu o tempo limite de {timeout // 60} minutos.")
        try:
            line = lines.get(timeout=min(15, remaining))
        except queue.Empty:
            if progress: progress(f"{stage} Ainda em andamento; aguarde a conclusão ou o diagnóstico.")
            continue
        if line is None:
            closed = True
            continue
        detail = _safe_output(line)
        if progress and detail:
            progress(f"{stage} {detail}")
    return process.wait(), detail


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
            code, detail = run_with_progress([str(source), "-I", "-B", "-m", "venv", str(venv_path(root))], environment=_environment(), timeout=120, stage="Criando o ambiente isolado /venv.", progress=progress)
        except RuntimeError as error:
            raise RuntimeError("Não foi possível criar o ambiente isolado /venv.") from error
        if code:
            raise RuntimeError(f"O Python-base não conseguiu criar o ambiente isolado /venv.{(' Detalhe: ' + detail) if detail else ''}")
    if not executable.is_file():
        raise RuntimeError("O Python global configurado não existe.")
    if requirements:
        if progress:
            progress("Instalando dependências no runtime único do Shikigami.")
        command = _pip_command(executable, ["install", "--disable-pip-version-check", "--no-input", "--upgrade", *requirements])
        try:
            code, detail = run_with_progress(command, environment=_environment(), timeout=600, stage="Instalando dependências no runtime único do Shikigami.", progress=progress)
        except RuntimeError as error:
            raise RuntimeError("Não foi possível instalar dependências no runtime do Shikigami.") from error
        if code:
            raise RuntimeError(f"O instalador de dependências do runtime do Shikigami falhou.{(' Detalhe: ' + detail) if detail else ''}")
    try:
        code, detail = run_with_progress(_python_code(executable, probe), environment=_environment(), timeout=15, stage="Validando as dependências do runtime único.", progress=progress)
    except RuntimeError as error:
        raise RuntimeError("Não foi possível validar o runtime do Shikigami.") from error
    if code:
        raise RuntimeError(f"O runtime do Shikigami não possui as dependências esperadas.{(' Detalhe: ' + detail) if detail else ''}")
    return "Runtime Python único do Shikigami pronto."
