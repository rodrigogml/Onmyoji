import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from onmyoji_daemon.rpc import RpcServer, RpcError, call, disconnected


def test_private_rpc_cannot_bind_a_public_interface():
    with pytest.raises(ValueError, match="loopback"):
        RpcServer("0.0.0.0", 0, "fixture", lambda *args: None)


def test_long_rpc_preserves_control_and_rejects_saturation():
    started, release, stop = threading.Event(), threading.Event(), threading.Event()
    def handle(method, params):
        if method == "long":
            started.set()
            assert release.wait(5)
        return method
    server = RpcServer("127.0.0.1", 0, "secret", handle, long_methods=("long",), long_limit=1)
    thread = threading.Thread(target=server.serve_forever, args=(stop,), daemon=True)
    thread.start()
    try:
        with ThreadPoolExecutor() as pool:
            result = pool.submit(call, "127.0.0.1", server.port, "secret", "long", {}, None)
            assert started.wait(3)
            assert call("127.0.0.1", server.port, "secret", "ping") == "ping"
            with pytest.raises(RpcError, match="saturados"):
                call("127.0.0.1", server.port, "secret", "long")
            release.set()
            assert result.result(3) == "long"
    finally:
        release.set(); stop.set(); server.close(); thread.join(2)


def test_disconnect_is_visible_and_payload_limit_precedes_handler():
    entered, detached, stop = threading.Event(), threading.Event(), threading.Event()
    def handle(method, params):
        entered.set()
        while not disconnected():
            assert not stop.wait(0.01)
        detached.set()
    server = RpcServer("127.0.0.1", 0, "s", handle, long_methods=("long",), event_max_bytes=128)
    thread = threading.Thread(target=server.serve_forever, args=(stop,), daemon=True); thread.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as client:
            client.sendall((json.dumps({"token": "s", "method": "long", "params": {}}) + "\n").encode())
            assert entered.wait(2)
        assert detached.wait(2)
        entered.clear()
        with pytest.raises(RpcError) as caught:
            call("127.0.0.1", server.port, "s", "long", {"payload": "x" * 200})
        assert caught.value.code == "request_too_large"
        assert not entered.is_set()
    finally:
        stop.set(); server.close(); thread.join(2)
