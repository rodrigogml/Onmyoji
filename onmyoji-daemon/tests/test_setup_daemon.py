from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


def setup_module():
    path = Path(__file__).parents[1] / "setupDaemon.py"
    spec = importlib.util.spec_from_file_location("setup_daemon", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_gateway_execution_defaults_and_preservation_across_other_actions(tmp_path):
    module = setup_module(); module.bootstrap(tmp_path)
    initial = module.telegram_data(tmp_path)
    assert initial["agent"]["busy_input_policy"] == "queue"
    assert initial["agent"]["interrupt_grace_seconds"] == 15
    assert initial["agent"]["post_turn_results"] is False
    assert initial["limits"]["preparation_workers"] == 4
    module.save_gateway_execution(tmp_path, "steer", 6, 20, True)
    module.save_default_thought_preferences(tmp_path, False, True)
    saved = module.telegram_data(tmp_path)
    assert saved["agent"]["busy_input_policy"] == "steer"
    assert saved["agent"]["interrupt_grace_seconds"] == 20
    assert saved["agent"]["post_turn_results"] is True
    assert saved["limits"]["preparation_workers"] == 6
    assert saved["telegram"]["default_share_thoughts"] is False


@pytest.mark.parametrize("schema", [1, 2])
def test_setup_reads_both_editable_versions_and_always_saves_version_two(tmp_path, schema):
    module = setup_module(); module.bootstrap(tmp_path)
    content = module.target(tmp_path).read_text(encoding="utf-8").replace("schema_version = 2", f"schema_version = {schema}", 1)
    module.target(tmp_path).write_text(content, encoding="utf-8")
    assert module.telegram_data(tmp_path)["schema_version"] == schema
    module.save_default_thought_preferences(tmp_path, True, False)
    saved = module.telegram_data(tmp_path)
    assert saved["schema_version"] == 2
    assert saved["telegram"]["default_delete_thoughts"] is False


@pytest.mark.parametrize("value", ["0", "3", "true", '"2"'])
def test_setup_refuses_unknown_schema_without_rewriting(tmp_path, value):
    module = setup_module(); module.bootstrap(tmp_path)
    content = module.target(tmp_path).read_text(encoding="utf-8").replace("schema_version = 2", f"schema_version = {value}", 1)
    module.target(tmp_path).write_text(content, encoding="utf-8")
    original = module.target(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="não suportada"):
        module.telegram_data(tmp_path)
    with pytest.raises(ValueError, match="não suportada"):
        module.save_telegram(tmp_path, "fixture", "fixture", {"schema_version": 3})
    assert module.target(tmp_path).read_bytes() == original


@pytest.mark.parametrize("policy,workers,grace,post", [(None, 4, 15, False), ("invalid", 4, 15, False), ([], 4, 15, False), ("queue", 0, 15, False), ("queue", 17, 15, False), ("queue", True, 15, False), ("queue", 4, 0, False), ("queue", 4, 61, False), ("queue", 4, 301, False), ("queue", 4, True, False), ("queue", 4, 15, "false")])
def test_gateway_execution_rejects_invalid_values_without_changing_config(tmp_path, policy, workers, grace, post):
    module = setup_module(); module.bootstrap(tmp_path)
    original = module.target(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        module.save_gateway_execution(tmp_path, policy, workers, grace, post)
    assert module.target(tmp_path).read_bytes() == original


def test_gateway_execution_cli_persists_intent_without_network(tmp_path):
    module = setup_module()
    arguments = ["setupDaemon.py", "--onmyoji-root", str(tmp_path), "--action", "set-gateway-execution", "--busy-input-policy", "steer", "--preparation-workers", "3", "--interrupt-grace-seconds", "12", "--post-turn-results", "true"]
    with patch.object(sys, "argv", arguments), patch.object(module.subprocess, "run") as run:
        assert module.main() == 0
    assert module.telegram_data(tmp_path)["agent"]["busy_input_policy"] == "steer"
    assert module.telegram_data(tmp_path)["limits"]["preparation_workers"] == 3
    run.assert_not_called()


def test_voice_setup_authorizes_only_telegram_staging(tmp_path):
    module = setup_module(); workspace = tmp_path / "workspace"; workspace.mkdir()
    configs = tmp_path / "configs"; configs.mkdir()
    (configs / "onmyoji-system.toml").write_text('[shikigami]\nidentity = "Exemplo"\ninstalled = true\n\n[codex]\nmodel = "gpt-5.6-terra"\nmodel_reasoning_effort = "medium"\nproject_directory = "' + workspace.as_posix() + '"\nsandbox_mode = "workspace-write"\napproval_policy = "never"\nadditional_writable_directories = []\n', encoding="utf-8")
    mcp = configs / "mcp"; mcp.mkdir()
    (mcp / "eccovox.toml").write_text('schema_version = 1\n\n[defaults]\nrequest_timeout_seconds = 120\nmax_audio_bytes = 100\nmax_text_characters = 100\n\n[profiles.eccovox]\nenabled = true\ndescription = "Voz do gateway"\nbase_url = "http://127.0.0.1:8870"\nreadable_roots = []\nwritable_roots = []\n', encoding="utf-8")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    target = module.bootstrap(tmp_path); data = module.telegram_data(tmp_path); module.save_telegram(tmp_path, "vault", "APIs/Telegram:Bot", data)

    staging = module.save_voice_reply(tmp_path, True, "eccovox", 15, "pm_alex", "pt-BR")

    assert staging == workspace / ".onmyoji" / "telegram" / "staging" and staging.is_dir()
    ecco = tomllib.loads((configs / "mcp" / "eccovox.toml").read_text(encoding="utf-8"))["profiles"]["eccovox"]
    assert ecco["readable_roots"] == [str(staging)] and ecco["writable_roots"] == [str(staging)]
    complete_system = tomllib.loads((configs / "onmyoji-system.toml").read_text(encoding="utf-8"))
    assert complete_system["shikigami"] == {"identity": "Exemplo", "installed": True}
    system = complete_system["codex"]
    assert system["additional_writable_directories"] == []
    voice = tomllib.loads(target.read_text(encoding="utf-8"))["voice_reply"]
    assert voice["enabled"] and voice["voice"] == "pm_alex" and voice["language"] == "pt-BR"


def test_outbound_media_does_not_require_an_eccovox_profile(tmp_path):
    module = setup_module(); workspace = tmp_path / "workspace"; workspace.mkdir()
    configs = tmp_path / "configs"; configs.mkdir()
    (configs / "onmyoji-system.toml").write_text('[codex]\nproject_directory = "' + workspace.as_posix() + '"\nsandbox_mode = "workspace-write"\n', encoding="utf-8")
    module.bootstrap(tmp_path); data = module.telegram_data(tmp_path); module.save_telegram(tmp_path, "vault", "APIs/Telegram:Bot", data)

    staging = module.save_agent_outbound_media(tmp_path, True)

    saved = tomllib.loads(module.target(tmp_path).read_text(encoding="utf-8"))
    assert staging == workspace / ".onmyoji" / "telegram" / "staging"
    assert saved["telegram"]["agent_outbound_media"] is True
    assert saved["voice_reply"]["eccovox_profile"] == ""


def test_default_thought_preferences_are_persisted_for_new_conversations(tmp_path):
    module = setup_module(); module.bootstrap(tmp_path)
    data = module.telegram_data(tmp_path); module.save_telegram(tmp_path, "vault", "APIs/Telegram:Bot", data)

    module.save_default_thought_preferences(tmp_path, False, False)

    saved = tomllib.loads(module.target(tmp_path).read_text(encoding="utf-8"))
    assert saved["telegram"]["default_share_thoughts"] is False
    assert saved["telegram"]["default_delete_thoughts"] is False


def test_max_parallel_conversations_is_persisted_with_safe_limits(tmp_path):
    module = setup_module(); module.bootstrap(tmp_path)
    data = module.telegram_data(tmp_path); module.save_telegram(tmp_path, "vault", "APIs/Telegram:Bot", data)

    module.save_max_parallel_conversations(tmp_path, 4)

    saved = tomllib.loads(module.target(tmp_path).read_text(encoding="utf-8"))
    assert saved["agent"]["max_parallel_conversations"] == 4

    try:
        module.save_max_parallel_conversations(tmp_path, 17)
    except ValueError as error:
        assert str(error) == "O máximo de conversas em paralelo deve estar entre 1 e 16."
    else:
        raise AssertionError("O limite acima de 16 deveria ser rejeitado.")


def test_context_limits_are_persisted_and_preserved_by_other_actions(tmp_path):
    module = setup_module(); module.bootstrap(tmp_path)

    module.save_context_limits(tmp_path, 30, 3600, 256 * 1024, 8)
    module.save_default_thought_preferences(tmp_path, False, True)

    saved = tomllib.loads(module.target(tmp_path).read_text(encoding="utf-8"))["limits"]
    assert saved["context_max_messages"] == 30
    assert saved["context_max_age_seconds"] == 3600
    assert saved["context_max_bytes"] == 256 * 1024
    assert saved["context_compaction_message_interval"] == 8


def test_context_limits_cli_persists_all_values_without_network(tmp_path):
    module = setup_module()
    arguments = ["setupDaemon.py", "--onmyoji-root", str(tmp_path), "--action", "set-context-limits", "--context-max-messages", "30", "--context-max-age-seconds", "3600", "--context-max-bytes", str(256 * 1024), "--context-compaction-message-interval", "8"]

    with patch.object(sys, "argv", arguments), patch.object(module.subprocess, "run") as run:
        assert module.main() == 0

    saved = module.telegram_data(tmp_path)["limits"]
    assert saved["context_max_messages"] == 30
    assert saved["context_max_age_seconds"] == 3600
    assert saved["context_max_bytes"] == 256 * 1024
    assert saved["context_compaction_message_interval"] == 8
    run.assert_not_called()


@pytest.mark.parametrize("values", [(-1, 0, 64 * 1024, 0), (1, -1, 64 * 1024, 0), (1, 0, 64 * 1024 - 1, 0), (1, 0, 4 * 1024 * 1024, 0), (1, 0, 64 * 1024, -1)])
def test_context_limits_reject_invalid_values_without_changing_config(tmp_path, values):
    module = setup_module(); module.bootstrap(tmp_path); original = module.target(tmp_path).read_bytes()

    with pytest.raises(ValueError):
        module.save_context_limits(tmp_path, *values)

    assert module.target(tmp_path).read_bytes() == original


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


def test_missing_telegram_token_entry_is_not_reported_as_a_vault_failure(tmp_path):
    module = setup_module(); resolver = MagicMock(); resolver.entry_exists.return_value = False

    with patch.object(module, "KeePassCredentialResolver", return_value=resolver):
        exists, detail = module.entry_exists(tmp_path, "default", "APIs/Telegram:Lavelinha")

    assert exists is False
    assert detail == "A entrada ainda não existe no cofre MCP."
    resolver.password_exists.assert_not_called()


def test_save_totp_requires_existing_passwords_in_the_telegram_vault(tmp_path):
    module = setup_module(); module.bootstrap(tmp_path)
    data = module.telegram_data(tmp_path); module.save_telegram(tmp_path, "vault", "APIs/Telegram:Bot", data)

    with patch.object(module, "KeePassCredentialResolver") as resolver:
        resolver.return_value.entry_exists.return_value = False
        try:
            module.save_totp(tmp_path, True, "APIs/TelegramTOTP:Example:Real", "APIs/TelegramTOTP:Example:Fake")
        except ValueError as error:
            assert "senha TOTP real não existe" in str(error)
        else:
            raise AssertionError("O TOTP não deve ser habilitado com senha ausente.")


def test_save_totp_persists_only_validated_password_references(tmp_path):
    module = setup_module(); module.bootstrap(tmp_path)
    data = module.telegram_data(tmp_path); module.save_telegram(tmp_path, "vault", "APIs/Telegram:Bot", data)

    with patch.object(module, "KeePassCredentialResolver") as resolver:
        resolver.return_value.entry_exists.return_value = True
        resolver.return_value.password_exists.return_value = True
        module.save_totp(tmp_path, True, "APIs/TelegramTOTP:Example:Real", "APIs/TelegramTOTP:Example:Fake")

    saved = tomllib.loads(module.target(tmp_path).read_text(encoding="utf-8"))
    assert saved["totp"] == {"enabled": True, "real_password_entry": "APIs/TelegramTOTP:Example:Real", "fake_password_entry": "APIs/TelegramTOTP:Example:Fake", "period_seconds": 30}


def test_telegram_validation_marks_only_the_legacy_generic_entry_as_pending(tmp_path):
    module = setup_module(); module.bootstrap(tmp_path)
    data = module.telegram_data(tmp_path)
    module.save_telegram(tmp_path, "default", "APIs/Telegram:Shikigami", data)

    checks = module.validation(tmp_path)
    reference = next(check for check in checks if check["label"] == "Referência do token")
    assert reference["state"] == "pending"

    module.save_telegram(tmp_path, "default", "APIs/Telegram:Lavelinha")
    checks = module.validation(tmp_path)
    reference = next(check for check in checks if check["label"] == "Referência do token")
    assert reference == {"state": "ok", "label": "Referência do token", "detail": "APIs/Telegram:Lavelinha"}


def test_telegram_validation_marks_a_gateway_error_as_superseded_by_a_later_token_test(tmp_path):
    module = setup_module(); module.bootstrap(tmp_path)
    now = module.time.time()
    state = tmp_path / "configs" / "daemon" / "services" / "telegram" / "state" / "gateway-status.json"; state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"last_error": "O cofre MCP não disponibilizou a credencial solicitada.", "last_error_at": now - 60}), encoding="utf-8")
    marker = tmp_path / "configs" / "daemon" / "runtime" / "telegram-test.json"; marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"tested_at": now, "bot": "example_bot"}), encoding="utf-8")

    checks = module.validation(tmp_path)

    error = next(check for check in checks if check["label"] == "Último erro do gateway")
    assert error["state"] == "ok"
    assert "Erro anterior" in error["detail"] and "superado pela validação do token" in error["detail"]


def test_daemon_setup_uses_only_the_internal_mcp_vault() -> None:
    source = (Path(__file__).parents[1] / "setupDaemon.py").read_text(encoding="utf-8")

    former_catalog = "-".join(("available", "skills"))
    assert former_catalog not in source
    assert "KeePassCredentialResolver" in source


def test_telegram_configuration_does_not_persist_platform_or_legacy_vault_sections(tmp_path):
    module = setup_module(); module.bootstrap(tmp_path)
    data = module.telegram_data(tmp_path)
    data["telegram"]["keepass_profile"] = "old-profile"
    data["app_server"] = {"enabled": True}
    data["instructions"] = {"enabled": False}

    module.save_telegram(tmp_path, "vault", "APIs/Telegram:Bot", data)

    saved = tomllib.loads(module.target(tmp_path).read_text(encoding="utf-8"))
    assert saved["telegram"]["vault_profile"] == "vault"
    assert "keepass_profile" not in saved["telegram"]
    assert "app_server" not in saved and "instructions" not in saved


def test_daemon_bootstrap_prepares_only_the_platform(tmp_path):
    module = setup_module()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version = 1\ninstance_id = "test"\ninstance_name = "Exemplo"\n', encoding="utf-8")

    configured = module.bootstrap_daemon(tmp_path)

    data = tomllib.loads(configured.read_text(encoding="utf-8"))
    assert data["app_server"]["enabled"] is False
    assert data["instructions"]["shikigami_file"] == "instructions.md"
    assert not (tmp_path / "instructions.md").exists()
    assert not (tmp_path / "configs" / "daemon" / "services" / "telegram" / "config.toml").exists()


def test_daemon_configuration_preserves_unknown_sections(tmp_path):
    module = setup_module()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version = 1\ninstance_id = "test"\ninstance_name = "Exemplo"\n\n[future]\nvalue = "preserve"\n', encoding="utf-8")

    module.bootstrap_daemon(tmp_path)
    data = module.daemon_data(tmp_path); data["app_server"]["enabled"] = True; module.save_daemon_data(tmp_path, data)

    complete = tomllib.loads(manifest.read_text(encoding="utf-8"))
    assert complete["future"] == {"value": "preserve"}
    assert complete["app_server"]["enabled"] is True


def test_runtime_selection_is_saved_in_central_system_configuration(tmp_path):
    module = setup_module(); executable = tmp_path / "python.exe"; executable.touch()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version = 1\ninstance_id = "test"\ninstance_name = "Exemplo"\n', encoding="utf-8")
    module.bootstrap_daemon(tmp_path)

    module.save_runtime(tmp_path, "global", str(executable))

    runtime = tomllib.loads((tmp_path / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))["python"]
    assert runtime == {"mode": "global", "base_python": "", "executable": str(executable)}


def test_set_runtime_cli_exits_successfully_after_saving(tmp_path):
    module = setup_module(); executable = tmp_path / "python.exe"; executable.touch()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version = 1\ninstance_id = "test"\ninstance_name = "Exemplo"\n', encoding="utf-8")
    module.bootstrap_daemon(tmp_path)

    completed = subprocess.run([sys.executable, str(Path(__file__).parents[1] / "setupDaemon.py"), "--onmyoji-root", str(tmp_path), "--action", "set-runtime", "--runtime-mode", "isolated", "--python", str(executable), "--base-python", str(executable)], text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "Runtime do daemon salvo."


def test_app_server_configuration_does_not_create_telegram_configuration(tmp_path):
    module = setup_module()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version = 1\ninstance_id = "test"\ninstance_name = "Exemplo"\n', encoding="utf-8")

    module.bootstrap_daemon(tmp_path)
    module.save_app_server(tmp_path, True, 120)

    assert not (tmp_path / "configs" / "daemon" / "services" / "telegram" / "config.toml").exists()
    assert tomllib.loads(manifest.read_text(encoding="utf-8"))["app_server"]["idle_timeout_seconds"] == 120


def test_failed_agent_test_replaces_previous_success_marker(tmp_path):
    module = setup_module()
    marker = tmp_path / "configs" / "daemon" / "runtime" / "codex-test.json"; marker.parent.mkdir(parents=True)
    marker.write_text('{"tested_at": 1, "ok": true, "detail": "old"}', encoding="utf-8")

    ok, _message = module.test_agent(tmp_path)

    assert not ok
    assert json.loads(marker.read_text(encoding="utf-8"))["ok"] is False
def test_telegram_import_failure_is_reported_without_traceback(tmp_path, monkeypatch):
    import builtins
    module = setup_module()
    original_import = builtins.__import__
    def failing_import(name, *args, **kwargs):
        if name == "onmyoji_daemon.telegram":
            raise ModuleNotFoundError("No module named 'jsonschema'")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", failing_import)

    ok, detail = module.test_telegram(tmp_path)

    assert not ok
    assert "jsonschema" in detail
    assert "Traceback" not in detail
