"""Runtime Python único, configurado por instância de Shikigami."""
from __future__ import annotations

import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import tomllib
from time import monotonic
from collections import deque
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


_DISCARDED_ENVIRONMENT = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PIP_BREAK_SYSTEM_PACKAGES")


def _environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key.upper() not in _DISCARDED_ENVIRONMENT}
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


def installation_requires_sudo(root: Path) -> bool:
    """Verifica a escrita no destino global como a conta que executa o setup.

    Ambientes isolados, Windows e processos já administrativos dispensam sudo.
    A consulta não cria diretórios nem instala componentes.

    Raises:
        RuntimeError: Se o Python selecionado não permitir consultar os destinos
            de instalação e suas permissões.
    """
    if is_isolated(root) or os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0:
        return False
    executable = python(root)
    code = """import os, sysconfig
from pathlib import Path
def writable(value):
    target = Path(value)
    while not target.exists():
        if target.parent == target:
            return False
        target = target.parent
    return target.is_dir() and os.access(target, os.W_OK | os.X_OK)
print('0' if all(writable(sysconfig.get_path(name)) for name in ('purelib', 'platlib', 'scripts')) else '1')
"""
    try:
        completed = subprocess.run(_python_code(executable, code), env=_environment(), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Não foi possível verificar as permissões de instalação do Python global.") from error
    value = (completed.stdout or "").strip()
    if completed.returncode or value not in {"0", "1"}:
        detail = _safe_output(completed.stderr or completed.stdout or "")
        raise RuntimeError(f"Não foi possível verificar as permissões de instalação do Python global.{(' Detalhe: ' + detail) if detail else ''}")
    return value == "1"


def _sudo_pip_command(command: list[str]) -> list[str]:
    """Monta a instalação global sem exigir uma nova autenticação previamente."""
    sudo = shutil.which("sudo")
    if not sudo:
        raise RuntimeError("sudo não está disponível. Execute a instalação pelo setup com privilégios administrativos ou selecione isolated em Python e bibliotecas.")
    unset = [argument for name in _DISCARDED_ENVIRONMENT for argument in ("-u", name)]
    return [sudo, "-n", "--", "env", *unset, "PIP_BREAK_SYSTEM_PACKAGES=1", "PYTHONNOUSERSITE=1", "PYTHONDONTWRITEBYTECODE=1", *command]


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
    deadline = monotonic() + timeout; details: deque[str] = deque(maxlen=8); closed = False
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
        if detail:
            details.append(detail)
        if progress and detail:
            progress(f"{stage} {detail}")
    return process.wait(), "\n".join(details)


def _install(executable: Path, arguments: list[str], *, environment: dict[str, str], use_sudo: bool, stage: str, progress: Callable[[str], None] | None) -> tuple[int, str]:
    """Executa pip e autentica somente quando o sudo exigir uma senha.

    O chamador autoriza ``use_sudo`` apenas para uma instalação global POSIX
    não administrativa. NOPASSWD e credenciais em cache não abrem autenticação;
    falhas do pip ou recusas de política nunca repetem a instalação. Uma falha
    exclusiva de autenticação permite uma única repetição depois de ``sudo -v``.

    Raises:
        RuntimeError: Se não houver sudo, a autenticação for recusada ou algum
            processo não puder iniciar ou concluir dentro do prazo.
    """
    command = _pip_command(executable, arguments)
    execution_environment = environment
    if use_sudo:
        command = _sudo_pip_command(command)
        execution_environment = {**environment, "LC_ALL": "C"}
    code, detail = run_with_progress(command, environment=execution_environment, timeout=600, stage=stage, progress=progress)
    if not use_sudo or code != 1 or detail.strip() != "sudo: a password is required":
        return code, detail
    if progress:
        progress("O sudo exige autenticação para esta instalação. A senha será solicitada pelo próprio sudo no terminal.")
    try:
        authenticated = subprocess.run([command[0], "-v"], env=execution_environment, check=False)
    except OSError as error:
        raise RuntimeError("Não foi possível solicitar autorização administrativa ao sudo.") from error
    if authenticated.returncode:
        raise RuntimeError("sudo não autorizou a instalação global. Execute a instalação pelo setup com privilégios administrativos ou selecione isolated em Python e bibliotecas.")
    return run_with_progress(command, environment=execution_environment, timeout=600, stage=stage, progress=progress)


def ensure(root: Path, requirements: list[str], probe: str, progress: Callable[[str], None] | None = None, *, use_sudo: bool = False) -> str:
    """Prepara o runtime selecionado e instala requisitos no mesmo interpretador.

    A seleção global permite ao pip instalar em Python protegido por PEP 668;
    essa permissão existe somente no ambiente do processo de instalação.
    O modo isolado nunca recebe essa permissão, mesmo quando herdada do terminal.
    ``use_sudo`` autoriza elevar somente o pip global em sistemas POSIX; NOPASSWD
    e credenciais em cache dispensam autenticação, e uma senha só é solicitada
    pelo sudo quando exigida pela política. A validação mantém a conta original.
    Pacotes externos sem RECORD só recebem uma cópia quando o destino global
    é separado e tem prioridade de importação; seus arquivos são preservados.

    Raises:
        RuntimeError: Se a configuração for inválida, o interpretador não existir
            ou a criação, instalação ou validação das dependências falhar.
    """
    executable = python(root)
    isolated = is_isolated(root)
    if isolated and not executable.is_file():
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
        environment = _environment()
        if not isolated:
            environment["PIP_BREAK_SYSTEM_PACKAGES"] = "1"
            if progress:
                progress("Modo global: a instalação pode atualizar bibliotecas do sistema, inclusive em Python protegido por PEP 668.")
        if progress:
            progress("Instalando dependências no runtime único do Shikigami.")
        elevated = use_sudo and not isolated and os.name != "nt" and getattr(os, "geteuid", lambda: 0)() != 0
        def install(arguments: list[str], stage: str) -> tuple[int, str]:
            return _install(executable, arguments, environment=environment, use_sudo=elevated, stage=stage, progress=progress)
        arguments = ["install", "--disable-pip-version-check", "--no-input", "--upgrade", *requirements]
        stage = "Instalando dependências no runtime único do Shikigami."
        try:
            code, detail = install(arguments, stage)
            if code and not isolated and os.name != "nt":
                from .runtime_recovery import recover_distribution
                recovered: set[str] = set()
                while code and recover_distribution(root, executable, requirements, detail, progress, install, recovered):
                    code, detail = install(arguments, stage)
        except RuntimeError as error:
            raise RuntimeError(f"Não foi possível instalar dependências no runtime do Shikigami. {error}") from error
        if code:
            message = f"O instalador de dependências do runtime do Shikigami falhou.{(' Detalhe: ' + detail) if detail else ''}"
            if not isolated and re.search(r"permission denied|permissão negada|\[errno 13\]|access is denied|acesso negado|\[winerror 5\]", detail, re.IGNORECASE):
                message += " Execute a instalação pelo setup com privilégios administrativos ou selecione isolated em Python e bibliotecas."
            raise RuntimeError(message)
    try:
        code, detail = run_with_progress(_python_code(executable, probe), environment=_environment(), timeout=15, stage="Validando as dependências do runtime único.", progress=progress)
    except RuntimeError as error:
        raise RuntimeError("Não foi possível validar o runtime do Shikigami.") from error
    if code:
        raise RuntimeError(f"O runtime do Shikigami não possui as dependências esperadas.{(' Detalhe: ' + detail) if detail else ''}")
    return "Runtime Python único do Shikigami pronto."
