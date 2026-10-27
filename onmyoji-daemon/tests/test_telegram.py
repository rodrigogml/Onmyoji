from __future__ import annotations

import json
import io
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from onmyoji_daemon.telegram import AppServerTurn, CodexAppServer, Contacts, Gateway, McpVault, Settings
from onmyoji_daemon.launcher import _approval_policy, _sandbox_policy


def write_settings(root: Path, data_dir: Path) -> None:
    (root / "configs").mkdir(parents=True); (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nexecutable = "codex"\nmodel = "gpt-5.6-terra"\nmodel_reasoning_effort = "medium"\nproject_directory = "' + root.as_posix() + '"\nsandbox_mode = "workspace-write"\napproval_policy = "never"\n', encoding="utf-8")
    data_dir.mkdir(parents=True); config = root / "shikigami" / "daemon" / "telegram.toml"; config.parent.mkdir(parents=True); config.write_text('schema_version = 1\n[telegram]\nvault_profile = "telegram"\ntoken_entry = "APIs/Telegram:Example"\n', encoding="utf-8")


def test_settings_bind_to_instance_codex_home_and_workspace(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    settings = Settings.load(tmp_path, data)
    assert settings.root == tmp_path.resolve() and settings.project == tmp_path.resolve()


def test_app_server_settings_default_to_disabled_and_enforce_idle_minimum(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    settings = Settings.load(tmp_path, data)
    assert not settings.app_server_enabled and settings.app_server_idle_seconds == 1800
    config = tmp_path / "shikigami" / "daemon" / "telegram.toml"; config.write_text(config.read_text(encoding="utf-8") + "\n[app_server]\nenabled = true\nidle_timeout_seconds = 120\n", encoding="utf-8")
    settings = Settings.load(tmp_path, data)
    assert settings.app_server_enabled and settings.app_server_idle_seconds == 120


def test_app_server_settings_prefer_daemon_platform_configuration(tmp_path):
    data = tmp_path / "data"; write_settings(tmp_path, data)
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True)
    manifest.write_text('[app_server]\nenabled = true\nidle_timeout_seconds = 120\n\n[instructions]\nenabled = true\nshikigami_file = "instructions.md"\n', encoding="utf-8")

    settings = Settings.load(tmp_path, data)

    assert settings.app_server_enabled and settings.app_server_idle_seconds == 120


def test_app_server_does_not_pass_profiles_to_the_codex_subcommand(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    settings = Settings.load(tmp_path, data)
    captured = {}

    class Process:
        stdin = io.StringIO()
        stdout = io.StringIO()
        stderr = io.StringIO()
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout=None): return 0

    launch = (["codex", "app-server", "--stdio"], settings.project, {"CODEX_HOME": str(settings.root)})
    with patch("onmyoji_daemon.telegram.CodexLaunch.from_system") as configured, patch("onmyoji_daemon.telegram.subprocess.Popen", side_effect=lambda command, **_kwargs: captured.setdefault("command", command) and Process()):
        configured.return_value.app_server_launch.return_value = launch
        client = CodexAppServer(settings)
        client.request = lambda *_args, **_kwargs: {}
        client.notify = lambda *_args, **_kwargs: None
        client.start()
        client.stop()

    assert captured["command"] == ["codex", "app-server", "--stdio"]
    configured.return_value.app_server_launch.assert_called_once()


def test_gateway_recycles_only_the_idle_app_server_when_mcp_reconciliation_is_pending(tmp_path, monkeypatch):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data))

    class Client:
        def __init__(self): self.stops = 0; self.starts = 0
        def running(self): return True
        def stop(self): self.stops += 1
        def start(self): self.starts += 1

    client = Client()
    gateway.app_server = client
    monkeypatch.setattr("onmyoji_daemon.telegram.mcp_reconciliation_pending", lambda _root: True)

    assert gateway._app_client() is client
    assert client.stops == 1 and client.starts == 1


def test_gateway_recycles_context_when_managed_memory_skill_is_repaired(tmp_path, monkeypatch):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data))
    gateway.database.execute("INSERT INTO conversations(chat_id, updated_at, last_interaction_at, codex_thread_id, instruction_baseline_hash) VALUES ('1', 1, 1, 'thread-old', 'hash-old')"); gateway.database.commit()

    class Client:
        def __init__(self): self.stops = 0; self.starts = 0
        def running(self): return True
        def stop(self): self.stops += 1
        def start(self): self.starts += 1

    client = Client(); gateway.app_server = client
    monkeypatch.setattr("onmyoji_daemon.telegram.CodexLaunch.synchronize_memory_skill", lambda _self: True)
    monkeypatch.setattr("onmyoji_daemon.telegram.mcp_reconciliation_pending", lambda _root: False)
    assert gateway._app_client() is client
    row = gateway.database.execute("SELECT codex_thread_id, instruction_baseline_hash FROM conversations WHERE chat_id='1'").fetchone()
    assert client.stops == 1 and client.starts == 1
    assert row == (None, None)


def test_gateway_sandbox_includes_only_configured_activity_root(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    staging = tmp_path / ".onmyoji" / "telegram" / "staging"
    (tmp_path / "configs" / "onmyoji-system.toml").write_text((tmp_path / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8") + f'additional_writable_directories = ["{staging.as_posix()}"]\n', encoding="utf-8")
    policy = Gateway(Settings.load(tmp_path, data))._app_sandbox_policy()
    assert policy["type"] == "workspaceWrite" and str(staging.resolve()) in policy["writableRoots"]


def test_interactive_console_translates_policy_and_relative_writable_directories(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    reports = tmp_path / "reports"; reports.mkdir()
    system = tmp_path / "configs" / "onmyoji-system.toml"
    system.write_text(system.read_text(encoding="utf-8").replace('approval_policy = "never"', 'approval_policy = "on-request"') + 'additional_writable_directories = ["reports"]\n', encoding="utf-8")
    settings = Settings.load(tmp_path, data); policy = _sandbox_policy(settings)
    assert _approval_policy(settings) == "onRequest"
    assert policy == {"type": "workspaceWrite", "writableRoots": [str(tmp_path.resolve()), str(reports.resolve())], "networkAccess": False}


def test_tts_uses_internal_mcp_eccovox_service(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    config = tmp_path / "shikigami" / "daemon" / "telegram.toml"; config.write_text(config.read_text(encoding="utf-8") + "\n[voice_reply]\nenabled = true\neccovox_profile = \"eccovox\"\n", encoding="utf-8")
    gateway = Gateway(Settings.load(tmp_path, data)); output = tmp_path / "voice.opus"; captured = {}
    def synthesize(_self, profile, text, output_path, response_format, **kwargs):
        captured.update({"profile": profile, "text": text, "output": output_path, "format": response_format, **kwargs})
        Path(output_path).write_bytes(b"audio")
    with patch("onmyoji_daemon.telegram.EccoVoxService.synthesize", synthesize): gateway._tts("Teste", output)
    assert captured["profile"] == "eccovox" and captured["output"] == str(output)


def test_contacts_are_local_and_do_not_share_owners(tmp_path):
    left, right = Contacts(tmp_path / "left" / "contacts.json"), Contacts(tmp_path / "right" / "contacts.json")
    left.add_owner({"id": 123, "first_name": "owner"})
    assert left.owners() == {123} and right.owners() == set()


def test_vault_token_uses_mcp_credential_resolver_without_exposing_the_token(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); settings = Settings.load(tmp_path, data)
    with patch("onmyoji_daemon.telegram.KeePassCredentialResolver.resolve", return_value="secret") as resolve:
        assert McpVault(settings).read(settings.token_entry) == "secret"
    resolve.assert_called_once_with("telegram", "APIs/Telegram:Example", "password")


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


def test_failed_app_server_turn_preserves_sanitized_protocol_reason(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); gateway = Gateway(Settings.load(tmp_path, data))
    active = AppServerTurn(9, "thread", __import__("threading").Event()); gateway.app_turns["thread"] = active
    gateway._app_notification("turn/completed", {"threadId": "thread", "turn": {"status": "failed", "error": {"code": "invalid_model", "message": "token=abc modelo indisponível"}}})
    assert active.completed.is_set() and active.status == "failed"
    assert "invalid_model" in active.failure_detail and "modelo indisponível" in active.failure_detail and "abc" not in active.failure_detail


def test_credit_exhaustion_has_a_safe_public_telegram_message():
    error = RuntimeError("O turno do Codex terminou com estado failed: Your workspace is out of credits. Add credits to continue.")
    assert Gateway._public_turn_error(error) == "O workspace do Codex está sem créditos. Adicione créditos ao workspace e tente novamente."
    assert Gateway._public_turn_error(RuntimeError("falha interna token=abc")) == "O agente não conseguiu concluir esta solicitação."


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


def test_forum_topic_creation_is_ignored_without_an_empty_message_warning(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    sent = []
    gateway.api = type("Api", (), {"send": lambda _self, chat, text, **_values: sent.append((chat, text)) or {"message_id": 1}})()

    gateway._update({"message": {"message_id": 1, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "forum_topic_created": {"name": "Nova conversa"}}})

    assert sent == []


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


def test_group_topic_isolated_and_only_owner_mention_enqueues_context(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"}); gateway.bot_username = "testbot"; gateway.bot_id = 88
    gateway.api = type("Api", (), {"delete": lambda *_args: None, "send": lambda *_args, **_kwargs: {"message_id": 1}})()
    queued = []; gateway._enqueue_turn = lambda channel, text, attachments: queued.append((channel, text, attachments))
    chat = {"id": -1001, "type": "supergroup"}
    outsider = {"id": 7, "first_name": "other"}
    gateway._group_message("group:-1001:11", chat, outsider, {"message_id": 1, "message_thread_id": 11}, "instrução não confiável")
    assert queued == []
    owner = {"id": 9, "first_name": "owner"}
    gateway._group_message("group:-1001:11", chat, owner, {"message_id": 2, "message_thread_id": 11, "entities": [{"type": "mention", "offset": 0, "length": 8}]}, "@testbot resumir")
    assert len(queued) == 1 and queued[0][0] == "group:-1001:11"
    assert '"trust": "UNTRUSTED_USER"' in queued[0][1] and '"trust": "OWNER"' in queued[0][1]


def test_private_topic_isolated_and_routes_responses_to_its_topic(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    gateway.settings = replace(gateway.settings, app_server_enabled=True)
    queued, sent = [], []
    gateway.api = type("Api", (), {"delete": lambda *_args: None, "typing": lambda *_args, **_kwargs: None, "send": lambda _self, chat, text, **values: sent.append((chat, text, values)) or {"message_id": 1}})()
    gateway._enqueue_turn = lambda channel, text, attachments: queued.append((channel, text, attachments))
    message = {"message_id": 3, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "message_thread_id": 42, "text": "olá"}
    gateway._update({"message": message})
    assert queued == [("private:9:42", "olá", [])]
    gateway._app_turn = lambda *_args: ("resposta", "text")
    gateway._turn_serial("private:9:42", "olá", [])
    assert sent[-1] == (9, "resposta", {"message_thread_id": 42})


def test_private_topic_ephemeral_controls_stay_in_the_active_topic(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); sent = []
    gateway.api = type("Api", (), {"send": lambda _self, chat, text, **values: sent.append((chat, text, values)) or {"message_id": 8}})()
    gateway._ephemeral("private:9:42", "controle")
    assert sent == [(9, "controle", {"protect_content": True, "message_thread_id": 42})]


def test_config_command_keeps_execution_preferences_scoped_to_each_private_topic(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    gateway.settings = replace(gateway.settings, model="default-model", effort="medium", owner_allowed_models=("topic-model", "other-model"), owner_allowed_efforts=("low", "high"))
    sent, edited = [], []
    gateway.api = type("Api", (), {
        "delete": lambda *_args: None,
        "send": lambda _self, chat, text, **values: sent.append((chat, text, values)) or {"message_id": len(sent)},
        "edit": lambda _self, chat, message, text, keyboard: edited.append((chat, message, text, keyboard)),
        "call": lambda *_args, **_kwargs: None,
    })()

    gateway._update({"message": {"message_id": 1, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": "/config"}})
    direct_token = next(iter(gateway.config_sessions))
    gateway._callback({"id": "direct", "from": {"id": 9}, "message": {"message_id": 1, "chat": {"id": 9, "type": "private"}}, "data": f"cfg:{direct_token}:model"})

    gateway._update({"message": {"message_id": 2, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "message_thread_id": 42, "text": "/config"}})
    topic_token = next(token for token in gateway.config_sessions if token != direct_token)
    gateway._callback({"id": "topic", "from": {"id": 9}, "message": {"message_id": 2, "chat": {"id": 9, "type": "private"}, "message_thread_id": 42}, "data": f"cfg:{topic_token}:effort"})

    assert gateway._execution_preferences(9) == ("topic-model", "medium")
    assert gateway._execution_preferences("private:9:42") == ("default-model", "low")
    assert sent[-1][2]["message_thread_id"] == 42 and len(edited) == 2

def test_config_execution_submenus_select_the_current_private_topic(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    gateway.settings = replace(gateway.settings, model="default-model", owner_allowed_models=("first-model", "second-model"), owner_allowed_efforts=("low", "high"))
    sent, edited = [], []
    gateway.api = type("Api", (), {
        "delete": lambda *_args: None,
        "send": lambda _self, chat, text, **values: sent.append((chat, text, values)) or {"message_id": len(sent)},
        "edit": lambda _self, chat, message, text, keyboard: edited.append((chat, message, text, keyboard)),
        "call": lambda *_args, **_kwargs: None,
    })()

    gateway._update({"message": {"message_id": 7, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "message_thread_id": 42, "text": "/config"}})
    token, session = next(iter(gateway.config_sessions.items()))
    callback_message = {"message_id": session["message_id"], "chat": {"id": 9, "type": "private"}, "message_thread_id": 42}
    gateway._callback({"id": "execution", "from": {"id": 9}, "message": callback_message, "data": f"cfg:{token}:execution"})
    gateway._callback({"id": "models", "from": {"id": 9}, "message": callback_message, "data": f"cfg:{token}:models"})

    title, keyboard = edited[-1][2:]
    assert title == "Configurações › Execução › Modelo"
    assert keyboard["inline_keyboard"][0][0]["text"] == "✓ first-model"
    assert keyboard["inline_keyboard"][1][0]["callback_data"] == f"cfg:{token}:model-1"

    gateway._callback({"id": "choose", "from": {"id": 9}, "message": callback_message, "data": f"cfg:{token}:model-1"})
    assert gateway._execution_preferences("private:9:42")[0] == "second-model"
    assert edited[-1][2] == "Configurações › Execução"


def test_config_command_uses_direct_messages_topic_id_for_a_direct_message_topic(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    sent = []
    gateway.api = type("Api", (), {
        "delete": lambda *_args: None,
        "send": lambda _self, chat, text, **values: sent.append((chat, text, values)) or {"message_id": 5},
    })()

    gateway._update({"message": {"message_id": 3, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "direct_messages_topic": {"topic_id": 900000000001}, "text": "/config"}})

    assert next(iter(gateway.config_sessions.values()))["chat_id"] == "direct:9:900000000001"
    assert sent[0][0] == 9 and sent[0][2]["direct_messages_topic_id"] == 900000000001


def test_context_window_reports_locally_dropped_messages(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); gateway = Gateway(Settings.load(tmp_path, data))
    channel, sender = "group:-1001:12", {"id": 7, "first_name": "other"}
    for index in range(3): gateway._cache_message(channel, {"message_id": index + 1, "date": 100 + index}, sender, f"m{index}", [])
    gateway.database.execute("UPDATE conversations SET context_max_messages=1, context_max_age_seconds=0 WHERE chat_id=?", (channel,)); gateway.database.commit()
    snapshot, attachments = gateway._context_snapshot(channel)
    assert '"dropped_messages": 2' in snapshot and '"max_messages"' in snapshot and attachments == []
