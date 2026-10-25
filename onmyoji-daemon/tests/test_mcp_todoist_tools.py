from onmyoji_daemon.mcp.todoist.tools import TodoistToolRegistry


class Configuration:
    def __init__(self, profiles): self.profiles = profiles; self.root = None
    def enabled_todoist_profiles(self): return self.profiles


def test_tool_schema_changes_with_number_of_profiles():
    assert TodoistToolRegistry(Configuration([])).definitions() == []
    one = TodoistToolRegistry(Configuration([{"name": "one", "description": "Um"}])).definitions()
    assert [tool["name"] for tool in one] == ["todoist.list_tasks"] and one[0]["input_schema"]["properties"] == {}
    many = TodoistToolRegistry(Configuration([{"name": "one", "description": "Um"}, {"name": "two", "description": "Dois"}])).definitions()
    assert [tool["name"] for tool in many] == ["todoist.list_profiles", "todoist.list_tasks"]
    assert many[1]["input_schema"]["required"] == ["profile"]


def test_list_profiles_exposes_only_name_and_context():
    registry = TodoistToolRegistry(Configuration([{"name": "one", "description": "Contexto"}, {"name": "two", "description": "Outro"}]))
    assert registry.call("todoist.list_profiles", {}) == {"profiles": [{"name": "one", "description": "Contexto"}, {"name": "two", "description": "Outro"}]}
