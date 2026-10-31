"""Contratos, integração e jobs documentais com fontes sintéticas locais."""
from __future__ import annotations

import copy
import time
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.document_processing.configuration import empty_configuration, profile_defaults
from onmyoji_daemon.mcp.document_processing.contracts import DocumentError
from onmyoji_daemon.mcp.pdf.tools import PdfToolRegistry


def configured(tmp_path, namespace="pdf"):
    instance = tmp_path / "instance"; instance.mkdir(exist_ok=True)
    workspace = tmp_path / "workspace"; workspace.mkdir(exist_ok=True)
    (instance / "configs").mkdir(exist_ok=True)
    (instance / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory = "' + workspace.as_posix() + '"\n', encoding="utf-8")
    config = McpConfiguration(instance); data = empty_configuration()
    data["profiles"]["default"] = profile = profile_defaults(instance, namespace)
    for value in [*profile["readable_roots"], *profile["writable_roots"], profile["work_directory"]]:
        from pathlib import Path
        Path(value).mkdir(parents=True, exist_ok=True)
    getattr(config, "save_" + namespace)(data)
    return config, data, workspace


def test_document_config_roundtrip_and_rejects_outside_workspace(tmp_path):
    config, data, workspace = configured(tmp_path)
    assert config.pdf() == data
    data["profiles"]["default"]["writable_roots"] = [str(tmp_path)]
    with pytest.raises(ValueError, match="workspace"):
        config.save_pdf(data)


def test_pdf_tools_are_opt_in_and_validate_closed_schemas(tmp_path):
    registry = PdfToolRegistry(McpConfiguration(tmp_path))
    assert registry.definitions() == []
    config, data, _ = configured(tmp_path)
    registry = PdfToolRegistry(config)
    try:
        tools = {tool["name"]: tool for tool in registry.definitions()}
        assert "pdf.make_searchable" in tools and "pdf.forms" in tools
        assert "profile" not in tools["pdf.inspect"]["input_schema"]["properties"]
        with pytest.raises(DocumentError, match="Parâmetros"):
            registry.call("pdf.inspect", {"input_path": "x", "request_id": "one", "unknown": True})
        with pytest.raises(DocumentError):
            registry.call("pdf.protect", {"input_path": "x", "output_path": "y", "action": "encrypt", "request_id": "two"})
        data["profiles"]["second"] = copy.deepcopy(data["profiles"]["default"])
        config.save_pdf(data)
        assert "pdf.list_profiles" in {tool["name"] for tool in registry.definitions()}
    finally:
        registry.close()


def test_setup_creates_a_pdf_profile_using_enter_defaults(tmp_path):
    from pathlib import Path
    from onmyoji_daemon.mcp.document_processing.setup import document_menu
    config, _, workspace = configured(tmp_path)
    config.save_pdf(empty_configuration())
    ui = Mock(); ui.prompt.side_effect = ["1", "n", "x"]
    ui.ask_new_profile_name.return_value = "default"
    ui.ask.side_effect = lambda label, current="", required=False: current
    ui.flow_choice.return_value = "Nenhum"

    document_menu(config, ui, "pdf")

    profile = config.pdf()["profiles"]["default"]
    assert profile["ocr_profile"] == ""
    assert profile["readable_roots"][-1] == profile["writable_roots"][0]
    assert Path(profile["work_directory"]).is_dir()
    assert all(Path(path).is_dir() for path in profile["readable_roots"])


def test_pdf_worker_publishes_new_artifact_and_keeps_source(tmp_path):
    fitz = pytest.importorskip("pymupdf")
    config, data, workspace = configured(tmp_path)
    source = workspace / ".onmyoji/pdf/input/source.pdf"
    output = workspace / ".onmyoji/pdf/output/rotated.pdf"
    with fitz.open() as doc:
        page = doc.new_page(); page.insert_text((70, 70), "Documento de teste")
        doc.save(source)
    original = source.read_bytes()
    registry = PdfToolRegistry(config)
    try:
        args = {"input_path": str(source), "output_path": str(output), "action": "rotate", "angle": 90, "request_id": "rotate-one", "confirm": True}
        started = registry.call("pdf.transform_pages", args)
        assert started["job_id"] == registry.call("pdf.transform_pages", args)["job_id"]
        for _ in range(200):
            state = registry.call("pdf.get_job", {"job_id": started["job_id"]})
            if state["status"] in {"succeeded", "failed", "cancelled", "interrupted"}: break
            time.sleep(0.05)
        assert state["status"] == "succeeded", state
        with fitz.open(output) as doc:
            assert doc[0].rotation == 90
        assert source.read_bytes() == original
        result = registry.call("pdf.get_job_result", {"job_id": started["job_id"]})
        assert "sha256" in str(result)
    finally:
        registry.close()
