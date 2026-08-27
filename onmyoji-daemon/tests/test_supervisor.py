from __future__ import annotations

import threading
import time

from onmyoji_daemon.rpc import call
from onmyoji_daemon.supervisor import Supervisor, endpoint
from onmyoji_daemon.management import default_service_description, default_service_name, install_instance, is_installed, set_enabled


def test_supervisor_exposes_only_registered_services(tmp_path):
    supervisor = Supervisor(tmp_path)
    assert [item["name"] for item in supervisor.handle("list-services", {})] == ["telegram"]
    assert supervisor.handle("status", {"service": "telegram"})["enabled"] is False


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


def test_instance_installation_and_service_identity_are_local(tmp_path):
    root = tmp_path / "Onmyoji-Lavelinha"
    root.mkdir()
    ok, _message = install_instance(root)
    assert ok and is_installed(root)
    assert default_service_name(root) == "Shikigami-Lavelinha"
    assert default_service_description(root) == "Shikigami Lavelinha Daemon"
    ok, _message = set_enabled(root, "telegram", True)
    assert ok
    assert Supervisor(root).services["telegram"].enabled
