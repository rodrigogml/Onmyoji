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


def test_email_account_subject_and_receipts_are_captured_per_reception(lab, monkeypatch):
    from onmyoji_daemon.mcp.email.service import EmailService
    enable(lab)
    requests = []
    monkeypatch.setattr(EmailService, "account", lambda self, name: ({"smtp": {}}, {}))
    # Conta de teste com SMTP não vazio satisfaz verificação operacional.
    monkeypatch.setattr(EmailService, "account", lambda self, name: ({"smtp": {"host": "test"}}, {}))
    monkeypatch.setattr(EmailService, "invoke", lambda self, name, method, args: requests.append((name, args)) or {"status": "accepted"})
    value = event(); value["response_channel"] = {"type": "email", "value": "fixture@example.com", "account": "original", "subject": "Primeiro assunto"}
    with ThreadPoolExecutor() as pool:
        future = pool.submit(lab.gateway.handle, "notification.submit", value)
        first = lab.client.wait_calls("turn/start")[0]["threadId"]
        lab.client.item(first, "Resposta"); lab.client.complete(first)
        assert future.result(3)["delivery"] == "confirmed"
        changed = {**value, "response_channel": {**value["response_channel"], "account": "changed", "subject": "Segundo assunto"}}
        second = pool.submit(lab.gateway.handle, "notification.submit", changed)
        thread = lab.client.wait_calls("turn/start", 2)[1]["threadId"]
        lab.client.item(thread, "Outra resposta"); lab.client.complete(thread)
        assert second.result(3)["delivery"] == "confirmed"
    assert [name for name, args in requests] == ["original", "changed"]
    assert requests[0][1]["request_id"] != requests[1][1]["request_id"]
    assert [args["subject"] for name, args in requests] == ["Primeiro assunto", "Segundo assunto"]
    assert all(args["request_id"].startswith("gateway:") and args["request_id"].endswith(":delivery") for name, args in requests)


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
    attempted = threading.Event()
    def fail(*args):
        attempts.append(args); attempted.set()
        return "failed"
    lab.gateway._notification_send = fail
    result = lab.gateway.handle("notification.submit", event(destination="unknown"))
    assert result["execution"] == "not_started"
    assert attempted.wait(3)
    lab.gateway._notification_sync(); lab.gateway._notification_sync()
    assert len(attempts) == 1


def test_detaching_rpc_during_smtp_releases_waiter_before_delivery_finishes(lab, monkeypatch):
    from onmyoji_daemon.mcp.email.service import EmailService
    enable(lab)
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
        value = event(); value["response_channel"] = {"type": "email", "value": "fixture@example.com", "account": "fixture", "subject": "Fixture"}
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
    path = log.directory / ("agent-gateway-" + at[:10] + ".jsonl")
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


def test_invalid_transport_frames_are_logged_without_payload_or_agent(lab):
    enable(lab, watch=False)
    stopped = threading.Event()
    server = RpcServer("127.0.0.1", 0, "fixture", lab.gateway.handle, long_methods=("notification.submit",), on_rejection=lab.gateway._notification_transport_rejected)
    receiver = threading.Thread(target=server.serve_forever, args=(stopped,), daemon=True); receiver.start()
    try:
        for raw in (b'{"token":"private-fixture", broken}\n', b'\xff\n'):
            with socket.create_connection(("127.0.0.1", server.port), timeout=3) as client:
                client.sendall(raw)
                response = json.loads(client.makefile("rb").readline())
                assert response["error_code"] == "invalid_json"
        lab.gateway.notification_log.flush()
        logged = "".join(path.read_text(encoding="utf-8") for path in lab.gateway.notification_log.directory.glob("*.jsonl"))
        assert "transport_rejected" in logged and "private-fixture" not in logged
        assert lab.gateway.database.execute("SELECT COUNT(*) FROM execution_turns").fetchone() == (0,)
    finally:
        stopped.set(); server.close(); receiver.join(2)


def test_scheduler_notification_traverses_supervisor_and_shared_gateway(lab):
    from datetime import timedelta
    from types import SimpleNamespace
    from onmyoji_daemon.supervisor import Supervisor
    from onmyoji_daemon.task_scheduler.service import TaskScheduler
    from onmyoji_daemon.task_scheduler.calendar import instant, iso
    enable(lab)
    gateway = lab.gateway
    supervisor = Supervisor(gateway.settings.root)
    service_server = RpcServer("127.0.0.1", 0, "child-fixture", gateway.handle, long_methods=("notification.submit",))
    managed = supervisor.services["telegram"]
    managed.process = SimpleNamespace(poll=lambda: None, pid=1)
    managed.port, managed.token = service_server.port, "child-fixture"
    supervisor._endpoint()
    supervisor_thread = threading.Thread(target=supervisor.rpc.serve_forever, args=(supervisor.stop_event,), daemon=True)
    service_thread = threading.Thread(target=service_server.serve_forever, args=(gateway.stop_event,), daemon=True)
    supervisor_thread.start(); service_thread.start()
    now = instant("2026-11-10T12:00:00Z")
    scheduler = TaskScheduler(gateway.settings.root, gateway.settings.root / "configs" / "daemon" / "services" / "task-scheduler", workspace=gateway.settings.project, clock=lambda: now)
    try:
        envelope = event("reuse"); del envelope["event_id"]
        definition = {"schema_version": 1, "name": "RPC integrado", "enabled": True, "rule": {"type": "once", "at": iso(now - timedelta(seconds=1))}, "action": {"type": "notification", "event": envelope}}
        created = scheduler.handle("task.create", {"definition": definition})
        scheduler.changed.clear(); scheduler.tick()
        thread = lab.client.wait_calls("turn/start")[0]["threadId"]
        assert call("127.0.0.1", supervisor.rpc.port, supervisor.token, "notification.status")["enabled"]
        lab.client.item(thread, "Resposta do fluxo completo"); lab.client.complete(thread)
        assert scheduler.changed.wait(3)
        history = scheduler.handle("task.history", {"id": created["id"]})["items"]
        assert len(history) == 1 and history[0]["state"] == "completed"
        assert history[0]["event_id"] == history[0]["result"]["event_id"]
        assert history[0]["result"]["delivery"] == "confirmed"
        assert len(lab.client.wait_calls("turn/start")) == 1
    finally:
        scheduler.close(); supervisor.stop_event.set(); supervisor.rpc.close()
        gateway.stop_event.set(); service_server.close()
        supervisor_thread.join(2); service_thread.join(2)


def test_terminal_receipt_and_journal_commit_atomically(lab, monkeypatch):
    enable(lab, watch=False)
    gateway = lab.gateway
    value = event()
    receipt = {"event_id": value["event_id"], "received_at": "2026-11-10T12:00:00.000Z", "envelope": value}
    identifier = gateway.store.enqueue("9", gateway._generation("9"), "fixture", [], {"notification": value}, origin="event", receipt=receipt)
    execution = gateway.store.reserve("9", 0, identifier, 30)
    gateway.store.save_terminal_output(execution, "failed", None, "final", "text", False)
    original = gateway.notification_log.record
    def fail(stage, **values):
        if stage == "terminal":
            raise OSError("fixture journal unavailable")
        return original(stage, **values)
    monkeypatch.setattr(gateway.notification_log, "record", fail)
    with pytest.raises(OSError):
        gateway._notification_sync()
    assert gateway.database.execute("SELECT execution_state FROM notification_receipts WHERE inbox_id=?", (identifier,)).fetchone() == (None,)
    monkeypatch.setattr(gateway.notification_log, "record", original)
    gateway._notification_sync()
    assert gateway.database.execute("SELECT execution_state FROM notification_receipts WHERE inbox_id=?", (identifier,)).fetchone() == ("failed",)
    records = [json.loads(row[0])["stage"] for row in gateway.database.execute("SELECT payload FROM notification_log_journal")]
    assert records.count("terminal") == 1


def test_pending_incident_recovers_after_terminal_receipt_was_already_saved(lab):
    enable(lab, watch=False)
    gateway = lab.gateway
    gateway.notification_configuration.configure({"incident": {"type": "telegram", "value": "9"}})
    value = event()
    receipt = {"event_id": value["event_id"], "received_at": "2026-11-10T12:00:00.000Z", "envelope": value}
    identifier = gateway.store.enqueue("9", gateway._generation("9"), "fixture", [], {"notification": value}, origin="event", receipt=receipt)
    with gateway.database:
        gateway.database.execute("UPDATE notification_receipts SET execution_state='failed',reason='fixture' WHERE inbox_id=?", (identifier,))
    attempts = []
    gateway._notification_send = lambda *args: attempts.append(args) or "confirmed"
    gateway._notification_sync(); gateway._notification_sync()
    assert len(attempts) == 1


def test_event_only_settings_do_not_require_disabled_telegram_totp(lab):
    from onmyoji_daemon.telegram import Settings
    gateway = lab.gateway
    gateway.notification_configuration.configure({"telegram_receiver_enabled": False, "enabled": True})
    path = gateway.settings.data_dir / "config.toml"
    with path.open("a", encoding="utf-8") as stream:
        stream.write('\n[totp]\nenabled=true\n')
    loaded = Settings.load(gateway.settings.root, gateway.settings.data_dir)
    assert loaded.app_server_enabled
