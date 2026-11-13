"""Regressões das fronteiras de envio, terminalidade e correlação de recuperação."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from onmyoji_daemon.telegram_persistence import GatewayPersistenceError, GatewayStore, SCHEMA_VERSION


def make_store(database=None):
    db = database or sqlite3.connect(":memory:")
    db.execute("CREATE TABLE conversations(chat_id TEXT PRIMARY KEY,generation INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL)")
    db.execute("INSERT INTO conversations VALUES ('9',0,0)")
    db.execute("CREATE TABLE inbox_items(id TEXT PRIMARY KEY,chat_id TEXT NOT NULL,generation INTEGER NOT NULL,text TEXT NOT NULL,attachment_ids TEXT NOT NULL,turn_context TEXT NOT NULL DEFAULT '{}',state TEXT NOT NULL,created_at REAL NOT NULL)")
    return GatewayStore(db, threading.RLock())


@pytest.fixture
def store():
    value = make_store()
    yield value
    value.db.close()


def reserve(store, *, bind=True):
    inbox = store.enqueue("9", 0, "pedido de teste", [], {})
    execution = store.reserve("9", 0, inbox, 30)
    if bind:
        store.bind(execution, "epoch", "thread", "turn")
    return inbox, execution


def start(store):
    inbox, execution = reserve(store)
    operation = store.operation(inbox, execution, "turn/start", None)
    return inbox, execution, operation


@pytest.mark.parametrize("state", ["starting", "active", "uncertain"])
def test_restart_requeues_only_provably_unsent_reservation(store, state):
    inbox, execution = reserve(store, bind=state != "starting")
    if state == "uncertain":
        store.db.execute("UPDATE execution_turns SET state='uncertain',result='gateway_restarted' WHERE execution_id=?", (execution,))
        store.db.execute("UPDATE inbox_items SET state='uncertain',failure_reason='gateway_restarted' WHERE id=?", (inbox,))

    store.recover()

    assert store.next_input("9", 0)["id"] == inbox
    assert store.db.execute("SELECT state,result FROM execution_turns WHERE execution_id=?", (execution,)).fetchone() == ("terminal", "not_sent")
    assert store.db.execute("SELECT execution_id,failure_reason FROM inbox_items WHERE id=?", (inbox,)).fetchone() == (None, None)
    assert not store.blocked("9")


@pytest.mark.parametrize("cancelled,old_generation", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize("legacy_uncertain", [False, True])
def test_unsent_recovery_does_not_revive_cancelled_or_old_generation(store, cancelled, old_generation, legacy_uncertain):
    inbox, execution = reserve(store)
    if legacy_uncertain:
        store.db.execute("UPDATE execution_turns SET state='uncertain',result='gateway_restarted' WHERE execution_id=?", (execution,))
        store.db.execute("UPDATE inbox_items SET state='uncertain' WHERE id=?", (inbox,))
    if cancelled:
        store.db.execute("UPDATE execution_turns SET user_cancelled=1 WHERE execution_id=?", (execution,))
    if old_generation:
        store.db.execute("UPDATE conversations SET generation=1 WHERE chat_id='9'")

    store.recover()

    assert store.next_input("9", 0) is None
    assert store.next_input("9", 1) is None
    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("cancelled",)
    assert store.db.execute("SELECT state,result FROM execution_turns WHERE execution_id=?", (execution,)).fetchone() == ("terminal", "cancelled")
    assert not store.blocked("9")


def test_existing_cancelled_inbox_is_not_requeued_by_recovery(store):
    inbox, _ = reserve(store)
    store.db.execute("UPDATE inbox_items SET state='cancelled' WHERE id=?", (inbox,))
    store.recover()
    assert store.next_input("9", 0) is None
    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("cancelled",)


def test_restart_preserves_uncertainty_after_operation_was_recorded(store):
    inbox, execution, operation = start(store)
    store.recover()
    assert store.next_input("9", 0) is None
    assert store.db.execute("SELECT state FROM input_operations WHERE operation_id=?", (operation,)).fetchone() == ("uncertain",)
    assert store.db.execute("SELECT state FROM execution_turns WHERE execution_id=?", (execution,)).fetchone() == ("uncertain",)
    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("uncertain",)
    assert store.blocked("9")


@pytest.mark.parametrize("ingress_state", ["preparing", "uncertain"])
@pytest.mark.parametrize("phase", ["queued", "bound", "sent"])
def test_restart_closes_preparation_handoff_only_after_durable_inbox(store, ingress_state, phase):
    source, _ = store.ingest("test", {"update_id": 70}, "9", 0)
    store.ingress_state(source, ingress_state)
    inbox = store.enqueue("9", 0, "já preparado", [], {}, source=source)
    if phase != "queued":
        execution = store.reserve("9", 0, inbox, 30)
        store.bind(execution, "epoch", "thread")
        if phase == "sent":
            store.operation(inbox, execution, "turn/start", None)

    store.recover()

    assert store.db.execute("SELECT state FROM ingress_updates WHERE source_key=?", (source,)).fetchone() == ("done",)
    assert not store.ingress_pending("9")
    if phase == "sent":
        assert store.block_reasons("9") == {"uncertain_executions": 1, "uncertain_inputs": 1}
        assert store.next_input("9", 0) is None
    else:
        assert store.next_input("9", 0)["id"] == inbox
        assert not store.blocked("9")
    assert store.db.execute("SELECT COUNT(*) FROM inbox_items WHERE source_key=?", (source,)).fetchone() == (1,)


@pytest.mark.parametrize("has_mismatched_inbox", [False, True])
def test_restart_keeps_unproven_preparation_uncertain(store, has_mismatched_inbox):
    source, _ = store.ingest("test", {"update_id": 71}, "9", 0)
    store.ingress_state(source, "preparing")
    if has_mismatched_inbox:
        store.enqueue("9", 1, "outra geração", [], {}, source=source)

    store.recover()

    assert store.db.execute("SELECT state FROM ingress_updates WHERE source_key=?", (source,)).fetchone() == ("uncertain",)
    assert store.block_reasons("9") == {"uncertain_ingress": 1}
    assert not store.ingress_pending("9")


@pytest.mark.parametrize("state", ["active", "uncertain", "reconciling", "interrupting"])
def test_late_bind_adds_identity_without_resolving_execution_state(store, state):
    _, execution = reserve(store, bind=False)
    store.bind(execution, "epoch", "thread")
    store.db.execute("UPDATE execution_turns SET state=?,result='ack_missing' WHERE execution_id=?", (state, execution))

    store.bind(execution, "epoch", "thread", "late-turn")
    store.bind(execution, "epoch", "thread")

    assert store.db.execute("SELECT state,result,process_epoch,thread_id,turn_id FROM execution_turns WHERE execution_id=?", (execution,)).fetchone() == (state, "ack_missing", "epoch", "thread", "late-turn")


@pytest.mark.parametrize("identity", [("wrong-epoch", "thread", "turn"), ("epoch", "wrong-thread", "turn"), ("epoch", "thread", "wrong-turn")])
def test_bind_rejects_conflicting_execution_identity(store, identity):
    _, execution = reserve(store)
    with store.db:
        store.db.execute("UPDATE execution_turns SET state='uncertain' WHERE execution_id=?", (execution,))

    with pytest.raises(GatewayPersistenceError):
        store.bind(execution, *identity)

    assert store.db.execute("SELECT state,process_epoch,thread_id,turn_id FROM execution_turns WHERE execution_id=?", (execution,)).fetchone() == ("uncertain", "epoch", "thread", "turn")


def test_late_bind_never_revives_terminal_execution(store):
    _, execution = reserve(store)
    store.db.execute("UPDATE execution_turns SET state='terminal',result='owner_discarded' WHERE execution_id=?", (execution,))

    store.bind(execution, "other-epoch", "other-thread", "other-turn")

    assert store.db.execute("SELECT state,result,process_epoch,thread_id,turn_id FROM execution_turns WHERE execution_id=?", (execution,)).fetchone() == ("terminal", "owner_discarded", "epoch", "thread", "turn")


@pytest.mark.parametrize("result", ["completed", "completed_silent", "failed", "interrupted"])
@pytest.mark.parametrize("terminal_first", [True, False])
def test_terminal_proof_of_start_wins_over_missing_initial_ack(store, result, terminal_first):
    inbox, execution, operation = start(store)
    if not terminal_first:
        store.operation_result(operation, "uncertain", "start_ack_missing")
    store.save_terminal_output(execution, result, None, "final", "text", False)
    if terminal_first:
        store.operation_result(operation, "uncertain", "start_ack_missing")
    store.terminal(execution, "lost")

    assert store.db.execute("SELECT state,result FROM execution_turns WHERE execution_id=?", (execution,)).fetchone() == ("terminal", result)
    assert store.db.execute("SELECT state FROM input_operations WHERE operation_id=?", (operation,)).fetchone() == ("accepted",)
    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("done",)
    assert not store.blocked("9")


def test_terminal_without_bound_turn_does_not_prove_start_acceptance(store):
    inbox, execution = reserve(store, bind=False)
    operation = store.operation(inbox, execution, "turn/start", None)
    store.save_terminal_output(execution, "completed", None, "final", "text", False)
    store.operation_result(operation, "uncertain", "start_ack_missing")
    assert store.db.execute("SELECT state FROM input_operations WHERE operation_id=?", (operation,)).fetchone() == ("uncertain",)
    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("uncertain",)
    assert store.blocked("9")


def test_not_started_terminal_is_not_evidence_of_start_acceptance(store):
    inbox, execution, operation = start(store)
    store.terminal(execution, "not_started")
    store.operation_result(operation, "uncertain", "start_ack_missing")
    assert store.db.execute("SELECT state FROM input_operations WHERE operation_id=?", (operation,)).fetchone() == ("uncertain",)
    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("uncertain",)


@pytest.mark.parametrize("terminal_first", [True, False])
def test_turn_completion_never_proves_uncertain_steer_acceptance(store, terminal_first):
    _, execution, operation = start(store)
    store.operation_result(operation, "accepted")
    correction = store.enqueue("9", 0, "correção adicional", [], {}, route="steer")
    steer = store.operation(correction, execution, "turn/steer", "turn")
    if not terminal_first:
        store.operation_result(steer, "uncertain", "steer_ack_missing")
    store.save_terminal_output(execution, "completed", None, "final", "text", False)
    if terminal_first:
        store.operation_result(steer, "uncertain", "steer_ack_missing")

    assert store.db.execute("SELECT state,result FROM execution_turns WHERE execution_id=?", (execution,)).fetchone() == ("terminal", "completed")
    assert store.db.execute("SELECT state FROM input_operations WHERE operation_id=?", (steer,)).fetchone() == ("uncertain",)
    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (correction,)).fetchone() == ("uncertain",)
    assert store.block_reasons("9") == {"uncertain_inputs": 1}


def test_repeated_terminal_preserves_confirmed_output_and_delivery_identity(store):
    _, execution, _ = start(store)
    store.save_terminal_output(execution, "completed", "resposta confirmada", "item-original", "audio", True)
    before = store.db.execute("SELECT result,terminal_text,final_item_id,reply_mode,delete_thoughts,completed_at FROM execution_turns WHERE execution_id=?", (execution,)).fetchone()
    store.save_terminal_output(execution, "failed", "histórico incompleto", "item-recovered", "text", False)
    assert store.db.execute("SELECT result,terminal_text,final_item_id,reply_mode,delete_thoughts,completed_at FROM execution_turns WHERE execution_id=?", (execution,)).fetchone() == before


@pytest.mark.parametrize("known", ["accepted", "rejected", "not_sent"])
def test_known_rpc_result_cannot_be_overwritten_by_timeout_race(store, known):
    inbox, _, operation = start(store)
    store.operation_result(operation, known, "known_result")
    before = store.db.execute("SELECT state,failure_reason FROM inbox_items WHERE id=?", (inbox,)).fetchone()
    store.operation_result(operation, "uncertain", "late_timeout")
    assert store.db.execute("SELECT state,reason FROM input_operations WHERE operation_id=?", (operation,)).fetchone() == (known, "known_result")
    assert store.db.execute("SELECT state,failure_reason FROM inbox_items WHERE id=?", (inbox,)).fetchone() == before


@pytest.mark.parametrize("late_result", ["accepted", "rejected", "uncertain"])
def test_discard_is_a_tombstone_for_late_responses_and_terminal_events(store, late_result):
    inbox, execution, operation = start(store)
    store.operation_result(operation, "uncertain", "start_ack_missing")
    store.resolve("9", discard=True)
    store.operation_result(operation, late_result, "late_response")
    store.save_terminal_output(execution, "completed", "resposta tardia", "final", "text", False)
    store.terminal(execution, "completed")

    assert store.db.execute("SELECT state,result,terminal_text FROM execution_turns WHERE execution_id=?", (execution,)).fetchone() == ("terminal", "owner_discarded", None)
    assert store.db.execute("SELECT state FROM input_operations WHERE operation_id=?", (operation,)).fetchone() == ("resolved",)
    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("cancelled",)
    assert not store.blocked("9")


def test_cancelled_inbox_cannot_be_revived_by_late_acceptance(store):
    inbox, _, operation = start(store)
    store.reset("9", 0)
    store.db.execute("UPDATE conversations SET generation=1 WHERE chat_id='9'")
    store.operation_result(operation, "accepted", "late_response")
    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("cancelled",)
    assert store.next_input("9", 1) is None


def test_discard_releases_inputs_of_lost_execution_and_keeps_unrelated_queue(store):
    inbox, execution, operation = start(store)
    store.operation_result(operation, "accepted")
    correction = store.enqueue("9", 0, "correção em trânsito", [], {})
    steer = store.operation(correction, execution, "turn/steer", "turn")
    # O steering em trânsito ainda está pending sem execution_id na inbox;
    # seu vínculo durável já existe na operação, e é ele que deve ser usado.
    queued = store.enqueue("9", 0, "pedido seguinte", [], {})
    store.db.execute("UPDATE execution_turns SET state='uncertain',result='process_lost' WHERE execution_id=?", (execution,))
    store.resolve("9", discard=True)

    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("cancelled",)
    assert store.db.execute("SELECT state FROM inbox_items WHERE id=?", (correction,)).fetchone() == ("cancelled",)
    assert store.db.execute("SELECT state FROM input_operations WHERE operation_id=?", (operation,)).fetchone() == ("accepted",)
    assert store.db.execute("SELECT state FROM input_operations WHERE operation_id=?", (steer,)).fetchone() == ("resolved",)
    assert store.next_input("9", 0)["id"] == queued
    assert not store.blocked("9")


def test_block_reasons_distinguishes_ingress_from_pending_response(store):
    source, _ = store.ingest("fixture", {"update_id": 1}, "9", 0)
    store.ingress_claim(source)
    store.recover()
    assert store.block_reasons("9") == {"uncertain_ingress": 1}
    store.resolve("9", discard=True)
    _, execution, operation = start(store)
    store.operation_result(operation, "accepted")
    store.save_terminal_output(execution, "completed", "resposta", "final", "text", False)
    assert store.block_reasons("9") == {"pending_deliveries": 1}
    store.resolve("9", discard=True)
    assert store.blocked("9")
    assert store.block_reasons("9") == {"pending_deliveries": 1}


def test_uncertain_delivery_is_reported_without_blocking_next_execution(store):
    store.delivery_plan("delivery", "9", 0, None, None, None, "receipt", "receipt", {})
    store.delivery_result("delivery", "uncertain", reason="http_ack_missing")
    assert store.block_reasons("9") == {"uncertain_deliveries": 1}
    assert not store.blocked("9")


def test_block_reasons_includes_reconciling_and_legacy_inputs(store):
    inbox, execution = reserve(store)
    store.db.execute("UPDATE execution_turns SET state='reconciling' WHERE execution_id=?", (execution,))
    store.db.execute("UPDATE inbox_items SET state='uncertain_legacy' WHERE id=?", (inbox,))
    assert store.block_reasons("9") == {"reconciling_executions": 1, "uncertain_inputs": 1}
    assert store.blocked("9")
    assert store.block_reasons("other-conversation") == {}


def test_notification_receipt_pending_after_terminal_is_a_visible_block_reason(store):
    inbox, execution, operation = start(store)
    store.operation_result(operation, "accepted")
    store.db.execute("INSERT INTO notification_receipts(inbox_id,event_id,received_at,envelope) VALUES (?,?,?,?)", (inbox, "event", "2026-11-13T00:00:00Z", "{}"))
    store.save_terminal_output(execution, "completed_silent", None, "final", "text", False)
    assert store.block_reasons("9") == {"pending_receipts": 1}
    assert store.blocked("9")


def test_rpc_identity_is_durable_typed_and_bound_to_process(store):
    _, execution, operation = start(store)
    store.record_operation_rpc(operation, "epoch", 7)
    store.record_operation_rpc(operation, "epoch", 7)
    reopened = GatewayStore(store.db, threading.RLock())
    record = reopened.operation_for_rpc("epoch", 7)
    assert record["operation_id"] == operation and record["execution_id"] == execution
    assert record["process_epoch"] == "epoch" and record["thread_id"] == "thread"
    assert reopened.operation_for_rpc("epoch", "7") is None
    assert reopened.operation_for_rpc("other-epoch", 7) is None
    store.db.execute("UPDATE execution_turns SET process_epoch='other-epoch' WHERE execution_id=?", (execution,))
    assert reopened.operation_for_rpc("epoch", 7) is None


def test_rpc_mapping_rejects_changed_or_shared_identity(store):
    _, execution, operation = start(store)
    store.record_operation_rpc(operation, "epoch", 7)
    with pytest.raises(GatewayPersistenceError):
        store.record_operation_rpc(operation, "epoch", 8)
    correction = store.enqueue("9", 0, "outra entrada", [], {})
    other = store.operation(correction, execution, "turn/steer", "turn")
    with pytest.raises(GatewayPersistenceError):
        store.record_operation_rpc(other, "epoch", 7)
    with pytest.raises(GatewayPersistenceError):
        store.record_operation_rpc(other, "wrong-epoch", 8)


@pytest.mark.parametrize("interruption", ["cancel", "generation"])
def test_rpc_admission_rechecks_cancellation_and_generation(store, interruption):
    _, execution, operation = start(store)
    if interruption == "cancel":
        store.db.execute("UPDATE execution_turns SET user_cancelled=1 WHERE execution_id=?", (execution,))
    else:
        store.db.execute("UPDATE conversations SET generation=1 WHERE chat_id='9'")
    with pytest.raises(GatewayPersistenceError):
        store.record_operation_rpc(operation, "epoch", 9)
    assert store.operation_for_rpc("epoch", 9) is None


@pytest.mark.parametrize("identifier", [True, None, "", 1.5])
def test_rpc_mapping_rejects_invalid_identifiers(store, identifier):
    _, _, operation = start(store)
    with pytest.raises(GatewayPersistenceError):
        store.record_operation_rpc(operation, "epoch", identifier)


def test_rpc_migration_is_additive_and_preserves_existing_operations(store):
    _, _, operation = start(store)
    before = store.db.execute("SELECT * FROM input_operations").fetchall()
    store.db.execute("DROP TABLE input_operation_rpcs")
    store.db.execute("DELETE FROM gateway_schema_migrations WHERE version=6")
    GatewayStore(store.db, threading.RLock())
    assert store.db.execute("SELECT * FROM input_operations").fetchall() == before
    assert store.db.execute("SELECT MAX(version) FROM gateway_schema_migrations").fetchone() == (SCHEMA_VERSION,)
    store.record_operation_rpc(operation, "epoch", 1)
    assert store.operation_for_rpc("epoch", 1)["operation_id"] == operation
