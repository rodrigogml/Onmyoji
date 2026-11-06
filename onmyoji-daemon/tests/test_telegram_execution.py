"""Contratos de aceitação, recuperação e isolamento do atendimento assíncrono."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon.telegram import AppServerTurn, ConversationExecution, Gateway, Settings
from onmyoji_daemon.codex_app_server import CodexProtocolError
from onmyoji_daemon.telegram_delivery import DeliveryOwner, DeliverySuppressed, DeliveryUncertain
from onmyoji_daemon.telegram_persistence import GatewayPersistenceError, GatewayStore


@pytest.fixture
def gateway(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    config = root / "configs"; config.mkdir(parents=True); workspace.mkdir()
    (config / "onmyoji-system.toml").write_text('[codex]\nexecutable="codex"\nproject_directory=' + json.dumps(workspace.as_posix()) + '\nmodel="probe"\n', encoding="utf-8")
    daemon = config / "daemon"; daemon.mkdir()
    (daemon / "daemon.toml").write_text('[app_server]\nenabled=true\n', encoding="utf-8")
    data = daemon / "services" / "telegram"; data.mkdir(parents=True)
    (data / "config.toml").write_text('schema_version=1\n[telegram]\nvault_profile="fixture"\ntoken_entry="fixture"\n', encoding="utf-8")
    instance = Gateway(Settings.load(root, data))
    sent, deleted = [], []
    class Api:
        def send(self, chat, text, **values):
            sent.append((chat, text, values)); return {"message_id": len(sent)}
        def delete(self, chat, message): deleted.append((chat, message))
        def typing(self, *_args, **_kwargs): pass
    instance.api = Api()
    instance._test_sent, instance._test_deleted = sent, deleted
    yield instance
    instance.stop_event.set()
    with instance.workers_changed:
        instance.workers_changed.notify_all()


def input_item(gateway, text="entrada", *, route="auto", source=None):
    generation = gateway._generation("9")
    identifier = gateway.store.enqueue("9", generation, text, [], {"source_trust": "OWNER"}, source=source, route=route)
    return gateway.store.next_input("9", generation), identifier


def active_slot(gateway):
    item, identifier = input_item(gateway)
    execution = gateway.store.reserve("9", 0, identifier, 30)
    active = AppServerTurn(9, "thread", threading.Event(), turn_id="turn", conversation_id="9", process_epoch="epoch", execution_id=execution, inbox_id=identifier)
    slot = ConversationExecution(execution, "9", 0, item, active=active)
    gateway.app_turns["thread"] = active; gateway._slots["9"] = slot
    gateway.store.bind(execution, "epoch", "thread", "turn")
    operation = gateway.store.operation(identifier, execution, "turn/start", None)
    gateway.store.operation_result(operation, "accepted")
    return slot


def test_replay_has_one_inbox_and_checkpoint_follows_durable_ingress(gateway):
    update = {"update_id": 50, "message": {"text": "dados"}}
    source, inserted = gateway.store.ingest("bot", update, "9", 0)
    assert inserted and gateway.store.checkpoint("bot") == 51
    assert gateway.store.ingest("bot", update, "9", 0) == (source, False)
    first = gateway.store.enqueue("9", 0, "dados", [], {}, source=source)
    assert first and gateway.store.enqueue("9", 0, "dados", [], {}, source=source) is None
    assert len(gateway.store.ingress_pending()) == 1


def test_restart_before_transport_operation_can_requeue_but_after_write_is_uncertain(gateway):
    item, identifier = input_item(gateway)
    execution = gateway.store.reserve("9", 0, identifier, 30)
    gateway.store.recover()
    assert gateway.store.next_input("9", 0)["id"] == identifier
    execution = gateway.store.reserve("9", 0, identifier, 30)
    operation = gateway.store.operation(identifier, execution, "turn/start", None)
    gateway.store.recover()
    assert gateway.store.blocked("9")
    assert gateway.store.next_input("9", 0) is None
    assert gateway.database.execute("SELECT state FROM input_operations WHERE operation_id=?", (operation,)).fetchone() == ("uncertain",)


def test_legacy_running_is_not_replayed_by_migration(gateway):
    gateway.database.execute("INSERT INTO inbox_items(id,chat_id,generation,text,attachment_ids,turn_context,state,created_at) VALUES ('old','9',0,'ação','[]','{}','running',0)")
    gateway.database.execute("DELETE FROM gateway_schema_migrations"); gateway.database.commit()
    # A cópia mínima representa banco anterior à introdução das tabelas novas.
    legacy = sqlite3.connect(":memory:")
    legacy.execute("CREATE TABLE inbox_items(id TEXT PRIMARY KEY,chat_id TEXT,generation INTEGER,text TEXT,attachment_ids TEXT,turn_context TEXT,state TEXT,created_at REAL)")
    legacy.execute("INSERT INTO inbox_items VALUES ('old','9',0,'ação','[]','{}','running',0)"); legacy.commit()
    store = GatewayStore(legacy, threading.RLock())
    assert legacy.execute("SELECT state FROM inbox_items").fetchone() == ("uncertain_legacy",)
    assert store.blocked("9")


def test_reserve_enforces_single_conversation_and_current_generation(gateway):
    item, identifier = input_item(gateway)
    gateway.store.reserve("9", 0, identifier, 30)
    other, other_id = input_item(gateway, "segunda")
    with pytest.raises(sqlite3.IntegrityError):
        gateway.store.reserve("9", 0, other_id, 30)
    with pytest.raises(GatewayPersistenceError):
        gateway.store.reserve("9", 1, other_id, 30)


def test_external_send_timeout_is_never_retried_automatically(gateway):
    calls = []
    owner = DeliveryOwner("9", gateway._generation("9"))
    def send():
        calls.append("efeito"); raise TimeoutError("confirmação perdida")
    for _ in range(2):
        with pytest.raises(DeliveryUncertain):
            gateway.deliveries.effect(owner, "same", "final", "item", {}, send)
    assert calls == ["efeito"]
    assert gateway.database.execute("SELECT state FROM gateway_deliveries WHERE delivery_key='same'").fetchone() == ("uncertain",)


def test_confirmed_send_reuses_receipt_and_generation_reset_suppresses_unsent(gateway):
    owner = DeliveryOwner("9", gateway._generation("9"))
    calls = []
    def send(): calls.append(1); return {"message_id": 7}
    assert gateway.deliveries.effect(owner, "a", "final", "a", {}, send)["message_id"] == 7
    assert gateway.deliveries.effect(owner, "a", "final", "a", {}, send)["already_sent"]
    gateway._new_conversation("9")
    with pytest.raises(DeliverySuppressed):
        gateway.deliveries.effect(owner, "b", "final", "b", {}, send)
    assert calls == [1]


def test_delivery_without_message_id_is_uncertain_and_preserves_thoughts(gateway):
    owner = DeliveryOwner("9", gateway._generation("9"))
    with pytest.raises(DeliveryUncertain):
        gateway.deliveries.effect(owner, "missing", "final", "x", {}, lambda: {})
    active = AppServerTurn(9, "t", threading.Event(), conversation_id="9")
    active.thought_ids = [1]
    gateway._cleanup_thoughts(active)
    assert gateway._test_deleted == []


@pytest.mark.parametrize("share,delete", [(False, False), (False, True), (True, False), (True, True)])
def test_thought_preferences_and_terminal_cleanup_are_scoped(gateway, share, delete):
    gateway._generation("9")
    gateway.database.execute("UPDATE conversations SET share_thoughts=?,delete_thoughts=? WHERE chat_id='9'", (share, delete)); gateway.database.commit()
    active = AppServerTurn(9, "thread", threading.Event(), conversation_id="9", turn_id="turn")
    gateway.app_turns["thread"] = active
    for identifier in ("a", "a", "b"):
        gateway._app_notification("item/completed", {"threadId": "thread", "turnId": "turn", "item": {"id": identifier, "type": "agentMessage", "phase": "commentary", "text": "mesmo texto"}})
    assert len(gateway._test_sent) == (2 if share else 0)
    gateway._cleanup_thoughts(active)
    assert not gateway._test_deleted
    gateway._app_notification("turn/completed", {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}})
    active.terminal_announced = True
    gateway._cleanup_thoughts(active); gateway._cleanup_thoughts(active)
    assert len(gateway._test_deleted) == (2 if share and delete else 0)


def test_wrong_epoch_turn_and_unknown_child_cannot_finish_or_use_tools(gateway):
    slot = active_slot(gateway)
    event = {"process_epoch": "epoch"}
    gateway._app_event("turn/completed", {"threadId": "thread", "turn": {"id": "other", "status": "completed"}}, event)
    gateway._app_event("turn/completed", {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}}, {"process_epoch": "old"})
    assert not slot.active.completed.is_set()
    with pytest.raises(RuntimeError):
        gateway._app_request_event("item/tool/call", {"threadId": "child", "turnId": "turn", "namespace": "telegram_gateway", "tool": "send_message", "arguments": {"text": "indevido"}}, event)
    assert gateway._test_sent == []


def test_new_generation_does_not_release_old_resources_until_terminal(gateway):
    slot = active_slot(gateway)
    gateway.app_server = SimpleNamespace(request=lambda *_args: {}, running=lambda: True)
    gateway._new_conversation("9")
    assert gateway._generation("9") == 1 and slot.active.suppressed
    assert ("9", 0) in gateway._generation_cleanup and not slot.active.completed.is_set()
    gateway._app_event("turn/completed", {"threadId": "thread", "turn": {"id": "turn", "status": "interrupted"}}, {"process_epoch": "epoch"})
    assert slot.active.completed.is_set()
    gateway._finish_resources(slot.active)
    assert ("9", 0) not in gateway._generation_cleanup


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "uncertain"])
def test_steering_race_distinguishes_acceptance_rejection_and_unknown(gateway, outcome):
    slot = active_slot(gateway)
    item, identifier = input_item(gateway, "correção", route="steer")
    def request(method, params):
        assert method == "turn/steer" and params["expectedTurnId"] == "turn"
        if outcome == "rejected": raise CodexProtocolError("sem turno", kind="rejected")
        if outcome == "uncertain": raise CodexProtocolError("ACK perdido", kind="uncertain")
        slot.active.completed.set()
        return {"turnId": "turn"}
    gateway.app_server = SimpleNamespace(request=request)
    gateway._steer_input(slot, item)
    state, route = gateway.database.execute("SELECT state,route FROM inbox_items WHERE id=?", (identifier,)).fetchone()
    assert state == {"accepted": "accepted", "rejected": "pending", "uncertain": "uncertain"}[outcome]
    if outcome == "rejected": assert route == "queue"
    assert gateway.store.blocked("9") == (outcome == "uncertain")


def test_worker_rechecks_queue_when_input_arrives_at_empty_exit(gateway, monkeypatch):
    entered, proceed, executed = threading.Event(), threading.Event(), threading.Event()
    original = gateway.store.next_input
    first = [True]
    def next_input(conversation, generation):
        if first[0]:
            first[0] = False; entered.set(); assert proceed.wait(5); return None
        return original(conversation, generation)
    monkeypatch.setattr(gateway.store, "next_input", next_input)
    monkeypatch.setattr(gateway, "_turn", lambda *_args: executed.set())
    gateway.workers.add("9")
    worker = threading.Thread(target=gateway._worker, args=("9",)); worker.start()
    assert entered.wait(5)
    gateway.store.enqueue("9", gateway._generation("9"), "entrada concorrente", [], {})
    proceed.set()
    with gateway.workers_changed: gateway.workers_changed.notify_all()
    assert executed.wait(5)
    gateway.stop_event.set()
    with gateway.workers_changed: gateway.workers_changed.notify_all()
    worker.join(5); assert not worker.is_alive()


def test_missing_attachment_does_not_execute_reduced_request(gateway):
    item, identifier = input_item(gateway)
    item["attachment_ids"] = '["missing"]'
    with pytest.raises(RuntimeError, match="não está mais disponível"):
        gateway._load_input("9", item)


def test_discard_uncertain_releases_local_block_without_repeating(gateway):
    slot = active_slot(gateway)
    item, identifier = input_item(gateway, "incerta")
    op = gateway.store.operation(identifier, slot.identifier, "turn/steer", "turn")
    gateway.store.operation_result(op, "uncertain", "lost")
    with pytest.raises(GatewayPersistenceError): gateway.store.resolve("9", discard=False)
    gateway.store.resolve("9", discard=True)
    assert not gateway.store.blocked("9")
    assert gateway.database.execute("SELECT state FROM inbox_items WHERE id=?", (identifier,)).fetchone() == ("cancelled",)


def test_retention_preserves_tombstone_and_uncertainty(gateway):
    source, _ = gateway.store.ingest("bot", {"update_id": 1}, "9", 0)
    gateway.store.ingress_state(source, "done")
    gateway.store.purge(now=10**12)
    assert gateway.database.execute("SELECT payload FROM ingress_updates WHERE source_key=?", (source,)).fetchone() == (None,)
    assert gateway.store.ingest("bot", {"update_id": 1}, "9", 0) == (source, False)


def test_tmp_fixture_is_in_repository_tmp(gateway, tmp_path):
    root = Path(__file__).resolve().parents[2]
    assert tmp_path.resolve().is_relative_to(root / "tmp")
    assert gateway.settings.project.is_relative_to(root / "tmp")
