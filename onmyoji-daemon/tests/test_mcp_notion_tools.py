from unittest.mock import Mock

from onmyoji_daemon.mcp.notion.service import OPERATIONS
from onmyoji_daemon.mcp.notion.tools import NotionToolRegistry


class Configuration:
    def __init__(self, profiles):
        self.profiles = profiles
        self.root = None

    def enabled_notion_profiles(self):
        return self.profiles


def test_all_migrated_operations_are_published_for_one_profile():
    assert NotionToolRegistry(Configuration([])).definitions() == []
    tools = NotionToolRegistry(Configuration([{"name": "workspace", "description": "Notion principal"}])).definitions()

    assert {tool["name"] for tool in tools} == {"notion." + operation for operation in OPERATIONS}
    assert 'Perfil ativo: "workspace" — Notion principal' in tools[0]["description"]
    update = next(tool for tool in tools if tool["name"] == "notion.pages.update")
    assert update["input_schema"]["required"] == ["params", "confirm"]
    upload = next(tool for tool in tools if tool["name"] == "notion.file_uploads.send")
    assert upload["input_schema"]["required"] == ["params", "body", "confirm"]


def test_multiple_profiles_require_selection_and_offer_discovery():
    profiles = [{"name": "one", "description": "Um"}, {"name": "two", "description": "Dois"}]
    registry = NotionToolRegistry(Configuration(profiles))
    tools = registry.definitions()

    assert tools[0]["name"] == "notion.list_profiles"
    assert tools[1]["input_schema"]["required"][0] == "profile"
    assert registry.call("notion.list_profiles", {}) == {"profiles": profiles}


def test_registry_delegates_with_implicit_single_profile(monkeypatch):
    service = Mock()
    service.invoke.return_value = {"data": {"object": "user"}}
    monkeypatch.setattr("onmyoji_daemon.mcp.notion.tools.NotionService", lambda _root: service)
    registry = NotionToolRegistry(Configuration([{"name": "workspace", "description": "Notion"}]))

    assert registry.call("notion.users.self", {}) == {"data": {"object": "user"}}
    service.invoke.assert_called_once_with("workspace", "users.self", {})
