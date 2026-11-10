"""Continuação coordenada de subagentes com origem e recuperação explícitas."""
from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon.codex_app_server import CodexProtocolError
from onmyoji_daemon.telegram_persistence import GatewayStore
from onmyoji_daemon.telegram_work import RelatedWorkMonitor


def result(status="completed", text="Resultado verificável", child="child"):
    return {"thread": {"id": child, "status": {"type": "idle"}, "turns": [{"id": "child-turn", "status": status, "items": [
        {"id": "thought", "type": "reasoning", "content": ["private reasoning"]},
        {"id": "comment", "type": "agentMessage", "phase": "commentary", "text": "andamento"},
        {"id": "final", "type": "agentMessage", "phase": "final_answer", "text": text},
    ]}]}}


@pytest.fixture
def lab(tmp_path):
    database = sqlite3.connect(tmp_path / "work.sqlite3", check_same_thread=False)
    database.execute("CREATE TABLE inbox_items(id TEXT PRIMARY KEY,chat_id TEXT NOT NULL,generation INTEGER NOT NULL,text TEXT NOT NULL,attachment_ids TEXT NOT NULL,turn_context TEXT NOT NULL DEFAULT '{}',state TEXT NOT NULL,created_at REAL NOT NULL)")
    store = GatewayStore(database, threading.RLock())
    database.execute("INSERT INTO execution_turns(execution_id,conversation_id,generation,process_epoch,thread_id,turn_id,state,result,created_at,deadline,completed_at) VALUES ('exec','conversation',1,'epoch','parent','parent-turn','terminal','completed',0,1000,50)")
    database.execute("INSERT INTO related_work(work_id,execution_id,parent_thread_id,child_thread_id,attempt,state,updated_at) VALUES ('work','exec','parent','child',1,'running',10)")
    database.commit()
    clock, generation, callbacks, calls = [100.0], [1], [], []
    features = {"post_turn"}
    caps = SimpleNamespace(supports=lambda feature: feature in features)
    client = SimpleNamespace(process_epoch="epoch", alive=True, response=result())
    client.running = lambda: client.alive

    def request(method, params, timeout):
        acquired = []

        def verify_unlocked():
            ok = store.lock.acquire(blocking=False)
            acquired.append(ok)
            if ok:
                store.lock.release()

        worker = threading.Thread(target=verify_unlocked)
        worker.start()
        worker.join(2)
        assert acquired == [True], "RPC não pode ocorrer sob mutex do banco"
        calls.append((method, params, timeout))
        return client.response

    client.request = request
    monitor = RelatedWorkMonitor(store, lambda: caps, lambda: client, lambda _conversation: generation[0],
                                 lambda *args: callbacks.append(args), enabled=True, clock=lambda: clock[0])
    value = SimpleNamespace(store=store, database=database, monitor=monitor, client=client,
                            clock=clock, generation=generation, callbacks=callbacks, calls=calls, features=features)
    try:
        yield value
    finally:
        database.close()


def state(lab):
    return lab.database.execute("SELECT state,result_hash,result_ref,result_version,delivered_version FROM related_work WHERE work_id='work'").fetchone()


def test_completed_child_becomes_one_data_continuation_with_verified_provenance(lab):
    assert lab.monitor.has_pending("epoch")
    assert lab.monitor.tick() == 1
    owner, generation, execution, work, attempt, data, key = lab.callbacks[0]
    assert (owner, generation, execution, work, attempt) == ("conversation", 1, "exec", "work", 1)
    assert data["status"] == "completed" and data["text"] == "Resultado verificável"
    assert data["provenance"] == {"kind": "subagent_result", "work_id": "work", "parent_execution_id": "exec", "parent_thread_id": "parent", "child_thread_id": "child", "attempt": 1}
    assert "private reasoning" not in json.dumps(data)
    assert key.startswith("work-result:")
    assert lab.calls == [("thread/read", {"threadId": "child", "includeTurns": True}, 5)]
    assert state(lab)[3:] == (1, 1)
    assert lab.monitor.tick() == 0
    assert len(lab.callbacks) == 1 and len(lab.calls) == 1
    assert not lab.monitor.has_pending()


@pytest.mark.parametrize("disabled", ["config", "gate"])
def test_missing_configuration_or_gate_does_not_query_or_start_process(lab, disabled):
    if disabled == "config":
        lab.monitor.enabled = False
    else:
        lab.features.clear()
    assert lab.monitor.tick() == 0
    assert not lab.monitor.has_pending()
    assert lab.calls == [] and lab.callbacks == []


def test_active_parent_keeps_result_coordination_in_its_own_turn(lab):
    lab.database.execute("UPDATE execution_turns SET state='active',completed_at=NULL")
    lab.database.commit()
    assert lab.monitor.tick() == 0
    assert lab.calls == []


def test_completed_child_observed_before_parent_final_is_already_consolidated(lab):
    lab.database.execute("UPDATE related_work SET state='completed',updated_at=40")
    lab.database.commit()
    assert lab.monitor.tick() == 0
    assert state(lab)[0] == "consolidated"
    assert lab.callbacks == [] and lab.calls == []


@pytest.mark.parametrize("parent_result", ["interrupted", "failed", "owner_discarded"])
def test_cancelled_or_failed_parent_cannot_autonomously_restart(lab, parent_result):
    lab.database.execute("UPDATE execution_turns SET result=?", (parent_result,))
    lab.database.commit()
    assert lab.monitor.tick() == 0
    assert state(lab)[0] == "cancelled"
    assert lab.callbacks == []


def test_new_generation_suppresses_old_child_result(lab):
    lab.generation[0] = 2
    assert lab.monitor.tick() == 0
    assert state(lab)[0] == "cancelled"
    assert lab.callbacks == [] and lab.calls == []


def test_generation_and_parent_cancellation_are_rechecked_after_rpc(lab):
    def changed(_method, _params, timeout):
        lab.generation[0] = 2
        return result()

    lab.client.request = changed
    assert lab.monitor.tick() == 0
    assert state(lab)[0] == "cancelled"
    assert lab.callbacks == []


def test_same_generation_cancel_during_rpc_cannot_enqueue_result(lab):
    def changed(_method, _params, timeout):
        lab.database.execute("UPDATE execution_turns SET result='interrupted'")
        lab.database.commit()
        return result()

    lab.client.request = changed
    assert lab.monitor.tick() == 0
    assert state(lab)[0] == "cancelled"
    assert lab.callbacks == []


def test_unavailable_or_changed_process_without_g6_is_indeterminate(lab):
    lab.client.process_epoch = "new-epoch"
    assert lab.monitor.tick() == 0
    assert state(lab)[0] == "indeterminate"
    assert lab.calls == [] and lab.callbacks == []


def test_g6_allows_only_provable_terminal_history_after_process_restart(lab):
    lab.features.add("process_resume")
    lab.client.process_epoch = "new-epoch"
    assert lab.monitor.tick() == 1
    assert lab.callbacks[0][5]["text"] == "Resultado verificável"


def test_g6_does_not_assume_resume_generates_unfinished_child(lab):
    lab.features.add("process_resume")
    lab.client.process_epoch = "new-epoch"
    lab.client.response = result("inProgress")
    assert lab.monitor.tick() == 0
    assert state(lab)[0] == "indeterminate"
    assert [call[0] for call in lab.calls] == ["thread/read"]


def test_process_lost_during_read_cannot_claim_completed_result(lab):
    def lost(_method, _params, timeout):
        lab.client.alive = False
        return result()

    lab.client.request = lost
    assert lab.monitor.tick() == 0
    assert state(lab)[0] == "indeterminate"
    assert lab.callbacks == []


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_child_failure_is_data_for_principal_without_new_authority(lab, status):
    lab.client.response = result(status, "Relatório parcial")
    assert lab.monitor.tick() == 1
    data = lab.callbacks[0][5]
    assert data["status"] == status and data["text"] == "Relatório parcial"
    assert data["provenance"]["kind"] == "subagent_result"


@pytest.mark.parametrize("response", [{}, {"thread": {"id": "other", "turns": []}}, {"thread": {"id": "child", "status": {"type": "idle"}}}, result("inProgress"), None])
def test_incomplete_history_or_wrong_thread_never_implies_completion(lab, response):
    lab.client.response = response
    assert lab.monitor.tick() == 0
    assert state(lab)[2] is None
    assert lab.callbacks == []


def test_poll_failure_keeps_deadline_and_interval_without_retrying_external_work(lab):
    def failed(method, params, timeout):
        lab.calls.append((method, params, timeout))
        raise CodexProtocolError("timeout", kind="uncertain", written=True)

    lab.client.request = failed
    assert lab.monitor.tick() == 0
    assert lab.monitor.tick() == 0
    assert len(lab.calls) == 1
    deadline = lab.database.execute("SELECT deadline FROM related_work_monitor").fetchone()[0]
    lab.clock[0] += 6
    assert lab.monitor.tick() == 0
    assert len(lab.calls) == 2
    assert lab.database.execute("SELECT deadline FROM related_work_monitor").fetchone()[0] == deadline


def test_expired_child_requests_decision_without_claiming_it_was_cancelled(lab):
    lab.database.execute("UPDATE execution_turns SET deadline=90")
    lab.database.commit()
    assert lab.monitor.tick() == 1
    data = lab.callbacks[0][5]
    assert data["status"] == "blocked" and data["reason"] == "work_deadline_exceeded"
    assert data["child_terminal"] is False and data["requires_decision"] is True
    assert lab.calls == []
    assert not lab.monitor.has_pending()


def test_result_size_limit_preserves_utf8_and_full_content_digest(lab):
    lab.monitor.max_result_bytes = 5
    lab.client.response = result(text="ação muito longa")
    assert lab.monitor.tick() == 1
    data = lab.callbacks[0][5]
    assert data["truncated"] and len(data["content_sha256"]) == 64
    assert len(data["text"].encode("utf-8")) <= 5
    assert "\ufffd" not in data["text"]


def test_enqueue_after_commit_failure_uses_same_key_and_one_inbox(lab):
    inbox = {}

    def enqueue(*args):
        key = args[-1]
        first = key not in inbox
        inbox.setdefault(key, args)
        if first:
            raise RuntimeError("crash after durable enqueue")

    lab.monitor.enqueue_result_callback = enqueue
    assert lab.monitor.tick() == 0
    assert lab.database.execute("SELECT state FROM related_work_continuations").fetchone()[0] == "uncertain"
    lab.monitor.recover()
    assert lab.monitor.tick() == 1
    assert len(inbox) == 1 and state(lab)[4] == 1
    assert lab.monitor.tick() == 0


def test_recovery_marks_unfinished_child_indeterminate_without_g6(lab):
    lab.monitor.recover()
    assert state(lab)[0] == "indeterminate"
    assert lab.monitor.tick() == 0
    assert lab.calls == []


def test_process_loss_only_invalidates_matching_epoch_without_result(lab):
    lab.monitor.process_lost("other")
    assert state(lab)[0] == "running"
    lab.monitor.process_lost("epoch")
    assert state(lab)[0] == "indeterminate"


def test_saved_result_can_be_recovered_without_presuming_worker_survival(lab):
    lab.monitor.enqueue_result_callback = lambda *_args: (_ for _ in ()).throw(RuntimeError("enqueue failed"))
    assert lab.monitor.tick() == 0
    assert state(lab)[2]
    lab.monitor.process_lost("epoch")
    lab.monitor.recover()
    lab.client.alive = False
    lab.monitor.enqueue_result_callback = lambda *args: lab.callbacks.append(args)
    assert lab.monitor.tick() == 1
    assert len(lab.calls) == 1


def test_enqueue_retries_are_bounded_and_do_not_repeat_effects(lab):
    lab.monitor.max_enqueue_attempts = 2
    lab.monitor.enqueue_result_callback = lambda *_args: (_ for _ in ()).throw(RuntimeError("queue unavailable"))
    assert lab.monitor.tick() == lab.monitor.tick() == lab.monitor.tick() == 0
    assert lab.database.execute("SELECT enqueue_attempts FROM related_work_continuations").fetchone()[0] == 1
    lab.clock[0] += 5
    assert lab.monitor.tick() == 0
    lab.clock[0] += 10
    assert lab.monitor.tick() == 0
    assert lab.database.execute("SELECT state,enqueue_attempts FROM related_work_continuations").fetchone() == ("blocked", 2)


def test_tampered_stored_result_is_not_delivered(lab):
    lab.database.execute("UPDATE related_work SET result_ref='{}',result_hash='wrong',result_version=1,state='completed'")
    lab.database.commit()
    assert lab.monitor.tick() == 0
    assert state(lab)[0] == "indeterminate"
    assert lab.callbacks == []


def test_simultaneous_tick_does_not_poll_or_enqueue_twice(lab):
    entered, release = threading.Event(), threading.Event()

    def blocked(_method, _params, timeout):
        entered.set()
        assert release.wait(2)
        return result()

    lab.client.request = blocked
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(lab.monitor.tick()))
    worker.start()
    assert entered.wait(2)
    assert lab.monitor.tick() == 0
    release.set()
    worker.join(2)
    assert outcomes == [1] and len(lab.callbacks) == 1


@pytest.mark.parametrize("option", ["timeout_seconds", "poll_interval", "rpc_timeout", "batch_limit", "max_result_bytes", "max_enqueue_attempts"])
def test_invalid_monitor_limits_are_rejected(lab, option):
    with pytest.raises(ValueError, match="limits"):
        RelatedWorkMonitor(lab.store, lambda: None, lambda: None, lambda _: 1, lambda *_: None, **{option: 0})


@pytest.mark.parametrize("option,value", [("timeout_seconds", float("nan")), ("poll_interval", float("inf")), ("rpc_timeout", None), ("batch_limit", True), ("max_result_bytes", 3.5), ("max_enqueue_attempts", "2")])
def test_nonfinite_or_wrongly_typed_limits_cannot_disable_deadline(lab, option, value):
    with pytest.raises(ValueError, match="limits"):
        RelatedWorkMonitor(lab.store, lambda: None, lambda: None, lambda _: 1, lambda *_: None, **{option: value})


def test_blob_instead_of_text_result_is_rejected(lab):
    lab.database.execute("UPDATE related_work SET result_ref=?,result_hash='wrong',result_version=1,state='completed'", (b'{}',))
    lab.database.commit()
    assert lab.monitor.tick() == 0
    assert state(lab)[0] == "indeterminate"


def test_unavailable_capability_reader_keeps_fallback(lab):
    lab.monitor.capabilities_getter = lambda: (_ for _ in ()).throw(OSError("private report unavailable"))
    assert not lab.monitor.has_pending()
    assert lab.monitor.tick() == 0
    assert not lab.calls
