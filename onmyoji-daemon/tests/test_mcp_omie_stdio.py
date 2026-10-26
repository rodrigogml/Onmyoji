"""Handshake real stdio e validação de tools Omie sem acesso ao cofre/API."""
import asyncio
import os
from pathlib import Path
import sys

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration


def test_stdio_publishes_omie_and_rejects_invalid_call_without_vault(tmp_path):
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["test"] = {"cli_command": ["nonexistent-keepass-cli"], "database": {"windows": "not-a-real-vault.kdbx", "linux": "/not-a-real-vault.kdbx"}}
    credentials["profiles"]["test"] = {"vault": "test", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "nonexistent-target"}, "linux": {"mode": "command", "command": ["false"]}}}
    config.save_credentials(credentials)
    omie = config.omie()
    omie["profiles"]["empresa"] = {"enabled": True, "description": "ERP de teste stdio", "credential_profile": "test", "credential_entry_path": "APIs/Omie/test", "app_key_field": "username", "app_secret_field": "password"}
    config.save_omie(omie)
    source = str(Path(__file__).resolve().parents[1] / "src")
    environment = {**os.environ, "PYTHONPATH": source, "PYTHONUTF8": "1"}
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(tmp_path)], env=environment)

    async def check():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=10) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert len(tools) == 72
                creation = next(tool for tool in tools if tool.name == "omie.customers_create")
                assert "ERP de teste stdio" in creation.description
                assert creation.input_schema["properties"]["body"]["required"] == ["cnpj_cpf", "razao_social"]
                result = await session.call_tool("omie.document_types_get", {"params": {"codigo": 123}})
                assert result.is_error is True
                assert "nonexistent" not in str(result.content)

    asyncio.run(check())
