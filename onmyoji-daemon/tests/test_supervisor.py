from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from onmyoji_daemon import management
from onmyoji_daemon.registry import btg_monitor_command, miniapps_command, telegram_command
from onmyoji_daemon.rpc import call
from onmyoji_daemon.supervisor import Supervisor, endpoint
from onmyoji_daemon.management import default_service_description, default_service_name, install_instance, is_installed, set_enabled


def test_supervisor_exposes_registered_services(tmp_path):
    supervisor = Supervisor(tmp_path)
    assert [item["name"] for item in supervisor.handle("list-services", {})] == ["telegram", "mini-apps", "btg-payments-monitor"]
    assert supervisor.handle("status", {"service": "telegram"})["enabled"] is False
    assert supervisor.handle("status", {"service": "mini-apps"})["enabled"] is False
    assert supervisor.handle("status", {"service": "btg-payments-monitor"})["enabled"] is False


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
    monkeypatch.setattr("onmyoji_daemon.management.ensure_runtime", lambda _root, **_kwargs: (True, "ready"))
    root = tmp_path / "Onmyoji-Exemplo"
    root.mkdir()
    ok, _message = install_instance(root)
    assert ok and is_installed(root)
    assert default_service_name(root) == "Shikigami-Exemplo"
    assert default_service_description(root) == "Shikigami Exemplo Daemon"
    ok, _message = set_enabled(root, "telegram", True)
    assert ok, _message
    assert Supervisor(root).services["telegram"].enabled
    ok, _message = set_enabled(root, "mini-apps", True)
    assert ok
    assert Supervisor(root).services["mini-apps"].enabled


def test_instance_identity_comes_from_local_system_configuration(tmp_path):
    configs = tmp_path / "configs"; configs.mkdir()
    (configs / "onmyoji-system.toml").write_text('[shikigami]\nidentity = "Nome Local"\ninstalled = true\n', encoding="utf-8")
    assert default_service_name(tmp_path) == "Shikigami-NomeLocal"
    assert default_service_description(tmp_path) == "Shikigami Nome Local Daemon"


def test_configuration_revision_requires_the_running_process_to_match(tmp_path, monkeypatch):
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version = 1\ninstance_id = "test"\n', encoding="utf-8")
    revision = management.configuration_revision(tmp_path)
    process = tmp_path / "configs" / "daemon" / "runtime" / "process.json"; process.parent.mkdir(parents=True)
    process.write_text('{"pid": 123, "root": "' + str(tmp_path.resolve()).replace("\\", "\\\\") + '", "configuration_revision": "' + revision + '"}', encoding="utf-8")
    monkeypatch.setattr(management, "_pid_alive", lambda _pid: True)

    assert management.applied_configuration_revision(tmp_path) == revision
    manifest.write_text('schema_version = 1\ninstance_id = "changed"\n', encoding="utf-8")
    assert management.configuration_revision(tmp_path) != management.applied_configuration_revision(tmp_path)


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
    assert "--without-pip" in calls[0]
    assert calls[1][0] == management.sys.executable and calls[1][-1] == "--version"
    assert calls[2][0] == management.sys.executable and "install" in calls[2]
    assert "--target" in calls[2]
    assert "--editable" not in calls[2]
    assert any(str(value).startswith("aiohttp>=") for value in calls[2])


def test_windows_service_host_is_prepared_inside_dedicated_environment(tmp_path, monkeypatch):
    scripts = tmp_path / "configs" / "daemon" / "venv" / "Scripts"
    executable = scripts / "python.exe"; executable.parent.mkdir(parents=True); executable.touch()
    base = tmp_path / "Python312"; base.mkdir(); (base / "python312.dll").write_bytes(b"python")
    (scripts.parent / "pyvenv.cfg").write_text(f"home = {base}\n", encoding="utf-8")
    site_packages = scripts.parent / "Lib" / "site-packages"
    source = scripts.parent / "pythonservice.exe"; source.write_bytes(b"service")
    dll = site_packages / "pywin32_system32" / "pywintypes312.dll"; dll.parent.mkdir(parents=True); dll.write_bytes(b"dll")
    monkeypatch.setattr(management, "daemon_python", lambda _root: executable)

    ok, _message = management.prepare_windows_service_host(tmp_path)

    assert ok, _message
    assert (scripts / "pythonservice.exe").read_bytes() == b"service"
    assert (scripts / "pywintypes312.dll").read_bytes() == b"dll"
    assert (scripts / "python312.dll").read_bytes() == b"python"


def test_service_commands_use_the_runtime_selected_by_windows_service(tmp_path, monkeypatch):
    executable = tmp_path / "venv" / "Scripts" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setenv("ONMYOJI_DAEMON_PYTHON", str(executable))
    data_dir = tmp_path / "configs" / "daemon" / "services" / "telegram"

    assert telegram_command(data_dir)[0] == str(executable)
    assert miniapps_command(data_dir)[0] == str(executable)
    assert btg_monitor_command(data_dir)[0] == str(executable)


def test_windows_service_action_waits_for_the_service_to_run(tmp_path, monkeypatch):
    management._write_json(management.service_path(tmp_path), {"name": "Teste", "root": str(tmp_path.resolve())})
    monkeypatch.setattr(management.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(management, "service_status", lambda _root: (True, "STATE              : 4  RUNNING"))

    ok, message = management.service_action(tmp_path, "start")

    assert ok
    assert "em execução" in message


def test_windows_service_action_reports_when_start_does_not_reach_running(tmp_path, monkeypatch):
    management._write_json(management.service_path(tmp_path), {"name": "Teste", "root": str(tmp_path.resolve())})
    monkeypatch.setattr(management.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(management, "service_status", lambda _root: (True, "STATE              : 1  STOPPED"))
    monkeypatch.setattr(management.time, "sleep", lambda _seconds: None)

    ok, message = management.service_action(tmp_path, "start")

    assert not ok
    assert "não chegou ao estado RUNNING" in message


def test_external_runtime_is_not_changed_without_install_permission(tmp_path, monkeypatch):
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    executable = tmp_path / "python.exe"; executable.touch()
    manifest.write_text('[runtime]\nmode = "global"\nexecutable = "' + str(executable).replace("\\", "\\\\") + '"\n', encoding="utf-8")
    monkeypatch.setattr(management, "_runtime_ready", lambda _executable: False)
    calls = []
    monkeypatch.setattr(management.subprocess, "run", lambda command, **_kwargs: calls.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""))

    ok, message = management.ensure_runtime(tmp_path, allow_install=False)

    assert not ok and "Instalar/validar daemon" in message
    assert calls == []


def test_daemon_python_uses_selected_external_runtime(tmp_path):
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('[runtime]\nmode = "existing"\nexecutable = "C:/Python/venv/Scripts/python.exe"\n', encoding="utf-8")

    assert management.daemon_python(tmp_path) == Path("C:/Python/venv/Scripts/python.exe")


def test_systemd_unit_uses_explicit_account_only_for_system_scope(tmp_path):
    system = management._systemd_unit(tmp_path, "/opt/shikigami/python", Path("/opt/onmyoji-daemon/src"), "Daemon", "system", "shikigami", "agents")
    user = management._systemd_unit(tmp_path, "/opt/shikigami/python", Path("/opt/onmyoji-daemon/src"), "Daemon", "user", "", "")

    assert "User=shikigami" in system and "Group=agents" in system
    assert "WantedBy=multi-user.target" in system
    assert "User=" not in user and "Group=" not in user
    assert "WantedBy=default.target" in user


def test_miniapps_uses_private_daemon_python_when_available(tmp_path):
    data_dir = tmp_path / "configs" / "daemon" / "services" / "mini-apps"
    executable = data_dir.parent.parent / "venv" / ("Scripts/python.exe" if management.os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True); executable.touch()

    command = miniapps_command(data_dir)

    assert command[0] == str(executable)
