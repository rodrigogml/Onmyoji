from onmyoji_daemon.mcp.bis10cmd.service import OPERATIONS, WRITE_ACTIONS
from onmyoji_daemon.mcp.bis10cmd.tools import Bis10CmdToolRegistry


class Configuration:
    root = None
    def __init__(self, profiles): self.profiles = profiles
    def enabled_bis10cmd_profiles(self): return self.profiles


def test_all_operations_are_published_with_profile_and_confirmation_rules():
    one = Bis10CmdToolRegistry(Configuration([{"name": "bis", "description": "BIS10."}])).definitions()
    assert {item["name"] for item in one} == {"bis10cmd." + name for name in OPERATIONS}
    for item in one:
        operation = item["name"].split(".", 1)[1]
        assert ("confirm" in item["input_schema"].get("required", [])) == (operation in WRITE_ACTIONS)
        assert "profile" not in item["input_schema"].get("required", [])
    many = Bis10CmdToolRegistry(Configuration([{"name": "a", "description": "A"}, {"name": "b", "description": "B"}])).definitions()
    assert many[0]["name"] == "bis10cmd.list_profiles"
    assert all("profile" in item["input_schema"]["required"] for item in many[1:])
