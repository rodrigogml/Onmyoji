from datetime import timedelta
from pathlib import Path
import sys
import threading

import pytest

from onmyoji_daemon.agent_notification.http import NotificationHttpServer
from onmyoji_daemon.agent_notification.configuration import NotificationConfiguration
from onmyoji_daemon.rpc import RpcError
from onmyoji_daemon.task_scheduler.actions import event_action, script_action
from onmyoji_daemon.task_scheduler.calendar import instant, iso, latest_due, next_at, validate_rule
from onmyoji_daemon.task_scheduler.repository import SORT_FIELDS, TaskRepository
from onmyoji_daemon.task_scheduler.service import TaskScheduler


def event_toml(*, enabled=True, time=None, prompt="Teste"):
    time = time or 'type = "cron"\nexpression = "* * * * *"\ntimezone = "UTC"'
    return f'''schema_version = 1
enabled = {str(enabled).lower()}
description = "Laboratório"
created_at = "2026-11-10T00:00:00Z"

[time]
{time}

[action]
type = "event"

[action.event]
schema_version = 1
prompt = "{prompt}"
thread_policy = "reuse"

[action.event.response_channel]
type = "telegram"
value = "9"
'''


def script_toml(path="job.py", *, enabled=True, time=None, timeout=900):
    time = time or 'type = "once"\nat = "2026-11-10T00:00:00Z"'
    return f'''schema_version = 1
enabled = {str(enabled).lower()}
description = "Script"
created_at = "2026-11-10T00:00:00Z"

[time]
{time}

[action]
type = "script"
path = "{path}"
args = []
timeout_seconds = {timeout}
'''


@pytest.fixture
def repository(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    schedules = tmp_path / "instance" / "schedules"; schedules.mkdir(parents=True)
    now = [instant("2026-11-10T00:00:00Z")]
    store = TaskRepository(tmp_path / "instance" / "state" / "scheduler.sqlite3", workspace, lambda: now[0], schedules)
    yield store, now, schedules
    store.close()


def test_calendar_accepts_only_once_and_five_field_cron():
    now = instant("2026-11-10T00:00:00Z")
    once = validate_rule({"type": "once", "at": "2026-11-10T10:00:00+09:00"}, now)
    assert once == {"type": "once", "at": "2026-11-10T01:00:00.000Z"}
    cron = validate_rule({"type": "cron", "expression": "0 9 * * 1-5", "timezone": "America/Sao_Paulo"}, now)
    assert iso(next_at(cron, now)) == "2026-11-10T12:00:00.000Z"
    for invalid in ({"type": "interval", "anchor_at": iso(now), "every_seconds": 60}, {"type": "cron", "expression": "0 * * * *"}, {"type": "cron", "expression": "0 0 * * * *", "timezone": "UTC"}, {"type": "once", "at": "2026-01-01"}):
        with pytest.raises(RpcError):
            validate_rule(invalid, now)


def test_cron_dst_nonexistent_is_skipped_and_duplicate_uses_first():
    rule = {"type": "cron", "expression": "30 2 * * *", "timezone": "America/New_York"}
    assert iso(next_at(rule, instant("2026-03-08T05:00:00Z"))) == "2026-03-09T06:30:00.000Z"
    rule["expression"] = "30 1 * * *"
    assert iso(next_at(rule, instant("2026-11-01T04:00:00Z"))) == "2026-11-01T05:30:00.000Z"
    assert iso(next_at(rule, instant("2026-11-01T05:30:00Z"))) == "2026-11-02T06:30:00.000Z"
    assert iso(latest_due(rule, instant("2026-11-01T06:10:00Z"))) == "2026-11-01T05:30:00.000Z"


def test_toml_is_source_of_truth_and_mtime_avoids_spurious_revision(repository):
    store, _now, schedules = repository
    path = schedules / "daily.toml"
    path.write_text(event_toml(enabled=False), encoding="utf-8")
    store.reconcile()
    first = store.get("daily")
    assert first["description"] == "Laboratório" and first["status"] == "disabled"
    store.reconcile()
    assert store.get("daily")["revision"] == first["revision"]
    path.write_text(event_toml(enabled=True, prompt="Alterado"), encoding="utf-8")
    store.reconcile(force=True)
    assert store.get("daily")["revision"] == first["revision"] + 1
    assert store.get("daily")["action"]["event"]["prompt"] == "Alterado"


def test_invalid_file_pauses_definition_and_fix_restores_it(repository):
    store, _now, schedules = repository
    path = schedules / "broken.toml"
    path.write_text("schema_version = [", encoding="utf-8")
    store.reconcile()
    invalid = store.get("broken")
    assert invalid["status"] == "invalid" and invalid["diagnosis"] == "invalid_toml"
    path.write_text(event_toml(enabled=True), encoding="utf-8")
    store.reconcile(force=True)
    assert store.get("broken")["status"] == "enabled"


def test_late_runs_coalesce_and_overlap_keeps_one_pending(repository):
    store, now, schedules = repository
    (schedules / "coalesce.toml").write_text(event_toml(), encoding="utf-8")
    store.reconcile()
    now[0] += timedelta(minutes=10, seconds=20)
    store.materialize()
    first = store.claim()
    assert first["scheduled_at"] == "2026-11-10T00:10:00.000Z"
    assert store.begin(first["id"])
    now[0] += timedelta(minutes=5)
    store.materialize()
    now[0] += timedelta(minutes=5)
    store.materialize()
    assert store.summary() == {"pending": 1, "running": 1}
    pending = store.history()["items"][0]
    assert pending["scheduled_at"] == "2026-11-10T00:20:00.000Z"


def test_change_during_run_only_changes_next_snapshot(repository):
    store, now, schedules = repository
    path = schedules / "changing.toml"
    path.write_text(event_toml(prompt="Antigo"), encoding="utf-8")
    store.reconcile(); now[0] += timedelta(minutes=1); store.materialize()
    active = store.claim(); assert store.begin(active["id"])
    path.write_text(event_toml(prompt="Novo"), encoding="utf-8")
    store.reconcile(force=True)
    now[0] += timedelta(minutes=1); store.materialize()
    store.finish(active["id"], "completed")
    following = store.claim()
    assert active["snapshot"]["action"]["event"]["prompt"] == "Antigo"
    assert following["snapshot"]["action"]["event"]["prompt"] == "Novo"
    assert following["event_id"] != active["event_id"]


def test_deletion_prevents_next_and_history_expires_after_twenty_days(repository):
    store, now, schedules = repository
    path = schedules / "removed.toml"
    path.write_text(event_toml(), encoding="utf-8")
    store.reconcile(); now[0] += timedelta(minutes=1); store.materialize()
    path.unlink(); store.reconcile(force=True)
    assert store.claim() is None and store.history("removed")["items"][0]["state"] == "discarded"
    now[0] += timedelta(days=19); store.purge()
    assert store.history("removed")["total"] == 1
    now[0] += timedelta(days=2); store.purge()
    assert store.history("removed")["total"] == 0


def test_restart_resumes_reserved_and_marks_started_uncertain(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    schedules = tmp_path / "data" / "schedules"; schedules.mkdir(parents=True)
    for name in ("one", "two"):
        (schedules / f"{name}.toml").write_text(event_toml(time='type = "once"\nat = "2026-11-09T00:00:00Z"'), encoding="utf-8")
    path = tmp_path / "data" / "state" / "scheduler.sqlite3"
    now = [instant("2026-11-10T00:00:00Z")]
    store = TaskRepository(path, workspace, lambda: now[0], schedules)
    store.reconcile(); store.materialize()
    reserved, started = store.claim(), store.claim()
    assert store.begin(started["id"])
    store.close()
    reopened = TaskRepository(path, workspace, lambda: now[0], schedules)
    try:
        assert reopened.summary() == {"reserved": 1, "uncertain": 1}
        assert reopened.claim()["id"] == reserved["id"]
    finally:
        reopened.close()


def test_script_uses_daemon_python_workspace_path_and_captures_output(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    script = workspace / "job.py"
    script.write_text("import sys; print(sys.executable); print('ok')", encoding="utf-8")
    occurrence = {"id": "script-run", "snapshot": {"action": {"type": "script", "path": "job.py", "args": [], "timeout_seconds": 5}}}
    state, result, reason = script_action(root, workspace, occurrence, threading.Event(), before_dispatch=lambda: True)
    output = Path(result["stdout"]["path"]).read_text(encoding="utf-8")
    assert state == "completed" and reason is None
    assert str(Path(sys.executable)) in output and output.rstrip().endswith("ok")


def test_event_action_generates_occurrence_uuid_and_calls_local_http(tmp_path):
    class Gateway:
        def __init__(self):
            self.events = []
        def _notification_submit(self, value, *, wait):
            self.events.append((value, wait))
            return {"event_id": value["event_id"], "received_at": "2026-11-10T00:00:00.000Z", "status": "completed", "execution": "completed", "delivery": "confirmed"}
    root = tmp_path / "instance"; root.mkdir()
    gateway = Gateway(); server = NotificationHttpServer(0, gateway); server.start()
    NotificationConfiguration(root).configure({"enabled": True, "port": server.port})
    occurrence = {"event_id": "019a6a2e-7820-7000-8000-000000000001", "snapshot": {"action": {"type": "event", "event": {"schema_version": 1, "prompt": "Teste", "response_channel": {"type": "telegram", "value": "9"}, "thread_policy": "reuse"}}}}
    try:
        state, result, reason = event_action(root, occurrence, before_dispatch=lambda: True)
    finally:
        server.close()
    assert state == "completed" and reason is None
    assert gateway.events == [({**occurrence["snapshot"]["action"]["event"], "event_id": occurrence["event_id"]}, True)]


def test_service_is_read_only_and_lists_all_ten_sort_fields(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    data = root / "data"; (data / "schedules").mkdir(parents=True)
    (data / "schedules" / "disabled.toml").write_text(event_toml(enabled=False), encoding="utf-8")
    scheduler = TaskScheduler(root, data, workspace=workspace)
    try:
        scheduler.start()
        assert scheduler.handle("task.list", {})["items"][0]["id"] == "disabled"
        for field in SORT_FIELDS:
            assert scheduler.handle("task.list", {"sort": field})["sort"] == field
        with pytest.raises(RpcError) as caught:
            scheduler.handle("task.create", {"definition": {}})
        assert caught.value.code == "authoring_unsupported"
    finally:
        scheduler.close()
