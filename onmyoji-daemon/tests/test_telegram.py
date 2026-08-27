from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from onmyoji_daemon.telegram import Contacts, Gateway, Settings, Vault


def write_settings(root: Path, data_dir: Path) -> None:
    (root / "configs").mkdir(parents=True); (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nexecutable = "codex"\nmodel = "gpt-5.6-terra"\nmodel_reasoning_effort = "medium"\nproject_directory = "' + root.as_posix() + '"\nsandbox_mode = "workspace-write"\napproval_policy = "never"\n', encoding="utf-8")
    data_dir.mkdir(parents=True); (data_dir / "telegram.toml").write_text('schema_version = 1\n[telegram]\nkeepass_profile = "telegram"\ntoken_entry = "APIs/Telegram:Lavelinha"\n', encoding="utf-8")


def test_settings_bind_to_instance_codex_home_and_workspace(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    settings = Settings.load(tmp_path, data)
    assert settings.root == tmp_path.resolve() and settings.project == tmp_path.resolve()


def test_contacts_are_local_and_do_not_share_owners(tmp_path):
    left, right = Contacts(tmp_path / "left" / "contacts.json"), Contacts(tmp_path / "right" / "contacts.json")
    left.add_owner({"id": 123, "first_name": "owner"})
    assert left.owners() == {123} and right.owners() == set()


def test_vault_token_uses_onmyoji_keepass_wrapper_without_token_argument(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); settings = Settings.load(tmp_path, data)
    captured = {}
    def run(command, **kwargs):
        captured["command"], captured["input"] = command, kwargs["input"]
        return type("Result", (), {"stdout": json.dumps({"ok": True, "result": {"value": "secret"}})})()
    with patch("onmyoji_daemon.telegram.subprocess.run", side_effect=run): assert Vault(settings).read(settings.token_entry) == "secret"
    assert "secret" not in " ".join(captured["command"])
    assert json.loads(captured["input"])["entry"]["path"] == "APIs/Telegram:Lavelinha"


def test_totp_filter_supports_case_insensitive_wildcards():
    entries = ["APIs/MSN/Rodrigo", "APIs/msn/Outro", "Banco/Rodrigo"]
    assert Gateway._filter_totp(entries, "MSN*Rodrigo") == ["APIs/MSN/Rodrigo"]


def test_config_keyboard_exposes_legacy_conversation_preferences():
    title, top = Gateway._config_keyboard("token", {"share_thoughts": True, "delete_thoughts": True})
    thoughts_title, thoughts = Gateway._config_keyboard("token", {"share_thoughts": False, "delete_thoughts": True}, True)
    assert title == "Configurações do bot" and top["inline_keyboard"][0][0]["callback_data"] == "cfg:token:thoughts"
    assert thoughts_title == "Configurações › Pensamentos"
    assert "Compartilha Pensamentos" in thoughts["inline_keyboard"][0][0]["text"]
