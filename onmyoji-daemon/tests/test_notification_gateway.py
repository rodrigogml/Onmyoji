"""Aceite do ingresso usando o mesmo laboratório de execução do Telegram."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import threading

import pytest

from .test_telegram_gateway_async import lab, submit
from onmyoji_daemon.agent_notification.contract import uuid7
from onmyoji_daemon.agent_notification.logging import timestamp
from onmyoji_daemon.rpc import RpcError
from onmyoji_daemon.telegram_persistence import GatewayPersistenceError


def event(policy="new", destination="9"):
    return {"schema_version": 1, "event_id": uuid7(), "prompt": "Evento de laboratório", "response_channel": {"type": "telegram", "value": destination}, "thread_policy": policy}


def enable(lab, *, watch=True):
    lab.gateway.notification_configuration.configure({"enabled": True})
    lab.gateway._channel_register("9", "Owner", 9)
    # O laboratório não abre os receptores externos de Gateway.start(), mas
    # conserva o mesmo watcher operacional de notificações do serviço real.
    if watch and not getattr(lab, "notification_watcher", None):
        lab.notification_watcher = threading.Thread(target=lab.gateway._notification_watch, daemon=True)
        lab.notification_watcher.start()


def test_notification_reuses_gateway_and_reprocesses_same_uuid(lab):
    enable(lab)
    value = event()
    with ThreadPoolExecutor() as pool:
        first = pool.submit(lab.gateway.handle, "notification.submit", value)
        thread = lab.client.wait_calls("turn/start")[0]["threadId"]
        assert lab.gateway.handle("notification.status", {})["enabled"]
        lab.client.item(thread, "Resposta do evento")
        lab.client.complete(thread)
        assert first.result(3)["delivery"] == "confirmed"
        second = pool.submit(lab.gateway.handle, "notification.submit", value)
        second_thread = lab.client.wait_calls("turn/start", 2)[1]["threadId"]
        assert second_thread != thread
        lab.client.item(second_thread, "Segunda resposta")
        lab.client.complete(second_thread)
        result = second.result(3)
        assert result["event_id"] == value["event_id"] and result["execution"] == "completed"
    assert len(lab.gateway.database.execute("SELECT * FROM notification_receipts").fetchall()) == 2
    assert all(params["dynamicTools"][0]["name"] == "telegram_gateway" for name, params in lab.client.calls if name == "thread/start")
    assert lab.gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == (second_thread,)


def test_failed_new_keeps_old_association_and_delivery_failure_keeps_new(lab):
    enable(lab)
    lab.gateway._generation("9")
    with lab.gateway.database:
        lab.gateway.database.execute("UPDATE conversations SET codex_thread_id='old' WHERE chat_id='9'")
    with ThreadPoolExecutor() as pool:
        failed = pool.submit(lab.gateway.handle, "notification.submit", event())
        thread = lab.client.wait_calls("turn/start")[0]["threadId"]
        lab.client.complete(thread, "failed")
        assert failed.result(3)["execution"] == "failed"
        assert lab.gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == ("old",)
        from onmyoji_daemon.telegram_delivery import DeliveryUncertain
        def unavailable(*args):
            raise DeliveryUncertain("delivery failed")
        lab.gateway._deliver_terminal = unavailable
        success = pool.submit(lab.gateway.handle, "notification.submit", event())
        thread = lab.client.wait_calls("turn/start", 2)[1]["threadId"]
        lab.client.item(thread, "Resposta")
        lab.client.complete(thread)
        result = success.result(3)
        assert result["delivery"] == "uncertain" and result["reason"] == "delivery_uncertain"
        assert lab.gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == (thread,)


def test_priority_and_busy_destination_do_not_block_other_destinations(lab):
    enable(lab)
    lab.gateway.work = threading.BoundedSemaphore(1)
    gate = threading.Event()
    lab.gateway.work.acquire()
    value = event()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(lab.gateway.handle, "notification.submit", value)
        # Recepção persistida fornece uma barreira determinística.
        original = lab.gateway.store.candidates
        def candidates():
            result = original()
            if "9" in result:
                gate.set()
            return result
        lab.gateway.store.candidates = candidates
        assert gate.wait(3)
        submit(lab, "Telegram prioritário", conversation="10")
        lab.gateway.work.release()
        first = lab.client.wait_calls("turn/start")[0]["threadId"]
        assert lab.gateway._slots["10"].active.thread_id == first
        lab.client.complete(first)
        second = lab.client.wait_calls("turn/start", 2)[1]["threadId"]
        lab.client.item(second, "Resposta do evento")
        lab.client.complete(second)
        assert future.result(3)["execution"] == "completed"


def test_event_is_non_preemptive_when_telegram_arrives_after_dispatch(lab):
    enable(lab)
    lab.gateway.work = threading.BoundedSemaphore(1)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(lab.gateway.handle, "notification.submit", event())
        first = lab.client.wait_calls("turn/start")[0]["threadId"]
        assert lab.gateway._slots["9"].active.thread_id == first
        submit(lab, "Telegram posterior", conversation="10")
        assert len(lab.client.wait_calls("turn/start")) == 1
        lab.client.item(first, "Resposta do evento"); lab.client.complete(first)
        assert future.result(3)["status"] == "completed"
        second = lab.client.wait_calls("turn/start", 2)[1]["threadId"]
        assert lab.gateway._slots["10"].active.thread_id == second
        lab.client.complete(second)


def test_shared_inbox_enforces_fifty_per_destination_and_1024_global(lab):
    gateway = lab.gateway
    generation = gateway._generation("limited")
    for index in range(50):
        gateway.store.enqueue("limited", generation, str(index), [], {})
    with pytest.raises(GatewayPersistenceError):
        gateway.store.enqueue("limited", generation, "overflow", [], {})
    for index in range(974):
        destination = "global-" + str(index // 49)
        gateway.store.enqueue(destination, 0, str(index), [], {})
    assert gateway.database.execute("SELECT COUNT(*) FROM inbox_items WHERE state='pending'").fetchone() == (1024,)
    with pytest.raises(GatewayPersistenceError):
        gateway.store.enqueue("global-last", 0, "overflow", [], {})


def test_shared_scheduler_is_fifo_inside_each_priority_class(lab):
    gateway = lab.gateway
    gateway.store.enqueue("event-first", 0, "1", [], {}, origin="event")
    gateway.store.enqueue("event-second", 0, "2", [], {}, origin="event")
    gateway.store.enqueue("telegram-first", 0, "3", [], {}, origin="telegram")
    gateway.store.enqueue("telegram-second", 0, "4", [], {}, origin="telegram")
    assert gateway.store.candidates() == ["telegram-first", "telegram-second", "event-first", "event-second"]


def test_invalid_destination_does_not_start_agent_and_disabled_rejects(lab):
    with pytest.raises(RpcError) as caught:
        lab.gateway.handle("notification.submit", event())
    assert caught.value.code == "ingress_disabled"
    enable(lab)
    result = lab.gateway.handle("notification.submit", event(destination="unknown"))
    assert result["execution"] == "not_started"
    assert result["delivery"] == "not_attempted"
    assert result["reason"] == "destination_unavailable"
    assert not lab.client.calls


def test_diagnosis_checks_selected_accounts_without_sending(lab, monkeypatch):
    enable(lab)
    from onmyoji_daemon.mcp.email.service import EmailService
    from onmyoji_daemon.mcp.email.contracts import EmailError
    lab.gateway.notification_configuration.configure({"incident": {"type": "email", "value": "incident@example.com", "account": "removed", "subject": "Incidente"}})
    def unavailable(*args):
        raise EmailError("account_unavailable", "fixture")
    monkeypatch.setattr(EmailService, "account", unavailable)
    monkeypatch.setattr(EmailService, "invoke", lambda *args: pytest.fail("diagnosis must not send"))
    checks = lab.gateway.handle("notification.diagnose", {})["checks"]
    assert checks["contract_valid"] and checks["http_port"] == 39392 and checks["email_account_per_event"]
    assert not checks["incident_available"] and checks["incident_reason"] == "account_unavailable"
    assert not lab.client.calls


def test_log_recovery_is_idempotent_and_retention_is_confined(lab):
    log = lab.gateway.notification_log
    log.record("fixture", event_id=uuid7())
    log.flush(); log.flush()
    files = list(log.directory.glob("*.jsonl"))
    assert len(files) == 1 and len(files[0].read_text(encoding="utf-8").splitlines()) == 1
    old = log.directory / "agent-gateway-2000-01-01.jsonl"
    unrelated = log.directory / "keep.jsonl"
    old.write_text("{}", encoding="utf-8"); unrelated.write_text("{}", encoding="utf-8")
    log.purge()
    assert not old.exists() and unrelated.exists()


def test_logs_capture_input_output_error_uuid_and_utc_without_transport_secrets(lab):
    enable(lab)
    successful = event()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(lab.gateway.handle, "notification.submit", successful)
        thread = lab.client.wait_calls("turn/start")[0]["threadId"]
        lab.client.item(thread, "Saída auditável"); lab.client.complete(thread)
        assert future.result(3)["status"] == "completed"
    failed = event(destination="unknown")
    assert lab.gateway.handle("notification.submit", failed)["status"] == "failed"
    lab.gateway.notification_log.flush()
    entries = [json.loads(line) for path in lab.gateway.notification_log.directory.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines()]
    received = next(entry for entry in entries if entry.get("stage") == "received" and entry.get("event_id") == successful["event_id"])
    terminal = next(entry for entry in entries if entry.get("stage") == "terminal" and entry.get("event_id") == successful["event_id"])
    failure = next(entry for entry in entries if entry.get("stage") == "terminal" and entry.get("event_id") == failed["event_id"])
    assert received["envelope"]["prompt"] == successful["prompt"] and received["at"].endswith("Z")
    assert terminal["output"] == "Saída auditável"
    assert failure["reason"] == "destination_unavailable"
    serialized = json.dumps(entries, ensure_ascii=False).casefold()
    assert all(secret not in serialized for secret in ('"authorization"', '"headers"', '"token"'))


@pytest.mark.parametrize("policy", ["reuse", "new"])
def test_telegram_and_events_share_channel_thread_tools_and_input_pipeline(lab, policy):
    enable(lab)
    submit(lab, "Mensagem Telegram inicial")
    first = lab.client.wait_calls("turn/start")[0]["threadId"]
    lab.client.item(first, "Resposta inicial"); lab.client.complete(first)
    lab.api.wait_text("Resposta inicial")
    with ThreadPoolExecutor() as pool:
        future = pool.submit(lab.gateway.handle, "notification.submit", event(policy))
        params = lab.client.wait_calls("turn/start", 2)[1]
        thread = params["threadId"]
        assert (thread == first) == (policy == "reuse")
        assert lab.gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == (first,)
        envelope = json.loads(params["input"][0]["text"])
        assert envelope["telegram_turn"]["content"] == "Evento de laboratório"
        active = lab.gateway._slots["9"].active
        assert active.channel_type == "telegram" and active.chat_id == 9
        lab.gateway._tool_local.event = {"process_epoch": lab.client.process_epoch}
        try:
            assert lab.gateway._tool_active({"threadId": thread, "turnId": active.turn_id}) is active
        finally:
            lab.gateway._tool_local.event = None
        lab.client.item(thread, "Resposta ao evento"); lab.client.complete(thread)
        assert future.result(3)["delivery"] == "confirmed"
    submit(lab, "Mensagem Telegram posterior")
    last = lab.client.wait_calls("turn/start", 3)[2]
    assert last["threadId"] == thread
    lab.client.complete(thread)
    catalogs = lab.client.wait_calls("thread/start")
    assert all(params["serviceName"] == "onmyoji_telegram" for params in catalogs)
    assert all(params["dynamicTools"][0]["name"] == "telegram_gateway" for params in catalogs)


def test_event_honors_channel_audio_preference(lab):
    from dataclasses import replace
    enable(lab)
    lab.gateway.settings = replace(lab.gateway.settings, voice_enabled=True)
    lab.gateway._set_reply_mode("9", "audio")
    def synthesize(text, path):
        path.write_bytes(b"fixture audio")
    lab.gateway._tts = synthesize
    with ThreadPoolExecutor() as pool:
        future = pool.submit(lab.gateway.handle, "notification.submit", event())
        thread = lab.client.wait_calls("turn/start")[0]["threadId"]
        assert lab.gateway._slots["9"].active.reply_mode == "audio"
        lab.client.item(thread, "Resposta em áudio"); lab.client.complete(thread)
        assert future.result(3)["delivery"] == "confirmed"
    assert len(lab.api.files) == 1 and lab.api.files[0]["method"] == "sendVoice"


def test_empty_agent_result_fails_without_delivery_or_thread_replacement(lab):
    enable(lab)
    lab.gateway._generation("9")
    with lab.gateway.database:
        lab.gateway.database.execute("UPDATE conversations SET codex_thread_id='old' WHERE chat_id='9'")
    with ThreadPoolExecutor() as pool:
        future = pool.submit(lab.gateway.handle, "notification.submit", event())
        thread = lab.client.wait_calls("turn/start")[0]["threadId"]
        lab.client.complete(thread)
        result = future.result(3)
    assert result["status"] == "failed" and result["reason"] == "empty_agent_result"
    assert result["delivery"] == "not_attempted"
    assert lab.gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == ("old",)
    assert not lab.api.sent
