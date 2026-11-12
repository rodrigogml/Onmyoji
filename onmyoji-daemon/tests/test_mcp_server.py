from onmyoji_daemon import management
from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.server import McpApplication, create_server, main
from onmyoji_daemon.mcp.telegram.tools import TelegramToolRegistry


class Registry:
    def definitions(self): return [{"name": "todoist.list_tasks"}]
    def call(self, name, arguments): return {"name": name, "arguments": arguments}


def test_application_delegates_to_registered_modules(tmp_path):
    application = McpApplication(tmp_path)
    application.todoist = Registry()
    definitions = application.definitions()
    assert "todoist.list_tasks" in {definition["name"] for definition in definitions}
    assert "mini_apps.publish" in {definition["name"] for definition in definitions}
    assert "gateway.listeners_list" in {definition["name"] for definition in definitions}
    assert "cloudflare_tunnel.provision" in {definition["name"] for definition in definitions}
    generic_invocation = ".".join(("mini_apps", "invoke"))
    assert generic_invocation not in {definition["name"] for definition in definitions}
    assert application.call("todoist.list_tasks", {}) == {"name": "todoist.list_tasks", "arguments": {}}
    application.publication = Registry()
    assert application.call("gateway.status", {}) == {"name": "gateway.status", "arguments": {}}
    assert application.call("cloudflare_tunnel.status", {}) == {"name": "cloudflare_tunnel.status", "arguments": {}}


def test_main_starts_the_stdio_server_with_the_resolved_root(tmp_path, monkeypatch):
    started = []

    def run(coroutine):
        started.append(coroutine)
        coroutine.close()

    monkeypatch.setattr("onmyoji_daemon.mcp.server.asyncio.run", run)

    assert main(["--onmyoji-root", str(tmp_path)]) == 0
    assert len(started) == 1


def test_server_advertises_workflow_routes_only_when_memory_is_enabled(tmp_path):
    assert create_server(tmp_path).create_initialization_options().instructions is None
    McpConfiguration(tmp_path).save_memory({"schema_version": 1, "enabled": True, "max_results": 5})
    instructions = create_server(tmp_path).create_initialization_options().instructions
    assert instructions and "memory-decision" in instructions
    assert "workflows.read" in instructions


def test_server_initialization_explains_available_telegram_destinations(tmp_path):
    management.prepare_instance(tmp_path)
    assert create_server(tmp_path).create_initialization_options().instructions is None
    ok, message = management.set_enabled(tmp_path, "telegram", True)
    assert ok, message

    server = create_server(tmp_path)
    instructions = server.create_initialization_options().instructions
    assert instructions == TelegramToolRegistry(tmp_path).developer_instructions()
    assert "[ONMYŌJI — TELEGRAM MCP]" in instructions
    assert "telegram.list_channels" in instructions and "telegram.send" in instructions
    advertised = {item["name"] for item in server.document_application.definitions()}
    assert {"telegram.list_channels", "telegram.send"} <= advertised

    ok, message = management.set_enabled(tmp_path, "telegram", False)
    assert ok, message
    assert create_server(tmp_path).create_initialization_options().instructions is None
