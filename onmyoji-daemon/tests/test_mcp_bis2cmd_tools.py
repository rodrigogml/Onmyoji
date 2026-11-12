from onmyoji_daemon.mcp.bis2cmd.service import COMMANDS, WRITE_OPERATIONS
from onmyoji_daemon.mcp.bis2cmd.tools import Bis2CmdToolRegistry
class Configuration:
    root = None
    def __init__(self, profiles): self.profiles = profiles
    def enabled_bis2cmd_profiles(self): return self.profiles
def test_all_commands_are_published_with_profile_rule():
    one = Bis2CmdToolRegistry(Configuration([{"name": "bis", "description": "BIS."}])).definitions()
    assert {item["name"] for item in one} == {"bis2cmd." + name for name in COMMANDS}
    for item in one:
        operation = item["name"].split(".", 1)[1]
        assert ("confirm" in item["input_schema"].get("required", [])) == (operation in WRITE_OPERATIONS)
    many = Bis2CmdToolRegistry(Configuration([{"name": "a", "description": "A"}, {"name": "b", "description": "B"}])).definitions()
    assert many[0]["name"] == "bis2cmd.list_profiles" and "profile" in many[1]["input_schema"]["required"]
