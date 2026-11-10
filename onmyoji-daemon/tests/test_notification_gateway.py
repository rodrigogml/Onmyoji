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
        assert success.result(3)["delivery"] == "uncertain"
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
        lab.client.complete(second)
        assert future.result(3)["execution"] == "completed"


def test_invalid_destination_does_not_start_agent_and_disabled_rejects(lab):
    with pytest.raises(RpcError) as caught:
        lab.gateway.handle("notification.submit", event())
    assert caught.value.code == "ingress_disabled"
    enable(lab)
    result = lab.gateway.handle("notification.submit", event(destination="unknown"))
    assert result["execution"] == "not_started"
    assert result["delivery"] == "not_attempted"
    assert not lab.client.calls


def test_log_recovery_is_idempotent_and_retention_is_confined(lab):
    log = lab.gateway.notification_log
    log.record("fixture", event_id=uuid7())
    log.flush(); log.flush()
    files = list(log.directory.glob("*.jsonl"))
    assert len(files) == 1 and len(files[0].read_text(encoding="utf-8").splitlines()) == 1
    old = log.directory / "notification-2000-01-01.jsonl"
    unrelated = log.directory / "keep.jsonl"
    old.write_text("{}", encoding="utf-8"); unrelated.write_text("{}", encoding="utf-8")
    log.purge()
    assert not old.exists() and unrelated.exists()


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
