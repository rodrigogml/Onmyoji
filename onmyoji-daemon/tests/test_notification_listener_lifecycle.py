"""Isola falhas do listener de eventos e conserva sua configuração em runtime."""
from __future__ import annotations

import errno
import http.client
import json
import socket
import threading

import pytest

from .test_notification_gateway import event
from .test_telegram_gateway_async import lab
from onmyoji_daemon.agent_notification import http as notification_http
from onmyoji_daemon.agent_notification.http import NotificationHttpServer


@pytest.fixture
def gateway(lab):
    yield lab.gateway
    lab.gateway._stop_notification_http()


def occupied_socket():
    blocker = socket.socket()
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    return blocker


def install_listener(gateway):
    server = NotificationHttpServer(0, gateway)
    gateway.notification_configuration.configure({"port": server.port})
    gateway._notification_http = server
    server.start()
    return server


def post(server, value):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
    try:
        connection.request("POST", "/v1/events?wait=false", json.dumps(value).encode("utf-8"))
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_occupied_event_port_does_not_disable_telegram_rpc_or_stop_the_gateway(gateway):
    with occupied_socket() as blocker:
        port = blocker.getsockname()[1]
        gateway.notification_configuration.configure({"port": port})

        gateway._start_notification_http()

        status = gateway._notification_http_status()
        assert status["state"] == "failed"
        assert status["host"] == "127.0.0.1" and status["port"] == port
        assert f"127.0.0.1:{port}" in status["last_error"]
        assert "código" in status["last_error"]
        assert gateway._notification_http is None
        assert gateway.handle("ping", {}) == {"service": "telegram", "state": "running"}
        assert not gateway.stop_event.is_set()
        assert gateway.notification_configuration.load()["enabled"] is False


@pytest.mark.parametrize("code, reason", [
    (errno.EADDRINUSE, "já está em uso"),
    (errno.EACCES, "recusou o acesso"),
    (errno.EIO, "não permitiu abrir"),
])
def test_listener_failure_reports_address_and_code_without_raw_exception_data(gateway, monkeypatch, code, reason):
    def denied(*_args, **_kwargs):
        raise OSError(code, "token=private-fixture")

    monkeypatch.setattr(notification_http, "NotificationHttpServer", denied)

    gateway._start_notification_http()

    error = gateway._notification_http_status()["last_error"]
    assert "127.0.0.1:39392" in error
    assert reason in error and str(code) in error
    assert "private-fixture" not in error


def test_configure_retries_a_failed_listener_after_the_port_is_released(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "_wake_worker", lambda _conversation: None)
    with occupied_socket() as blocker:
        port = blocker.getsockname()[1]
        gateway.notification_configuration.configure({"port": port})
        gateway._start_notification_http()
        assert gateway._notification_http_status()["state"] == "failed"

    configured = gateway.handle("notification.configure", {"enabled": True})

    assert configured["http"] == {"state": "running", "host": "127.0.0.1", "port": port, "last_error": None}
    assert configured["enabled"] is True
    assert configured["restart_required"] is False
    assert gateway._notification_http.running


def test_live_enable_preserves_the_listener_and_disabled_requests_still_receive_503(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "_wake_worker", lambda _conversation: None)
    server = install_listener(gateway)
    value = event()
    status, result = post(server, value)
    assert status == 503 and result["error"]["code"] == "ingress_disabled"

    gateway._start_notification_http()
    configured = gateway.handle("notification.configure", {"enabled": True})
    status, result = post(server, value)

    assert gateway._notification_http is server
    assert configured["http"]["state"] == "running"
    assert configured["restart_required"] is False
    assert status == 202 and result["accepted"] is True


@pytest.mark.parametrize("method", ["notification.status", "notification.diagnose"])
def test_notification_diagnostics_reflect_the_real_http_listener(gateway, method):
    stopped = gateway.handle(method, {})
    assert stopped["http"]["state"] == "stopped"
    assert stopped["checks"]["http_listener"] is False
    server = install_listener(gateway)

    running = gateway.handle(method, {})

    assert running["http"] == {"state": "running", "host": "127.0.0.1", "port": server.port, "last_error": None}
    assert running["checks"]["http_listener"] is True


def test_configure_keeps_bind_failure_explicit_in_its_result(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "_wake_worker", lambda _conversation: None)
    with occupied_socket() as blocker:
        gateway.notification_configuration.configure({"port": blocker.getsockname()[1]})

        configured = gateway.handle("notification.configure", {"enabled": True})

        assert configured["enabled"] is True
        assert configured["http"]["state"] == "failed"
        assert configured["http"]["last_error"]
        assert gateway.handle("notification.status", {})["checks"]["http_listener"] is False


def test_changed_configuration_reports_the_current_bound_port_until_restart(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "_wake_worker", lambda _conversation: None)
    server = install_listener(gateway)
    with occupied_socket() as blocker:
        configured_port = blocker.getsockname()[1]

        configured = gateway.handle("notification.configure", {"port": configured_port})

        assert configured["restart_required"] is True
        assert configured["port"] == configured_port
        assert configured["http"]["state"] == "running"
        assert configured["http"]["port"] == server.port


def test_stopped_gateway_does_not_reopen_http_during_configuration(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "_wake_worker", lambda _conversation: None)
    gateway.stop_event.set()
    monkeypatch.setattr(notification_http, "NotificationHttpServer", lambda *_args: pytest.fail("Gateway encerrando não deve abrir listener"))

    configured = gateway.handle("notification.configure", {"enabled": True})

    assert configured["http"]["state"] == "stopped"


def test_listener_created_but_not_started_can_close_without_blocking(gateway):
    server = NotificationHttpServer(0, gateway)
    completed = threading.Event()

    def close():
        server.close()
        completed.set()

    thread = threading.Thread(target=close, daemon=True)
    thread.start()
    assert completed.wait(1), "Fechar antes de start não pode aguardar serve_forever."
    thread.join(1)
    server.close()
    assert server.server.fileno() == -1
    assert not server.running
    with pytest.raises(RuntimeError, match="encerrado"):
        server.start()


def test_started_http_listener_start_and_close_are_idempotent(gateway):
    server = install_listener(gateway)
    server.start()
    gateway._stop_notification_http()
    gateway._stop_notification_http()
    server.close()

    assert not server.running
    assert server.server.fileno() == -1
    assert not server.thread.is_alive()
    assert gateway._notification_http_status() == {"state": "stopped", "host": "127.0.0.1", "port": server.port, "last_error": None}


def test_failure_after_socket_creation_closes_the_unstarted_server(gateway, monkeypatch):
    instances = []

    class FailingStart:
        def __init__(self, *_args):
            self.closed = False
            instances.append(self)

        def start(self):
            raise OSError(errno.ENOBUFS, "no buffers")

        def close(self):
            self.closed = True

    monkeypatch.setattr(notification_http, "NotificationHttpServer", FailingStart)

    gateway._start_notification_http()

    assert instances[0].closed
    assert gateway._notification_http is None
    assert gateway._notification_http_status()["state"] == "failed"
