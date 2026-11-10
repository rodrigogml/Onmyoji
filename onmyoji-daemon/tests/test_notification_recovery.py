"""Fronteiras reais do transporte e recuperação sem efeitos externos reais."""
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import threading

import pytest

from .test_telegram_gateway_async import lab
from .test_notification_gateway import enable, event
from onmyoji_daemon.rpc import RpcServer, call


def test_disconnect_after_acceptance_keeps_execution_owned_by_gateway(lab):
    enable(lab)
    server = RpcServer("127.0.0.1", 0, "fixture", lab.gateway.handle, long_methods=("notification.submit",), long_limit=1)
    stopped = threading.Event()
    receiver = threading.Thread(target=server.serve_forever, args=(stopped,), daemon=True); receiver.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=3) as client:
            request = {"token": "fixture", "method": "notification.submit", "params": event()}
            client.sendall((json.dumps(request) + "\n").encode())
            thread = lab.client.wait_calls("turn/start")[0]["threadId"]
            assert call("127.0.0.1", server.port, "fixture", "notification.status")["enabled"]
        lab.client.item(thread, "Resposta após desconexão")
        lab.client.complete(thread)
        lab.api.wait_text("Resposta após desconexão")
        assert len(lab.client.wait_calls("turn/start")) == 1
    finally:
        stopped.set(); server.close(); receiver.join(2)


def test_smtp_account_is_captured_at_admission_and_receipts_are_per_reception(lab, monkeypatch):
    from onmyoji_daemon.mcp.email.service import EmailService
    enable(lab)
    configuration = lab.gateway.notification_configuration
    configuration.configure({"smtp_account": "original"})
    requests = []
    monkeypatch.setattr(EmailService, "account", lambda self, name: ({"smtp": {}}, {}))
    # Conta de teste com SMTP não vazio satisfaz verificação operacional.
    monkeypatch.setattr(EmailService, "account", lambda self, name: ({"smtp": {"host": "test"}}, {}))
    monkeypatch.setattr(EmailService, "invoke", lambda self, name, method, args: requests.append((name, args)) or {"status": "accepted"})
    value = event(); value["response_channel"] = {"type": "email", "value": "fixture@example.com"}
    with ThreadPoolExecutor() as pool:
        future = pool.submit(lab.gateway.handle, "notification.submit", value)
        first = lab.client.wait_calls("turn/start")[0]["threadId"]
        configuration.configure({"smtp_account": "changed"})
        lab.client.item(first, "Resposta"); lab.client.complete(first)
        assert future.result(3)["delivery"] == "confirmed"
        second = pool.submit(lab.gateway.handle, "notification.submit", value)
        thread = lab.client.wait_calls("turn/start", 2)[1]["threadId"]
        lab.client.item(thread, "Outra resposta"); lab.client.complete(thread)
        assert second.result(3)["delivery"] == "confirmed"
    assert [name for name, args in requests] == ["original", "changed"]
    assert requests[0][1]["request_id"] != requests[1][1]["request_id"]
    assert requests[0][1]["subject"].endswith(value["event_id"])


def test_delivery_inflight_on_restart_becomes_uncertain_without_resending(lab):
    enable(lab)
    destination = "9"
    value = event()
    receipt = {"event_id": value["event_id"], "received_at": "2026-11-10T12:00:00.000Z", "envelope": value}
    identifier = lab.gateway.store.enqueue(destination, lab.gateway._generation(destination), "teste", [], {"notification": value}, origin="event", receipt=receipt)
    execution = lab.gateway.store.reserve(destination, 0, identifier, 30)
    operation = lab.gateway.store.operation(identifier, execution, "turn/start", None)
    lab.gateway.store.operation_result(operation, "accepted")
    lab.gateway.store.save_terminal_output(execution, "completed", "Resposta persistida", "final", "text", False)
    with lab.gateway.database:
        lab.gateway.database.execute("UPDATE notification_receipts SET delivery_state='sending' WHERE inbox_id=?", (identifier,))
    lab.gateway.store.recover()
    lab.gateway._notification_sync()
    result = lab.gateway.database.execute("SELECT execution_state,delivery_state FROM notification_receipts WHERE inbox_id=?", (identifier,)).fetchone()
    assert result == ("completed", "uncertain")
    assert not lab.api.sent and not lab.client.calls


def test_incident_failure_does_not_recurse(lab):
    enable(lab)
    lab.gateway.notification_configuration.configure({"incident": {"type": "telegram", "value": "9"}})
    attempts = []
    lab.gateway._notification_send = lambda *args: attempts.append(args) or "failed"
    result = lab.gateway.handle("notification.submit", event(destination="unknown"))
    assert result["execution"] == "not_started"
    lab.gateway._notification_sync(); lab.gateway._notification_sync()
    assert len(attempts) == 1
