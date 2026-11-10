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
    data["profiles"]["voz"] = {"enabled": True, "description": "Áudio do workspace", "base_url": "http://127.0.0.1:8870", "default_output_directory": str(write), "readable_roots": [str(read)], "writable_roots": [str(write)]}
    config.save_eccovox(data); return config


def test_eccovox_publishes_implicit_single_profile_tools(tmp_path):
    registry = EccoVoxToolRegistry(configured(tmp_path))
    definitions = {item["name"]: item for item in registry.definitions()}
    assert set(definitions) == {"eccovox.get_health", "eccovox.transcribe_audio", "eccovox.synthesize_speech"}
    assert "profile" not in definitions["eccovox.get_health"]["input_schema"]["properties"]
    assert "output_path" not in definitions["eccovox.synthesize_speech"]["input_schema"]["required"]


def test_eccovox_profile_context_is_listed_when_multiple(tmp_path):
    config = configured(tmp_path); data = config.eccovox(); data["profiles"]["outra"] = {**data["profiles"]["voz"], "description": "Outra voz"}; config.save_eccovox(data)
    definitions = {item["name"]: item for item in EccoVoxToolRegistry(config).definitions()}
    assert definitions["eccovox.list_profiles"]["name"] == "eccovox.list_profiles"
    assert definitions["eccovox.get_health"]["input_schema"]["required"] == ["profile"]


def test_eccovox_configuration_is_kept_in_mcp_subdirectory(tmp_path):
    config = configured(tmp_path)
    assert config.eccovox_path == tmp_path / "configs" / "mcp" / "eccovox.toml"
    assert json.loads(json.dumps(config.eccovox()))["profiles"]["voz"]["enabled"] is True


def test_eccovox_profile_persists_its_default_voice(tmp_path):
    config = configured(tmp_path); data = config.eccovox(); data["profiles"]["voz"].update({"voice": "pm_alex", "language": "pt-BR"}); config.save_eccovox(data)

    assert McpConfiguration(tmp_path).eccovox()["profiles"]["voz"]["voice"] == "pm_alex"
    assert 'voice = "pm_alex"' in config.eccovox_path.read_text(encoding="utf-8")
    assert 'language = "pt-BR"' in config.eccovox_path.read_text(encoding="utf-8")


def test_synthesis_uses_the_profile_default_voice_when_not_overridden(tmp_path, monkeypatch):
    config = configured(tmp_path); data = config.eccovox(); data["profiles"]["voz"].update({"voice": "pm_alex", "language": "pt-BR"}); config.save_eccovox(data)
    captured = {}

    def request(_self, _config, path, body, _content_type):
        captured.update({"path": path, "payload": json.loads(body)})
        return b"audio", "audio/mpeg"

    monkeypatch.setattr(EccoVoxService, "_request", request)
    output = tmp_path / "write" / "answer.mp3"
    EccoVoxService(tmp_path).synthesize("voz", "Olá", str(output), "mp3")

    assert captured == {"path": "/v1/audio/speech", "payload": {"input": "Olá", "responseFormat": "mp3", "voice": "pm_alex", "language": "pt-BR"}}


def test_capabilities_are_read_from_the_local_runtime(monkeypatch):
    payload = {"capabilities": {"tts": {"status": "ready", "voices": [{"id": "pm_alex"}], "languages": [{"id": "pt-BR"}]}}}
    monkeypatch.setattr(EccoVoxService, "_request", lambda *_args: (json.dumps(payload).encode(), "application/json"))

    assert EccoVoxService.capabilities_at("http://127.0.0.1:8870") == payload


def test_synthesis_without_output_path_uses_profile_default_directory(tmp_path, monkeypatch):
    config = configured(tmp_path); output_directory = tmp_path / "write" / "default"; data = config.eccovox(); data["profiles"]["voz"]["default_output_directory"] = str(output_directory); config.save_eccovox(data)
    monkeypatch.setattr(EccoVoxService, "_request", lambda *_args: (b"audio", "audio/mpeg"))

    result = EccoVoxService(tmp_path).synthesize("voz", "Olá")

    assert Path(result["output_path"]).parent == output_directory and Path(result["output_path"]).suffix == ".mp3"


def test_eccovox_rejects_symbolic_link_as_synthesis_output(tmp_path):
    config = configured(tmp_path); target = tmp_path / "write" / "target.mp3"; link = tmp_path / "write" / "link.mp3"
    try: link.symlink_to(target)
    except OSError: pytest.skip("Links simbólicos indisponíveis nesta plataforma.")
    with pytest.raises(EccoVoxError, match="link simbólico"):
        EccoVoxService(tmp_path)._contained(str(link), (tmp_path / "write",), "output_path", False)
