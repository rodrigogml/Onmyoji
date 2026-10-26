"""Handshake stdio BTG e rejeição de escrita bancária sem cofre ou API reais."""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration


def test_stdio_btg_publishes_oauth_and_reads_but_no_bank_writes(tmp_path):
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    config = McpConfiguration(root)
    credentials = config.credentials()
    credentials["vaults"]["test"] = {"cli_command": ["nonexistent-cli"], "database": {"windows": "not-a-vault.kdbx", "linux": "/not-a-vault.kdbx"}}
    credentials["profiles"]["test"] = {"vault": "test", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "nonexistent-target"}, "linux": {"mode": "command", "command": ["false"]}}}
    config.save_credentials(credentials)
    btg = config.btg()
    btg["profiles"]["company"] = {"enabled": True, "description": "Conta sintética stdio", "environment": "sandbox", "company_id": "30306294000145", "credential_profile": "test", "credential_entry_path": "BTG", "account_id": "test-account", "account_number": "001", "branch_code": "50"}
    config.save_btg(btg)
    workspace.mkdir()
    (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory=' + json.dumps(str(workspace)), encoding="utf-8")
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "PYTHONUTF8": "1"}
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(root)], env=environment)

    async def check():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=10) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert len(tools) == 11
                assert all("Conta sintética stdio" in tool.description for tool in tools)
                assert not any("submit" in tool.name for tool in tools)
                result = await session.call_tool("btg.start_authorization", {"confirm": False})
                assert result.is_error is True and "confirm=true" in str(result.content)
                result = await session.call_tool("btg.list_statements", {"params": {"type": "simple"}})
                assert result.is_error is True and "nonexistent" not in str(result.content)
                result = await session.call_tool("btg.submit_payment", {})
                assert result.is_error is True

    asyncio.run(check())
