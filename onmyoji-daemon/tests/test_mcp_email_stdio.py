"""Handshake e despacho reais por stdio, sem serviços externos."""
import asyncio
import os
from pathlib import Path
import sys

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.email.configuration import empty_configuration


def test_email_stdio_announces_workflow_and_validates_calls(tmp_path):
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["test"] = {"cli_command": ["not-used"], "database": {"windows": "vault.kdbx", "linux": "vault.kdbx"}}
    credentials["profiles"]["test"] = {"vault": "test", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "Test"}, "linux": {"mode": "command", "command": ["false"]}}}
    config.save_credentials(credentials)
    account = {"enabled": True, "description": "E-mail do teste stdio", "address": "test@example.test", "display_name": "Teste", "allowed_senders": [], "readable_roots": [], "writable_roots": [], "folders": {"inbox": "INBOX"}, "sent_copy": "server",
               "smtp": {"protocol": "smtp", "host": "smtp.example.test", "port": 465, "security": "tls", "username": "test@example.test", "auth": {"mode": "password", "credential_profile": "test", "credential_entry_path": "Email/test"}}}
    document = empty_configuration(); document["accounts"]["default"] = account; config.save_email(document)
    source = str(Path(__file__).resolve().parents[1] / "src")
    parameters = StdioServerParameters(command=sys.executable, args=["-B", "-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(tmp_path)], env={**os.environ, "PYTHONPATH": source, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})

    async def check():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=10) as session:
                initialization = await session.initialize()
                assert "email-operations" in initialization.instructions
                tools = (await session.list_tools()).tools
                names = {tool.name for tool in tools}
                assert "email.send_message" in names and "email.list_folders" not in names
                send = next(tool for tool in tools if tool.name == "email.send_message")
                assert "E-mail do teste stdio" in send.description
                assert "account" not in send.input_schema["properties"]
                result = await session.call_tool("email.send_message", {"to": ["x@example.test"], "request_id": "one"})
                assert result.is_error is True
                result = await session.call_tool("workflows.read", {"id": "email-operations"})
                assert not result.is_error
                assert "unknown" in str(result.content)
    asyncio.run(check())
