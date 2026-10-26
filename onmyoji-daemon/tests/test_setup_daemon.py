from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path


def setup_module():
    path = Path(__file__).parents[1] / "setupDaemon.py"
    spec = importlib.util.spec_from_file_location("setup_daemon", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_voice_setup_authorizes_only_telegram_staging(tmp_path):
    module = setup_module(); workspace = tmp_path / "workspace"; workspace.mkdir()
    configs = tmp_path / "configs"; configs.mkdir()
    (configs / "onmyoji-system.toml").write_text('[shikigami]\nidentity = "Exemplo"\ninstalled = true\n\n[codex]\nmodel = "gpt-5.6-terra"\nmodel_reasoning_effort = "medium"\nproject_directory = "' + workspace.as_posix() + '"\nsandbox_mode = "workspace-write"\napproval_policy = "never"\nadditional_writable_directories = []\n', encoding="utf-8")
    mcp = configs / "mcp"; mcp.mkdir()
    (mcp / "eccovox.toml").write_text('schema_version = 1\n\n[defaults]\nrequest_timeout_seconds = 120\nmax_audio_bytes = 100\nmax_text_characters = 100\n\n[profiles.eccovox]\nenabled = true\ndescription = "Voz do gateway"\nbase_url = "http://127.0.0.1:8870"\nreadable_roots = []\nwritable_roots = []\n', encoding="utf-8")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    target = module.bootstrap(tmp_path); data = module.telegram_data(tmp_path); module.save_telegram(tmp_path, "vault", "APIs/Telegram:Bot", data)

    staging = module.save_voice_reply(tmp_path, True, "eccovox", 15, True)

    assert staging == workspace / ".onmyoji" / "telegram" / "staging" and staging.is_dir()
    ecco = tomllib.loads((configs / "mcp" / "eccovox.toml").read_text(encoding="utf-8"))["profiles"]["eccovox"]
    assert ecco["readable_roots"] == [str(staging)] and ecco["writable_roots"] == [str(staging)]
    complete_system = tomllib.loads((configs / "onmyoji-system.toml").read_text(encoding="utf-8"))
    assert complete_system["shikigami"] == {"identity": "Exemplo", "installed": True}
    system = complete_system["codex"]
    assert system["additional_writable_directories"] == [str(staging)]
    assert tomllib.loads(target.read_text(encoding="utf-8"))["voice_reply"]["enabled"]


def test_validation_requires_an_absolute_codex_executable_for_services(tmp_path):
    module = setup_module(); workspace = tmp_path / "workspace"; workspace.mkdir()
    configs = tmp_path / "configs"; configs.mkdir()
    (configs / "onmyoji-system.toml").write_text('[codex]\nexecutable = "codex"\nproject_directory = "' + workspace.as_posix() + '"\n', encoding="utf-8")
    module.bootstrap(tmp_path)

    checks = module.validation(tmp_path)

    executable = next(item for item in checks if item["label"] == "Executável Codex para serviço")
    assert executable["state"] == "error"


def test_telegram_validation_reports_missing_mcp_vault_profiles(tmp_path):
    module = setup_module(); module.bootstrap(tmp_path)

    checks = module.validation(tmp_path)

    profiles = next(item for item in checks if item["label"] == "Perfis de vault MCP")
    assert profiles["state"] == "error"


def test_daemon_bootstrap_prepares_only_the_platform(tmp_path):
    module = setup_module()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version = 1\ninstance_id = "test"\ninstance_name = "Exemplo"\n', encoding="utf-8")

    configured = module.bootstrap_daemon(tmp_path)

    data = tomllib.loads(configured.read_text(encoding="utf-8"))
    assert data["app_server"]["enabled"] is False
    assert data["instructions"]["shikigami_file"] == "instructions.md"
    assert (tmp_path / "shikigami" / "instructions.md").is_file()
    assert not (tmp_path / "shikigami" / "daemon" / "telegram.toml").exists()


def test_daemon_configuration_preserves_unknown_sections(tmp_path):
    module = setup_module()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version = 1\ninstance_id = "test"\ninstance_name = "Exemplo"\n\n[future]\nvalue = "preserve"\n', encoding="utf-8")

    module.bootstrap_daemon(tmp_path)
    data = module.daemon_data(tmp_path); data["app_server"]["enabled"] = True; module.save_daemon_data(tmp_path, data)

    complete = tomllib.loads(manifest.read_text(encoding="utf-8"))
    assert complete["future"] == {"value": "preserve"}
    assert complete["app_server"]["enabled"] is True


def test_runtime_selection_is_saved_in_daemon_manifest(tmp_path):
    module = setup_module(); executable = tmp_path / "python.exe"; executable.touch()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version = 1\ninstance_id = "test"\ninstance_name = "Exemplo"\n', encoding="utf-8")
    module.bootstrap_daemon(tmp_path)

    module.save_runtime(tmp_path, "global", str(executable))

    runtime = tomllib.loads((tmp_path / "configs" / "daemon" / "daemon.toml").read_text(encoding="utf-8"))["runtime"]
    assert runtime == {"mode": "global", "base_python": "", "executable": str(executable)}


def test_app_server_configuration_does_not_create_telegram_configuration(tmp_path):
    module = setup_module()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version = 1\ninstance_id = "test"\ninstance_name = "Exemplo"\n', encoding="utf-8")

    module.bootstrap_daemon(tmp_path)
    module.save_app_server(tmp_path, True, 120)

    assert not (tmp_path / "shikigami" / "daemon" / "telegram.toml").exists()
    assert tomllib.loads(manifest.read_text(encoding="utf-8"))["app_server"]["idle_timeout_seconds"] == 120


def test_failed_agent_test_replaces_previous_success_marker(tmp_path):
    module = setup_module()
    marker = tmp_path / "configs" / "daemon" / "runtime" / "codex-test.json"; marker.parent.mkdir(parents=True)
    marker.write_text('{"tested_at": 1, "ok": true, "detail": "old"}', encoding="utf-8")

    ok, _message = module.test_agent(tmp_path)

    assert not ok
    assert json.loads(marker.read_text(encoding="utf-8"))["ok"] is False
