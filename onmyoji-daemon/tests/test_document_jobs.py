"""Contratos de fila, idempotência, reinício e resultado do worker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from onmyoji_daemon.mcp.document_processing.contracts import DocumentError
from onmyoji_daemon.mcp.document_processing.jobs import Jobs
from onmyoji_daemon.mcp.document_processing.worker import main as run_worker


def profile(tmp_path):
    root = tmp_path / "instance"; root.mkdir()
    workspace = tmp_path / "workspace"; workspace.mkdir()
    return root, {"readable_roots": [str(workspace)], "writable_roots": [str(workspace)], "work_directory": str(workspace / "jobs")}


def test_repeated_request_returns_original_job_and_rejects_changed_source(tmp_path):
    root, configured = profile(tmp_path)
    source = Path(configured["readable_roots"][0]) / "in.pdf"; source.write_bytes(b"first")
    jobs = Jobs(root, "pdf")
    try:
        with patch.object(jobs, "_launch", side_effect=OSError):
            first = jobs.submit("default", configured, {}, "inspect", {"input_path": str(source)}, "same")
            assert jobs.submit("default", configured, {}, "inspect", {"input_path": str(source)}, "same")["job_id"] == first["job_id"]
            source.write_bytes(b"changed")
            with pytest.raises(DocumentError, match="request_id"):
                jobs.submit("default", configured, {}, "inspect", {"input_path": str(source)}, "same")
            with pytest.raises(DocumentError, match="perfil"):
                jobs.get("other", first["job_id"])
    finally:
        jobs.close()


def test_restart_marks_unfinished_jobs_interrupted_without_repeating(tmp_path):
    root, configured = profile(tmp_path)
    jobs = Jobs(root, "pdf")
    try:
        with patch.object(jobs, "_launch", side_effect=OSError):
            submitted = jobs.submit("default", configured, {}, "inspect", {}, "pending")
            jobs.close()
        jobs = Jobs(root, "pdf"); jobs.register("default", configured)
        assert jobs.get("default", submitted["job_id"])["status"] in {"interrupted", "failed"}
        assert jobs.list("other") == {"jobs": [], "total": 0}
    finally:
        jobs.close()


def test_result_paginates_collections_and_cancel_is_idempotent(tmp_path):
    root, configured = profile(tmp_path)
    jobs = Jobs(root, "pdf")
    try:
        with jobs._lock:
            submitted = jobs.submit("default", configured, {}, "inspect", {}, "paging")
            identifier = submitted["job_id"]
            jobs._records[identifier]["status"] = "succeeded"
            (jobs._directories[identifier] / "result.json").write_text(json.dumps({"pages": list(range(5)), "engine": "test"}), encoding="utf-8")
        result = jobs.result("default", identifier, 2, 2)
        assert result["result"] == {"pages": [2, 3], "engine": "test"} and result["next_offset"] == 4
        assert jobs.cancel("default", identifier)["status"] == "succeeded"
        with pytest.raises(DocumentError, match="offset"):
            jobs.result("default", identifier, -1)
    finally:
        jobs.close()


def test_worker_dependency_failure_is_sanitized(tmp_path):
    root, configured = profile(tmp_path)
    directory = tmp_path / "request"; directory.mkdir()
    request = directory / "request.json"
    request.write_text(json.dumps({"namespace": "pdf", "root": str(root), "profile_name": "default", "profile": configured, "defaults": {}, "operation": "inspect", "arguments": {}}), encoding="utf-8")
    with patch.dict("sys.modules", {"onmyoji_daemon.mcp.pdf.service": None}):
        assert run_worker(str(request)) == 2
    completion = json.loads((directory / "completion.json").read_text(encoding="utf-8"))
    assert completion["error"]["code"] == "dependency_missing"
    assert "Traceback" not in str(completion)


def wait_job(jobs, identifier):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = jobs.get("default", identifier)
        if state["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
            return state
        time.sleep(0.05)
    raise AssertionError("O processo documental não concluiu o teste no prazo.")


def test_real_pdf_worker_reads_then_publishes_an_independent_document(tmp_path):
    fitz = pytest.importorskip("pymupdf")
    root, configured = profile(tmp_path)
    workspace = Path(configured["readable_roots"][0])
    source = workspace / "source.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((40, 40), "Texto de teste")
        doc.new_page().insert_text((40, 40), "Segunda pagina")
        doc.save(source)
    jobs = Jobs(root, "pdf")
    try:
        first = jobs.submit("default", configured, {}, "inspect", {"input_path": str(source)}, "inspect-real")
        state = wait_job(jobs, first["job_id"])
        assert state["status"] == "succeeded", state
        assert jobs.result("default", first["job_id"])["result"]["pages"] == 2
        output = workspace / "selected.pdf"
        second = jobs.submit("default", configured, {}, "select_pages", {"input_path": str(source), "output_path": str(output), "pages": "2", "confirm": True}, "select-real")
        state = wait_job(jobs, second["job_id"])
        assert state["status"] == "succeeded", state
        assert state["artifacts"][0]["path"] == str(output)
        with fitz.open(output) as doc:
            assert len(doc) == 1 and "Segunda" in doc[0].get_text()
        with fitz.open(source) as doc:
            assert len(doc) == 2
    finally:
        jobs.close()


def test_real_worker_fails_on_malformed_pdf_without_publication(tmp_path):
    pytest.importorskip("pymupdf")
    root, configured = profile(tmp_path)
    source = Path(configured["readable_roots"][0]) / "malformed.pdf"; source.write_bytes(b"not a pdf")
    jobs = Jobs(root, "pdf")
    try:
        submitted = jobs.submit("default", configured, {}, "inspect", {"input_path": str(source)}, "bad-real")
        state = wait_job(jobs, submitted["job_id"])
        assert state["status"] == "failed" and "Traceback" not in str(state)
        assert source.read_bytes() == b"not a pdf"
    finally:
        jobs.close()


def test_input_lists_participate_in_request_idempotency(tmp_path):
    root, configured = profile(tmp_path)
    source = Path(configured["readable_roots"][0]) / "source.pdf"; source.write_bytes(b"first")
    jobs = Jobs(root, "pdf")
    try:
        with jobs._lock:
            jobs.submit("default", configured, {}, "merge", {"input_paths": [str(source)]}, "merge-same")
            source.write_bytes(b"changed")
            with pytest.raises(DocumentError, match="request_id"):
                jobs.submit("default", configured, {}, "merge", {"input_paths": [str(source)]}, "merge-same")
    finally:
        jobs.close()


def test_large_nested_results_have_bounded_fragments_without_gaps(tmp_path):
    root, configured = profile(tmp_path)
    jobs = Jobs(root, "pdf")
    document = {"pages": [{"text": "x" * 70000}], "engine": "test"}
    try:
        with jobs._lock:
            submitted = jobs.submit("default", configured, {}, "extract_text", {}, "large-result")
            identifier = submitted["job_id"]
            jobs._records[identifier]["status"] = "succeeded"
            (jobs._directories[identifier] / "result.json").write_text(json.dumps(document), encoding="utf-8")
        fragments = []
        offset = 0
        while offset is not None:
            result = jobs.result("default", identifier, offset)
            assert len(result["result_fragment"]) <= 32000
            fragments.append(result["result_fragment"])
            offset = result["next_offset"]
        assert json.loads("".join(fragments)) == document
    finally:
        jobs.close()


def test_cancel_and_shutdown_terminate_running_worker_processes(tmp_path):
    root, configured = profile(tmp_path)
    jobs = Jobs(root, "pdf")
    children = []
    def launch(_identifier):
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        children.append(child)
        return child
    def running(identifier):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if jobs.get("default", identifier)["status"] == "running":
                return
            time.sleep(0.02)
        raise AssertionError("Worker não foi iniciado.")
    try:
        with patch.object(jobs, "_launch", side_effect=launch):
            first = jobs.submit("default", configured, {}, "inspect", {}, "cancel-running")
            running(first["job_id"])
            assert jobs.cancel("default", first["job_id"])["status"] == "cancelled"
            assert children[0].poll() is not None
            second = jobs.submit("default", configured, {}, "inspect", {}, "shutdown-running")
            running(second["job_id"])
            jobs.close()
            assert children[1].poll() is not None
    finally:
        jobs.close()
        for child in children:
            if child.poll() is None:
                child.kill(); child.wait()
