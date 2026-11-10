"""Handshake e execução documental pelo transporte MCP stdio real."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.document_processing.configuration import empty_configuration, profile_defaults


def configured(tmp_path):
    instance, workspace = tmp_path / "instance", tmp_path / "workspace"
    (instance / "configs").mkdir(parents=True)
    workspace.mkdir()
    (instance / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory = "' + workspace.as_posix() + '"\n', encoding="utf-8")
    config = McpConfiguration(instance)
    for namespace in ("pdf", "ocr"):
        data = empty_configuration()
        data["profiles"]["default"] = profile = profile_defaults(instance, namespace)
        for value in [*profile["readable_roots"], *profile["writable_roots"], profile["work_directory"]]:
            Path(value).mkdir(parents=True, exist_ok=True)
        getattr(config, "save_" + namespace)(data)
    return instance, workspace


def decode(result):
    return json.loads(next(item.text for item in result.content if item.type == "text"))


def parameters(instance, *, dependencies=True):
    from mcp.client.stdio import StdioServerParameters
    source = str(Path(__file__).resolve().parents[1] / "src")
    inherited = os.environ.get("PYTHONPATH", "") if dependencies else ""
    environment = {**os.environ, "PYTHONPATH": source + (os.pathsep + inherited if inherited else ""), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    return StdioServerParameters(command=sys.executable, args=["-B", "-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(instance)], env=environment)


def test_documents_stdio_handshake_schemas_and_isolated_pdf_job(tmp_path):
    pytest.importorskip("mcp")
    fitz = pytest.importorskip("pymupdf")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import stdio_client
    instance, workspace = configured(tmp_path)
    source = workspace / ".onmyoji/pdf/input/example.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((40, 40), "Documento stdio")
        doc.save(source)

    async def verify():
        async with stdio_client(parameters(instance)) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=15) as session:
                handshake = await session.initialize()
                assert "pdf-operations" in handshake.instructions and "ocr-operations" in handshake.instructions
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert "pdf.inspect" in tools and "ocr.recognize" in tools
                assert tools["pdf.inspect"].input_schema["additionalProperties"] is False
                assert "profile" not in tools["pdf.inspect"].input_schema["properties"]
                invalid = await session.call_tool("pdf.inspect", {"input_path": str(source), "request_id": "bad", "extra": True})
                assert invalid.is_error
                started = await session.call_tool("pdf.inspect", {"input_path": str(source), "request_id": "inspect-stdio"})
                assert not started.is_error
                identifier = decode(started)["job_id"]
                for _ in range(200):
                    response = await session.call_tool("pdf.get_job", {"job_id": identifier})
                    state = decode(response)
                    if state["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                        break
                    await asyncio.sleep(0.05)
                assert state["status"] == "succeeded", state
                result = await session.call_tool("pdf.get_job_result", {"job_id": identifier})
                assert decode(result)["result"]["pages"] == 1
                # Um ID do namespace PDF não concede acesso pelo namespace OCR.
                inaccessible = await session.call_tool("ocr.get_job", {"job_id": identifier})
                assert inaccessible.is_error
    asyncio.run(verify())


def test_stdio_remains_available_when_optional_document_engines_are_absent(tmp_path):
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import stdio_client
    instance, _ = configured(tmp_path)

    async def verify():
        async with stdio_client(parameters(instance, dependencies=False)) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=15) as session:
                await session.initialize()
                assert "pdf.inspect" in {tool.name for tool in (await session.list_tools()).tools}
                result = await session.call_tool("pdf.get_capabilities", {})
                assert not result.is_error
                assert "packages" in decode(result)
                workflow = await session.call_tool("workflows.read", {"id": "pdf-operations"})
                assert not workflow.is_error
    asyncio.run(verify())
