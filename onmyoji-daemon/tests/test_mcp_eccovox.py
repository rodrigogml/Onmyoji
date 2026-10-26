from __future__ import annotations

import json
from pathlib import Path

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.eccovox.tools import EccoVoxToolRegistry
from onmyoji_daemon.mcp.eccovox.service import EccoVoxError, EccoVoxService


def configured(root: Path) -> McpConfiguration:
    read, write = root / "read", root / "write"; read.mkdir(); write.mkdir()
    config = McpConfiguration(root); data = config.eccovox()
    data["profiles"]["voz"] = {"enabled": True, "description": "Áudio do workspace", "base_url": "http://127.0.0.1:8870", "readable_roots": [str(read)], "writable_roots": [str(write)]}
    config.save_eccovox(data); return config


def test_eccovox_publishes_implicit_single_profile_tools(tmp_path):
    registry = EccoVoxToolRegistry(configured(tmp_path))
    definitions = {item["name"]: item for item in registry.definitions()}
    assert set(definitions) == {"eccovox.get_health", "eccovox.transcribe_audio", "eccovox.synthesize_speech"}
    assert "profile" not in definitions["eccovox.get_health"]["input_schema"]["properties"]


def test_eccovox_profile_context_is_listed_when_multiple(tmp_path):
    config = configured(tmp_path); data = config.eccovox(); data["profiles"]["outra"] = {**data["profiles"]["voz"], "description": "Outra voz"}; config.save_eccovox(data)
    definitions = {item["name"]: item for item in EccoVoxToolRegistry(config).definitions()}
    assert definitions["eccovox.list_profiles"]["name"] == "eccovox.list_profiles"
    assert definitions["eccovox.get_health"]["input_schema"]["required"] == ["profile"]


def test_eccovox_configuration_is_kept_in_mcp_subdirectory(tmp_path):
    config = configured(tmp_path)
    assert config.eccovox_path == tmp_path / "configs" / "mcp" / "eccovox.toml"
    assert json.loads(json.dumps(config.eccovox()))["profiles"]["voz"]["enabled"] is True


def test_eccovox_rejects_symbolic_link_as_synthesis_output(tmp_path):
    config = configured(tmp_path); target = tmp_path / "write" / "target.mp3"; link = tmp_path / "write" / "link.mp3"
    try: link.symlink_to(target)
    except OSError: pytest.skip("Links simbólicos indisponíveis nesta plataforma.")
    with pytest.raises(EccoVoxError, match="link simbólico"):
        EccoVoxService(tmp_path)._contained(str(link), (tmp_path / "write",), "output_path", False)
