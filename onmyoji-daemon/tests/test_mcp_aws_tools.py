from unittest.mock import Mock

from onmyoji_daemon.mcp.aws.service import OPERATIONS
from onmyoji_daemon.mcp.aws.tools import AwsToolRegistry


class Configuration:
    root = None
    def __init__(self, profiles): self.profiles = profiles
    def enabled_aws_profiles(self): return self.profiles


def test_tools_follow_single_and_multiple_profile_rule():
    one = AwsToolRegistry(Configuration([{"name": "prod", "description": "Conta de produção."}])).definitions()
    assert {tool["name"] for tool in one} == {"aws." + operation for operation in OPERATIONS}
    assert all("profile" not in tool["input_schema"].get("required", []) for tool in one)
    delete = next(tool for tool in one if tool["name"] == "aws.s3_object_delete")
    assert "confirm" in delete["input_schema"]["required"]
    many = AwsToolRegistry(Configuration([{"name": "prod", "description": "Produção."}, {"name": "dev", "description": "Desenvolvimento."}])).definitions()
    assert many[0]["name"] == "aws.list_profiles"
    assert "profile" in many[1]["input_schema"]["required"]


def test_registry_dispatches_selected_profile(monkeypatch):
    profiles = [{"name": "prod", "description": "Produção."}]
    service = Mock(); service.invoke.return_value = {"data": {"Account": "123456789012"}}
    monkeypatch.setattr("onmyoji_daemon.mcp.aws.tools.AwsService", lambda _root: service)
    assert AwsToolRegistry(Configuration(profiles)).call("aws.identity_get", {})["data"]["Account"] == "123456789012"
    service.invoke.assert_called_once_with("prod", "identity_get", {})
