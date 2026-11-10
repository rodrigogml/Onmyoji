from pathlib import Path

from onmyoji_daemon.mcp.spotify.service import OPERATIONS
from onmyoji_daemon.mcp.spotify.tools import SpotifyToolRegistry


class Configuration:
    root = Path(".")
    profiles = [{"name": "music", "description": "Conta pessoal."}]
    def enabled_spotify_profiles(self): return self.profiles


def test_single_profile_is_implicit_and_all_operations_are_published():
    definitions = SpotifyToolRegistry(Configuration()).definitions()
    assert len(definitions) == len(OPERATIONS)
    assert all("profile" not in item["input_schema"]["properties"] for item in definitions)
    pause = next(item for item in definitions if item["name"] == "spotify.player.pause")
    assert "confirm" in pause["input_schema"]["required"]


def test_multiple_profiles_publish_discovery_and_require_profile():
    config = Configuration(); config.profiles = [{"name": "a", "description": "A"}, {"name": "b", "description": "B"}]
    definitions = SpotifyToolRegistry(config).definitions()
    assert definitions[0]["name"] == "spotify.list_profiles"
    assert all("profile" in item["input_schema"].get("required", []) for item in definitions[1:])


def test_no_enabled_profile_publishes_nothing():
    config = Configuration(); config.profiles = []
    assert SpotifyToolRegistry(config).definitions() == []
