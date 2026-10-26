from unittest.mock import Mock

from onmyoji_daemon.mcp.cloudflare.tools import CloudflareToolRegistry


class Configuration:
    def __init__(self, profiles):
        self.profiles = profiles
        self.root = None

    def enabled_cloudflare_profiles(self):
        return self.profiles


def test_tool_schema_changes_with_number_of_profiles():
    assert CloudflareToolRegistry(Configuration([])).definitions() == []
    one = CloudflareToolRegistry(Configuration([{"name": "dns", "description": "DNS público"}])).definitions()
    assert one[0]["name"] == "cloudflare.list_zones"
    assert "profile" not in one[0]["input_schema"]["properties"]
    assert 'Perfil ativo: "dns" — DNS público' in one[0]["description"]
    create = next(tool for tool in one if tool["name"] == "cloudflare.create_dns_record")
    assert create["input_schema"]["required"] == ["body", "confirm"]

    many = CloudflareToolRegistry(Configuration([{"name": "one", "description": "Um"}, {"name": "two", "description": "Dois"}])).definitions()
    assert many[0]["name"] == "cloudflare.list_profiles"
    assert many[1]["input_schema"]["required"] == ["profile"]


def test_list_profiles_exposes_only_name_and_context():
    profiles = [{"name": "one", "description": "Um"}, {"name": "two", "description": "Dois"}]
    assert CloudflareToolRegistry(Configuration(profiles)).call("cloudflare.list_profiles", {}) == {"profiles": profiles}


def test_registry_delegates_operation_with_implicit_single_profile(monkeypatch):
    service = Mock()
    service.invoke.return_value = {"data": []}
    monkeypatch.setattr("onmyoji_daemon.mcp.cloudflare.tools.CloudflareService", lambda _root: service)
    registry = CloudflareToolRegistry(Configuration([{"name": "dns", "description": "DNS"}]))

    assert registry.call("cloudflare.list_zones", {"query": {"page": 1}}) == {"data": []}
    service.invoke.assert_called_once_with("dns", "list_zones", {"query": {"page": 1}})
