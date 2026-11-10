from onmyoji_daemon.mcp.mysql.tools import MySQLToolRegistry
class Configuration:
    root = None
    def __init__(self, profiles): self.profiles = profiles
    def enabled_mysql_profiles(self): return self.profiles
def test_profile_publication_and_confirm_schema():
    one = MySQLToolRegistry(Configuration([{"name": "db", "description": "Banco."}])).definitions()
    assert {item["name"] for item in one} == {"mysql.query", "mysql.execute", "mysql.script", "mysql.client", "mysql.ping"}
    assert "confirm" in next(item for item in one if item["name"] == "mysql.execute")["input_schema"]["required"]
    many = MySQLToolRegistry(Configuration([{"name": "a", "description": "A"}, {"name": "b", "description": "B"}])).definitions()
    assert many[0]["name"] == "mysql.list_profiles" and "profile" in many[1]["input_schema"]["required"]
