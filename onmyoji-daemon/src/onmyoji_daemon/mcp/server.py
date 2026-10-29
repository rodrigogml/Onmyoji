"""Servidor MCP local do Onmyōji, exposto somente por stdio."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from .configuration import McpConfiguration
from .todoist.tools import TodoistToolRegistry
from .omie.tools import OmieToolRegistry
from .btg.tools import BtgToolRegistry
from .cloudflare.tools import CloudflareToolRegistry
from .eccovox.tools import EccoVoxToolRegistry
from .forward_email.tools import ForwardEmailToolRegistry
from .notion.tools import NotionToolRegistry
from .aws.tools import AwsToolRegistry
from .mysql.tools import MySQLToolRegistry
from .ssh.tools import SshToolRegistry
from .bis2cmd.tools import Bis2CmdToolRegistry
from .bis10cmd.tools import Bis10CmdToolRegistry
from .google.tools import GoogleToolRegistry
from .spotify.tools import SpotifyToolRegistry
from .memory.tools import MemoryToolRegistry
from .data_tables.tools import DataTablesToolRegistry
from .telegram.tools import TelegramToolRegistry
from .mini_apps.tools import MiniAppsToolRegistry
from .workflows import WorkflowRegistry


class McpApplication:
    """Orquestra módulos MCP sem depender do transporte do protocolo."""

    def __init__(self, root: Path):
        self.configuration = McpConfiguration(root)
        self.todoist = TodoistToolRegistry(self.configuration)
        self.omie = OmieToolRegistry(self.configuration)
        self.btg = BtgToolRegistry(self.configuration)
        self.cloudflare = CloudflareToolRegistry(self.configuration)
        self.eccovox = EccoVoxToolRegistry(self.configuration)
        self.forward_email = ForwardEmailToolRegistry(self.configuration)
        self.notion = NotionToolRegistry(self.configuration)
        self.aws = AwsToolRegistry(self.configuration)
        self.mysql = MySQLToolRegistry(self.configuration)
        self.ssh = SshToolRegistry(self.configuration)
        self.bis2cmd = Bis2CmdToolRegistry(self.configuration)
        self.bis10cmd = Bis10CmdToolRegistry(self.configuration)
        self.google = GoogleToolRegistry(self.configuration)
        self.spotify = SpotifyToolRegistry(self.configuration)
        self.memory = MemoryToolRegistry(self.configuration)
        self.data_tables = DataTablesToolRegistry(self.configuration)
        self.telegram = TelegramToolRegistry(self.configuration.root)
        self.mini_apps = MiniAppsToolRegistry(self.configuration.root)
        self.workflows = WorkflowRegistry(self.configuration)

    def definitions(self) -> list[dict[str, Any]]:
        return self.todoist.definitions() + self.omie.definitions() + self.btg.definitions() + self.cloudflare.definitions() + self.eccovox.definitions() + self.forward_email.definitions() + self.notion.definitions() + self.aws.definitions() + self.mysql.definitions() + self.ssh.definitions() + self.bis2cmd.definitions() + self.bis10cmd.definitions() + self.google.definitions() + self.spotify.definitions() + self.memory.definitions() + self.data_tables.definitions() + self.telegram.definitions() + self.mini_apps.definitions() + self.workflows.definitions()

    def instructions(self) -> str | None:
        """Publica somente o índice curto dos workflows habilitados."""
        return self.workflows.developer_instructions()

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name.startswith("btg."):
            return self.btg.call(name, arguments)
        if name.startswith("omie."):
            return self.omie.call(name, arguments)
        if name.startswith("cloudflare."):
            return self.cloudflare.call(name, arguments)
        if name.startswith("eccovox."):
            return self.eccovox.call(name, arguments)
        if name.startswith("forward_email."):
            return self.forward_email.call(name, arguments)
        if name.startswith("notion."):
            return self.notion.call(name, arguments)
        if name.startswith("aws."):
            return self.aws.call(name, arguments)
        if name.startswith("mysql."):
            return self.mysql.call(name, arguments)
        if name.startswith("ssh."):
            return self.ssh.call(name, arguments)
        if name.startswith("bis2cmd."):
            return self.bis2cmd.call(name, arguments)
        if name.startswith("bis10cmd."):
            return self.bis10cmd.call(name, arguments)
        if name.startswith("google."):
            return self.google.call(name, arguments)
        if name.startswith("spotify."):
            return self.spotify.call(name, arguments)
        if name.startswith("memory."):
            return self.memory.call(name, arguments)
        if name.startswith("data_tables."):
            return self.data_tables.call(name, arguments)
        if name.startswith("telegram."):
            return self.telegram.call(name, arguments)
        if name.startswith("mini_apps."):
            return self.mini_apps.call(name, arguments)
        if name.startswith("workflows."):
            return self.workflows.call(name, arguments)
        return self.todoist.call(name, arguments)


def create_server(root: Path):
    """Cria o servidor MCP com tools calculadas a cada ``tools/list``."""
    from mcp.server import Server
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

    application = McpApplication(root)

    async def list_tools(_ctx, _params):
        return ListToolsResult(tools=[Tool(name=item["name"], description=item["description"], input_schema=item["input_schema"]) for item in application.definitions()])

    async def call_tool(_ctx, params):
        try:
            data = application.call(params.name, params.arguments or {})
            text = json.dumps(data, ensure_ascii=False)
            return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)
        except Exception as error:
            # Erros de domínio já são sanitizados; nunca serialize exceções ou tracebacks.
            return CallToolResult(content=[TextContent(type="text", text=str(error))], is_error=True)

    return Server("Onmyoji", version="0.1.0", instructions=application.instructions(), on_list_tools=list_tools, on_call_tool=call_tool)


async def serve(root: Path) -> None:
    """Atende um único cliente MCP no transporte stdio."""
    from mcp.server.stdio import stdio_server

    server = create_server(root)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onmyoji-mcp")
    parser.add_argument("--onmyoji-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        asyncio.run(serve(args.onmyoji_root.resolve()))
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"Erro ao iniciar o MCP Onmyoji: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
