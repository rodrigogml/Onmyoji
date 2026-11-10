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
    enable(lab, watch=False)
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


def test_detaching_rpc_during_smtp_releases_waiter_before_delivery_finishes(lab, monkeypatch):
    from onmyoji_daemon.mcp.email.service import EmailService
    enable(lab)
    lab.gateway.notification_configuration.configure({"smtp_account": "fixture"})
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(EmailService, "account", lambda self, name: ({"smtp": {"host": "fixture"}}, {}))
    def send(*args):
        entered.set()
        assert release.wait(5)
        return {"status": "accepted"}
    monkeypatch.setattr(EmailService, "invoke", send)
    server = RpcServer("127.0.0.1", 0, "fixture", lab.gateway.handle, long_methods=("notification.submit",), long_limit=1)
    receiver = threading.Thread(target=server.serve_forever, args=(stopped,), daemon=True); receiver.start()
    client = socket.create_connection(("127.0.0.1", server.port), timeout=3)
    try:
        value = event(); value["response_channel"] = {"type": "email", "value": "fixture@example.com"}
        client.sendall((json.dumps({"token": "fixture", "method": "notification.submit", "params": value}) + "\n").encode())
        thread = lab.client.wait_calls("turn/start")[0]["threadId"]
        lab.client.item(thread, "Resposta"); lab.client.complete(thread)
        assert entered.wait(2)
        client.close()
        assert server.long_limit.acquire(timeout=2), "A espera RPC não pode ser dona do envio SMTP."
        server.long_limit.release()
        assert not release.is_set()
    finally:
        client.close(); release.set(); stopped.set(); server.close(); receiver.join(2)


def test_terminal_delivery_blocks_destination_even_without_in_memory_slot(lab):
    from .test_telegram_gateway_async import submit
    enable(lab, watch=False)
    value = event()
    receipt = {"event_id": value["event_id"], "received_at": "2026-11-10T12:00:00.000Z", "envelope": value}
    gateway = lab.gateway
    identifier = gateway.store.enqueue("9", gateway._generation("9"), "anterior", [], {"notification": value}, origin="event", receipt=receipt)
    execution = gateway.store.reserve("9", 0, identifier, 30)
    operation = gateway.store.operation(identifier, execution, "turn/start", None)
    gateway.store.operation_result(operation, "accepted")
    gateway.store.save_terminal_output(execution, "completed", "Entrega recuperada", "final", "text", False)
    assert gateway.store.blocked("9")
    submit(lab, "Depois da entrega", conversation="9")
    submit(lab, "Destino elegível", conversation="10")
    first = lab.client.wait_calls("turn/start")[0]["threadId"]
    assert gateway._slots["10"].active.thread_id == first
    assert "9" not in gateway._slots
    gateway._notification_sync()
    second = lab.client.wait_calls("turn/start", 2)[1]["threadId"]
    assert gateway._slots["9"].active.thread_id == second
    lab.client.complete(first); lab.client.complete(second)


def test_global_sequence_is_not_reused_after_inbox_cleanup(lab):
    gateway = lab.gateway
    gateway._generation("9")
    first = gateway.store.enqueue("9", 0, "first", [], {})
    previous = gateway.database.execute("SELECT global_sequence FROM inbox_items WHERE id=?", (first,)).fetchone()[0]
    with gateway.database:
        gateway.database.execute("DELETE FROM inbox_items")
    second = gateway.store.enqueue("9", 0, "second", [], {})
    current = gateway.database.execute("SELECT global_sequence FROM inbox_items WHERE id=?", (second,)).fetchone()[0]
    assert current > previous


def test_partial_log_append_is_repaired_from_transactional_journal(lab):
    enable(lab, watch=False)
    log = lab.gateway.notification_log
    log.record("fixture", event_id="fixture")
    identifier, at = lab.gateway.database.execute("SELECT id,at FROM notification_log_journal").fetchone()
    log.directory.mkdir(parents=True)
    path = log.directory / ("notification-" + at[:10] + ".jsonl")
    path.write_bytes(('{"journal_entry": ' + str(identifier) + ', "at": "broken').encode())
    log.flush(); log.flush()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["stage"] == "fixture"
    assert lab.gateway.database.execute("SELECT flushed FROM notification_log_journal").fetchone() == (1,)


def test_log_retention_refuses_redirected_directory(lab):
    import os
    enable(lab, watch=False)
    log = lab.gateway.notification_log
    outside = lab.tmp / "unrelated-logs"; outside.mkdir()
    retained = outside / "notification-2000-01-01.jsonl"
    retained.write_text("{}", encoding="utf-8")
    log.directory.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(log.directory))
    else:
        log.directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        log.purge()
    assert retained.read_text(encoding="utf-8") == "{}"
