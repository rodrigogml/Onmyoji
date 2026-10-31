"""Instalação de bibliotecas limitada ao ambiente privado da instância."""
import subprocess

import pytest

from onmyoji_daemon.mcp import runtime


def test_mcp_install_targets_private_interpreter_and_uses_local_temporary_files(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "external-packages")
    monkeypatch.setenv("PYTHONHOME", "external-python")
    monkeypatch.setenv("PIP_TARGET", "external-installation")
    monkeypatch.setenv("PIP_PREFIX", "external-prefix")
    ready = iter([False, True])
    monkeypatch.setattr(runtime, "mcp_runtime_ready", lambda _root: next(ready))
    monkeypatch.setattr(runtime, "_pip_entrypoint", lambda: "pip-entry.py")
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0))

    assert "instalado" in runtime.ensure_mcp_runtime(tmp_path)

    assert len(calls) == 2
    assert str(runtime.mcp_runtime_directory(tmp_path)) == calls[0][0][-1]
    assert calls[1][0][calls[1][0].index("--python") + 1] == str(runtime.mcp_runtime_python(tmp_path))
    for command, options in calls:
        assert "-I" in command and "-B" in command
        assert options["cwd"] == str(tmp_path / "tmp" / "mcp-install")
        environment = options["env"]
        assert environment["TEMP"] == environment["TMP"] == environment["TMPDIR"] == options["cwd"]
        assert environment["PIP_CACHE_DIR"].startswith(options["cwd"])
        assert not {"PYTHONPATH", "PYTHONHOME", "PIP_TARGET", "PIP_PREFIX"} & environment.keys()


def test_mcp_runtime_probe_ignores_host_packages(tmp_path, monkeypatch):
    executable = runtime.mcp_runtime_python(tmp_path)
    executable.parent.mkdir(parents=True); executable.touch()
    monkeypatch.setenv("PYTHONPATH", "host-libraries")
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0))

    assert runtime.mcp_runtime_ready(tmp_path)

    command, options = calls[0]
    assert command[0] == str(executable) and "-I" in command
    assert "PYTHONPATH" not in options["env"]


def test_mcp_setup_progress_exposes_stages_and_sanitizes_installer_details(tmp_path, monkeypatch):
    ready = iter([False, True])
    messages = []
    monkeypatch.setattr(runtime, "mcp_runtime_ready", lambda _root: next(ready))
    monkeypatch.setattr(runtime, "_pip_entrypoint", lambda: "pip-entry.py")
    monkeypatch.setattr(runtime.subprocess, "run", lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, stderr="token=private-value"))

    with pytest.raises(runtime.McpRuntimeError) as error:
        runtime.ensure_mcp_runtime(tmp_path, progress=messages.append)

    assert any("Verificando" in message for message in messages)
    assert any("Criando" in message for message in messages)
    assert "private-value" not in str(error.value)
    assert "[redacted]" in str(error.value)
