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


def test_cron_can_skip_a_whole_nonexistent_calendar_day():
    rule = {"type": "cron", "expression": "* * * * *", "timezone": "Pacific/Apia"}
    # A mudança de linha de data em 2011 removeu 30 de dezembro local.
    assert iso(next_at(rule, instant("2011-12-30T09:59:00Z"))) == "2011-12-30T10:00:00.000Z"


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
    assert store.history()["items"][0]["scheduled_at"] == first["scheduled_at"]
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


def test_process_requires_explicit_shell_interpreter(repository):
    store, _now = repository
    value = definition()
    value["action"] = {"type": "process", "executable": str(store.workspace / "routine.cmd"), "cwd": str(store.workspace)}
    with pytest.raises(RpcError) as caught:
        store.create(value)
    assert caught.value.code == "invalid_action"


def test_process_output_is_capped_while_both_streams_are_drained(tmp_path):
    from onmyoji_daemon.task_scheduler.actions import MAX_OUTPUT_BYTES
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    count = MAX_OUTPUT_BYTES + 65536
    code = f"import sys; sys.stdout.buffer.write(b'x'*{count}); sys.stderr.buffer.write(b'y'*{count})"
    occurrence = {"id": "output", "snapshot": {"action": {"type": "process", "executable": sys.executable, "arguments": ["-B", "-c", code], "cwd": str(workspace), "timeout_seconds": 10}}}
    state, result, reason = process_action(root, workspace, occurrence, threading.Event())
    assert state == "completed" and reason is None
    for name in ("stdout", "stderr"):
        assert result[name]["bytes"] == count and result[name]["truncated"]
        assert Path(result[name]["path"]).stat().st_size == MAX_OUTPUT_BYTES


def test_containment_failure_cannot_release_the_user_command(tmp_path, monkeypatch):
    from onmyoji_daemon.task_scheduler import actions
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    marker = workspace / "must-not-exist.txt"
    code = "from pathlib import Path; Path('must-not-exist.txt').write_text('executed')"
    occurrence = {"id": "containment", "snapshot": {"action": {"type": "process", "executable": sys.executable, "arguments": ["-B", "-c", code], "cwd": str(workspace), "timeout_seconds": 5}}}
    def refuse(process):
        raise OSError("fixture containment failure")
    monkeypatch.setattr(actions, "ServiceProcessGroup", refuse)
    state, result, reason = process_action(root, workspace, occurrence, threading.Event())
    assert state == "failed" and reason == "process_preparation_failed"
    assert not marker.exists()


def test_timeout_terminates_grandchild_process_as_well(tmp_path):
    import os
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    code = "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(60)']); print(child.pid,flush=True); time.sleep(60)"
    occurrence = {"id": "grandchild", "snapshot": {"action": {"type": "process", "executable": sys.executable, "arguments": ["-B", "-c", code], "cwd": str(workspace), "timeout_seconds": 2}}}
    state, result, reason = process_action(root, workspace, occurrence, threading.Event())
    assert state == "failed" and reason == "process_timeout"
    pid = int(Path(result["stdout"]["path"]).read_text().strip())
    if os.name == "nt":
        import _winapi
        try:
            handle = _winapi.OpenProcess(0x100000, False, pid)
        except OSError:
            return
        try:
            assert _winapi.WaitForSingleObject(handle, 0) == 0
        finally:
            _winapi.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        # Zombies não executam; podem aguardar reap pelo PID 1 do laboratório.
        assert Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0] == "Z"


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
    def action(root, occurrence, *, before_dispatch=None):
        assert before_dispatch()
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


def test_restart_resumes_reserved_but_never_replays_started_action(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    path = tmp_path / "state" / "scheduler.sqlite3"
    now = [instant("2026-11-10T00:00:00Z")]
    store = TaskRepository(path, workspace, lambda: now[0])
    first = store.create(definition())
    second = store.create(definition())
    now[0] += timedelta(minutes=1)
    store.materialize()
    reserved, started = store.claim(), store.claim()
    assert store.claim() is None
    assert store.begin(started["id"])
    store.close()
    reopened = TaskRepository(path, workspace, lambda: now[0])
    try:
        assert reopened.summary() == {"reserved": 1, "uncertain": 1}
        resumed = reopened.claim()
        assert resumed["id"] == reserved["id"] and resumed["event_id"] == reserved["event_id"]
        assert reopened.claim() is None
        assert reopened.begin(resumed["id"])
        reopened.finish(resumed["id"], "completed")
        assert reopened.claim() is None
    finally:
        reopened.close()


def test_disable_withdraws_a_reservation_before_its_external_boundary(repository):
    store, now = repository
    created = store.create(definition())
    now[0] += timedelta(minutes=1)
    store.materialize(); reserved = store.claim()
    store.set_enabled(created["id"], 1, False)
    assert not store.begin(reserved["id"])
    assert store.history()["items"][0]["state"] == "discarded"


def test_schema_one_is_backed_up_and_newer_runtime_schema_is_rejected(tmp_path):
    import sqlite3
    workspace = tmp_path / "workspace"; workspace.mkdir()
    path = tmp_path / "state" / "scheduler.sqlite3"
    store = TaskRepository(path, workspace)
    created = store.create(definition(enabled=False))
    with store.db:
        store.db.execute("PRAGMA user_version=1")
    store.close()
    migrated = TaskRepository(path, workspace)
    try:
        assert migrated.db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert migrated.get(created["id"])["name"] == "Laboratório"
        with sqlite3.connect(path.with_name("scheduler-before-dispatch-v1.sqlite3")) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        with migrated.db:
            migrated.db.execute("PRAGMA user_version=999")
    finally:
        migrated.close()
    with pytest.raises(RpcError) as caught:
        TaskRepository(path, workspace)
    assert caught.value.code == "schema_newer"


@pytest.mark.parametrize("field", ["name", "state", "next_run_at", "created_at", "action", "channel", "destination", "thread_policy"])
@pytest.mark.parametrize("descending", [False, True])
def test_all_displayed_columns_sort_with_nulls_last_and_stable_ids(repository, field, descending):
    store, now = repository
    for index in range(25):
        value = definition(enabled=index % 3 != 0)
        value["name"] = "Nome " + str(index % 5)
        if index % 4 == 0:
            value["action"] = {"type": "process", "executable": sys.executable, "cwd": str(store.workspace)}
        else:
            value["action"]["event"]["thread_policy"] = "new" if index % 2 else "reuse"
            value["action"]["event"]["response_channel"]["value"] = str(index)
        store.create(value)
        now[0] += timedelta(seconds=1)
    listed = store.list(sort=field, descending=descending, limit=100)["items"]
    key = "action_type" if field == "action" else field
    present = [row[key] for row in listed if row[key] is not None]
    assert present == sorted(present, reverse=descending)
    if any(row[key] is None for row in listed):
        first_null = next(index for index, row in enumerate(listed) if row[key] is None)
        assert all(row[key] is None for row in listed[first_null:])
    for value in set(present):
        ids = [row["id"] for row in listed if row[key] == value]
        assert ids == sorted(ids)
    page_one, page_two = store.list(limit=20), store.list(offset=20, limit=20)
    assert len(page_one["items"]) == 20 and len(page_two["items"]) == 5
    assert len({row["id"] for row in [*page_one["items"], *page_two["items"]]}) == 25


def test_history_expiration_preserves_all_nonterminal_work_and_definitions(repository):
    store, now = repository
    for _ in range(3):
        store.create(definition())
    now[0] += timedelta(minutes=1)
    store.materialize()
    completed, uncertain = store.claim(), store.claim()
    store.finish(completed["id"], "completed")
    assert store.begin(uncertain["id"])
    store.finish(uncertain["id"], "uncertain")
    now[0] += timedelta(days=91)
    store.purge()
    assert store.summary() == {"pending": 1, "uncertain": 1}
    assert store.list()["total"] == 3
