"""Prazos do transporte RPC encaminhado pelo supervisor, sem rede ou espera."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from onmyoji_daemon import supervisor as supervisor_module
from onmyoji_daemon.supervisor import Supervisor


@pytest.mark.parametrize("method,service_name,expected_timeout", [
    ("telegram.outbound.send_location", "telegram", 120),
    ("telegram.reply.send_location", "telegram", 120),
    ("telegram.outbound.send", "telegram", 120),
    ("telegram.status", "telegram", 5),
    ("telegram.channels.list", "telegram", 5),
    ("mini-apps.oauth.start", "mini-apps", 200),
    ("mini-apps.oauth.status", "mini-apps", 200),
    ("mini-apps.oauth.cancel", "mini-apps", 200),
    ("mini-apps.status", "mini-apps", 5),
])
def test_handle_forwards_rpc_with_operation_timeout(tmp_path, monkeypatch, method, service_name, expected_timeout):
    """O prazo chega ao transporte real de ``_call_service``, sem alterar payload ou resultado."""
    monkeypatch.setattr(supervisor_module, "free_port", lambda: 39900)
    monkeypatch.setattr(supervisor_module, "RpcServer", lambda *_args: SimpleNamespace(port=39900))
    supervisor = Supervisor(tmp_path)
    service = supervisor.services[service_name]
    service.process = SimpleNamespace(poll=lambda: None)
    service.port, service.token = 39901, "child-service-token"
    payload = {"contract_test": "forwarded"}
    receipt = {"contract_test": "reply"}
    calls = []

    def transport(host, port, token, operation, params, *, timeout):
        calls.append((host, port, token, operation, params, timeout))
        return receipt

    monkeypatch.setattr(supervisor_module, "call", transport)

    assert supervisor.handle(method, payload) is receipt
    assert calls == [("127.0.0.1", 39901, "child-service-token", method, payload, expected_timeout)]
    assert calls[0][4] is payload
