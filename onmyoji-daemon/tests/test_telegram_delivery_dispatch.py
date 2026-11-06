"""Entregas enfileiradas sem bloquear notificações nem compartilhar lanes."""
from __future__ import annotations

from concurrent.futures import CancelledError
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon.telegram_delivery import DeliveryQueueFull, GatewayDelivery


@pytest.fixture
def dispatchers():
    created = []

    def make(**options):
        instance = GatewayDelivery(SimpleNamespace(), lambda _conversation: 0, **options)
        created.append(instance)
        return instance

    yield make
    for instance in created:
        instance._executor.shutdown(wait=False, cancel_futures=True)


def wait(event):
    assert event.wait(3), "O efeito concorrente esperado não foi observado"


def test_blocked_conversation_does_not_hold_another_conversation(dispatchers):
    delivery = dispatchers()
    entered, release = threading.Event(), threading.Event()

    def slow():
        entered.set()
        wait(release)
        return "a"

    first = delivery.submit("a", slow)
    wait(entered)
    try:
        other = delivery.submit("b", lambda: "b")
        assert other.result(timeout=3) == "b"
        assert not first.done()
        assert delivery.pending_count == delivery.pending_for("a") == 1
        assert delivery.pending_for("b") == 0
    finally:
        release.set()
    assert first.result(timeout=3) == "a"
    assert delivery.pending_count == 0


def test_same_conversation_is_fifo_with_only_one_runner(dispatchers):
    delivery = dispatchers()
    entered, release = threading.Event(), threading.Event()
    observed = []

    def first():
        entered.set()
        wait(release)
        observed.append(1)
        return 1

    futures = [delivery.submit("a", first)]
    wait(entered)
    futures.extend(delivery.submit("a", lambda value=value: observed.append(value) or value) for value in range(2, 20))
    try:
        # Um pool ingênuo teria todos os workers esperando o mutex de "a".
        assert delivery.submit("b", lambda: "independente").result(timeout=3) == "independente"
        assert observed == []
        assert delivery.pending_for("a") == 19
    finally:
        release.set()
    assert [future.result(timeout=3) for future in futures] == list(range(1, 20))
    assert observed == list(range(1, 20))
    assert delivery.pending_for("a") == delivery.pending_count == 0


def test_distinct_conversations_never_share_a_hash_lane(dispatchers):
    delivery = dispatchers()
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        wait(release)

    first = delivery.submit("held", hold)
    wait(entered)
    try:
        others = [delivery.submit(f"conversation-{number}", lambda number=number: number) for number in range(40)]
        assert [future.result(timeout=3) for future in others] == list(range(40))
        assert not first.done()
    finally:
        release.set()
    first.result(timeout=3)


def test_global_bound_rejects_before_admission_or_effect(dispatchers):
    delivery = dispatchers(pending_limit=2)
    entered, release = threading.Event(), threading.Event()
    effects = []

    def hold():
        entered.set()
        wait(release)
        effects.append("first")

    first = delivery.submit("a", hold)
    wait(entered)
    second = delivery.submit("a", lambda: effects.append("second"))
    try:
        with pytest.raises(DeliveryQueueFull) as failure:
            delivery.submit("b", lambda: effects.append("overflow"))
        assert failure.value.kind == "not_sent"
        assert delivery.pending_count == 2 and delivery.pending_for("b") == 0
        assert effects == []
    finally:
        release.set()
    first.result(timeout=3)
    second.result(timeout=3)
    assert effects == ["first", "second"]
    assert delivery.submit("b", lambda: "capacity returned").result(timeout=3) == "capacity returned"


def test_future_exception_does_not_stall_the_conversation(dispatchers):
    delivery = dispatchers()

    def failing():
        raise ValueError("controlled error")

    failed = delivery.submit("a", failing)
    next_one = delivery.submit("a", lambda: 7)
    with pytest.raises(ValueError, match="controlled error"):
        failed.result(timeout=3)
    assert next_one.result(timeout=3) == 7
    assert delivery.pending_count == 0


def test_cancelled_queue_item_never_runs_or_bypasses_memory_bound(dispatchers):
    delivery = dispatchers(pending_limit=2)
    entered, release = threading.Event(), threading.Event()
    effects = []

    def hold():
        entered.set()
        wait(release)

    first = delivery.submit("a", hold)
    wait(entered)
    cancelled = delivery.submit("a", lambda: effects.append("cancelled"))
    assert cancelled.cancel()
    try:
        with pytest.raises(DeliveryQueueFull):
            delivery.submit("a", lambda: effects.append("overflow"))
        assert delivery.pending_count == 2
    finally:
        release.set()
    first.result(timeout=3)
    with pytest.raises(CancelledError):
        cancelled.result(timeout=3)
    assert delivery.submit("a", lambda: "after cancellation").result(timeout=3) == "after cancellation"
    assert not effects and delivery.pending_count == 0


def test_done_callback_can_enqueue_at_runner_exit_without_lost_wakeup(dispatchers):
    delivery = dispatchers(pending_limit=1)
    entered, release, chained_done = threading.Event(), threading.Event(), threading.Event()
    chained = []

    def first():
        entered.set()
        wait(release)
        return "first"

    future = delivery.submit("a", first)
    wait(entered)

    def enqueue_after_completed(_future):
        chained.append(delivery.submit("a", lambda: "chained"))
        chained_done.set()

    future.add_done_callback(enqueue_after_completed)
    release.set()
    assert future.result(timeout=3) == "first"
    wait(chained_done)
    assert chained[0].result(timeout=3) == "chained"
    # Novo submit também funciona se o runner já encerrou sua marca.
    assert delivery.submit("a", lambda: "later").result(timeout=3) == "later"
    assert delivery.pending_count == delivery.pending_for("a") == 0


def test_parallel_conversation_effects_never_exceed_configured_limit(dispatchers):
    delivery = dispatchers(max_parallel_conversations=2)
    changed, release = threading.Condition(), threading.Event()
    active, peak, entered = [0], [0], [0]

    def effect():
        with changed:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            entered[0] += 1
            changed.notify_all()
        wait(release)
        with changed:
            active[0] -= 1
        return "ok"

    futures = [delivery.submit(str(number), effect) for number in range(8)]
    with changed:
        assert changed.wait_for(lambda: entered[0] == 2, timeout=3)
        assert active[0] == peak[0] == 2
    release.set()
    assert [future.result(timeout=3) for future in futures] == ["ok"] * 8
    assert peak[0] == 2 and delivery.pending_count == 0


def test_operation_can_read_state_and_submit_other_work_without_mutex_deadlock(dispatchers):
    delivery = dispatchers()

    def operation():
        assert delivery.pending_for("a") == 1
        other = delivery.submit("b", lambda: 5)
        return other.result(timeout=3)

    assert delivery.submit("a", operation).result(timeout=3) == 5
    assert delivery.pending_count == 0


@pytest.mark.parametrize("options", [{"pending_limit": 0}, {"pending_limit": True}, {"pending_limit": None}, {"max_parallel_conversations": 0}, {"max_parallel_conversations": 17}, {"max_parallel_conversations": 1.5}])
def test_invalid_dispatch_limits_are_rejected(options):
    with pytest.raises(ValueError, match="limits"):
        GatewayDelivery(SimpleNamespace(), lambda _conversation: 0, **options)


def test_invalid_submission_has_no_pending_effect(dispatchers):
    delivery = dispatchers()
    with pytest.raises(ValueError):
        delivery.submit("", lambda: 1)
    with pytest.raises(TypeError):
        delivery.submit("a", None)
    assert delivery.pending_count == 0


def test_unavailable_executor_rejects_without_leaking_runner_or_count(dispatchers):
    delivery = dispatchers()
    delivery._executor.shutdown(wait=True)
    with pytest.raises(DeliveryQueueFull):
        delivery.submit("a", lambda: "must not run")
    assert delivery.pending_for("a") == delivery.pending_count == 0
