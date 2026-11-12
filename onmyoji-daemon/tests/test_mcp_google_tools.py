from pathlib import Path

import pytest

from onmyoji_daemon.mcp.google.service import OPERATIONS, WRITE_OPERATIONS
from onmyoji_daemon.mcp.google.tools import GoogleToolRegistry


class Configuration:
    root = Path(".")
    profiles = [{"name": "work", "description": "Conta de trabalho."}]
    def enabled_google_profiles(self): return self.profiles


def test_single_profile_is_implicit_and_all_operations_are_published():
    definitions = GoogleToolRegistry(Configuration()).definitions()
    assert len(definitions) == len(OPERATIONS)
    assert all("profile" not in item["input_schema"]["properties"] for item in definitions)
    send = next(item for item in definitions if item["name"] == "google.gmail.messages.send")
    assert "confirm" in send["input_schema"]["required"]


def test_multiple_profiles_publish_discovery_and_require_profile():
    config = Configuration(); config.profiles = [{"name": "a", "description": "A"}, {"name": "b", "description": "B"}]
    definitions = GoogleToolRegistry(config).definitions()
    assert definitions[0]["name"] == "google.list_profiles"
    assert all("profile" in item["input_schema"].get("required", []) for item in definitions[1:])


def test_no_enabled_profile_publishes_nothing():
    config = Configuration(); config.profiles = []
    assert GoogleToolRegistry(config).definitions() == []


def test_every_non_get_operation_requires_confirmation():
    assert WRITE_OPERATIONS == {name for name, (method, _path) in OPERATIONS.items() if method != "GET"}
