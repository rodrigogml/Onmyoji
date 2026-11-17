from __future__ import annotations

import os
import json
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from onmyoji_daemon import management, runtime


def write_system(root: Path, body: str) -> None:
    path = root / "configs" / "onmyoji-system.toml"
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")


def test_isolated_runtime_is_always_at_instance_root(tmp_path: Path) -> None:
    write_system(tmp_path, '[python]\nmode = "isolated"\nbase_python = "C:/Python/python.exe"\nexecutable = ""\n')
    assert runtime.configuration(tmp_path)["mode"] == "isolated"
    assert runtime.venv_path(tmp_path) == tmp_path / "venv"
    assert runtime.python(tmp_path) == tmp_path / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def test_global_runtime_never_uses_instance_venv(tmp_path: Path) -> None:
    executable = Path(os.sys.executable)
    write_system(tmp_path, '[python]\nmode = "global"\nbase_python = ""\nexecutable = ' + json.dumps(str(executable)) + '\n')
    assert runtime.configuration(tmp_path)["mode"] == "global"
    assert runtime.python(tmp_path) == executable
    assert not runtime.python(tmp_path).is_relative_to(runtime.venv_path(tmp_path))


def test_legacy_existing_runtime_maps_to_root_isolated_environment(tmp_path: Path) -> None:
    path = tmp_path / "configs" / "daemon" / "daemon.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[runtime]\nmode = "existing"\nexecutable = "C:/legacy/Scripts/python.exe"\n', encoding="utf-8")
    value = runtime.configuration(tmp_path)
    assert value == {"mode": "isolated", "base_python": "C:/legacy/Scripts/python.exe", "executable": ""}
    assert runtime.python(tmp_path).is_relative_to(tmp_path / "venv")


def test_external_output_is_sanitized() -> None:
    assert runtime._safe_output("token=abc123 password: hidden Bearer value") == "token=[redacted] password: [redacted] Bearer [redacted]"


def test_windows_python_commands_bypass_wmi_probe(monkeypatch) -> None:
    monkeypatch.setattr(runtime.os, "name", "nt")
    command = runtime._pip_command(Path("python.exe"), ["install", "example"])
    assert "platform.system" in command[4] and command[-2:] == ["install", "example"]


def test_daemon_runtime_probe_covers_scheduler_dependencies(tmp_path: Path, monkeypatch) -> None:
    """Agendamentos não devem ficar invisivelmente quebrados em runtimes antigos."""
    executable = tmp_path / "python"
    executable.touch()
    calls = []

    def incomplete_python(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "No module named 'croniter'")

    monkeypatch.setattr(management.subprocess, "run", incomplete_python)
    assert management._runtime_ready(executable) is False
    assert calls[0][1:4] == ["-I", "-B", "-c"]
    assert "parsed('croniter')" in calls[0][-1]
    assert "parsed('tzdata')" in calls[0][-1]
    assert any(value.startswith("croniter>=") for value in management.runtime_requirements())
    assert any(value.startswith("tzdata>=") for value in management.runtime_requirements())


@pytest.mark.parametrize("mode", ["global", "isolated"])
@pytest.mark.parametrize("inherited_override", [None, "0", "1"])
@pytest.mark.parametrize("show_progress", [False, True])
def test_ensure_limits_managed_python_override_to_global_installation(tmp_path: Path, monkeypatch, mode: str, inherited_override: str | None, show_progress: bool) -> None:
    """A escolha global autoriza somente o pip chamado pelo runtime, sem mudar o ambiente pai."""
    if inherited_override is None:
        monkeypatch.delenv("PIP_BREAK_SYSTEM_PACKAGES", raising=False)
    else:
        monkeypatch.setenv("PIP_BREAK_SYSTEM_PACKAGES", inherited_override)
    write_system(tmp_path, '[python]\nmode = ' + json.dumps(mode) + '\nexecutable = ' + json.dumps(str(tmp_path / "python-global")) + '\n')
    executable = runtime.python(tmp_path)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch()
    calls = []
    messages = []

    def run(command, **kwargs):
        calls.append((command, kwargs["environment"]))
        return 0, ""

    monkeypatch.setattr(runtime, "run_with_progress", run)

    assert runtime.ensure(tmp_path, ["example>=1,<2"], "import example", progress=messages.append if show_progress else None) == "Runtime Python único do Shikigami pronto."
    assert len(calls) == 2
    assert calls[0][0][0] == str(executable)
    assert "install" in calls[0][0]
    assert calls[0][0][-1] == "example>=1,<2"
    assert calls[0][1].get("PIP_BREAK_SYSTEM_PACKAGES") == ("1" if mode == "global" else None)
    assert calls[1][0][0] == str(executable)
    assert "import example" in calls[1][0][-1]
    assert "PIP_BREAK_SYSTEM_PACKAGES" not in calls[1][1]
    assert os.environ.get("PIP_BREAK_SYSTEM_PACKAGES") == inherited_override
    assert any("Modo global:" in message for message in messages) == (show_progress and mode == "global")
    if mode == "global":
        assert not runtime.venv_path(tmp_path).exists()


@pytest.mark.parametrize("mode", ["global", "isolated"])
def test_ensure_without_requirements_only_validates_runtime(tmp_path: Path, monkeypatch, mode: str) -> None:
    """Uma validação sem instalação não recebe autorização para alterar o Python global."""
    monkeypatch.setenv("PIP_BREAK_SYSTEM_PACKAGES", "1")
    write_system(tmp_path, '[python]\nmode = ' + json.dumps(mode) + '\nexecutable = ' + json.dumps(str(tmp_path / "python-global")) + '\n')
    executable = runtime.python(tmp_path)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch()
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["environment"]))
        return 0, ""

    monkeypatch.setattr(runtime, "run_with_progress", run)

    assert runtime.ensure(tmp_path, [], "import example") == "Runtime Python único do Shikigami pronto."
    assert len(calls) == 1
    assert "import example" in calls[0][0][-1]
    assert "PIP_BREAK_SYSTEM_PACKAGES" not in calls[0][1]


def test_ensure_does_not_validate_after_pip_failure(tmp_path: Path, monkeypatch) -> None:
    """Uma falha de instalação preserva o diagnóstico e interrompe a preparação."""
    executable = tmp_path / "python-global"
    executable.touch()
    write_system(tmp_path, '[python]\nmode = "global"\nexecutable = ' + json.dumps(str(executable)) + '\n')
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return 1, "Permission denied"

    monkeypatch.setattr(runtime, "run_with_progress", run)

    with pytest.raises(runtime.RuntimeError, match="Permission denied"):
        runtime.ensure(tmp_path, ["example"], "import example")
    assert len(calls) == 1
    assert "install" in calls[0]


def test_ensure_reports_failed_validation_after_installation(tmp_path: Path, monkeypatch) -> None:
    """O pip terminar com sucesso não dispensa a validação das dependências instaladas."""
    executable = tmp_path / "python-global"
    executable.touch()
    write_system(tmp_path, '[python]\nmode = "global"\nexecutable = ' + json.dumps(str(executable)) + '\n')
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return (0, "") if len(calls) == 1 else (1, "No module named 'example'")

    monkeypatch.setattr(runtime, "run_with_progress", run)

    with pytest.raises(runtime.RuntimeError, match="No module named 'example'"):
        runtime.ensure(tmp_path, ["example"], "import example")
    assert len(calls) == 2
    assert "import example" in calls[1][-1]


@pytest.mark.parametrize("mode, expected_error", [("global", "Python global configurado não existe"), ("isolated", "Python-base configurado para criar /venv não existe")])
def test_ensure_rejects_missing_python_before_starting_processes(tmp_path: Path, monkeypatch, mode: str, expected_error: str) -> None:
    write_system(tmp_path, '[python]\nmode = ' + json.dumps(mode) + '\nbase_python = ' + json.dumps(str(tmp_path / "missing-base")) + '\nexecutable = ' + json.dumps(str(tmp_path / "missing-global")) + '\n')

    def unexpected_run(*args, **kwargs):
        pytest.fail("Um Python inexistente não pode iniciar a preparação do runtime.")

    monkeypatch.setattr(runtime, "run_with_progress", unexpected_run)

    with pytest.raises(runtime.RuntimeError, match=expected_error):
        runtime.ensure(tmp_path, ["example"], "import example")


def test_ensure_creates_root_isolated_environment_before_installing(tmp_path: Path, monkeypatch) -> None:
    """A criação e a instalação isoladas descartam uma autorização global herdada."""
    monkeypatch.setenv("PIP_BREAK_SYSTEM_PACKAGES", "1")
    source = tmp_path / "python-base"
    source.touch()
    write_system(tmp_path, '[python]\nmode = "isolated"\nbase_python = ' + json.dumps(str(source)) + '\n')
    executable = runtime.python(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["environment"]))
        if len(calls) == 1:
            executable.parent.mkdir(parents=True)
            executable.touch()
        return 0, ""

    monkeypatch.setattr(runtime, "run_with_progress", run)

    assert runtime.ensure(tmp_path, ["example"], "import example") == "Runtime Python único do Shikigami pronto."
    assert len(calls) == 3
    assert calls[0][0] == [str(source), "-I", "-B", "-m", "venv", str(tmp_path / "venv")]
    assert calls[1][0][0] == str(executable)
    assert "install" in calls[1][0]
    assert calls[2][0][0] == str(executable)
    assert all("PIP_BREAK_SYSTEM_PACKAGES" not in environment for _, environment in calls)


def test_ensure_stops_when_isolated_environment_creation_fails(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "python-base"
    source.touch()
    write_system(tmp_path, '[python]\nmode = "isolated"\nbase_python = ' + json.dumps(str(source)) + '\n')
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return 1, "ensurepip is not available"

    monkeypatch.setattr(runtime, "run_with_progress", run)

    with pytest.raises(runtime.RuntimeError, match="ensurepip is not available"):
        runtime.ensure(tmp_path, ["example"], "import example")
    assert len(calls) == 1
    assert calls[0][-2:] == ["venv", str(tmp_path / "venv")]


def global_runtime(tmp_path: Path) -> Path:
    executable = tmp_path / "python-global"
    executable.touch()
    write_system(tmp_path, '[python]\nmode = "global"\nexecutable = ' + json.dumps(str(executable)) + '\n')
    return executable


def posix_account(monkeypatch, uid: int = 1000) -> None:
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="posix", environ=os.environ, geteuid=lambda: uid))


@pytest.mark.parametrize("output, expected", [("0\n", False), ("1\n", True)])
def test_installation_permissions_are_checked_in_selected_python(tmp_path: Path, monkeypatch, output: str, expected: bool) -> None:
    """A consulta usa os destinos e as permissões do interpretador global escolhido."""
    executable = global_runtime(tmp_path)
    posix_account(monkeypatch)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.installation_requires_sudo(tmp_path) is expected
    command, options = calls[0]
    assert command[:4] == [str(executable), "-I", "-B", "-c"]
    assert "('purelib', 'platlib', 'scripts')" in command[-1]
    assert "os.W_OK | os.X_OK" in command[-1]
    assert "target = target.parent" in command[-1]
    assert options["env"]["PYTHONNOUSERSITE"] == "1"
    assert options["capture_output"] is True


@pytest.mark.parametrize("mode, platform_name, uid", [("isolated", "posix", 1000), ("global", "nt", 1000), ("global", "posix", 0)])
def test_installation_permissions_skip_accounts_that_do_not_require_sudo(tmp_path: Path, monkeypatch, mode: str, platform_name: str, uid: int) -> None:
    write_system(tmp_path, '[python]\nmode = ' + json.dumps(mode) + '\n')
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name=platform_name, environ=os.environ, geteuid=lambda: uid))

    def unexpected_run(*args, **kwargs):
        pytest.fail("Este runtime dispensa a consulta de permissões globais.")

    monkeypatch.setattr(runtime.subprocess, "run", unexpected_run)
    assert runtime.installation_requires_sudo(tmp_path) is False


@pytest.mark.parametrize("returncode, stdout, stderr", [(1, "", "token=hidden permission denied"), (0, "invalid", "")])
def test_installation_permissions_reject_failed_or_invalid_probes(tmp_path: Path, monkeypatch, returncode: int, stdout: str, stderr: str) -> None:
    global_runtime(tmp_path)
    posix_account(monkeypatch)
    monkeypatch.setattr(runtime.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, returncode, stdout, stderr))
    with pytest.raises(runtime.RuntimeError, match="verificar as permissões") as raised:
        runtime.installation_requires_sudo(tmp_path)
    assert "hidden" not in str(raised.value)
    if stderr:
        assert "[redacted]" in str(raised.value)


@pytest.mark.parametrize("error", [OSError("unavailable"), subprocess.TimeoutExpired("python", 15)])
def test_installation_permissions_report_process_failures(tmp_path: Path, monkeypatch, error: Exception) -> None:
    global_runtime(tmp_path)
    posix_account(monkeypatch)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(runtime.subprocess, "run", fail)
    with pytest.raises(runtime.RuntimeError, match="verificar as permissões"):
        runtime.installation_requires_sudo(tmp_path)


@pytest.mark.parametrize("show_progress", [False, True])
def test_ensure_elevates_only_global_pip_after_terminal_authentication(tmp_path: Path, monkeypatch, show_progress: bool) -> None:
    """A senha é lida pelo sudo e a validação continua sob a conta operacional."""
    executable = global_runtime(tmp_path)
    posix_account(monkeypatch)
    monkeypatch.setenv("PIP_TARGET", "/unexpected")
    monkeypatch.setenv("PIP_USER", "1")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "/usr/bin/sudo" if name == "sudo" else None)
    authentications = []
    calls = []
    messages = []

    def authenticate(command, **kwargs):
        authentications.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    def run(command, **kwargs):
        calls.append((command, kwargs["environment"]))
        return 0, ""

    monkeypatch.setattr(runtime.subprocess, "run", authenticate)
    monkeypatch.setattr(runtime, "run_with_progress", run)
    assert runtime.ensure(tmp_path, ["example"], "import example", progress=messages.append if show_progress else None, use_sudo=True) == "Runtime Python único do Shikigami pronto."
    assert authentications == [(["/usr/bin/sudo", "-v"], {"check": False})]
    installation, environment = calls[0]
    assert installation[:4] == ["/usr/bin/sudo", "-n", "--", "env"]
    assert "PIP_BREAK_SYSTEM_PACKAGES=1" in installation
    assert "PYTHONNOUSERSITE=1" in installation
    assert "PYTHONDONTWRITEBYTECODE=1" in installation
    for name in ("PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PYTHONUSERBASE"):
        assert installation[installation.index(name) - 1] == "-u"
        assert name not in environment
    assert installation[installation.index(str(executable)):] == [str(executable), "-I", "-B", "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "example"]
    assert calls[1][0] == [str(executable), "-I", "-B", "-c", "import example"]
    assert "PIP_BREAK_SYSTEM_PACKAGES" not in calls[1][1]
    assert any("senha" in message for message in messages) is show_progress
    assert os.environ["PIP_TARGET"] == "/unexpected"
    assert os.environ["PIP_USER"] == "1"


@pytest.mark.parametrize("mode, platform_name, uid, requirements", [("isolated", "posix", 1000, ["example"]), ("global", "nt", 1000, ["example"]), ("global", "posix", 0, ["example"]), ("global", "posix", 1000, [])])
def test_ensure_ignores_sudo_outside_global_nonroot_installation(tmp_path: Path, monkeypatch, mode: str, platform_name: str, uid: int, requirements: list[str]) -> None:
    write_system(tmp_path, '[python]\nmode = ' + json.dumps(mode) + '\nexecutable = ' + json.dumps(str(tmp_path / "python-global")) + '\n')
    executable = runtime.python(tmp_path)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch()
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name=platform_name, environ=os.environ, geteuid=lambda: uid))
    # O caminho isolado já foi preparado com a convenção da plataforma hospedeira.
    monkeypatch.setattr(runtime, "python", lambda root: executable)
    calls = []

    def unexpected_sudo(*args, **kwargs):
        pytest.fail("Esta operação não pode solicitar sudo.")

    def run(command, **kwargs):
        calls.append(command)
        return 0, ""

    monkeypatch.setattr(runtime.shutil, "which", unexpected_sudo)
    monkeypatch.setattr(runtime, "run_with_progress", run)
    runtime.ensure(tmp_path, requirements, "import example", use_sudo=True)
    assert all(command[0] == str(executable) for command in calls)
    assert len(calls) == (2 if requirements else 1)


@pytest.mark.parametrize("sudo, auth_result, expected", [(None, 0, "sudo não está disponível"), ("/usr/bin/sudo", 1, "sudo não autorizou"), ("/usr/bin/sudo", OSError("unavailable"), "solicitar autorização")])
def test_ensure_stops_before_pip_when_sudo_is_unavailable_or_denied(tmp_path: Path, monkeypatch, sudo: str | None, auth_result: int | Exception, expected: str) -> None:
    global_runtime(tmp_path)
    posix_account(monkeypatch)
    monkeypatch.setattr(runtime.shutil, "which", lambda name: sudo)

    def authenticate(command, **kwargs):
        if isinstance(auth_result, Exception):
            raise auth_result
        return subprocess.CompletedProcess(command, auth_result)

    def unexpected_pip(*args, **kwargs):
        pytest.fail("Uma instalação sem autorização administrativa não pode iniciar o pip.")

    monkeypatch.setattr(runtime.subprocess, "run", authenticate)
    monkeypatch.setattr(runtime, "run_with_progress", unexpected_pip)
    with pytest.raises(runtime.RuntimeError, match=expected):
        runtime.ensure(tmp_path, ["example"], "import example", use_sudo=True)


@pytest.mark.parametrize("detail", ["[Errno 13] Permission denied", "Permissão negada", "Access is denied", "[WinError 5] Acesso negado"])
def test_global_installation_denied_explains_available_repairs(tmp_path: Path, monkeypatch, detail: str) -> None:
    global_runtime(tmp_path)
    monkeypatch.setattr(runtime, "run_with_progress", lambda *args, **kwargs: (1, detail))
    with pytest.raises(runtime.RuntimeError) as raised:
        runtime.ensure(tmp_path, ["example"], "import example")
    message = str(raised.value)
    assert detail in message
    assert "privilégios administrativos" in message
    assert "isolated em Python e bibliotecas" in message


@pytest.mark.parametrize("show_progress", [False, True])
def test_progress_preserves_sanitized_failure_before_blank_output(monkeypatch, show_progress: bool) -> None:
    """Sugestões e linhas vazias finais não escondem a causa da falha do pip."""
    process = SimpleNamespace(stdout=io.StringIO("Installing packages\nERROR: Permission denied token=hidden\nConsider using --user\n\n  \n"), wait=lambda: 1)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    messages = []
    code, detail = runtime.run_with_progress(["python", "-m", "pip"], environment={}, timeout=15, stage="Instalando.", progress=messages.append if show_progress else None)
    assert code == 1
    assert "Permission denied" in detail
    assert "token=[redacted]" in detail
    assert "hidden" not in detail
    assert detail.endswith("Consider using --user")
    assert len(messages) == (3 if show_progress else 0)
    assert all("hidden" not in message for message in messages)


def test_progress_limits_retained_output(monkeypatch) -> None:
    process = SimpleNamespace(stdout=io.StringIO("\n".join(str(index) for index in range(12))), wait=lambda: 0)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: process)
    code, detail = runtime.run_with_progress(["python"], environment={}, timeout=15, stage="Validando.", progress=None)
    assert code == 0
    assert detail.splitlines() == [str(index) for index in range(4, 12)]
