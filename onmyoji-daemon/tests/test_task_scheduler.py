from datetime import timedelta
from pathlib import Path
import sys
import threading

import pytest

from onmyoji_daemon.agent_notification.http import NotificationHttpServer
from onmyoji_daemon.agent_notification.configuration import NotificationConfiguration
from onmyoji_daemon.rpc import RpcError
from onmyoji_daemon.task_scheduler.actions import MAX_OUTPUT_BYTES, email_check_action, event_action, script_action
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
    timeout_line = f"timeout_seconds = {timeout}\n" if timeout is not None else ""
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
{timeout_line}
'''


@pytest.fixture
def repository(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    schedules = tmp_path / "instance" / "schedules"; schedules.mkdir(parents=True)
    now = [instant("2026-11-10T00:00:00Z")]
    store = TaskRepository(tmp_path / "instance" / "state" / "scheduler.sqlite3", workspace, lambda: now[0], schedules)
    yield store, now, schedules
    store.close()


def test_calendar_accepts_once_cron_and_anchored_interval():
    now = instant("2026-11-10T00:00:00Z")
    once = validate_rule({"type": "once", "at": "2026-11-10T10:00:00+09:00"}, now)
    assert once == {"type": "once", "at": "2026-11-10T01:00:00.000Z"}
    cron = validate_rule({"type": "cron", "expression": "0 9 * * 1-5", "timezone": "America/Sao_Paulo"}, now)
    assert iso(next_at(cron, now)) == "2026-11-10T12:00:00.000Z"
    interval = validate_rule({"type": "interval", "anchor_at": "2026-11-10T00:02:00Z", "every_seconds": 900}, now)
    assert iso(next_at(interval, now)) == "2026-11-10T00:02:00.000Z"
    assert iso(next_at(interval, instant("2026-11-10T00:02:00Z"))) == "2026-11-10T00:17:00.000Z"
    assert iso(latest_due(interval, instant("2026-11-10T00:31:59Z"))) == "2026-11-10T00:17:00.000Z"
    for invalid in ({"type": "interval", "anchor_at": iso(now), "every_seconds": 59}, {"type": "interval", "anchor_at": iso(now), "every_seconds": True}, {"type": "cron", "expression": "0 * * * *"}, {"type": "cron", "expression": "0 0 * * * *", "timezone": "UTC"}, {"type": "once", "at": "2026-01-01"}):
        with pytest.raises(RpcError):
            validate_rule(invalid, now)


def test_interval_coalesces_from_activation_anchor_across_restart(repository):
    store, now, schedules = repository
    (schedules / "interval.toml").write_text(event_toml(time='type = "interval"\nanchor_at = "2026-11-10T00:02:00Z"\nevery_seconds = 900'), encoding="utf-8")
    store.reconcile()
    assert store.get("interval")["next_run_at"] == "2026-11-10T00:02:00.000Z"
    now[0] = instant("2026-11-10T00:48:00Z"); store.materialize()
    occurrence = store.history("interval")["items"][0]
    assert occurrence["scheduled_at"] == "2026-11-10T00:47:00.000Z"
    assert store.get("interval")["next_run_at"] == "2026-11-10T01:02:00.000Z"


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


def test_reserved_email_check_definition_uses_interval_and_account(repository):
    store, _now, schedules = repository
    (schedules / "onmyoji-email-default.toml").write_text('''schema_version = 1
enabled = true
description = "Checagem automática"
created_at = "2026-11-10T00:00:00Z"
managed_by = "onmyoji.email-monitor"
[time]
type = "interval"
anchor_at = "2026-11-10T00:00:00Z"
every_seconds = 900
[action]
type = "email_check"
account = "default"
timeout_seconds = 300
''', encoding="utf-8")
    store.reconcile()
    value = store.get("onmyoji-email-default")
    assert value["status"] == "enabled" and value["action"] == {"type": "email_check", "account": "default", "timeout_seconds": 300}
    assert value["expression"] == "900s"


def test_email_check_action_and_managed_marker_are_mutually_reserved(repository):
    store, _now, schedules = repository
    email_check = '''schema_version = 1
enabled = true
description = "Manual"
created_at = "2026-11-10T00:00:00Z"
[time]
type = "interval"
anchor_at = "2026-11-10T00:00:00Z"
every_seconds = 900
[action]
type = "email_check"
account = "default"
'''
    (schedules / "manual-email.toml").write_text(email_check, encoding="utf-8")
    managed_event = event_toml().replace(
        'created_at = "2026-11-10T00:00:00Z"',
        'created_at = "2026-11-10T00:00:00Z"\nmanaged_by = "onmyoji.email-monitor"')
    (schedules / "managed-event.toml").write_text(managed_event, encoding="utf-8")

    store.reconcile()

    assert store.get("manual-email")["status"] == "invalid"
    assert store.get("managed-event")["status"] == "invalid"


def test_sort_keeps_missing_values_last_and_id_as_stable_tiebreak(repository):
    store, _now, schedules = repository
    for identifier in ("zeta", "alpha", "middle"):
        (schedules / f"{identifier}.toml").write_text(event_toml(enabled=False), encoding="utf-8")
    store.reconcile()
    assert [item["id"] for item in store.list(descending=True, limit=20)["items"]] == ["alpha", "middle", "zeta"]
    assert [item["id"] for item in store.list(sort="expression", descending=True, limit=20)["items"]] == ["alpha", "middle", "zeta"]


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
    now[0] += timedelta(minutes=1); store.materialize()
    assert store.summary() == {"pending": 1, "running": 1}
    path.write_text(event_toml(prompt="Novo"), encoding="utf-8")
    store.reconcile(force=True)
    assert store.summary() == {"discarded": 1, "running": 1}
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


def test_script_timeout_and_output_limits_are_enforced(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    (workspace / "slow.py").write_text("import time; time.sleep(30)", encoding="utf-8")
    timeout = {"id": "script-timeout", "snapshot": {"action": {"type": "script", "path": "slow.py", "args": [], "timeout_seconds": 1}}}
    state, _result, reason = script_action(root, workspace, timeout, threading.Event(), before_dispatch=lambda: True)
    assert state == "failed" and reason == "process_timeout"
    count = MAX_OUTPUT_BYTES + 1024
    (workspace / "output.py").write_text(f"import sys; sys.stdout.buffer.write(b'x'*{count}); sys.stderr.buffer.write(b'y'*{count})", encoding="utf-8")
    output = {"id": "script-output", "snapshot": {"action": {"type": "script", "path": "output.py", "args": [], "timeout_seconds": 10}}}
    state, result, reason = script_action(root, workspace, output, threading.Event(), before_dispatch=lambda: True)
    assert state == "completed" and reason is None
    for stream in ("stdout", "stderr"):
        assert result[stream]["bytes"] == count and result[stream]["truncated"]
        assert Path(result[stream]["path"]).stat().st_size == MAX_OUTPUT_BYTES


def test_script_stopped_before_boundary_leaves_reservation_resumable(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    (workspace / "job.py").write_text("print('resumed')", encoding="utf-8")
    occurrence = {"id": "script-resume", "snapshot": {"action": {"type": "script", "path": "job.py", "args": [], "timeout_seconds": 5}}}
    stop = threading.Event(); stop.set()
    begins = []
    state, result, reason = script_action(root, workspace, occurrence, stop, before_dispatch=lambda: begins.append(True) or True)
    assert (state, result, reason) == ("failed", None, "service_stopping_before_action")
    assert not begins and not (workspace / ".onmyoji" / "task-scheduler" / occurrence["id"]).exists()
    state, result, reason = script_action(root, workspace, occurrence, threading.Event(), before_dispatch=lambda: True)
    assert state == "completed" and reason is None
    assert Path(result["stdout"]["path"]).read_text(encoding="utf-8").strip() == "resumed"


def test_configurable_script_default_and_workspace_boundary(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    data = root / "data"; (data / "schedules").mkdir(parents=True)
    (data / "config.toml").write_text("script_timeout_seconds = 7\n", encoding="utf-8")
    (workspace / "job.py").write_text("print('ok')", encoding="utf-8")
    (data / "schedules" / "script.toml").write_text(script_toml(timeout=None), encoding="utf-8")
    scheduler = TaskScheduler(root, data, workspace=workspace)
    try:
        scheduler.repository.reconcile()
        assert scheduler.repository.get("script")["action"]["timeout_seconds"] == 7
        outside = tmp_path / "outside.py"; outside.write_text("print('outside')", encoding="utf-8")
        (data / "schedules" / "escape.toml").write_text(script_toml("../outside.py", timeout=None), encoding="utf-8")
        scheduler.repository.reconcile(force=True)
        assert scheduler.repository.get("escape")["status"] == "invalid"
    finally:
        scheduler.close()


def test_definition_mtimes_are_rechecked_at_five_second_intervals(tmp_path, monkeypatch):
    from onmyoji_daemon.task_scheduler import service
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    scheduler = TaskScheduler(root, root / "data", workspace=workspace)
    moments = [104.999]
    calls = []
    scheduler._last_reconcile = 100.0
    monkeypatch.setattr(service.time, "monotonic", lambda: moments[0])
    monkeypatch.setattr(scheduler.repository, "reconcile", lambda: calls.append(moments[0]))
    try:
        assert not scheduler._reconcile_if_due() and not calls
        moments[0] = 105.0
        assert scheduler._reconcile_if_due() and calls == [105.0]
        moments[0] = 109.999
        assert not scheduler._reconcile_if_due() and calls == [105.0]
        moments[0] = 110.0
        assert scheduler._reconcile_if_due() and calls == [105.0, 110.0]
    finally:
        scheduler.close()


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


def test_email_check_action_uses_mcp_runtime_entry_without_agent(tmp_path, monkeypatch):
    root = tmp_path / "instance"; root.mkdir(); runtime = tmp_path / "mcp-python"; runtime.write_text("", encoding="utf-8")
    observed = {}
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs): observed.update(command=command, kwargs=kwargs); self.stdout = self.stderr = None
        def poll(self): return 0
        def communicate(self, timeout): return b'{"ok":true,"result":{"processed":0}}', b""
    class Group:
        def __init__(self, process): pass
        def close(self): pass
    monkeypatch.setattr("onmyoji_daemon.task_scheduler.actions.mcp_runtime_python", lambda _root: runtime)
    monkeypatch.setattr("onmyoji_daemon.task_scheduler.actions.subprocess.Popen", Process)
    monkeypatch.setattr("onmyoji_daemon.task_scheduler.actions.ServiceProcessGroup", Group)
    occurrence = {"snapshot": {"action": {"type": "email_check", "account": "default", "timeout_seconds": 300}}}
    state, result, reason = email_check_action(root, occurrence, threading.Event(), before_dispatch=lambda: True)
    assert state == "completed" and reason is None and result["result"]["processed"] == 0
    assert observed["command"][:4] == [str(runtime), "-B", "-m", "onmyoji_daemon.mcp.email.monitor"]
    assert "agent_notification" not in " ".join(observed["command"])


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
