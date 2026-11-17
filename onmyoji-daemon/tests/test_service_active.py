from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from onmyoji_daemon import management


@pytest.mark.parametrize("info", [{}, {"name": ""}, {"name": None}])
def test_service_is_active_without_a_service_does_not_query_manager(tmp_path, monkeypatch, info):
    monkeypatch.setattr(management, "service_info", lambda _root: info)
    monkeypatch.setattr(management.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Sem serviço registrado não há consulta."))

    assert management.service_is_active(tmp_path) is False


@pytest.mark.parametrize("scope, option", [("system", []), ("user", ["--user"]), (None, [])])
@pytest.mark.parametrize("state, expected", [("active", True), ("activating", True), ("reloading", True), ("inactive", False), ("failed", False), ("deactivating", False), ("", False)])
def test_linux_service_is_active_uses_registered_scope_without_sudo(tmp_path, monkeypatch, scope, option, state, expected):
    calls = []
    monkeypatch.setattr(management, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(management, "service_info", lambda _root: {"name": "shikigami", "scope": scope})
    def run(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 0, stdout=state + "\n", stderr="")
    monkeypatch.setattr(management.subprocess, "run", run)

    assert management.service_is_active(tmp_path) is expected
    assert calls == [(["systemctl", *option, "show", "--property=ActiveState", "--value", "shikigami"], {"text": True, "capture_output": True, "check": False, "timeout": 10})]


@pytest.mark.parametrize("label", ["STATE", "ESTADO"])
@pytest.mark.parametrize("state, expected", [("RUNNING", True), ("START_PENDING", True), ("CONTINUE_PENDING", True), ("STOPPED", False), ("STOP_PENDING", False), ("PAUSED", False), ("PAUSE_PENDING", False), ("UNKNOWN", False)])
def test_windows_service_is_active_preserves_running_intent(tmp_path, monkeypatch, label, state, expected):
    calls = []
    monkeypatch.setattr(management, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(management, "service_info", lambda _root: {"name": "shikigami"})
    def run(command, **_options):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=f"{label} : 4 {state}\n", stderr="")
    monkeypatch.setattr(management.subprocess, "run", run)

    assert management.service_is_active(tmp_path) is expected
    assert calls == [["sc.exe", "query", "shikigami"]]


@pytest.mark.parametrize("platform, output", [("posix", "active"), ("nt", "STATE : 4 RUNNING")])
def test_service_is_active_rejects_failed_queries(tmp_path, monkeypatch, platform, output):
    monkeypatch.setattr(management, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(management, "service_info", lambda _root: {"name": "shikigami"})
    monkeypatch.setattr(management.subprocess, "run", lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, stdout=output, stderr="consulta falhou"))

    assert management.service_is_active(tmp_path) is False


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("error", [FileNotFoundError("gestor ausente"), PermissionError("sem acesso"), subprocess.TimeoutExpired("query", 10), subprocess.CalledProcessError(1, "query")])
def test_service_is_active_treats_query_errors_as_inactive(tmp_path, monkeypatch, platform, error):
    monkeypatch.setattr(management, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(management, "service_info", lambda _root: {"name": "shikigami"})
    def run(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(management.subprocess, "run", run)

    assert management.service_is_active(tmp_path) is False
