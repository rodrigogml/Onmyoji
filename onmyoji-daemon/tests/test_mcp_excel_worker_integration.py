"""Aceite opt-in de Excel real através do supervisor e worker privados."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest

from onmyoji_daemon.mcp.excel.component.process import process_identity
from onmyoji_daemon.mcp.excel.config.configuration import DEFAULTS, profile_defaults
from onmyoji_daemon.mcp.excel.jobs import ExcelJobs


pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("ONMYOJI_TEST_EXCEL") != "1", reason="Requer aceite explícito de Excel desktop real no host Windows.")


def configured(tmp_path):
    instance, work = tmp_path / "instance", tmp_path / "work"
    (instance / "configs").mkdir(parents=True)
    work.mkdir()
    (instance / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory="' + work.as_posix() + '"\n', encoding="utf-8")
    profile = profile_defaults(instance)
    return ExcelJobs(instance), profile, work


def wait_terminal(jobs, identifier):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        state = jobs.get("default", identifier)
        if state["status"] not in {"queued", "running"}:
            return state
        time.sleep(0.025)
    pytest.fail("O supervisor não concluiu o job nativo.")


def wait_owner(jobs, identifier):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with jobs._lock:
            path = jobs._job_file(identifier, "ownership.json")
            if path.exists() and jobs._records[identifier]["status"] == "running":
                return json.loads(path.read_text(encoding="utf-8"))
            if jobs._records[identifier]["status"] not in {"queued", "running"}:
                pytest.fail(str(jobs.get("default", identifier)))
        time.sleep(0.01)
    pytest.fail("O worker não registrou uma instância Excel própria.")


def assert_stopped(owner):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        actual = process_identity(owner["pid"])
        if actual is None or actual["creation_time"] != owner["creation_time"]:
            return
        time.sleep(0.025)
    pytest.fail("O processo Excel próprio permaneceu ativo.")


def test_native_worker_creates_recalculates_and_publishes_verified_copy(tmp_path):
    jobs, profile, work = configured(tmp_path)
    source, output = work / "source.xlsx", work / "updated.xlsx"
    try:
        created = jobs.submit("default", profile, DEFAULTS, "create", {
            "output_path": str(source), "operations": [
                {"type": "sheet", "action": "add", "name": "Dados"},
                {"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[12], [7]]},
                {"type": "set_formula", "sheet": "Dados", "range": "B1", "formula": "=SUM(A1:A2)"},
            ], "calculation": {"mode": "full_rebuild"}, "validation": {"reject_new_formula_errors": True}}, "create-real")
        state = wait_terminal(jobs, created["job_id"])
        assert state["status"] == "succeeded", json.dumps(state, ensure_ascii=False)
        original = source.read_bytes()
        changed = jobs.submit("default", profile, DEFAULTS, "apply_changes", {
            "input_path": str(source), "output_path": str(output), "operations": [
                {"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[20]]}
            ], "calculation": {"mode": "full_rebuild"}, "validation": {"reject_new_formula_errors": True}}, "change-real")
        state = wait_terminal(jobs, changed["job_id"])
        assert state["status"] == "succeeded", json.dumps(state, ensure_ascii=False)
        assert source.read_bytes() == original and output.is_file()
        read = jobs.submit("default", profile, DEFAULTS, "read_range", {
            "input_path": str(output), "backend": "direct", "sheet": "Dados", "range": "B1", "fields": ["formula", "cached_value"]}, "read-real")
        state = wait_terminal(jobs, read["job_id"])
        assert state["status"] == "succeeded", state
        result = jobs.result("default", read["job_id"])["result"]
        assert result["cells"][0]["cached_value"] == 27
        for identifier in (created["job_id"], changed["job_id"]):
            assert_stopped(json.loads(jobs._job_file(identifier, "ownership.json").read_text(encoding="utf-8")))
    finally:
        jobs.close()


@pytest.mark.parametrize("ending", ["cancel", "timeout", "memory"])
def test_native_worker_ends_only_its_owned_excel(tmp_path, ending):
    jobs, profile, work = configured(tmp_path)
    try:
        # A probe has no external documents or side effects. Pause supervision
        # immediately after ownership registration to inject the terminal event.
        started = jobs.submit("default", profile, DEFAULTS, "native_probe", {}, "stop-real")
        owner = wait_owner(jobs, started["job_id"])
        with jobs._lock:
            assert jobs._records[started["job_id"]]["status"] == "running"
            if ending == "cancel":
                state = jobs.cancel("default", started["job_id"])
            elif ending == "timeout":
                jobs._records[started["job_id"]]["started_at"] = time.time() - DEFAULTS["timeout_seconds"] - 1
            else:
                jobs._memory_limits["default"] = 1
        state = wait_terminal(jobs, started["job_id"])
        assert state["status"] == ("cancelled" if ending == "cancel" else "failed"), state
        if ending != "cancel":
            assert state["error"]["code"] == ("deadline_exceeded" if ending == "timeout" else "memory_limit_exceeded"), state
        assert_stopped(owner)
        assert not list(work.glob("*.xlsx"))
    finally:
        jobs.close()


def test_native_presentation_views_sparklines_shapes_controls_and_chart_exports(tmp_path):
    jobs, profile, work = configured(tmp_path)
    output = work / "presentation.xlsx"
    try:
        created = jobs.submit("default", profile, DEFAULTS, "create", {
            "output_path": str(output), "operations": [
                {"type": "sheet", "action": "add", "name": "Dados"},
                {"type": "set_values", "sheet": "Dados", "range": "A1", "values": [
                    ["Produto", "Q1", "Q2", "Q3"], ["A", 10, 20, 30], ["B", 20, 30, 40], ["C", 30, 40, 50]]},
                {"type": "sparkline", "action": "create", "sheet": "Dados", "range": "F2:F4", "source_range": "B2:D4", "sparkline_type": "line"},
                {"type": "shape", "action": "add", "sheet": "Dados", "name": "Titulo", "text": "Vendas", "left": 400, "top": 0},
                {"type": "control", "action": "add", "sheet": "Dados", "name": "Opcao", "control_type": "checkbox", "caption": "Incluir", "left": 400, "top": 80},
                {"type": "chart", "action": "create", "sheet": "Dados", "name": "Vendas", "range": "A1:D4", "chart_type": 51,
                 "has_legend": True, "axes": [{"axis": "value", "title": "Valor", "minimum": 0}], "data_labels": {"show_value": True}},
                {"type": "view", "sheet": "Dados", "freeze_rows": 1, "freeze_columns": 1, "zoom": 90, "show_gridlines": False},
            ], "calculation": {"mode": "full_rebuild"}}, "presentation-create")
        state = wait_terminal(jobs, created["job_id"])
        assert state["status"] == "succeeded", json.dumps(state, ensure_ascii=False)
        from onmyoji_daemon.mcp.excel.component.ooxml import SpreadsheetPackage
        import zipfile
        with SpreadsheetPackage(output) as package:
            part = package.sheet("Dados")["part"]
        with zipfile.ZipFile(output) as archive:
            sheet_xml = archive.read(part)
            assert b'zoomScale="90"' in sheet_xml and b'state="frozen"' in sheet_xml
            assert b"sparklineGroups" in sheet_xml
        for selected_format in ("json", "png"):
            arguments = {"input_path": str(output), "output_path": str(work / ("export." + selected_format)),
                         "format": selected_format, "sheet": "Dados"}
            if selected_format == "png":
                arguments["chart_name"] = "Vendas"
            exported = jobs.submit("default", profile, DEFAULTS, "export", arguments, "presentation-export-" + selected_format)
            state = wait_terminal(jobs, exported["job_id"])
            assert state["status"] == "succeeded", json.dumps(state, ensure_ascii=False)
        assert (work / "export.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert json.loads((work / "export.json").read_text(encoding="utf-8"))["values"][1][1] == 10
    finally:
        jobs.close()


@pytest.mark.parametrize("export_format", ["csv", "pdf"])
def test_native_export_calculates_before_materializing_output(tmp_path, export_format):
    """Uma exportação solicitada com rebuild não pode entregar o cache anterior."""
    if export_format == "pdf":
        pytest.importorskip("pypdf")
    jobs, profile, work = configured(tmp_path)
    initial, stale, output = work / "initial.xlsx", work / "stale.xlsx", work / ("fresh." + export_format)
    try:
        created = jobs.submit("default", profile, DEFAULTS, "create", {
            "output_path": str(initial), "operations": [
                {"type": "sheet", "action": "add", "name": "Dados"},
                {"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[2]]},
                {"type": "set_formula", "sheet": "Dados", "range": "B1", "formula": "=A1+3"}
            ], "calculation": {"mode": "full_rebuild"}}, "export-cache-create")
        state = wait_terminal(jobs, created["job_id"])
        assert state["status"] == "succeeded", state
        edited = jobs.submit("default", profile, DEFAULTS, "apply_changes", {
            "input_path": str(initial), "output_path": str(stale), "operations": [
                {"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[10]]}
            ], "calculation": {"mode": "none"}}, "export-cache-edit")
        state = wait_terminal(jobs, edited["job_id"])
        assert state["status"] == "succeeded", state
        from onmyoji_daemon.mcp.excel.component.ooxml import SpreadsheetPackage
        with SpreadsheetPackage(stale) as package:
            assert package.cells("Dados")["B1"]["cached_value"] == 5
        exported = jobs.submit("default", profile, DEFAULTS, "export", {
            "input_path": str(stale), "output_path": str(output), "format": export_format, "sheet": "Dados",
            "encoding": "utf-8", "calculation": {"mode": "full_rebuild"}}, "export-cache-final")
        state = wait_terminal(jobs, exported["job_id"])
        assert state["status"] == "succeeded", state
        if export_format == "csv":
            import csv
            with output.open(encoding="utf-8", newline="") as stream:
                values = next(csv.reader(stream))
            assert float(values[0]) == 10 and float(values[1]) == 13
        else:
            assert output.read_bytes().startswith(b"%PDF-")
            from pypdf import PdfReader
            import re
            pdf_text = " ".join(page.extract_text() or "" for page in PdfReader(output).pages)
            assert re.search(r"\b13\b", pdf_text), pdf_text
        with SpreadsheetPackage(stale) as package:
            assert package.cells("Dados")["B1"]["cached_value"] == 5
    finally:
        jobs.close()
