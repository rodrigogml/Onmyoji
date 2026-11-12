"""Handshake MCP real e erros estruturados sem dependência de Daemon ativo."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest


def test_publication_stdio_advertises_new_contracts_and_sanitizes_unavailable_daemon(tmp_path):
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
                assert {"gateway.status", "gateway.listeners_remove", "cloudflare_tunnel.provision"} <= tools.keys()
                assert not any(name.startswith("mini_apps.tunnel_") for name in tools)
                assert tools["cloudflare_tunnel.provision"].input_schema["required"] == ["confirm"]
                response = await session.call_tool("gateway.status", {})
                assert response.is_error
                content = json.loads(next(item.text for item in response.content if item.type == "text"))
                assert content["error"]["code"] == "daemon_unavailable"
                assert response.structured_content == content
                assert str(tmp_path) not in json.dumps(content)
                assert "Traceback" not in json.dumps(content)

    asyncio.run(verify())
