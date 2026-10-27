from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.server import McpApplication, create_server, main


class Registry:
    def definitions(self): return [{"name": "todoist.list_tasks"}]
    def call(self, name, arguments): return {"name": name, "arguments": arguments}


def test_application_delegates_to_registered_modules(tmp_path):
    application = McpApplication(tmp_path)
    application.todoist = Registry()
    assert application.definitions() == [{"name": "todoist.list_tasks"}]
    assert application.call("todoist.list_tasks", {}) == {"name": "todoist.list_tasks", "arguments": {}}


def test_main_starts_the_stdio_server_with_the_resolved_root(tmp_path, monkeypatch):
    started = []

    def run(coroutine):
        started.append(coroutine)
        coroutine.close()

    monkeypatch.setattr("onmyoji_daemon.mcp.server.asyncio.run", run)

    assert main(["--onmyoji-root", str(tmp_path)]) == 0
    assert len(started) == 1


def test_server_advertises_memory_workflow_only_when_memory_is_enabled(tmp_path):
    assert create_server(tmp_path).create_initialization_options().instructions is None
    McpConfiguration(tmp_path).save_memory({"schema_version": 1, "enabled": True, "max_results": 5})
    instructions = create_server(tmp_path).create_initialization_options().instructions
    assert instructions and "$onmyoji-memory" in instructions
    assert "antes de responder" in instructions.casefold()
