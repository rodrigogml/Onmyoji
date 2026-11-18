from __future__ import annotations

import threading

import pytest

from onmyoji_daemon.rpc import call
from onmyoji_daemon.supervisor import Supervisor, endpoint


def test_supervisor_serves_rpc_while_an_enabled_service_is_starting(tmp_path, monkeypatch):
    """O endpoint responde antes de o gateway terminar sua inicialização."""
    supervisor = Supervisor(tmp_path)
    supervisor.services["telegram"].enabled = True
    initializing = threading.Event()
    release = threading.Event()
    failures = []

    def start(name):
        assert name == "telegram"
        initializing.set()
        assert release.wait(5)
        supervisor.services[name].state = "running"
        return supervisor.status(name)

    def run():
        try:
            supervisor.run_forever()
        except Exception as error:
            failures.append(error)

    monkeypatch.setattr(supervisor, "start", start)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert initializing.wait(2)
        address = endpoint(tmp_path)
        ping = call(*address, "ping", timeout=1)
        assert ping["service"] == "onmyoji-daemon"
        assert ping["state"] == "running"
        assert ping["instance"] == supervisor.process_identity["instance"]
        services = call(*address, "list-services", timeout=1)
        telegram = next(service for service in services if service["name"] == "telegram")
        assert telegram["enabled"] is True
        assert telegram["state"] == "starting"
        assert telegram["pid"] is None
        release.set()
        assert call(*address, "shutdown", timeout=1) == {"state": "stopping"}
    finally:
        release.set()
        supervisor.stop_event.set()
        thread.join(3)
    assert not thread.is_alive()
    assert failures == []
    assert not supervisor.endpoint_file.exists()
    assert not supervisor.process_file.exists()
    assert not supervisor.lock_file.exists()


def test_shutdown_during_initialization_does_not_start_later_services(tmp_path, monkeypatch):
    """Uma parada aceita por RPC cancela as partidas ainda pendentes."""
    supervisor = Supervisor(tmp_path)
    supervisor.services["telegram"].enabled = True
    supervisor.services["mini-apps"].enabled = True
    initializing = threading.Event()
    release = threading.Event()
    started = []
    failures = []

    def start(name):
        started.append(name)
        initializing.set()
        assert release.wait(5)
        supervisor.services[name].state = "running"
        return supervisor.status(name)

    def run():
        try:
            supervisor.run_forever()
        except Exception as error:
            failures.append(error)

    monkeypatch.setattr(supervisor, "start", start)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert initializing.wait(2)
        address = endpoint(tmp_path)
        services = call(*address, "list-services", timeout=1)
        assert {service["name"]: service["state"] for service in services if service["enabled"]} == {"telegram": "starting", "mini-apps": "starting"}
        assert call(*address, "shutdown", timeout=1) == {"state": "stopping"}
        assert supervisor.stop_event.is_set()
        assert started == ["telegram"]
    finally:
        release.set()
        supervisor.stop_event.set()
        thread.join(3)
    assert not thread.is_alive()
    assert failures == []
    assert started == ["telegram"]
    assert all(service.state == "stopped" for service in supervisor.services.values())
    assert not supervisor.endpoint_file.exists()


def test_rpc_failure_stops_supervisor_and_removes_runtime_markers(tmp_path, monkeypatch):
    """Falha do transporte em sua thread não deixa o processo principal aguardando."""
    supervisor = Supervisor(tmp_path)

    def unavailable(_stop_event):
        raise OSError("RPC indisponível")

    monkeypatch.setattr(supervisor.rpc, "serve_forever", unavailable)

    with pytest.raises(OSError, match="RPC indisponível"):
        supervisor.run_forever()

    assert supervisor.stop_event.is_set()
    assert not supervisor.endpoint_file.exists()
    assert not supervisor.process_file.exists()
    assert not supervisor.lock_file.exists()


def test_stopping_supervisor_does_not_launch_or_recover_services(tmp_path, monkeypatch):
    """Pedidos de partida e recuperação respeitam a parada já solicitada."""
    supervisor = Supervisor(tmp_path)
    supervisor.stop_event.set()
    service = supervisor.services["telegram"]
    service.enabled = True
    service.state = "failed"
    monkeypatch.setattr("onmyoji_daemon.supervisor.subprocess.Popen", lambda *_args, **_kwargs: pytest.fail("O supervisor está encerrando."))
    try:
        assert supervisor.start("telegram")["state"] == "stopped"
        service.state = "failed"
        monkeypatch.setattr(supervisor, "start", lambda _name: pytest.fail("A recuperação não inicia novos processos durante a parada."))
        supervisor._recover_enabled_services_once()
        assert service.state == "failed"
    finally:
        supervisor.rpc.close()
