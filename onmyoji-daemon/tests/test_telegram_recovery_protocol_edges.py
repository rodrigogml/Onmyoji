"""Corridas entre provas tardias, timeout local e fechamento do worker."""
from __future__ import annotations

import threading

import pytest

from onmyoji_daemon.codex_app_server import CodexProtocolError

from .test_telegram_gateway_async import active_slot, lab, submit


def suppress_started(lab):
    original = lab.client.emit

    def emit(method, *args, **kwargs):
        if method != "turn/started":
            original(method, *args, **kwargs)

    lab.client.emit = emit


def response(lab, identifier, *, result=None, error=None):
    payload = {"id": identifier, "error": error} if error is not None else {"id": identifier, "result": result}
    lab.gateway._app_event("transport/late_response", payload, {"process_epoch": lab.client.process_epoch, "transport_generated": True})


def available_once(gateway):
    assert not gateway._uncertain_permits
    assert gateway.work.acquire(blocking=False)
    assert not gateway.work.acquire(blocking=False)
    gateway.work.release()


@pytest.mark.parametrize("proof", ["rejected", "accepted_and_completed"])
def test_late_proof_before_timeout_catch_is_not_downgraded(lab, proof):
    lab.gateway.work = threading.BoundedSemaphore(1)
    suppress_started(lab)
    captured = {}
    reached = threading.Event()

    def start(params, turn):
        slot = active_slot(lab)
        captured.update(slot=slot, thread=params["threadId"])
        lab.gateway.store.record_operation_rpc(slot.active.operation_id, lab.client.process_epoch, 810)
        if proof == "rejected":
            response(lab, 810, error={"code": -32602, "message": "Recusa definitiva"})
        else:
            response(lab, 810, result={"turn": {"id": turn, "status": "inProgress"}})
            lab.client.item(params["threadId"], "FINAL_ANTES_DO_CATCH")
            lab.client.complete(params["threadId"])
        reached.set()
        # O transporte já classificou a resposta como tardia, mas a thread que
        # aguardava o ACK ainda vai processar o seu timeout local.
        raise CodexProtocolError("Timeout local já vencido", kind="uncertain")

    lab.client.on_start = start
    inbox = submit(lab, "pedido cujo resultado vence o catch")
    assert reached.wait(3)
    assert captured["slot"].done.wait(3)
    expected = ("rejected", "failed", "not_started") if proof == "rejected" else ("accepted", "done", "completed")
    state = lab.gateway.database.execute("""SELECT o.state,i.state,e.result FROM input_operations o
        JOIN inbox_items i ON i.id=o.inbox_id JOIN execution_turns e ON e.execution_id=o.execution_id
        WHERE i.id=?""", (inbox,)).fetchone()
    assert state == expected
    assert not lab.gateway.store.blocked("9")
    assert captured["thread"] not in lab.gateway.app_turns
    available_once(lab.gateway)
    if proof == "accepted_and_completed":
        assert sum("FINAL_ANTES_DO_CATCH" in message["text"] for message in lab.api.sent) == 1
    assert len(lab.client.wait_calls("turn/start")) == 1


def test_completion_before_finally_retains_permit_releases_exactly_once(lab, monkeypatch):
    lab.gateway.work = threading.BoundedSemaphore(1)
    suppress_started(lab)
    captured = {}
    closing, resume = threading.Event(), threading.Event()
    original_finish = lab.gateway._finish_resources

    def finish(active):
        if active is captured.get("active"):
            closing.set()
            assert resume.wait(3)
        original_finish(active)

    monkeypatch.setattr(lab.gateway, "_finish_resources", finish)

    def start(params, turn):
        slot = active_slot(lab)
        captured.update(slot=slot, active=slot.active, thread=params["threadId"], turn=turn)
        lab.gateway.store.record_operation_rpc(slot.active.operation_id, lab.client.process_epoch, 811)
        raise CodexProtocolError("ACK inicial ausente", kind="uncertain")

    lab.client.on_start = start
    submit(lab, "pedido com conclusão durante fechamento")
    try:
        assert closing.wait(3)
        assert captured["active"].detached
        assert not lab.gateway._uncertain_permits
        assert not lab.gateway.work.acquire(blocking=False)
        response(lab, 811, result={"turn": {"id": captured["turn"], "status": "inProgress"}})
        lab.client.item(captured["thread"], "FINAL_DURANTE_FECHAMENTO")
        lab.client.complete(captured["thread"])
        assert captured["active"].completed.is_set()
    finally:
        resume.set()
    assert captured["slot"].done.wait(3)
    lab.client.complete(captured["thread"])
    lab.gateway._restore_terminal_outputs()
    lab.gateway._restore_terminal_outputs()
    assert captured["thread"] not in lab.gateway.app_turns
    assert not lab.gateway.store.blocked("9")
    available_once(lab.gateway)
    assert sum("FINAL_DURANTE_FECHAMENTO" in message["text"] for message in lab.api.sent) == 1
    assert len(lab.client.wait_calls("turn/start")) == 1


def test_late_steer_rejection_does_not_requeue_input_from_old_generation(lab, monkeypatch):
    gateway = lab.gateway
    monkeypatch.setattr(gateway, "_wake_worker", lambda _conversation: None)
    generation = gateway._generation("9")
    first = submit(lab, "pedido original", wake=False)
    execution = gateway.store.reserve("9", generation, first, 30)
    gateway.store.bind(execution, lab.client.process_epoch, "thread", "turn")
    start = gateway.store.operation(first, execution, "turn/start", None)
    gateway.store.operation_result(start, "accepted")
    steering = submit(lab, "correção que perdeu o ACK", route="steer", wake=False)
    operation = gateway.store.operation(steering, execution, "turn/steer", "turn")
    gateway.store.record_operation_rpc(operation, lab.client.process_epoch, 812)
    gateway.store.operation_result(operation, "uncertain", "ack_missing")
    gateway._new_conversation("9")

    response(lab, 812, error={"code": -32602, "message": "Steering rejeitado"})

    assert gateway.database.execute("SELECT state FROM input_operations WHERE operation_id=?", (operation,)).fetchone() == ("rejected",)
    assert gateway.database.execute("SELECT state FROM inbox_items WHERE id=?", (steering,)).fetchone() == ("cancelled",)
    assert gateway.store.next_input("9", generation + 1) is None
    assert not any(method in {"turn/start", "turn/steer"} for method, _ in lab.client.calls)
