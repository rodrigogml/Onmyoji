"""Regressões de isolamento, IPC e supervisão em processos próprios e sintéticos.

Nenhum teste deste módulo inicia Microsoft Excel. Subprocessos executam somente
Python de teste ou o worker em sua fase anterior à ativação nativa.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import Mock
from uuid import uuid4

import pytest

from onmyoji_daemon.mcp.document_processing.jobs import _write_json
from onmyoji_daemon.mcp.document_processing.paths import file_digest
from onmyoji_daemon.mcp.excel.component.process import process_identity, same_process
from onmyoji_daemon.mcp.excel.config.configuration import DEFAULTS, profile_defaults
from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.jobs import ExcelJobs
from onmyoji_daemon.mcp.excel.service.execution import execute


SOURCE = Path(__file__).resolve().parents[1] / "src"


def configured(tmp_path):
    instance, work = tmp_path / "instance", tmp_path / "workspace"
    (instance / "configs").mkdir(parents=True)
    work.mkdir()
    (instance / "configs" / "onmyoji-system.toml").write_text(
        '[codex]\nproject_directory = "' + work.as_posix() + '"\n', encoding="utf-8")
    profile = profile_defaults(instance)
    Path(profile["work_directory"]).mkdir(parents=True)
    return instance, work, profile


def process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}


def child_environment():
    return {**os.environ, "PYTHONPATH": str(SOURCE), "PYTHONSAFEPATH": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def wait_terminal(jobs, identifier, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = jobs.get("default", identifier)
        if state["status"] not in {"queued", "running"}:
            return state
        time.sleep(0.025)
    pytest.fail("O processo sintético não terminou no prazo do teste.")


def stop_child(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def test_recursive_source_fingerprints_invalidate_idempotency_for_auxiliary_assets(tmp_path):
    instance, work, profile = configured(tmp_path)
    source = work / "source.xlsx"; source.write_bytes(b"workbook-fixture-not-opened")
    asset = work / "image.png"; asset.write_bytes(b"first-image-fixture")
    jobs = ExcelJobs(instance)
    try:
        arguments = {"input_path": str(source), "operations": [{"type": "image", "action": "add", "input_path": str(asset)}]}
        with jobs._lock:
            first = jobs.submit("default", profile, DEFAULTS, "apply_changes", arguments, "asset-001")
            request = json.loads(jobs._job_file(first["job_id"], "request.json").read_text(encoding="utf-8"))
            assert set(request["sources"]) == {str(source), str(asset)}
            asset.write_bytes(b"different-image-fixture")
            with pytest.raises(ValueError) as caught:
                jobs.submit("default", profile, DEFAULTS, "apply_changes", arguments, "asset-001")
            assert caught.value.code == "request_conflict"
            jobs.cancel("default", first["job_id"])
    finally:
        jobs.close()


def test_nested_source_outside_workspace_is_rejected_before_queueing(tmp_path):
    instance, work, profile = configured(tmp_path)
    source = work / "source.xlsx"; source.write_bytes(b"source")
    external = tmp_path / "outside.png"; external.write_bytes(b"not-authorized")
    jobs = ExcelJobs(instance)
    try:
        with pytest.raises(ValueError) as caught:
            jobs.submit("default", profile, DEFAULTS, "apply_changes", {
                "input_path": str(source), "operations": [{"type": "image", "input_path": str(external)}]}, "outside-001")
        assert caught.value.code == "path_not_allowed"
        assert jobs._records == {}
        assert external.read_bytes() == b"not-authorized"
    finally:
        jobs.close()


def test_userform_resource_fingerprints_and_sibling_restriction(tmp_path):
    instance, work, profile = configured(tmp_path)
    module = work / "Form.frm"; module.write_text('VERSION 5.00\nOleObjectBlob = "Form.frx":0000\n', encoding="utf-8")
    resource = work / "Form.frx"; resource.write_bytes(b"resource")
    jobs = ExcelJobs(instance)
    try:
        sources = jobs._input_fingerprints(profile, {"input_module_path": str(module), "source": "Option Explicit"}, 1024)
        assert set(sources) == {str(module), str(resource)}
        resource.write_bytes(b"changed-resource")
        assert jobs._input_fingerprints(profile, {"input_module_path": str(module)}, 1024)[str(resource)] != sources[str(resource)]
        module.write_text('OleObjectBlob = "../outside.frx":0000\n', encoding="utf-8")
        with pytest.raises(ExcelExceptions) as caught:
            jobs._input_fingerprints(profile, {"input_module_path": str(module)}, 1024)
        assert caught.value.code == "path_not_allowed"
    finally:
        jobs.close()


def test_real_worker_routes_pk_literal_csv_as_text_and_preserves_source(tmp_path):
    instance, work, profile = configured(tmp_path)
    source = work / "country.csv"; source.write_bytes(b"PK,description\r\n001,product\r\n")
    original = source.read_bytes()
    jobs = ExcelJobs(instance)
    try:
        started = jobs.submit("default", profile, DEFAULTS, "read_range", {
            "input_path": str(source), "sheet": "Dados", "range": "A1:B2", "backend": "auto"}, "pk-csv-001")
        state = wait_terminal(jobs, started["job_id"])
        assert state["status"] == "succeeded", state
        result = jobs.result("default", started["job_id"])["result"]
        assert result["format"] == "csv" and result["cells"][0]["value"] == "PK"
        assert result["cells"][2]["value"] == "001"
        assert source.read_bytes() == original and state.get("artifacts") == []
    finally:
        jobs.close()


def test_worker_rejects_source_mutated_after_submission(tmp_path):
    instance, work, profile = configured(tmp_path)
    source = work / "changed.csv"; source.write_text("Name\nBefore\n", encoding="utf-8")
    jobs = ExcelJobs(instance)
    try:
        with jobs._lock:
            started = jobs.submit("default", profile, DEFAULTS, "inspect", {"input_path": str(source)}, "changed-001")
            source.write_text("Name\nAfter\n", encoding="utf-8")
        state = wait_terminal(jobs, started["job_id"])
        assert state["status"] == "failed" and state["error"]["code"] == "source_changed"
        assert not (jobs._directories[started["job_id"]] / "result.json").exists()
    finally:
        jobs.close()


def test_completed_job_survives_restart_without_reexecution(tmp_path, monkeypatch):
    instance, work, profile = configured(tmp_path)
    source = work / "read.csv"; source.write_text("Nome\nSintético\n", encoding="utf-8")
    arguments = {"input_path": str(source)}
    first = ExcelJobs(instance)
    try:
        started = first.submit("default", profile, DEFAULTS, "inspect", arguments, "restart-001")
        assert wait_terminal(first, started["job_id"])["status"] == "succeeded"
    finally:
        first.close()
    second = ExcelJobs(instance)
    launch = Mock(side_effect=AssertionError("Resultado terminal não pode iniciar novo worker."))
    monkeypatch.setattr(second, "_launch", launch)
    try:
        resumed = second.submit("default", profile, DEFAULTS, "inspect", arguments, "restart-001")
        assert resumed["job_id"] == started["job_id"] and resumed["status"] == "succeeded"
        assert second.result("default", resumed["job_id"])["result"]["format"] == "csv"
        assert not second._processes and not launch.called
    finally:
        second.close()


def test_second_supervisor_process_cannot_reconcile_active_profile(tmp_path):
    instance, work, profile = configured(tmp_path)
    jobs = ExcelJobs(instance)
    try:
        with jobs._lock:
            started = jobs.submit("default", profile, DEFAULTS, "inspect", {}, "lease-001")
            state_path = jobs._job_file(started["job_id"], "state.json")
            original = state_path.read_bytes()
            payload_path = work / "lease-payload.json"
            payload_path.write_text(json.dumps({"root": str(instance), "profile": profile}), encoding="utf-8")
            code = '''import json, sys
from pathlib import Path
from onmyoji_daemon.mcp.excel.jobs import ExcelJobs
from onmyoji_daemon.mcp.excel.config.configuration import DEFAULTS
from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
jobs = ExcelJobs(Path(payload["root"]))
try:
    jobs.register("default", payload["profile"], DEFAULTS)
    print("unexpected-success")
except ExcelExceptions as error:
    print(error.code)
finally:
    jobs.close()
'''
            child = subprocess.run([sys.executable, "-B", "-c", code, str(payload_path)], capture_output=True,
                                   text=True, timeout=10, env=child_environment(), **process_options())
            assert child.returncode == 0, child.stderr
            assert child.stdout.strip() == "profile_busy"
            assert state_path.read_bytes() == original
            jobs.cancel("default", started["job_id"])
    finally:
        jobs.close()


@pytest.mark.parametrize("failure", ["cancel", "deadline", "memory"])
def test_real_owned_python_worker_is_stopped_on_cancel_deadline_or_memory(tmp_path, monkeypatch, failure):
    instance, work, profile = configured(tmp_path)
    jobs = ExcelJobs(instance)
    launched = []
    code = "import time; allocation = bytearray(64 * 1024 * 1024); time.sleep(30)"
    def launch(identifier):
        process = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-B", "-c", code], cwd=jobs._directories[identifier],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=child_environment(), **process_options())
        launched.append(process)
        return process
    monkeypatch.setattr(jobs, "_launch", launch)
    defaults = {**DEFAULTS, "timeout_seconds": 1 if failure == "deadline" else 20,
                "max_memory_bytes": 16777216 if failure == "memory" else DEFAULTS["max_memory_bytes"]}
    try:
        started = jobs.submit("default", profile, defaults, "inspect", {}, "stop-" + failure)
        deadline = time.monotonic() + 10
        while not launched and time.monotonic() < deadline:
            time.sleep(0.025)
        assert launched, "O subprocesso sintético não iniciou."
        if failure == "cancel":
            state = jobs.cancel("default", started["job_id"])
            assert state["status"] == "cancelled"
        else:
            state = wait_terminal(jobs, started["job_id"])
            expected = "deadline_exceeded" if failure == "deadline" else "memory_limit_exceeded"
            assert state["status"] == "failed" and state["error"]["code"] == expected, state
        assert launched[0].poll() is not None
        assert not jobs._processes
    finally:
        jobs.close()
        for process in launched:
            stop_child(process)


@pytest.mark.parametrize("changed", [False, True])
def test_rollback_deletes_only_matching_publication_identity_and_hash(tmp_path, changed):
    instance, work, profile = configured(tmp_path)
    jobs = ExcelJobs(instance)
    try:
        with jobs._lock:
            started = jobs.submit("default", profile, DEFAULTS, "inspect", {}, "rollback-001")
            destination = work / "result.xlsx"; destination.write_bytes(b"owned-completed-artifact")
            identity = destination.stat()
            journal = {"artifacts": [{"path": str(destination), "sha256": file_digest(destination),
                                      "inode": identity.st_ino, "device": identity.st_dev, "bytes": identity.st_size}]}
            _write_json(jobs._job_file(started["job_id"], "publication.json"), journal)
            if changed:
                destination.write_bytes(b"user-modified-artifact")
            jobs._rollback_completed(started["job_id"])
            assert destination.exists() is changed
            if changed:
                assert destination.read_bytes() == b"user-modified-artifact"
            jobs.cancel("default", started["job_id"])
    finally:
        jobs.close()


def test_recovery_does_not_delete_publication_outside_current_roots(tmp_path):
    instance, work, profile = configured(tmp_path)
    outside = tmp_path / "outside.xlsx"; outside.write_bytes(b"unowned-file")
    jobs = ExcelJobs(instance)
    try:
        with jobs._lock:
            started = jobs.submit("default", profile, DEFAULTS, "inspect", {}, "outside-rollback-001")
            identity = outside.stat()
            _write_json(jobs._job_file(started["job_id"], "publication.json"), {"artifacts": [
                {"path": str(outside), "sha256": file_digest(outside), "inode": identity.st_ino, "device": identity.st_dev}]})
            jobs._rollback_completed(started["job_id"])
            assert outside.read_bytes() == b"unowned-file"
            jobs.cancel("default", started["job_id"])
    finally:
        jobs.close()


@pytest.mark.parametrize("incomplete", [{"executable": "python.exe"}, {"pid": 1, "executable": "python.exe"},
                                        {"pid": None, "creation_time": None, "session_id": None, "executable": "python.exe"}])
def test_incomplete_process_identity_never_proves_ownership(incomplete):
    assert not same_process(incomplete, incomplete)


def test_worker_exits_when_supervisor_identity_dies_before_activation(tmp_path):
    instance, work, profile = configured(tmp_path)
    identifier = uuid4().hex
    directory = Path(profile["work_directory"]) / "document-jobs" / "excel" / "default" / identifier
    directory.mkdir(parents=True)
    request_path, announcement = directory / "request.json", directory / "child.json"
    source = work / "input.csv"; source.write_text("Nome\nDado\n", encoding="utf-8")
    _write_json(request_path, {"root": str(instance), "namespace": "excel", "profile_name": "default", "profile": profile,
                               "defaults": DEFAULTS, "operation": "inspect", "arguments": {"input_path": str(source)},
                               "sources": {str(source): file_digest(source)}})
    code = '''import json, os, subprocess, sys, time
from pathlib import Path
from onmyoji_daemon.mcp.document_processing.jobs import _write_json, execution_environment
from onmyoji_daemon.mcp.excel.component.process import process_identity
request_path, announcement, source = (Path(value) for value in sys.argv[1:])
request = json.loads(request_path.read_text(encoding="utf-8"))
request["parent_identity"] = process_identity(os.getpid())
_write_json(request_path, request)
environment = execution_environment(request_path.parent, str(source))
environment.pop("PYTHONNOUSERSITE", None)
options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
worker = subprocess.Popen([sys.executable, "-B", "-m", "onmyoji_daemon.mcp.excel.worker", str(request_path)], cwd=request_path.parent,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment, **options)
_write_json(announcement, {"worker_identity": process_identity(worker.pid)})
time.sleep(30)
'''
    supervisor = subprocess.Popen([sys.executable, "-B", "-c", code, str(request_path), str(announcement), str(SOURCE)],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  env=child_environment(), **process_options())
    worker_identity = None
    try:
        deadline = time.monotonic() + 10
        while not announcement.exists() and time.monotonic() < deadline and supervisor.poll() is None:
            time.sleep(0.025)
        assert announcement.is_file(), "O supervisor sintético não anunciou seu worker."
        worker_identity = json.loads(announcement.read_text(encoding="utf-8"))["worker_identity"]
        assert same_process(worker_identity, process_identity(worker_identity["pid"]))
        # Let the real worker validate the parent and enter its pre-activation
        # wait; no activation file exists, so COM cannot start in this test.
        time.sleep(0.5)
        stop_child(supervisor)
        deadline = time.monotonic() + 5
        while same_process(worker_identity, process_identity(worker_identity["pid"])) and time.monotonic() < deadline:
            time.sleep(0.025)
        assert process_identity(worker_identity["pid"]) is None
        assert not (directory / "ownership.json").exists()
    finally:
        stop_child(supervisor)
        if worker_identity and same_process(worker_identity, process_identity(worker_identity["pid"])):
            # Only this test's recorded Python worker is eligible for cleanup.
            assert Path(worker_identity["executable"]).name.casefold().startswith("python")
            os.kill(worker_identity["pid"], signal.SIGTERM)
