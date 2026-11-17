from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

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


@pytest.mark.parametrize("document", [
    b'profiles = "PRIVATE_CONFIG"\n',
    b'profiles = []\n',
    b'defaults = "PRIVATE_CONFIG"\n',
    b'defaults = []\n',
    b'profiles = [\n',
    b'\xffPRIVATE_CONFIG',
])
def test_invalid_configuration_is_reported_without_exposing_its_content(tmp_path, document):
    """Estrutura, TOML e UTF-8 inválidos preservam o código de configuração."""
    path = tmp_path / "configs" / "mcp" / "eccovox.toml"
    path.parent.mkdir(parents=True)
    path.write_bytes(document)

    with pytest.raises(EccoVoxError) as raised:
        EccoVoxService(tmp_path).health("voz")

    assert raised.value.code == "invalid_config"
    assert raised.value.http_status is None
    assert "PRIVATE_CONFIG" not in str(raised.value)


def test_missing_configuration_has_a_stable_configuration_error(tmp_path):
    with pytest.raises(EccoVoxError) as raised:
        EccoVoxService(tmp_path).health("voz")

    assert raised.value.code == "invalid_config"


@pytest.mark.parametrize("profile_name,enabled", [("missing", True), ("voz", False)])
def test_missing_or_disabled_profile_remains_unavailable(tmp_path, profile_name, enabled):
    config = configured(tmp_path)
    data = config.eccovox()
    data["profiles"]["voz"]["enabled"] = enabled
    config.save_eccovox(data)

    with pytest.raises(EccoVoxError) as raised:
        EccoVoxService(tmp_path).health(profile_name)

    assert raised.value.code == "profile_unavailable"


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), "PRIVATE_CONFIG", []])
def test_invalid_numeric_configuration_preserves_the_configuration_error(tmp_path, value):
    config = configured(tmp_path)
    data = config.eccovox()
    data["defaults"]["request_timeout_seconds"] = value
    config.eccovox_path.write_text(config._render_eccovox(data), encoding="utf-8")

    with pytest.raises(EccoVoxError) as raised:
        EccoVoxService(tmp_path).health("voz")

    assert raised.value.code == "invalid_config"
    assert "PRIVATE_CONFIG" not in str(raised.value)


@pytest.mark.parametrize("writable_roots", [[], ["write"]])
def test_default_output_without_writable_roots_or_workspace_is_invalid_config(tmp_path, writable_roots):
    config = configured(tmp_path)
    data = config.eccovox()
    data["profiles"]["voz"]["default_output_directory"] = ""
    data["profiles"]["voz"]["writable_roots"] = [str(tmp_path / value) for value in writable_roots]
    config.save_eccovox(data)

    with pytest.raises(EccoVoxError) as raised:
        EccoVoxService(tmp_path).health("voz")

    assert raised.value.code == "invalid_config"


@pytest.mark.parametrize("field", ["readable_roots", "default_output_directory"])
def test_unresolvable_configured_paths_are_reported_as_invalid_config(tmp_path, monkeypatch, field):
    config = configured(tmp_path)
    data = config.eccovox()
    denied = tmp_path / "PRIVATE_PATH"
    data["profiles"]["voz"][field] = [str(denied)] if field == "readable_roots" else str(denied)
    config.save_eccovox(data)
    resolve = Path.resolve

    def deny_private_path(path, *args, **kwargs):
        if path == denied:
            raise OSError("PRIVATE_PATH")
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", deny_private_path)

    with pytest.raises(EccoVoxError) as raised:
        EccoVoxService(tmp_path).health("voz")

    assert raised.value.code == "invalid_config"
    assert "PRIVATE_PATH" not in str(raised.value)


@pytest.mark.parametrize("endpoint", ["/v1/audio/transcriptions", "/v1/audio/transcriptions/", "/v1/audio/speech", "/v1/health"])
@pytest.mark.parametrize("status", [400, 404, 405, 429, 500, 501, 503])
def test_http_errors_identify_unsupported_transcription_only_at_its_exact_endpoint(monkeypatch, endpoint, status):
    """404/405/501 de STT distinguem capacidade ausente de falhas do runtime."""
    def reject(_query, *, timeout):
        raise HTTPError("http://127.0.0.1:8870/PRIVATE_RUNTIME", status, "PRIVATE_RUNTIME", None, None)

    monkeypatch.setattr("onmyoji_daemon.mcp.eccovox.service.request.build_opener", lambda *_args: SimpleNamespace(open=reject))

    with pytest.raises(EccoVoxError) as raised:
        EccoVoxService._request({"base_url": "http://127.0.0.1:8870", "timeout": 1}, endpoint, b"audio", "audio/ogg")

    expected = "transcription_unsupported" if endpoint == "/v1/audio/transcriptions" and status in {404, 405, 501} else "runtime_error"
    assert raised.value.code == expected
    assert raised.value.http_status == status
    assert "PRIVATE_RUNTIME" not in str(raised.value)
