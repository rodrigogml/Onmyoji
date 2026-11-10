from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import threading

import pytest

from onmyoji_daemon.rpc import RpcError
from onmyoji_daemon.task_scheduler.calendar import instant, iso, latest_due, next_at, validate_rule
from onmyoji_daemon.task_scheduler.repository import TaskRepository
from onmyoji_daemon.task_scheduler.service import TaskScheduler
from onmyoji_daemon.task_scheduler.actions import process_action


def definition(rule=None, enabled=True):
    return {"schema_version": 1, "name": "Laboratório", "enabled": enabled, "rule": rule or {"type": "interval", "anchor_at": "2026-11-10T00:00:00Z", "every_seconds": 60}, "action": {"type": "notification", "event": {"schema_version": 1, "prompt": "Teste", "response_channel": {"type": "telegram", "value": "9"}, "thread_policy": "reuse"}}}


@pytest.fixture
def repository(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    now = [instant("2026-11-10T00:00:00Z")]
    repository = TaskRepository(tmp_path / "instance" / "state" / "scheduler.sqlite3", workspace, lambda: now[0])
    yield repository, now
    repository.close()


def test_once_interval_and_cron_calendar():
    now = instant("2026-11-10T00:00:00Z")
    interval = definition()["rule"]
    assert next_at(interval, now + timedelta(seconds=85)) == now + timedelta(seconds=120)
    assert latest_due(interval, now + timedelta(seconds=185)) == now + timedelta(seconds=180)
    cron = validate_rule({"type": "cron", "expression": "0 9 * * 1-5"}, now)
    assert iso(next_at(cron, now)) == "2026-11-10T12:00:00.000Z"
    assert next_at({"type": "once", "at": iso(now)}, now) is None


def test_cron_dst_nonexistent_skipped_and_repeated_only_first():
    rule = {"type": "cron", "expression": "30 2 * * *", "timezone": "America/New_York"}
    assert iso(next_at(rule, instant("2026-03-08T05:00:00Z"))) == "2026-03-09T06:30:00.000Z"
    rule["expression"] = "30 1 * * *"
    assert iso(next_at(rule, instant("2026-11-01T04:00:00Z"))) == "2026-11-01T05:30:00.000Z"
    assert iso(next_at(rule, instant("2026-11-01T05:30:00Z"))) == "2026-11-02T06:30:00.000Z"
    assert iso(latest_due(rule, instant("2026-11-01T06:10:00Z"))) == "2026-11-01T05:30:00.000Z"


@pytest.mark.parametrize("rule", [{"type": "once", "at": "2026-01-01"}, {"type": "interval", "anchor_at": "2026-01-01T00:00:00Z", "every_seconds": True}, {"type": "cron", "expression": "@daily"}, {"type": "cron", "expression": "0 0 31 2 *"}, {"type": "cron", "expression": "* * * * *", "timezone": "not/a/zone"}, {"type": "cron", "expression": "0 * * * * *"}])
def test_invalid_rules(rule):
    with pytest.raises(RpcError):
        validate_rule(rule, instant("2026-11-10T00:00:00Z"))


def test_revisions_coalescing_and_immutable_action(repository):
    store, now = repository
    created = store.create(definition())
    now[0] += timedelta(minutes=10, seconds=5)
    store.materialize()
    first = store.history()["items"][0]
    assert first["scheduled_at"] == "2026-11-10T00:10:00.000Z"
    assert first["window_start"] == "2026-11-10T00:01:00.000Z"
    changed = definition(); changed["action"]["event"]["prompt"] = "Novo"
    store.update(created["id"], created["revision"], changed)
    now[0] += timedelta(minutes=2)
    store.materialize()
    assert store.history()["total"] == 1
    assert store.history()["items"][0]["snapshot"]["action"]["event"]["prompt"] == "Teste"
    active = store.claim()
    now[0] += timedelta(minutes=3)
    store.materialize()
    assert store.history()["total"] == 2 and store.claim() is None
    store.finish(active["id"], "completed")
    assert store.claim()["snapshot"]["action"]["event"]["prompt"] == "Novo"
    with pytest.raises(RpcError) as caught:
        store.update(created["id"], 1, changed)
    assert caught.value.code == "revision_conflict"


def test_restart_blocks_only_uncertain_definition_and_resolve_requires_reason(repository):
    store, now = repository
    first = store.create(definition())
    now[0] += timedelta(minutes=1)
    store.materialize(); active = store.claim()
    store.finish(active["id"], "uncertain", reason="transport_lost")
    second = store.create(definition())
    now[0] += timedelta(minutes=1)
    store.materialize()
    other = store.claim()
    assert other["definition_id"] == second["id"] and store.claim() is None
    with pytest.raises(RpcError):
        store.delete(first["id"], first["revision"])
    with pytest.raises(RpcError):
        store.resolve(active["id"], "", True)
    store.resolve(active["id"], "Efeitos conferidos pelo operador", True)
    assert store.claim()["definition_id"] == first["id"]


def test_disable_discards_pending_and_reenable_does_not_replay(repository):
    store, now = repository
    created = store.create(definition())
    now[0] += timedelta(minutes=5)
    store.materialize()
    disabled = store.set_enabled(created["id"], 1, False)
    assert store.history()["items"][0]["state"] == "discarded"
    now[0] += timedelta(days=2)
    enabled = store.set_enabled(created["id"], disabled["revision"], True)
    assert instant(enabled["next_run_at"]) > now[0]
    assert store.claim() is None


def test_clock_backward_does_not_repeat_and_expiration_preserves_uncertainty(repository):
    store, now = repository
    created = store.create(definition({"type": "once", "at": iso(now[0] - timedelta(seconds=2))}))
    store.materialize()
    run = store.claim(); store.finish(run["id"], "uncertain")
    now[0] -= timedelta(hours=2)
    store.materialize()
    assert store.history()["total"] == 1
    now[0] += timedelta(days=100)
    store.purge()
    assert store.history()["total"] == 1


def test_process_action_runs_without_gateway_and_captures_exit(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    occurrence = {"id": "fixture", "snapshot": {"action": {"type": "process", "executable": sys.executable, "arguments": ["-B", "-c", "print('scheduler process fixture')"], "cwd": str(workspace), "timeout_seconds": 5}}}
    state, result, reason = process_action(root, workspace, occurrence, threading.Event())
    assert state == "completed" and reason is None
    assert Path(result["stdout"]["path"]).read_text().strip() == "scheduler process fixture"


def test_process_timeout_terminates_tree(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    occurrence = {"id": "timeout", "snapshot": {"action": {"type": "process", "executable": sys.executable, "arguments": ["-B", "-c", "import time; time.sleep(30)"], "cwd": str(workspace), "timeout_seconds": 1}}}
    state, result, reason = process_action(root, workspace, occurrence, threading.Event())
    assert state == "failed" and reason == "process_timeout"


def test_service_rpc_listing_preview_and_defaults(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    scheduler = TaskScheduler(root, root / "data", workspace=workspace)
    try:
        value = scheduler.handle("task.create", {"definition": definition(enabled=False)})
        assert scheduler.handle("task.status", {})["max_parallel_actions"] == 4
        assert scheduler.handle("task.list", {})["items"][0]["id"] == value["id"]
        assert len(scheduler.handle("task.preview", {"id": value["id"], "count": 3})["instants"]) == 3
        with pytest.raises(RpcError):
            scheduler.handle("task.list", {"sort": "DROP TABLE definitions"})
    finally:
        scheduler.close()


def test_global_four_slots_and_per_definition_overlap(tmp_path, monkeypatch):
    from onmyoji_daemon.task_scheduler import service
    from concurrent.futures import ThreadPoolExecutor
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    now = [instant("2026-11-10T00:00:00Z")]
    scheduler = TaskScheduler(root, root / "data", workspace=workspace, clock=lambda: now[0])
    entered = threading.Condition()
    release = threading.Event()
    calls = []
    def action(root, occurrence):
        with entered:
            calls.append(occurrence["definition_id"]); entered.notify_all()
        assert release.wait(5)
        return "completed", {}, None
    monkeypatch.setattr(service, "notification_action", action)
    try:
        for _ in range(6):
            scheduler.repository.create(definition())
        now[0] += timedelta(minutes=1)
        scheduler.tick()
        with entered:
            assert entered.wait_for(lambda: len(calls) == 4, timeout=3)
        now[0] += timedelta(minutes=10)
        scheduler.tick()
        assert len(calls) == 4
        assert scheduler.repository.summary()["pending"] == 6
        release.set()
    finally:
        release.set(); scheduler.close()


def test_supervised_scheduler_starts_only_after_containment(tmp_path):
    from onmyoji_daemon.supervisor import Supervisor
    from onmyoji_daemon.rpc import call
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    (root / "configs").mkdir(parents=True); workspace.mkdir()
    (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory=' + json.dumps(workspace.as_posix()), encoding="utf-8")
    supervisor = Supervisor(root)
    try:
        status = supervisor.start("task-scheduler")
        assert status["state"] == "running", status
        assert supervisor.handle("task.status", {})["max_parallel_actions"] == 4
        value = definition({"type": "once", "at": "2026-01-01T00:00:00Z"})
        value["action"] = {"type": "process", "executable": sys.executable, "arguments": ["-B", "-c", "print('supervised fixture')"], "cwd": str(workspace), "timeout_seconds": 5}
        created = supervisor.handle("task.create", {"definition": value})
        service = supervisor.services["task-scheduler"]
        # A chamada longa de controle não é usada; aguardar o arquivo é uma
        # barreira observável da execução real do processo de laboratório.
        import time
        deadline = time.monotonic() + 5
        tick = threading.Event()
        while time.monotonic() < deadline:
            history = supervisor.handle("task.history", {"id": created["id"]})
            if history["items"] and history["items"][0]["state"] == "completed":
                break
            tick.wait(0.02)
        assert history["items"][0]["state"] == "completed", history
    finally:
        supervisor.stop("task-scheduler"); supervisor.rpc.close()
