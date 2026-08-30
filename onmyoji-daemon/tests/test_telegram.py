from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from onmyoji_daemon.telegram import AppServerTurn, Contacts, Gateway, Settings, Vault


def write_settings(root: Path, data_dir: Path) -> None:
    (root / "configs").mkdir(parents=True); (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nexecutable = "codex"\nmodel = "gpt-5.6-terra"\nmodel_reasoning_effort = "medium"\nproject_directory = "' + root.as_posix() + '"\nsandbox_mode = "workspace-write"\napproval_policy = "never"\n', encoding="utf-8")
    data_dir.mkdir(parents=True); (data_dir / "telegram.toml").write_text('schema_version = 1\n[telegram]\nkeepass_profile = "telegram"\ntoken_entry = "APIs/Telegram:Lavelinha"\n', encoding="utf-8")


def test_settings_bind_to_instance_codex_home_and_workspace(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    settings = Settings.load(tmp_path, data)
    assert settings.root == tmp_path.resolve() and settings.project == tmp_path.resolve()


def test_app_server_settings_default_to_disabled_and_enforce_idle_minimum(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    settings = Settings.load(tmp_path, data)
    assert not settings.app_server_enabled and settings.app_server_idle_seconds == 1800
    (data / "telegram.toml").write_text((data / "telegram.toml").read_text(encoding="utf-8") + "\n[app_server]\nenabled = true\nidle_timeout_seconds = 120\n", encoding="utf-8")
    settings = Settings.load(tmp_path, data)
    assert settings.app_server_enabled and settings.app_server_idle_seconds == 120


def test_gateway_sandbox_includes_only_configured_activity_root(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    staging = tmp_path / ".onmyoji" / "telegram" / "staging"
    (tmp_path / "configs" / "onmyoji-system.toml").write_text((tmp_path / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8") + f'additional_writable_directories = ["{staging.as_posix()}"]\n', encoding="utf-8")
    policy = Gateway(Settings.load(tmp_path, data))._app_sandbox_policy()
    assert policy["type"] == "workspaceWrite" and str(staging.resolve()) in policy["writableRoots"]


def test_tts_uses_local_eccovox_profile_only_for_wrapper_selection(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    (data / "telegram.toml").write_text((data / "telegram.toml").read_text(encoding="utf-8") + "\n[voice_reply]\nenabled = true\neccovox_profile = \"eccovox\"\n", encoding="utf-8")
    gateway = Gateway(Settings.load(tmp_path, data)); output = tmp_path / "voice.opus"; captured = {}
    def run(command, **kwargs):
        captured["command"], captured["request"] = command, json.loads(kwargs["input"])
        output.write_bytes(b"audio")
        return type("Result", (), {"stdout": json.dumps({"ok": True})})()
    with patch("onmyoji_daemon.telegram.subprocess.run", side_effect=run): gateway._tts("Teste", output)
    assert captured["command"][-1] == "eccovox" and "profile" not in captured["request"]


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
    assert title == "Configurações do bot" and top["inline_keyboard"][0][0]["text"] == "💭 Pensamentos" and top["inline_keyboard"][0][0]["callback_data"] == "cfg:token:thoughts"
    assert thoughts_title == "Configurações › Pensamentos"
    assert "Compartilha Pensamentos" in thoughts["inline_keyboard"][0][0]["text"]


def test_app_server_thoughts_are_shared_deduplicated_and_cleaned(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); gateway = Gateway(Settings.load(tmp_path, data))
    sent, deleted = [], []
    gateway.api = type("Api", (), {"send": lambda _self, chat, text, **values: sent.append((chat, text, values)) or {"message_id": 71}, "delete": lambda _self, chat, message: deleted.append((chat, message))})()
    active = AppServerTurn(9, "thread", __import__("threading").Event()); gateway.app_turns["thread"] = active
    notification = {"threadId": "thread", "item": {"type": "reasoning", "summary": ["Plano seguro."]}}
    gateway._app_notification("item/completed", notification); gateway._app_notification("item/completed", notification)
    assert sent == [(9, "💭 Plano seguro.", {"protect_content": True})] and active.thought_ids == [71]
    gateway._cleanup_thoughts(active)
    assert deleted == [(9, 71)]


def test_new_conversation_forgets_persisted_app_server_thread(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); gateway = Gateway(Settings.load(tmp_path, data))
    gateway.database.execute("INSERT INTO conversations(chat_id, updated_at, codex_thread_id) VALUES (?, ?, ?)", ("9", 0, "thread-old")); gateway.database.commit()
    gateway._new_conversation(9)
    row = gateway.database.execute("SELECT generation, codex_thread_id FROM conversations WHERE chat_id='9'").fetchone()
    assert row == (1, None)


def test_attachments_are_retained_in_workspace_and_removed_by_new(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); gateway = Gateway(Settings.load(tmp_path, data))
    archive = gateway.archive_root / "9" / "0" / "entry" / "report.txt"; archive.parent.mkdir(parents=True); archive.write_text("ok", encoding="utf-8")
    gateway.database.execute("INSERT INTO conversations(chat_id, updated_at) VALUES (?, ?)", ("9", 0))
    gateway.database.execute("INSERT INTO conversation_attachments(id, chat_id, generation, kind, name, mime_type, size, archive_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ("entry", "9", 0, "document", "report.txt", "text/plain", 2, str(archive), 0)); gateway.database.commit()
    assert gateway._attachment_rows(9)[0]["name"] == "report.txt" and archive.is_file()
    gateway._new_conversation(9)
    assert gateway._attachment_rows(9) == [] and not archive.exists()


def test_attachment_activity_root_is_never_in_codex_home_configs(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); gateway = Gateway(Settings.load(tmp_path, data))
    assert gateway.activity_root == tmp_path / ".onmyoji" / "telegram"
    assert gateway.activity_root.is_relative_to(tmp_path) and not gateway.activity_root.is_relative_to(data)


def test_voice_without_caption_never_attempts_to_parse_an_empty_command(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    sent = []
    gateway.api = type("Api", (), {"send": lambda _self, chat, text, **_values: sent.append((chat, text)) or {"message_id": 1}})()
    gateway._update({"message": {"chat": {"type": "private"}, "from": {"id": 9}, "voice": {"file_id": "voice-id"}}})
    assert sent == [(9, "Anexos exigem que o App Server esteja habilitado na configuração do Gateway Telegram.")]


def test_every_received_command_is_deleted_before_routing(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); gateway = Gateway(Settings.load(tmp_path, data))
    deleted, sent = [], []
    gateway.api = type("Api", (), {"delete": lambda _self, chat, message: deleted.append((chat, message)), "send": lambda _self, chat, text, **_values: sent.append((chat, text)) or {"message_id": 1}})()
    gateway._update({"message": {"message_id": 70, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": "/comando-invalido"}})
    assert deleted == [(9, 70)]
    assert sent == []


def test_new_command_is_deleted_once_and_still_resets_conversation(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    deleted, sent = [], []
    gateway.api = type("Api", (), {"delete": lambda _self, chat, message: deleted.append((chat, message)), "send": lambda _self, chat, text, **_values: sent.append((chat, text)) or {"message_id": 1}})()
    gateway._update({"message": {"message_id": 71, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": "/new"}})
    assert deleted == [(9, 71)]
    assert sent == [(9, "Conversa reiniciada.")]


def test_totp_session_cleanup_removes_all_related_messages(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data))
    deleted = []
    gateway.api = type("Api", (), {"delete": lambda _self, chat, message: deleted.append((chat, message))})()
    gateway.totp_sessions[9] = {"message_ids": [40, 41]}
    gateway._clear_totp_session(9)
    assert deleted == [(9, 40), (9, 41)] and 9 not in gateway.totp_sessions
