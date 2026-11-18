"""Portas RPC não consomem listeners HTTP que ainda serão inicializados."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon import supervisor as supervisor_module
from onmyoji_daemon.registry import ServiceSpec
from onmyoji_daemon.supervisor import Supervisor


def configure_http_ports(root, notification_port=45192, gateway_port=45190):
    notification = root / "configs" / "daemon" / "services" / "telegram" / "notification.json"
    notification.parent.mkdir(parents=True)
    notification.write_text(json.dumps({"schema_version": 1, "port": notification_port}), encoding="utf-8")
    gateway = root / "configs" / "daemon" / "services" / "gateway" / "config.toml"
    gateway.parent.mkdir(parents=True)
    gateway.write_text(f"schema_version = 1\nhttp_port = {gateway_port}\n", encoding="utf-8")


def test_supervisor_binds_rpc_atomically_and_closes_reserved_http_candidates(tmp_path, monkeypatch):
    configure_http_ports(tmp_path)
    allocated = []
    candidates = iter([39390, 39391, 39392, 45190, 45192, 45000])

    def rpc(_host, port, *_args, **_kwargs):
        assert port == 0
        candidate = SimpleNamespace(port=next(candidates), closed=False)
        candidate.close = lambda: setattr(candidate, "closed", True)
        allocated.append(candidate)
        return candidate

    monkeypatch.setattr(supervisor_module, "RpcServer", rpc)
    monkeypatch.setattr(supervisor_module, "free_port", lambda: pytest.fail("RPC próprio deve fazer bind atômico"))

    supervisor = Supervisor(tmp_path)

    assert supervisor.rpc.port == 45000
    assert all(candidate.closed for candidate in allocated[:-1])
    assert not allocated[-1].closed
    supervisor.rpc.close()


def test_supervisor_does_not_retry_reserved_ports_indefinitely(tmp_path, monkeypatch):
    closed = []
    monkeypatch.setattr(supervisor_module, "RpcServer", lambda *_args, **_kwargs: SimpleNamespace(port=39392, close=lambda: closed.append(True)))

    with pytest.raises(OSError, match="porta RPC"):
        Supervisor(tmp_path)

    assert len(closed) == 64


def test_worker_rpc_excludes_http_ports_and_already_assigned_rpc_ports(tmp_path, monkeypatch):
    configure_http_ports(tmp_path)
    published = ServiceSpec("fixture-http", "fixture.", lambda _root: [], "Fixture HTTP", publication_port=45191)
    monkeypatch.setattr(supervisor_module, "SERVICES", {**supervisor_module.SERVICES, "fixture-http": published})
    supervisor = Supervisor(tmp_path)
    try:
        supervisor.services["mini-apps"].port = 45189
        excluded = [39390, 39391, 39392, 45190, 45191, 45192, 45189, supervisor.rpc.port]
        selected = next(port for port in range(40000, 65536) if port not in excluded)
        candidates = iter([*excluded, selected])
        monkeypatch.setattr(supervisor_module, "free_port", lambda: next(candidates))

        assert supervisor._worker_rpc_port() == selected
    finally:
        supervisor.rpc.close()


def test_worker_rpc_reports_exhaustion_instead_of_selecting_an_http_port(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path)
    try:
        attempts = []
        monkeypatch.setattr(supervisor_module, "free_port", lambda: attempts.append(True) or 39392)

        with pytest.raises(OSError, match="porta RPC"):
            supervisor._worker_rpc_port()

        assert len(attempts) == 64
    finally:
        supervisor.rpc.close()


@pytest.mark.parametrize("content", ["not [ valid toml", "schema_version = 1\nhttp_port = true\n", "schema_version = 1\nhttp_port = 99999\n"])
def test_invalid_gateway_configuration_does_not_block_supervisor_diagnostics(tmp_path, content):
    config = tmp_path / "configs" / "daemon" / "services" / "gateway" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding="utf-8")

    supervisor = Supervisor(tmp_path)
    try:
        assert {39390, 39391, 39392} <= supervisor._http_ports()
        assert supervisor.rpc.port not in supervisor._http_ports()
    finally:
        supervisor.rpc.close()


def test_worker_allocation_observes_http_port_changed_after_supervisor_start(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path)
    try:
        configure_http_ports(tmp_path, notification_port=45092, gateway_port=45090)
        candidates = iter([45092, 45090, 45080])
        monkeypatch.setattr(supervisor_module, "free_port", lambda: next(candidates))

        assert supervisor._worker_rpc_port() == 45080
    finally:
        supervisor.rpc.close()


def test_concurrent_worker_start_reserves_the_selected_rpc_port_before_another_selection(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path)
    token_pending, other_process_created = threading.Event(), threading.Event()
    candidates = threading.local()
    launched = {}
    failures = []
    first_port = next(port for port in range(40000, 65000) if port not in supervisor._http_ports() | {supervisor.rpc.port})
    second_port = next(port for port in range(first_port + 1, 65536) if port not in supervisor._http_ports() | {supervisor.rpc.port})

    def free_port():
        calls = getattr(candidates, "calls", 0)
        candidates.calls = calls + 1
        return second_port if calls else first_port

    def token(_length):
        if threading.current_thread().name == "start-telegram":
            token_pending.set()
            other_process_created.wait(1)
        return "fixture-token"

    def spawn(command, **_kwargs):
        service = "telegram" if "onmyoji_daemon.telegram" in command else "mini-apps"
        launched[service] = int(command[command.index("--port") + 1])
        if service == "mini-apps":
            other_process_created.set()
        return SimpleNamespace(pid=100, stdout=None, poll=lambda: 1, returncode=1)

    def start(name):
        try:
            supervisor.start(name)
        except Exception as error:
            failures.append(error)

    monkeypatch.setattr(supervisor_module, "free_port", free_port)
    monkeypatch.setattr(supervisor_module.secrets, "token_urlsafe", token)
    monkeypatch.setattr(supervisor_module.subprocess, "Popen", spawn)
    first = threading.Thread(target=start, args=("telegram",), name="start-telegram")
    second = threading.Thread(target=start, args=("mini-apps",), name="start-mini-apps")
    try:
        first.start()
        assert token_pending.wait(2)
        second.start()
        first.join(3)
        second.join(3)

        assert not first.is_alive() and not second.is_alive()
        assert not failures
        assert launched == {"telegram": first_port, "mini-apps": second_port}
    finally:
        other_process_created.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
        supervisor.rpc.close()
