from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path

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
