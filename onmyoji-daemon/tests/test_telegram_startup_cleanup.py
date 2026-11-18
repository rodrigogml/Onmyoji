"""Limpeza do startup e diagnóstico de falhas de bind do Telegram."""
import errno
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon import rpc, telegram


@pytest.mark.parametrize("failure", [None, "bind", "start", "serve"])
def test_main_reserves_rpc_before_start_and_cleans_up_all_initialization_phases(tmp_path, monkeypatch, failure):
    events = []

    def start():
        events.append("start")
        if failure == "start": raise RuntimeError("start failed")

    gateway = SimpleNamespace(
        notification_configuration=SimpleNamespace(load=lambda: {"long_limit": 1, "max_request_bytes": 1_048_576}),
        handle=lambda *_args: None, _notification_transport_rejected=lambda *_args: None,
        stop_event=threading.Event(), start=start,
        _shutdown_execution=lambda: events.append("shutdown"),
    )

    class Server:
        def __init__(self, *_args, **_kwargs):
            events.append("bind")
            if failure == "bind": raise OSError(errno.EADDRINUSE, "address in use")

        def serve_forever(self, _stop):
            events.append("serve")
            if failure == "serve": raise RuntimeError("serve failed")

        def close(self):
            events.append("close")

    monkeypatch.setattr(telegram.Settings, "load", lambda *_args: None)
    monkeypatch.setattr(telegram, "Gateway", lambda _settings: gateway)
    monkeypatch.setattr(telegram, "RpcServer", Server)
    arguments = ["--data-dir", str(tmp_path), "--onmyoji-root", str(tmp_path), "--port", "12345", "--token", "fixture"]
    if failure:
        with pytest.raises((OSError, RuntimeError)):
            telegram.main(arguments)
    else:
        assert telegram.main(arguments) == 0
    expected = ["bind"]
    if failure != "bind":
        expected.append("start")
        if failure != "start": expected.append("serve")
    expected.append("shutdown")
    if failure != "bind": expected.append("close")
    assert events == expected


def test_failed_gateway_start_stops_workers_without_releasing_writer_lease_prematurely(tmp_path):
    events = []

    def invalid():
        raise ValueError("invalid startup configuration")

    gateway = SimpleNamespace(
        settings=SimpleNamespace(state_dir=tmp_path), stop_event=threading.Event(),
        store=SimpleNamespace(acquire_lease=lambda _path: events.append("acquire"), release_lease=lambda: events.append("release")),
        notification_configuration=SimpleNamespace(load=invalid),
    )
    with pytest.raises(ValueError, match="invalid startup"):
        telegram.Gateway.start(gateway)
    assert gateway.stop_event.is_set()
    assert events == ["acquire"]


def test_rpc_bind_failure_closes_socket_and_identifies_its_address(monkeypatch):
    closed = []

    def bind(_address):
        raise OSError(errno.EADDRINUSE, "address in use")

    connection = SimpleNamespace(setsockopt=lambda *_args: None, bind=bind, close=lambda: closed.append(True))
    monkeypatch.setattr(rpc, "socket", SimpleNamespace(
        gethostbyname=lambda host: host, socket=lambda *_args: connection,
        AF_INET=2, SOCK_STREAM=1, SOL_SOCKET=1, SO_REUSEADDR=2,
    ))
    with pytest.raises(OSError, match="RPC local em 127.0.0.1:12345") as raised:
        rpc.RpcServer("127.0.0.1", 12345, "fixture", lambda *_args: None)
    assert raised.value.errno == errno.EADDRINUSE
    assert closed == [True]
