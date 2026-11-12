from onmyoji_daemon.mcp.ssh.tools import SshToolRegistry
class Configuration:
    root = None
    def __init__(self, profiles): self.profiles = profiles
    def enabled_ssh_profiles(self): return self.profiles
def test_profile_publication_and_confirmation():
    one = SshToolRegistry(Configuration([{"name": "server", "description": "Servidor."}])).definitions()
    assert {item["name"] for item in one} == {"ssh.exec", "ssh.upload", "ssh.download"}
    assert all("confirm" in item["input_schema"]["required"] for item in one)
    many = SshToolRegistry(Configuration([{"name": "a", "description": "A"}, {"name": "b", "description": "B"}])).definitions()
    assert many[0]["name"] == "ssh.list_profiles" and "profile" in many[1]["input_schema"]["required"]
