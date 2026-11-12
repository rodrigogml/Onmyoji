"""Compatibilidade dos erros estruturados com clientes RPC existentes."""
import socket
import threading

import pytest

from onmyoji_daemon.rpc import RpcError, RpcServer, call


def test_rpc_preserves_error_code_details_and_legacy_message():
    stopped = threading.Event()
    def handler(method, params):
        if method == "conflict":
            raise RpcError("Namespace reservado.", "prefix_conflict", {"path_prefix": "a"})
        if method == "legacy":
            raise ValueError("Erro legado.")
        return {"ready": True}

    server = RpcServer("127.0.0.1", 0, "test-token", handler)
    worker = threading.Thread(target=server.serve_forever, args=(stopped,), daemon=True)
    worker.start()
    try:
        with pytest.raises(RpcError) as conflict:
            call("127.0.0.1", server.port, "test-token", "conflict")
        assert str(conflict.value) == "Namespace reservado."
        assert conflict.value.code == "prefix_conflict"
        assert conflict.value.details == {"path_prefix": "a"}
        with pytest.raises(RpcError) as legacy:
            call("127.0.0.1", server.port, "test-token", "legacy")
        assert str(legacy.value) == "Erro legado."
        assert legacy.value.code is None and legacy.value.details is None
        assert call("127.0.0.1", server.port, "test-token", "ping") == {"ready": True}
    finally:
        stopped.set()
        worker.join(2)
        server.close()


def test_rpc_refuses_untrusted_token_without_invoking_handler():
    stopped, called = threading.Event(), []
    server = RpcServer("127.0.0.1", 0, "private", lambda method, params: called.append(method))
    worker = threading.Thread(target=server.serve_forever, args=(stopped,), daemon=True)
    worker.start()
    try:
        with pytest.raises(RpcError, match="unauthorized"):
            call("127.0.0.1", server.port, "wrong", "gateway.publication.sync")
        assert called == []
    finally:
        stopped.set()
        worker.join(2)
        server.close()
