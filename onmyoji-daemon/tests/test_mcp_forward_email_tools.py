from unittest.mock import Mock

from onmyoji_daemon.mcp.forward_email.tools import ForwardEmailToolRegistry


class Configuration:
    def __init__(self, profiles):
        self.profiles = profiles
        self.root = None

    def enabled_forward_email_profiles(self):
        return self.profiles


def test_tool_schema_changes_with_number_of_profiles():
    assert ForwardEmailToolRegistry(Configuration([])).definitions() == []
    one = ForwardEmailToolRegistry(Configuration([{"name": "mail", "description": "Correio principal"}])).definitions()
    assert one[0]["name"] == "forward_email.list_domains"
    assert "profile" not in one[0]["input_schema"]["properties"]
    assert 'Perfil ativo: "mail" — Correio principal' in one[0]["description"]
    create = next(tool for tool in one if tool["name"] == "forward_email.create_alias")
    assert create["input_schema"]["required"] == ["body", "confirm"]
    generate = next(tool for tool in one if tool["name"] == "forward_email.generate_alias_password")
    assert generate["input_schema"]["required"] == ["confirm"]
    assert generate["input_schema"]["anyOf"] == [{"required": ["alias_id"]}, {"required": ["alias"]}]

    many = ForwardEmailToolRegistry(Configuration([{"name": "one", "description": "Um"}, {"name": "two", "description": "Dois"}])).definitions()
    assert many[0]["name"] == "forward_email.list_profiles"
    assert many[1]["input_schema"]["required"] == ["profile"]


def test_list_profiles_exposes_only_name_and_context():
    profiles = [{"name": "one", "description": "Um"}, {"name": "two", "description": "Dois"}]
    assert ForwardEmailToolRegistry(Configuration(profiles)).call("forward_email.list_profiles", {}) == {"profiles": profiles}


def test_registry_delegates_with_implicit_single_profile(monkeypatch):
    service = Mock()
    service.invoke.return_value = {"data": []}
    monkeypatch.setattr("onmyoji_daemon.mcp.forward_email.tools.ForwardEmailService", lambda _root: service)
    registry = ForwardEmailToolRegistry(Configuration([{"name": "mail", "description": "Correio"}]))

    assert registry.call("forward_email.list_domains", {"query": {"page": 1}}) == {"data": []}
    service.invoke.assert_called_once_with("mail", "list_domains", {"query": {"page": 1}})
