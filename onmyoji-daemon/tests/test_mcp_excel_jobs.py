"""Supervisão Excel, políticas e jobs em workspace sintético."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import time
from unittest.mock import Mock
import zipfile

import pytest

from onmyoji_daemon.mcp.excel.component.process import FileLease, private_memory_bytes, process_identity, same_process, terminate_owned
from onmyoji_daemon.mcp.excel.config.configuration import DEFAULTS, profile_defaults
from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.jobs import ExcelJobs
from onmyoji_daemon.mcp.excel.service.execution import execute, get_capabilities


def configured(tmp_path):
    instance, work = tmp_path / "instance", tmp_path / "work"
    (instance / "configs").mkdir(parents=True)
    work.mkdir()
    (instance / "configs" / "onmyoji-system.toml").write_text(
        '[codex]\nproject_directory = "' + work.as_posix() + '"\n', encoding="utf-8")
    profile = profile_defaults(instance)
    Path(profile["work_directory"]).mkdir(parents=True)
    return instance, work, profile


def workbook(path):
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
        package.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        package.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Dados" sheetId="1" r:id="rId1"/></sheets></workbook>')
        package.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        package.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>12</v></c><c r="B1"><f>A1*2</f><v>24</v></c></row></sheetData></worksheet>')


def wait_job(jobs, identifier):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = jobs.get("default", identifier)
        if state["status"] not in {"queued", "running"}:
            return state
        time.sleep(0.03)
    pytest.fail("O worker Excel não concluiu o job no prazo do teste.")


def test_process_identity_does_not_depend_on_pid_alone():
    identity = process_identity(os.getpid())
    assert identity is not None and identity["pid"] == os.getpid()
    assert same_process(identity, process_identity(os.getpid()))
    assert not same_process({**identity, "creation_time": "another"}, identity)
    assert not same_process(identity, None)
    assert private_memory_bytes(os.getpid()) > 0
    with pytest.raises(ExcelExceptions):
        process_identity(0)
    with pytest.raises(ExcelExceptions):
        terminate_owned(identity)


def test_file_lease_is_exclusive_and_released(tmp_path):
    path = tmp_path / "jobs" / "supervisor.lock"
    with FileLease(path):
        with pytest.raises(ExcelExceptions, match="Outro servidor"):
            FileLease(path).acquire()
    with FileLease(path):
        assert path.is_file()


def test_static_capabilities_never_import_com(monkeypatch):
    import sys
    before = set(sys.modules)
    result = get_capabilities({"allow_write": False})
    assert result["direct"]["available"] and not result["direct"]["calculates"]
    assert result["native"]["verified"] is False
    assert not result["features"]["apply_changes"]["available"]
    assert not (set(sys.modules) - before) & {"pythoncom", "win32com.client"}


@pytest.mark.parametrize("operation,args,profile", [
    ("apply_changes", {}, {}),
    ("get_vba_source", {}, {}),
    ("run_macro", {}, {"allow_write": True, "allow_macros": True}),
    ("refresh", {}, {"allow_write": True}),
    ("recalculate", {"calculation": {"refresh_connections": True}}, {"allow_write": True}),
    ("call_object", {}, {"allow_write": True}),
])
def test_worker_dispatch_rejects_policies_before_native_start(operation, args, profile):
    context = Mock(profile=profile, defaults=DEFAULTS)
    with pytest.raises(ExcelExceptions, match="desabilitad|exige"):
        execute(operation, args, context)


def test_direct_cannot_be_selected_for_writes():
    context = Mock(profile={"allow_write": True}, defaults=DEFAULTS)
    with pytest.raises(ExcelExceptions, match="somente de leitura"):
        execute("recalculate", {"backend": "direct"}, context)


def test_job_worker_reads_direct_and_idempotency_tracks_source(tmp_path):
    instance, work, profile = configured(tmp_path)
    source = work / "source.xlsx"
    workbook(source)
    original = source.read_bytes()
    jobs = ExcelJobs(instance)
    try:
        arguments = {"input_path": str(source), "backend": "direct"}
        first = jobs.submit("default", profile, DEFAULTS, "inspect", arguments, "read-001")
        assert jobs.submit("default", profile, DEFAULTS, "inspect", arguments, "read-001")["job_id"] == first["job_id"]
        state = wait_job(jobs, first["job_id"])
        assert state["status"] == "succeeded", state
        result = jobs.result("default", first["job_id"])["result"]
        assert result["backend"] == "direct" and result["artifacts"] == []
        assert source.read_bytes() == original
        source.write_bytes(original + b"changed")
        with pytest.raises(ValueError, match="request_id"):
            jobs.submit("default", profile, DEFAULTS, "inspect", arguments, "read-001")
    finally:
        jobs.close()


def test_vba_source_text_is_not_interpreted_as_input_path(tmp_path, monkeypatch):
    instance, work, profile = configured(tmp_path)
    jobs = ExcelJobs(instance)
    monkeypatch.setattr(jobs, "_launch", Mock(side_effect=OSError("worker disabled in test")))
    try:
        started = jobs.submit("default", profile, DEFAULTS, "edit_vba", {"source": "Option Explicit\nSub Test()\nEnd Sub"}, "code-001")
        request = json.loads(jobs._job_file(started["job_id"], "request.json").read_text(encoding="utf-8"))
        assert request["sources"] == {}
        assert request["arguments"]["source"].startswith("Option Explicit")
    finally:
        jobs.close()


def test_changed_policies_conflict_with_idempotency_key(tmp_path, monkeypatch):
    instance, work, profile = configured(tmp_path)
    jobs = ExcelJobs(instance)
    monkeypatch.setattr(jobs, "_launch", Mock(side_effect=OSError("worker disabled in test")))
    try:
        jobs.submit("default", profile, DEFAULTS, "inspect", {}, "policy-001")
        changed = {**profile, "allow_refresh": True}
        with pytest.raises(ValueError, match="request_id"):
            jobs.submit("default", changed, DEFAULTS, "inspect", {}, "policy-001")
    finally:
        jobs.close()


def test_cancel_queued_job_and_reject_unknown_profile(tmp_path, monkeypatch):
    instance, work, profile = configured(tmp_path)
    jobs = ExcelJobs(instance)
    monkeypatch.setattr(jobs, "_launch", Mock(side_effect=OSError("worker disabled in test")))
    try:
        with jobs._lock:
            started = jobs.submit("default", profile, DEFAULTS, "inspect", {}, "cancel-001")
            assert jobs.cancel("default", started["job_id"])["status"] == "cancelled"
        with pytest.raises(ValueError, match="não encontrada"):
            jobs.get("other", started["job_id"])
        with pytest.raises(ExcelExceptions):
            jobs.register("../escape", profile, DEFAULTS)
    finally:
        jobs.close()


def test_startup_recovers_interrupted_job_without_reexecuting(tmp_path, monkeypatch):
    instance, work, profile = configured(tmp_path)
    jobs = ExcelJobs(instance)
    monkeypatch.setattr(jobs, "_launch", Mock(side_effect=OSError("worker disabled in test")))
    with jobs._lock:
        started = jobs.submit("default", profile, DEFAULTS, "inspect", {}, "recover-001")
        directory = jobs._directories[started["job_id"]]
    jobs.close()
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    state["status"] = "running"
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    recovered = ExcelJobs(instance)
    try:
        recovered.register("default", profile, DEFAULTS)
        assert recovered.get("default", started["job_id"])["status"] == "interrupted"
        assert not recovered._processes
    finally:
        recovered.close()
