"""Notificações úteis preservam o contrato sem consultar estado por cada delta."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon import telegram_execution
from onmyoji_daemon.telegram import AppServerTurn

from .test_telegram_gateway_async import FakeCodex, lab


@pytest.mark.parametrize("method", [
    "item/agentMessage/delta", "item/reasoning/textDelta",
    "item/commandExecution/outputDelta", "item/started",
    "thread/tokenUsage/updated", "thread/status/changed", "account/updated",
    "codex/event/exec_command_output_delta", "codex/event/agent_message_delta",
])
def test_unused_notifications_never_bind_a_turn_or_query_persistent_state(lab, method):
    gateway = lab.gateway
    active = AppServerTurn(9, "thread", threading.Event(), process_epoch="epoch")
    gateway.app_turns[active.thread_id] = active
    queries = []
    gateway.database.set_trace_callback(queries.append)
    try:
        for sequence in range(64):
            gateway._app_event(method, {"threadId": "thread", "turnId": "turn", "delta": "x"},
                               {"process_epoch": "epoch", "sequence": sequence})
    finally:
        gateway.database.set_trace_callback(None)
        gateway.app_turns.clear()

    assert queries == []
    assert active.turn_id is None
    assert not active.completed.is_set()
    assert lab.api.sent == []


@pytest.mark.parametrize("method", ["turn/started", "item/completed", "turn/completed"])
def test_relevant_notifications_preserve_identity_and_only_validate_generation_for_items(lab, monkeypatch, method):
    gateway = lab.gateway
    active = AppServerTurn(9, "thread", threading.Event(), process_epoch="epoch")
    gateway.app_turns[active.thread_id] = active
    checked_generations = []

    def generation(conversation):
        checked_generations.append(conversation)
        return 0

    monkeypatch.setattr(gateway, "_generation", generation)
    params = {"threadId": "thread", "turnId": "turn",
              "turn": {"id": "turn", "status": "completed"},
              "item": {"id": "final", "type": "agentMessage", "phase": "final_answer", "text": "Resposta útil"}}
    try:
        gateway._app_event(method, params, {"process_epoch": "epoch"})
        assert active.turn_id == "turn"
        assert active.completed.is_set() == (method == "turn/completed")
        assert active.final_text == ("Resposta útil" if method == "item/completed" else "")
        assert checked_generations == (["9"] if method == "item/completed" else [])
    finally:
        gateway.app_turns.clear()


def test_old_generation_cannot_publish_a_completed_item(lab, monkeypatch):
    gateway = lab.gateway
    active = AppServerTurn(9, "thread", threading.Event(), process_epoch="epoch", generation=0)
    gateway.app_turns[active.thread_id] = active
    monkeypatch.setattr(gateway, "_generation", lambda _conversation: 1)
    try:
        gateway._app_event("item/completed", {"threadId": "thread", "turnId": "turn", "item": {
            "id": "old", "type": "agentMessage", "phase": "final_answer", "text": "Resposta antiga",
        }}, {"process_epoch": "epoch"})
    finally:
        gateway.app_turns.clear()

    assert active.final_text == ""
    assert lab.api.sent == []


@pytest.mark.parametrize("method,target", [
    ("account/login/completed", "_login_event"),
    ("transport/late_response", "_late_input_response"),
])
def test_login_and_local_late_confirmation_do_not_require_turn_correlation(lab, monkeypatch, method, target):
    gateway = lab.gateway
    observed = []
    monkeypatch.setattr(gateway, target, lambda *args: observed.append(args))

    def unexpected(*_args):
        pytest.fail("Login e confirmação tardia não pertencem à correlação de notificações de turno.")

    monkeypatch.setattr(gateway, "_correlate", unexpected)
    params, event = {"id": 7}, {"process_epoch": "epoch", "transport_generated": True}
    gateway._app_event(method, params, event)

    assert observed == ([(method, params, event)] if target == "_login_event" else [(params, event)])


def test_transport_loss_still_releases_login_and_completes_active_wait(lab, monkeypatch):
    gateway = lab.gateway
    active = AppServerTurn(9, "thread", threading.Event(), process_epoch="epoch")
    gateway.app_turns[active.thread_id] = active
    login_events = []
    monkeypatch.setattr(gateway, "_login_event", lambda *args: login_events.append(args))
    params, event = {"reason": "test_disconnect"}, {"process_epoch": "epoch", "transport_generated": True}
    try:
        gateway._app_event("transport/lost", params, event)
    finally:
        gateway.app_turns.clear()

    assert login_events == [("transport/lost", params, event)]
    assert active.status == "lost" and active.completed.is_set()


def test_gateway_selects_only_consumed_remote_notifications(lab, monkeypatch):
    gateway = lab.gateway
    gateway.app_server = None
    created = []

    def client(settings, **options):
        created.append((settings, options))
        return FakeCodex()

    monkeypatch.setattr(telegram_execution, "CodexAppServer", client)
    monkeypatch.setattr(telegram_execution, "recover_process", lambda *_args: SimpleNamespace(process_epoch=None))
    monkeypatch.setattr(telegram_execution, "record_process", lambda *_args: None)

    gateway._app_client()

    assert created == [(gateway.settings, {"notification_methods": frozenset({
        "account/login/completed", "turn/started", "turn/completed", "item/completed",
    })})]
