from onmyoji_daemon.mcp.todoist.tools import TodoistToolRegistry


class Configuration:
    def __init__(self, profiles): self.profiles = profiles; self.root = None
    def enabled_todoist_profiles(self): return self.profiles


def test_tool_schema_changes_with_number_of_profiles():
    assert TodoistToolRegistry(Configuration([])).definitions() == []
    one = TodoistToolRegistry(Configuration([{"name": "one", "description": "Um"}])).definitions()
    assert "todoist.list_tasks" in [tool["name"] for tool in one]
    assert "todoist.create_task" in [tool["name"] for tool in one]
    assert "todoist.sync" in [tool["name"] for tool in one]
    assert one[0]["input_schema"]["properties"] == {"params": {"type": "object", "description": "Parâmetros de rota, como task_id ou project_id."}, "query": {"type": "object", "description": "Parâmetros de consulta da API."}, "body": {"type": "object", "description": "Corpo JSON aceito pela API Todoist."}}
    assert 'Perfil ativo: "one" — Um' in one[0]["description"]
    many = TodoistToolRegistry(Configuration([{"name": "one", "description": "Um"}, {"name": "two", "description": "Dois"}])).definitions()
    assert many[0]["name"] == "todoist.list_profiles"
    assert many[1]["name"] == "todoist.list_tasks"
    assert many[1]["input_schema"]["required"] == ["profile"]
    delete_task = next(tool for tool in many if tool["name"] == "todoist.delete_task")
    assert delete_task["input_schema"]["required"] == ["profile", "confirm"]


def test_list_profiles_exposes_only_name_and_context():
    registry = TodoistToolRegistry(Configuration([{"name": "one", "description": "Contexto"}, {"name": "two", "description": "Outro"}]))
    assert registry.call("todoist.list_profiles", {}) == {"profiles": [{"name": "one", "description": "Contexto"}, {"name": "two", "description": "Outro"}]}
