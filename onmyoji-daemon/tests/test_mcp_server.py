from onmyoji_daemon.mcp.server import McpApplication


class Registry:
    def definitions(self): return [{"name": "todoist.list_tasks"}]
    def call(self, name, arguments): return {"name": name, "arguments": arguments}


def test_application_delegates_to_registered_modules(tmp_path):
    application = McpApplication(tmp_path)
    application.todoist = Registry()
    assert application.definitions() == [{"name": "todoist.list_tasks"}]
    assert application.call("todoist.list_tasks", {}) == {"name": "todoist.list_tasks", "arguments": {}}
