"""Contratos públicos de abertura e acompanhamento de revisões humanas."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.file_review.exception import FileReviewMcpError
from onmyoji_daemon.mcp.file_review.service import FileReviewMcpService
from onmyoji_daemon.mcp.file_review.tools import FileReviewToolRegistry
from onmyoji_daemon.mcp.server import McpApplication
from onmyoji_daemon.rpc import RpcError


def test_tools_advertise_only_creation_status_and_cancellation(tmp_path):
    """O agente acompanha a revisão; aprovação não é uma operação MCP."""
    definitions = FileReviewToolRegistry(tmp_path).definitions()
    assert {item["name"] for item in definitions} == {"file_review.create", "file_review.status", "file_review.cancel"}
    for item in definitions:
        Draft202012Validator.check_schema(item["input_schema"])
        assert item["input_schema"]["additionalProperties"] is False
        assert ("confirm" in item["input_schema"]["required"]) == (item["name"] != "file_review.status")
    create = next(item["input_schema"] for item in definitions if item["name"] == "file_review.create")
    assert set(create["required"]) == {"original_path", "proposed_path", "confirm"}
    assert create["properties"]["confirm"] == {"type": "boolean", "const": True}


@pytest.mark.parametrize("name,arguments,action", [
    ("file_review.create", {"original_path": "C:/project/AGENTS.md", "proposed_path": "C:/project/AGENTS-2026-11-08.proposed.md", "confirm": True}, "create"),
    ("file_review.create", {"original_path": "C:/project/a.md", "proposed_path": "C:/project/a.proposed.md", "claim_timeout": 180, "lifetime": 1800, "idle_timeout": 600, "confirm": True}, "create"),
    ("file_review.status", {"review_id": "review-1"}, "status"),
    ("file_review.cancel", {"review_id": "review-1", "confirm": True}, "cancel"),
])
def test_valid_tools_forward_only_the_explicit_operation(tmp_path, name, arguments, action):
    with patch("onmyoji_daemon.mcp.file_review.tools.FileReviewMcpService") as service:
        service.return_value.invoke.return_value = {"review_id": "review-1"}
        assert FileReviewToolRegistry(tmp_path).call(name, arguments) == {"review_id": "review-1"}
        service.return_value.invoke.assert_called_once_with(action, arguments)


@pytest.mark.parametrize("name,arguments,code", [
    ("file_review.apply", {}, "invalid_request"),
    (None, {}, "invalid_request"),
    ("create", {}, "invalid_request"),
    ("file_review.status", None, "invalid_request"),
    ("file_review.status", [], "invalid_request"),
    ("file_review.status", {}, "invalid_request"),
    ("file_review.status", {"review_id": ""}, "invalid_request"),
    ("file_review.status", {"review_id": "   "}, "invalid_request"),
    ("file_review.status", {"review_id": None}, "invalid_request"),
    ("file_review.status", {"review_id": 1}, "invalid_request"),
    ("file_review.status", {"review_id": "id", "confirm": True}, "invalid_request"),
    ("file_review.create", {"original_path": "a", "proposed_path": "b"}, "confirmation_required"),
    ("file_review.create", {"original_path": "a", "proposed_path": "b", "confirm": 1}, "confirmation_required"),
    ("file_review.create", {"confirm": True}, "invalid_request"),
    ("file_review.create", {"original_path": "a", "proposed_path": "b", "confirm": True, "claim_timeout": 0}, "invalid_request"),
    ("file_review.create", {"original_path": "a", "proposed_path": "b", "confirm": True, "lifetime": -1}, "invalid_request"),
    ("file_review.create", {"original_path": "a", "proposed_path": "b", "confirm": True, "idle_timeout": True}, "invalid_request"),
    ("file_review.create", {"original_path": "a", "proposed_path": "b", "confirm": True, "lifetime": "10"}, "invalid_request"),
    ("file_review.create", {"original_path": "a", "proposed_path": "b", "confirm": True, "token": "private"}, "invalid_request"),
    ("file_review.cancel", {"review_id": "id", "confirm": False}, "confirmation_required"),
])
def test_invalid_contracts_are_rejected_before_rpc(tmp_path, name, arguments, code):
    with patch("onmyoji_daemon.mcp.file_review.tools.FileReviewMcpService") as service:
        with pytest.raises(FileReviewMcpError) as caught:
            FileReviewToolRegistry(tmp_path).call(name, arguments)
        assert caught.value.code == code
        service.assert_not_called()


@pytest.mark.parametrize("action,timeout", [("create", 30), ("status", 5), ("cancel", 5)])
def test_service_uses_authenticated_daemon_and_bounded_timeout(tmp_path, action, timeout):
    rpc = Mock(return_value={"review_id": "id"})
    endpoint = Mock(return_value=("127.0.0.1", 1234, "private-token"))
    service = FileReviewMcpService(tmp_path, rpc=rpc, endpoint_reader=endpoint)
    assert service.invoke(action, {"review_id": "id"}) == {"review_id": "id"}
    endpoint.assert_called_once_with(tmp_path.resolve())
    rpc.assert_called_once_with("127.0.0.1", 1234, "private-token", "mini-apps.file-review." + action, {"review_id": "id"}, timeout=timeout)


def test_service_rejects_arbitrary_actions_without_discovering_credentials(tmp_path):
    endpoint = Mock()
    with pytest.raises(FileReviewMcpError, match="inválida"):
        FileReviewMcpService(tmp_path, endpoint_reader=endpoint).invoke("apply", {})
    endpoint.assert_not_called()


@pytest.mark.parametrize("remote_code,expected", [("file_changed", "file_changed"), (None, "daemon_rejected"), ("token=secret", "daemon_rejected"), ("a" * 81, "daemon_rejected")])
def test_rpc_error_codes_survive_without_leaking_remote_text(tmp_path, remote_code, expected):
    rpc = Mock(side_effect=RpcError("token=private file content", remote_code, {"secret": "private"}))
    service = FileReviewMcpService(tmp_path, rpc=rpc, endpoint_reader=lambda _root: ("localhost", 1234, "private"))
    with pytest.raises(FileReviewMcpError) as caught:
        service.invoke("status", {"review_id": "id"})
    assert caught.value.as_dict() == {"code": expected, "message": "Revisão de arquivos indisponível ou operação recusada pelo daemon."}
    assert "private" not in str(caught.value.as_dict())


def test_endpoint_failures_are_sanitized(tmp_path):
    endpoint = Mock(side_effect=OSError("token=private"))
    with pytest.raises(FileReviewMcpError) as caught:
        FileReviewMcpService(tmp_path, endpoint_reader=endpoint).invoke("status", {"review_id": "id"})
    assert caught.value.code == "daemon_unavailable"
    assert "private" not in str(caught.value)


def test_application_advertises_and_dispatches_file_review_module(tmp_path):
    application = McpApplication(tmp_path)
    names = {item["name"] for item in application.definitions()}
    assert {"file_review.create", "file_review.status", "file_review.cancel"} <= names
    application.file_review = Mock()
    application.file_review.call.return_value = {"status": "editing"}
    assert application.call("file_review.status", {"review_id": "id"}) == {"status": "editing"}
    application.file_review.call.assert_called_once_with("file_review.status", {"review_id": "id"})


def test_stdio_publishes_contract_and_returns_structured_sanitized_errors(tmp_path):
    """Exercita o transporte MCP real sem exigir um Daemon ativo."""
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    source = str(Path(__file__).resolve().parents[1] / "src")
    inherited = os.environ.get("PYTHONPATH", "")
    environment = {**os.environ, "PYTHONPATH": source + (os.pathsep + inherited if inherited else ""), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    parameters = StdioServerParameters(command=sys.executable, args=["-B", "-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(tmp_path)], env=environment)

    async def verify():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=15) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert {"file_review.create", "file_review.status", "file_review.cancel"} <= tools.keys()
                assert "file_review.apply" not in tools
                response = await session.call_tool("file_review.status", {"review_id": "a" * 32})
                assert response.is_error
                content = json.loads(next(item.text for item in response.content if item.type == "text"))
                assert content["error"]["code"] == "daemon_unavailable"
                assert response.structured_content == content
                assert str(tmp_path) not in json.dumps(content)
                assert "Traceback" not in json.dumps(content)

    asyncio.run(verify())
