"""Contrato Excel pelo transporte MCP stdio real, usando OOXML sintético."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.excel.config.configuration import empty_configuration, profile_defaults


def configured(tmp_path):
    instance, work = tmp_path / "instance", tmp_path / "work"
    (instance / "configs").mkdir(parents=True)
    work.mkdir()
    (instance / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory="' + work.as_posix() + '"\n', encoding="utf-8")
    config = McpConfiguration(instance)
    data = empty_configuration()
    data["profiles"]["default"] = profile_defaults(instance)
    config.save_excel(data)
    source = work / "example.xlsx"
    with ZipFile(source, "w") as package:
        package.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        package.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        package.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Dados" sheetId="1" r:id="rId1"/></sheets></workbook>')
        package.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        package.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:B2"/><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Entrada</t></is></c><c r="B1"><v>2</v></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>Saída</t></is></c><c r="B2"><f>SUM(B1,1)</f><v>3</v></c></row></sheetData></worksheet>')
    return instance, work, source


def decode(result):
    return json.loads(next(item.text for item in result.content if item.type == "text"))


def test_excel_stdio_real_job_workflow_and_sanitized_contract_errors(tmp_path):
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    instance, _, source = configured(tmp_path)
    original = source.read_bytes()
    code_source = str(Path(__file__).parents[1] / "src")
    environment = {**os.environ, "PYTHONPATH": code_source + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    parameters = StdioServerParameters(command=sys.executable, args=["-B", "-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(instance)], env=environment)

    async def verify():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=20) as session:
                handshake = await session.initialize()
                assert "excel-operations" in handshake.instructions
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert {"excel.inspect", "excel.apply_changes", "excel.run_macro", "excel.read_range"} <= tools.keys()
                workflow = await session.call_tool("workflows.read", {"id": "excel-operations"})
                assert not workflow.is_error and "snapshot_id" in decode(workflow)["content"]
                invalid = await session.call_tool("excel.inspect", {"input_path": str(source), "request_id": "bad", "extra": True})
                assert invalid.is_error and decode(invalid)["error"]["code"] == "invalid_argument"
                request = {"input_path": str(source), "request_id": "stdio-inspect", "backend": "direct"}
                started = await session.call_tool("excel.inspect", request)
                assert not started.is_error, decode(started)
                identifier = decode(started)["job_id"]
                duplicate = await session.call_tool("excel.inspect", request)
                assert decode(duplicate)["job_id"] == identifier
                for _ in range(300):
                    response = await session.call_tool("excel.get_job", {"job_id": identifier})
                    state = decode(response)
                    if state["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                        break
                    await asyncio.sleep(0.05)
                assert state["status"] == "succeeded", state
                result = await session.call_tool("excel.get_job_result", {"job_id": identifier})
                assert not result.is_error
                assert decode(result)["result"]["backend"] == "direct"
                assert "Dados" in str(decode(result))
                missing = await session.call_tool("excel.get_job", {"job_id": "0" * 32})
                assert missing.is_error and decode(missing)["error"]["code"] == "job_not_found"
                denied = await session.call_tool("excel.run_macro", {"input_path": str(source), "request_id": "macro", "procedure": "Main", "confirm": True})
                assert denied.is_error
                assert decode(denied)["error"]["code"] == "macro_execution_disabled"
    asyncio.run(verify())
    assert source.read_bytes() == original


@pytest.mark.skipif(os.name != "nt" or os.environ.get("ONMYOJI_TEST_EXCEL") != "1", reason="Exige Windows/Excel e ONMYOJI_TEST_EXCEL=1.")
def test_excel_stdio_native_create_edit_recalculate_and_read_persisted_cache(tmp_path):
    """Exercita o caminho completo MCP > worker > COM > arquivo e leitura salva."""
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    instance, work, _ = configured(tmp_path)
    source, output = work / "created.xlsx", work / "updated.xlsx"
    code_source = str(Path(__file__).parents[1] / "src")
    environment = {**os.environ, "PYTHONPATH": code_source + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    parameters = StdioServerParameters(command=sys.executable, args=["-B", "-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(instance)], env=environment)

    async def verify():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=20) as session:
                await session.initialize()

                async def run(tool, arguments):
                    response = await session.call_tool("excel." + tool, arguments)
                    assert not response.is_error, decode(response)
                    identifier = decode(response)["job_id"]
                    for _ in range(600):
                        response = await session.call_tool("excel.get_job", {"job_id": identifier})
                        state = decode(response)
                        if state["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                            break
                        await asyncio.sleep(.05)
                    assert state["status"] == "succeeded", state
                    result = await session.call_tool("excel.get_job_result", {"job_id": identifier})
                    assert not result.is_error, decode(result)
                    return decode(result)["result"]

                created = await run("create", {"request_id": "create-native-stdio", "output_path": str(source), "confirm": True,
                    "operations": [{"type": "sheet", "action": "add", "name": "Dados"},
                                   {"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[1], [2]]},
                                   {"type": "set_formula", "sheet": "Dados", "range": "B1", "formula": "=SUM(A1:A2)"}],
                    "calculation": {"mode": "full_rebuild"}})
                assert created["calculation"]["status"] == "completed"
                original = source.read_bytes()
                changed = await run("apply_changes", {"request_id": "change-native-stdio", "input_path": str(source), "output_path": str(output), "confirm": True,
                    "operations": [{"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[5]]}],
                    "calculation": {"mode": "full_rebuild"}})
                assert changed["persisted_cache"]["status"] == "completed"
                assert source.read_bytes() == original
                for backend in ("direct", "native"):
                    read = await run("read_range", {"request_id": "read-" + backend, "input_path": str(output), "backend": backend,
                        "sheet": "Dados", "range": "B1", "fields": ["value", "formula", "cached_value"]})
                    cell = read["cells"][0]
                    assert cell["value"] is None
                    assert cell["cached_value"] == 7
                    assert "SUM(A1:A2)" in cell["formula"]
    asyncio.run(verify())


@pytest.mark.skipif(os.name != "nt" or os.environ.get("ONMYOJI_TEST_EXCEL") != "1", reason="Exige Windows/Excel e ONMYOJI_TEST_EXCEL=1.")
def test_excel_stdio_native_export_uses_results_of_requested_calculation(tmp_path):
    """Uma exportação com rebuild deve consumir o cálculo novo, não o cache antigo."""
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    instance, work, source = configured(tmp_path)
    with ZipFile(source) as package:
        parts = {name: package.read(name) for name in package.namelist()}
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(b'<c r="B1"><v>2</v></c>', b'<c r="B1"><v>10</v></c>')
    with ZipFile(source, "w") as package:
        for name, content in parts.items():
            package.writestr(name, content)
    original = source.read_bytes()
    output = work / "calculated.json"
    code_source = str(Path(__file__).parents[1] / "src")
    environment = {**os.environ, "PYTHONPATH": code_source + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    parameters = StdioServerParameters(command=sys.executable, args=["-B", "-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(instance)], env=environment)

    async def verify():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=20) as session:
                await session.initialize()
                response = await session.call_tool("excel.export", {"input_path": str(source), "output_path": str(output), "format": "json", "sheet": "Dados", "range": "A1:B2",
                    "request_id": "export-after-calculate", "confirm": True, "calculation": {"mode": "full_rebuild"}, "validation": {"reject_new_formula_errors": True}})
                assert not response.is_error, decode(response)
                identifier = decode(response)["job_id"]
                for _ in range(600):
                    state = decode(await session.call_tool("excel.get_job", {"job_id": identifier}))
                    if state["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                        break
                    await asyncio.sleep(.05)
                assert state["status"] == "succeeded", state
                result = decode(await session.call_tool("excel.get_job_result", {"job_id": identifier}))["result"]
                assert result["calculation"]["status"] == "completed"
                values = json.loads(output.read_text(encoding="utf-8"))["values"]
                assert values[0][1] == 10 and values[1][1] == 11, values
    asyncio.run(verify())
    assert source.read_bytes() == original
