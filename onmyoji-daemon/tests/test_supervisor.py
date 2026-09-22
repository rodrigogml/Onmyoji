from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from onmyoji_daemon import management
from onmyoji_daemon.registry import miniapps_command
from onmyoji_daemon.rpc import call
from onmyoji_daemon.supervisor import Supervisor, endpoint
from onmyoji_daemon.management import default_service_description, default_service_name, install_instance, is_installed, set_enabled


def test_supervisor_exposes_registered_services(tmp_path):
    supervisor = Supervisor(tmp_path)
    assert [item["name"] for item in supervisor.handle("list-services", {})] == ["telegram", "mini-apps"]
    assert supervisor.handle("status", {"service": "telegram"})["enabled"] is False
    assert supervisor.handle("status", {"service": "mini-apps"})["enabled"] is False


def test_supervisor_service_output_redacts_sensitive_values(tmp_path):
    supervisor = Supervisor(tmp_path)
    assert supervisor._safe_output("falhou token=abc password: xyz secret = zzz") == "falhou token=[redacted] password=[redacted] secret=[redacted]"


def test_supervisor_persists_enablement_and_local_rpc(tmp_path):
    supervisor = Supervisor(tmp_path)
    thread = threading.Thread(target=supervisor.run_forever, daemon=True); thread.start()
    for _ in range(100):
        if (tmp_path / "configs" / "daemon" / "runtime" / "endpoint.json").exists(): break
        time.sleep(0.02)
    host, port, token = endpoint(tmp_path)
    assert call(host, port, token, "ping")["service"] == "onmyoji-daemon"
    assert call(host, port, token, "enable", {"service": "telegram"})["enabled"]
    assert Supervisor(tmp_path).services["telegram"].enabled
    call(host, port, token, "shutdown"); thread.join(timeout=5)
    assert not thread.is_alive()


def test_supervisor_starts_mini_apps_service(tmp_path):
    supervisor = Supervisor(tmp_path)
    state = supervisor.start("mini-apps")
    try:
        assert state["state"] == "running"
        assert supervisor.handle("mini-apps.status", {})["state"] == "running"
    finally:
        supervisor.stop("mini-apps")


def test_instance_installation_and_service_identity_are_local(tmp_path, monkeypatch):
    monkeypatch.setattr("onmyoji_daemon.management.ensure_runtime", lambda _root: (True, "ready"))
    root = tmp_path / "Onmyoji-Exemplo"
    root.mkdir()
    ok, _message = install_instance(root)
    assert ok and is_installed(root)
    assert default_service_name(root) == "Shikigami-Exemplo"
    assert default_service_description(root) == "Shikigami Exemplo Daemon"
    ok, _message = set_enabled(root, "telegram", True)
    assert ok
    assert Supervisor(root).services["telegram"].enabled
    ok, _message = set_enabled(root, "mini-apps", True)
    assert ok
    assert Supervisor(root).services["mini-apps"].enabled


def test_runtime_install_creates_private_environment(tmp_path, monkeypatch):
    executable = tmp_path / "configs" / "daemon" / "venv" / "Scripts" / "python.exe"
    monkeypatch.setattr(management, "daemon_python", lambda _root: executable)
    readiness = iter((False, True))
    monkeypatch.setattr(management, "_runtime_ready", lambda _executable: next(readiness))
    calls = []
    monkeypatch.setattr(management.subprocess, "run", lambda command, **_kwargs: calls.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""))

    ok, message = management.ensure_runtime(tmp_path)

    assert ok and "validado" in message
    assert calls[0][:3] == [management.sys.executable, "-m", "venv"]
    assert calls[1][:4] == [str(executable), "-m", "pip", "install"]


def test_miniapps_uses_private_daemon_python_when_available(tmp_path):
    data_dir = tmp_path / "configs" / "daemon" / "services" / "mini-apps"
    executable = data_dir.parent.parent / "venv" / ("Scripts/python.exe" if management.os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True); executable.touch()

    command = miniapps_command(data_dir)

    assert command[0] == str(executable)
