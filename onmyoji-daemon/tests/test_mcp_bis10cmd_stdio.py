"""Contrato MCP real para contas a pagar, sem Java, cofre ou servidor BIS10."""
import asyncio
import os
from pathlib import Path
import sys

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration


def test_stdio_publishes_typed_bills_and_preserves_functional_errors(tmp_path):
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["test"] = {
        "cli_command": ["nonexistent-keepass-cli"],
        "database": {"windows": "not-a-vault.kdbx", "linux": "/not-a-vault.kdbx"},
    }
    credentials["profiles"]["test"] = {
        "vault": "test", "allowed_entry_roots": [],
        "auth": {"windows": {"mode": "windows_credential_manager", "target": "not-a-real-target"},
                 "linux": {"mode": "command", "command": ["false"]}},
    }
    config.save_credentials(credentials)
    document = config.bis10cmd()
    document["profiles"]["test"] = {
        "enabled": True, "description": "BIS10 sintético stdio", "jar_path": "not-a-real.jar",
        "working_dir": str(tmp_path), "host": "localhost", "port": 8080, "locale": "pt-BR",
        **{f"{prefix}_credential_profile": "test" for prefix in ("jndi", "bis")},
        **{f"{prefix}_credential_entry_path": "BIS10/test" for prefix in ("jndi", "bis")},
        **{f"{prefix}_username_field": "username" for prefix in ("jndi", "bis")},
        **{f"{prefix}_password_field": "password" for prefix in ("jndi", "bis")},
    }
    config.save_bis10cmd(document)
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                   "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    parameters = StdioServerParameters(command=sys.executable,
                                      args=["-B", "-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(tmp_path)],
                                      env=environment)

    async def check():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=10) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                schema = tools["bis10cmd.create_bill_from_code"].input_schema
                assert {"company_id", "code", "request_id", "confirm"} <= set(schema["required"])
                assert "args" not in schema["properties"]
                assert schema["properties"]["categories"]["items"]["additionalProperties"] is False
                assert {"bis10cmd.analyze_bill_code", "bis10cmd.get_bill", "bis10cmd.list_bills",
                        "bis10cmd.list_people", "bis10cmd.get_bill_request"} <= tools.keys()
                result = await session.call_tool("bis10cmd.create_bill_from_code", {"company_id": 1})
                assert result.is_error is True
                assert result.structured_content["error"]["code"] == "confirmation_required"
                result = await session.call_tool("bis10cmd.analyze_bill_code", {"company_id": 1, "code": "invalid"})
                assert result.is_error is True
                assert result.structured_content["error"]["code"] == "invalid_request"
                assert result.structured_content["error"]["data"]["field"] == "code"
                assert "nonexistent-keepass-cli" not in str(result.content)

    asyncio.run(check())
