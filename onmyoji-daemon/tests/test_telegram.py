from __future__ import annotations

import json
import io
import subprocess
import threading
import time
from urllib.error import HTTPError
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from onmyoji_daemon.telegram import AppServerTurn, CodexAppServer, CodexProtocolError, Contacts, Gateway, McpVault, Settings, TelegramApi, TelegramMediaUploadError
from onmyoji_daemon.launcher import _approval_policy, _sandbox_policy
from onmyoji_daemon.telegram_formatting import MAX_MESSAGE_LENGTH, plain_text, render_html, split_html, visible_text
from onmyoji_daemon.telegram_stickers import MAX_CONTEXT_LENGTH, convert_static, discover_static_sources, load as load_sticker_catalog, save as save_sticker_catalog


def write_settings(root: Path, data_dir: Path) -> None:
    (root / "configs").mkdir(parents=True); (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nexecutable = "codex"\nmodel = "gpt-5.6-terra"\nmodel_reasoning_effort = "medium"\nproject_directory = "' + root.as_posix() + '"\nsandbox_mode = "workspace-write"\napproval_policy = "never"\n', encoding="utf-8")
    daemon = root / "configs" / "daemon" / "daemon.toml"; daemon.parent.mkdir(parents=True); daemon.write_text('[app_server]\nenabled = false\nidle_timeout_seconds = 1800\n\n[instructions]\nenabled = true\nshikigami_file = "instructions.md"\n', encoding="utf-8")
    data_dir.mkdir(parents=True); config = data_dir / "config.toml"; config.write_text('schema_version = 1\n[telegram]\nvault_profile = "telegram"\ntoken_entry = "APIs/Telegram:Example"\n', encoding="utf-8")


def test_settings_bind_to_instance_codex_home_and_workspace(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    settings = Settings.load(tmp_path, data)
    assert settings.root == tmp_path.resolve() and settings.project == tmp_path.resolve()


def test_new_and_reset_conversations_use_configured_thought_defaults(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    config = data / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + 'default_share_thoughts = false\ndefault_delete_thoughts = false\n', encoding="utf-8")
    gateway = Gateway(Settings.load(tmp_path, data))

    assert gateway._conversation_settings(9)["share_thoughts"] is False
    assert gateway._conversation_settings(9)["delete_thoughts"] is False
    gateway.database.execute("UPDATE conversations SET share_thoughts=1, delete_thoughts=1 WHERE chat_id='9'"); gateway.database.commit()
    gateway._new_conversation(9)

    assert gateway._conversation_settings(9)["share_thoughts"] is False
    assert gateway._conversation_settings(9)["delete_thoughts"] is False


def test_app_server_settings_come_only_from_daemon_platform_configuration(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    settings = Settings.load(tmp_path, data)
    assert not settings.app_server_enabled and settings.app_server_idle_seconds == 1800
    config = tmp_path / "configs" / "daemon" / "services" / "telegram" / "config.toml"; config.write_text(config.read_text(encoding="utf-8") + "\n[app_server]\nenabled = true\nidle_timeout_seconds = 120\n", encoding="utf-8")
    settings = Settings.load(tmp_path, data)
    assert not settings.app_server_enabled and settings.app_server_idle_seconds == 1800


def test_app_server_settings_prefer_daemon_platform_configuration(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"; manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('[app_server]\nenabled = true\nidle_timeout_seconds = 120\n\n[instructions]\nenabled = true\nshikigami_file = "instructions.md"\n', encoding="utf-8")

    settings = Settings.load(tmp_path, data)

    assert settings.app_server_enabled and settings.app_server_idle_seconds == 120


def test_totp_enabled_comes_from_telegram_service_configuration(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    config = data / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[totp]\nenabled = true\nreal_password_entry = "APIs/TOTP:real"\nfake_password_entry = "APIs/TOTP:fake"\n', encoding="utf-8")

    resolver = patch("onmyoji_daemon.telegram.KeePassCredentialResolver")
    with resolver as configured:
        configured.return_value.entry_exists.return_value = True
        configured.return_value.password_exists.return_value = True
        gateway = Gateway(Settings.load(tmp_path, data))

    assert gateway._totp_enabled()


def test_totp_enabled_rejects_missing_vault_password(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    config = data / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[totp]\nenabled = true\nreal_password_entry = "APIs/TOTP:real"\nfake_password_entry = "APIs/TOTP:fake"\n', encoding="utf-8")

    with patch("onmyoji_daemon.telegram.KeePassCredentialResolver") as resolver:
        resolver.return_value.entry_exists.return_value = False
        try:
            Settings.load(tmp_path, data)
        except ValueError as error:
            assert "senhas real e falsa existentes" in str(error)
        else:
            raise AssertionError("A inicialização deveria rejeitar senhas TOTP ausentes.")


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
        client._launch_workers = lambda _runtime: None
        client._request_runtime = lambda *_args, **_kwargs: {}
        client._send_runtime = lambda *_args, **_kwargs: None
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


def test_gateway_restarts_app_server_but_preserves_context_when_shikigami_skills_are_synchronized(tmp_path, monkeypatch):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data))
    gateway.database.execute("INSERT INTO conversations(chat_id, updated_at, last_interaction_at, codex_thread_id, instruction_baseline_hash) VALUES ('1', 1, 1, 'thread-old', 'hash-old')"); gateway.database.commit()

    class Client:
        def __init__(self): self.stops = 0; self.starts = 0
        def running(self): return True
        def stop(self): self.stops += 1
        def start(self): self.starts += 1

    client = Client(); gateway.app_server = client
    monkeypatch.setattr("onmyoji_daemon.telegram.CodexLaunch.synchronize_shikigami_skills", lambda _self: True)
    monkeypatch.setattr("onmyoji_daemon.telegram.mcp_reconciliation_pending", lambda _root: False)
    assert gateway._app_client() is client
    row = gateway.database.execute("SELECT codex_thread_id, instruction_baseline_hash FROM conversations WHERE chat_id='1'").fetchone()
    assert client.stops == 1 and client.starts == 1
    assert row == ("thread-old", "hash-old")


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
    config = tmp_path / "configs" / "daemon" / "services" / "telegram" / "config.toml"; config.write_text(config.read_text(encoding="utf-8") + "\n[voice_reply]\nenabled = true\neccovox_profile = \"eccovox\"\nvoice = \"pm_alex\"\nlanguage = \"pt-BR\"\n", encoding="utf-8")
    gateway = Gateway(Settings.load(tmp_path, data)); output = tmp_path / "voice.opus"; captured = {}
    def synthesize(_self, profile, text, output_path, response_format, **kwargs):
        captured.update({"profile": profile, "text": text, "output": output_path, "format": response_format, **kwargs})
        Path(output_path).write_bytes(b"audio")
    with patch("onmyoji_daemon.telegram.EccoVoxService.synthesize", synthesize): gateway._tts("Teste", output)
    assert captured["profile"] == "eccovox" and captured["output"] == str(output) and captured["voice"] == "pm_alex" and captured["language"] == "pt-BR"


def test_contacts_are_local_and_do_not_share_owners(tmp_path):
    left, right = Contacts(tmp_path / "left" / "contacts.json"), Contacts(tmp_path / "right" / "contacts.json")
    left.add_owner({"id": 123, "first_name": "owner"})
    assert left.owners() == {123} and right.owners() == set()


def test_vault_token_uses_mcp_credential_resolver_without_exposing_the_token(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data); settings = Settings.load(tmp_path, data)
    with patch("onmyoji_daemon.telegram.KeePassCredentialResolver.resolve", return_value="secret") as resolve:
        assert McpVault(settings).read(settings.token_entry) == "secret"
    resolve.assert_called_once_with("telegram", "APIs/Telegram:Example", "password")


def test_gateway_records_the_instant_of_a_credential_error(tmp_path, monkeypatch):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); monkeypatch.setattr("onmyoji_daemon.telegram.time.time", lambda: 123.0)

    gateway._record_error("O cofre MCP não disponibilizou a credencial solicitada.")

    status = json.loads((gateway.settings.state_dir / "gateway-status.json").read_text(encoding="utf-8"))
    assert status["last_error_at"] == 123.0 and status["updated_at"] == 123.0


def test_gateway_records_process_recovery_code_without_exception_text(tmp_path):
    from onmyoji_daemon.telegram_process import ProcessRecoveryError
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data))
    gateway._record_error(ProcessRecoveryError("process_identity_unavailable"))

    status = json.loads((gateway.settings.state_dir / "gateway-status.json").read_text(encoding="utf-8"))
    assert status["last_error_code"] == "process_identity_unavailable"
    assert "Reconciliação" not in json.dumps(status)


def test_gateway_reports_process_failure_stage_without_private_error(tmp_path):
    from onmyoji_daemon.telegram_process import ProcessRecoveryError
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    write_settings(tmp_path, data)
    gateway = Gateway(replace(Settings.load(tmp_path, data), app_server_enabled=True))
    gateway.api = type("Api", (), {"typing": lambda *_args, **_kwargs: None})()
    gateway._send_agent_text = lambda *_args, **_kwargs: None
    def fail(*_args):
        gateway._execution_local.failure_stage = "app_server"
        raise ProcessRecoveryError("process_identity_unavailable")
    gateway._app_turn = fail

    gateway._turn_serial(9, "conteúdo privado", [])

    status = json.loads((gateway.settings.state_dir / "gateway-status.json").read_text(encoding="utf-8"))
    assert status["last_error_code"] == "process_identity_unavailable"
    assert status["diagnostics"][-1]["stage"] == "app_server"
    assert status["diagnostics"][-1]["reason"] == "process_identity_unavailable"
    assert "conteúdo privado" not in json.dumps(status, ensure_ascii=False)


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
    assert sent == [(9, "💭 Plano seguro.", {"parse_mode": "HTML", "protect_content": True})] and active.thought_ids == [71]
    gateway._cleanup_thoughts(active)
    assert deleted == []
    gateway._app_notification("turn/completed", {"threadId": "thread", "turn": {"status": "completed"}})
    active.terminal_announced = True
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


def test_usage_command_runs_codex_usage_and_returns_its_output(tmp_path, monkeypatch):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    deleted, sent, received = [], [], {}
    gateway.api = type("Api", (), {"delete": lambda _self, chat, message: deleted.append((chat, message)), "send": lambda _self, chat, text, **values: sent.append((chat, text, values)) or {"message_id": 1}})()

    class Launch:
        executable = "codex"
        workspace = tmp_path

        @staticmethod
        def environment(): return {"CODEX_HOME": str(tmp_path)}

    def run(command, **kwargs):
        received.update({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, "Uso atual do Codex", "")

    monkeypatch.setattr("onmyoji_daemon.telegram.CodexLaunch.from_system", lambda _root: Launch())
    monkeypatch.setattr("onmyoji_daemon.telegram.subprocess.run", run)

    gateway._update({"message": {"message_id": 72, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": "/usage"}})

    assert deleted == [(9, 72)]
    assert received["command"] == ["codex", "usage"]
    assert received["cwd"] == tmp_path and received["env"] == {"CODEX_HOME": str(tmp_path)} and received["timeout"] == 30
    assert sent == [(9, "Uso atual do Codex", {"protect_content": True, "parse_mode": "HTML"})]


def test_usage_is_included_in_the_owner_command_catalog(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    gateway.api = type("Api", (), {"set_owner_commands": lambda _self, _owner, _totp: True})()

    summary = gateway._configure_owner_commands()

    assert summary["expected"] == ["new", "config", "channel", "usage"]


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
    queued = []; gateway._enqueue_turn = lambda channel, text, attachments, _context=None: queued.append((channel, text, attachments))
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
    gateway._enqueue_turn = lambda channel, text, attachments, _context=None: queued.append((channel, text, attachments))
    message = {"message_id": 3, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "message_thread_id": 42, "text": "olá"}
    gateway._update({"message": message})
    assert queued == [("private:9:42", "olá", [])]
    gateway._app_turn = lambda *_args: ("resposta", "text")
    gateway._turn_serial("private:9:42", "olá", [])
    assert sent[-1] == (9, "resposta", {"parse_mode": "HTML", "message_thread_id": 42})


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


def test_config_model_catalog_only_exposes_models_and_efforts_allowed_by_setup(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data))
    gateway.settings = replace(gateway.settings, owner_allowed_models=("approved",), owner_allowed_efforts=("low", "high"))
    gateway._app_client = lambda: type("Client", (), {"request": lambda *_args, **_kwargs: {"data": [
        {"model": "approved", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "xhigh"}]},
        {"model": "unapproved", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]},
    ]}})()
    session = {}

    gateway._refresh_config_model_catalog(session)

    assert session == {"models": ("approved",), "efforts_by_model": {"approved": ("low",)}}


def test_config_render_failure_closes_session_and_reports_sanitized_reason(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    sent, deleted = [], []
    gateway.api = type("Api", (), {
        "call": lambda *_args, **_kwargs: None,
        "edit": lambda *_args: (_ for _ in ()).throw(RuntimeError("connection reset")),
        "delete": lambda _self, chat, message: deleted.append((chat, message)),
        "send": lambda _self, chat, text, **values: sent.append((chat, text, values)) or {"message_id": 17},
    })()
    gateway.config_sessions["token"] = {"chat_id": "9", "generation": gateway._generation("9"), "message_id": 7, "expires_at": time.time() + 60}

    gateway._config_callback(9, {}, ["cfg", "token", "thoughts"])

    assert "token" not in gateway.config_sessions and deleted == [(9, 7)]
    assert "connection reset" in sent[-1][1] and "menu foi fechado" in sent[-1][1]


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
    snapshot, attachments, summary = gateway._context_snapshot(channel)
    assert '"dropped_messages": 2' in snapshot and '"max_messages"' in snapshot and attachments == [] and summary == {"contains_untrusted_context": True}


def test_context_window_enforces_utf8_budget_before_app_server_frame(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(replace(Settings.load(tmp_path, data), context_max_bytes=64 * 1024, context_max_age_seconds=0))
    channel, sender = "group:-1001:12", {"id": 7, "first_name": "other"}
    for index in range(3):
        gateway._cache_message(channel, {"message_id": index + 1, "date": 100 + index}, sender, "á" * 14_000, [])

    snapshot, attachments, summary = gateway._context_snapshot(channel)
    context = json.loads(snapshot)["conversation_context"]

    assert len(snapshot.encode("utf-8")) <= gateway.settings.context_max_bytes
    assert context["context_window"]["included_messages"] == 2
    assert "max_bytes" in context["context_window"]["cutoff_reasons"]
    assert attachments == [] and summary == {"contains_untrusted_context": True}


def test_completed_conversation_schedules_native_compaction_once_per_interval(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(replace(Settings.load(tmp_path, data), context_compaction_message_interval=2))
    channel, sender = "9", {"id": 9, "first_name": "owner"}
    for index in range(2):
        gateway._cache_message(channel, {"message_id": index + 1}, sender, f"m{index}", [])
    gateway.database.execute("UPDATE conversations SET codex_thread_id='thread-1' WHERE chat_id=?", (channel,)); gateway.database.commit()
    requests = []
    client = type("Client", (), {"request": lambda _self, method, params, timeout: requests.append((method, params, timeout)) or {}})()

    gateway._schedule_context_compaction(channel, "thread-1", client)
    gateway._schedule_context_compaction(channel, "thread-1", client)

    assert [method for method, _params, _timeout in requests] == ["thread/compact/start"]
    assert gateway.database.execute("SELECT context_compaction_message_count FROM conversations WHERE chat_id=?", (channel,)).fetchone() == (2,)


def test_telegram_html_renderer_escapes_input_and_splits_without_losing_text():
    source = "<script> & **destaque** *ênfase* [site](https://example.test/?a=1&b=2)\n" + ("palavra " * 1000)
    rendered = render_html(source)
    chunks = split_html(rendered)
    assert "&lt;script&gt;" in rendered and "<b>destaque</b>" in rendered and "<i>ênfase</i>" in rendered
    assert all(len(chunk) <= MAX_MESSAGE_LENGTH for chunk in chunks)
    assert visible_text("".join(chunks)) == plain_text(source)


def test_agent_text_uses_html_parse_mode_for_every_chunk(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); sent = []
    gateway.api = type("Api", (), {"send": lambda _self, chat, text, **values: sent.append((chat, text, values)) or {"message_id": len(sent)}})()
    gateway._send_agent_text(9, "**texto** " + ("longo " * 1000))
    assert len(sent) > 1 and all(values["parse_mode"] == "HTML" and len(text) <= MAX_MESSAGE_LENGTH for _chat, text, values in sent)


def test_agent_can_send_photo_and_document_created_in_the_reserved_outbox(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(replace(Settings.load(tmp_path, data), agent_outbound_media=True)); sent = []

    class Api:
        def send_file(_self, method, chat_id, path, field, caption, **values):
            sent.append({"method": method, "chat_id": chat_id, "path": path, "field": field, "caption": caption, "values": values})
            return {"message_id": 7}

    active = AppServerTurn(9, "turn-1", threading.Event(), conversation_id="private:9")
    gateway.api = Api(); gateway.app_turns[active.thread_id] = active
    request = {"namespace": "telegram_gateway", "threadId": active.thread_id, "tool": "get_outbox", "arguments": {}}
    response = gateway._app_server_request("item/tool/call", request)
    outbox = Path(json.loads(response["contentItems"][0]["text"])["outbox"]); picture = outbox / "picture.png"; picture.write_bytes(b"image")

    response = gateway._app_server_request("item/tool/call", {**request, "tool": "send_file", "arguments": {"file_name": picture.name, "kind": "photo", "caption": "Imagem"}})

    assert json.loads(response["contentItems"][0]["text"])["sent"] is True
    response = gateway._app_server_request("item/tool/call", {**request, "tool": "send_file", "arguments": {"file_name": picture.name, "kind": "document"}})
    assert json.loads(response["contentItems"][0]["text"])["sent"] is True
    assert [(item["method"], item["chat_id"], item["path"], item["field"]) for item in sent] == [("sendPhoto", 9, picture, "photo"), ("sendDocument", 9, picture, "document")]


def test_sticker_catalog_accepts_supported_files_and_hides_entries_without_context(tmp_path):
    folder = tmp_path / "configs" / "daemon" / "services" / "telegram" / "stickers"; folder.mkdir(parents=True)
    for suffix in ("webp", "tgs", "webm"): (folder / f"sticker.{suffix}").write_bytes(b"sticker")
    (folder / "catalog.toml").write_text('schema_version = 1\n\n[stickers.static]\nfile = "sticker.webp"\ncontext = "Aprovação."\n\n[stickers.missing_context]\nfile = "sticker.tgs"\ncontext = ""\n\n[stickers.video]\nfile = "sticker.webm"\ncontext = "Celebração."\n', encoding="utf-8")

    catalog = load_sticker_catalog(tmp_path, 1024)

    assert [item.identifier for item in catalog.stickers] == ["static", "video"]
    assert any("missing_context" in issue for issue in catalog.issues)


def test_sticker_catalog_rejects_external_paths_missing_files_and_extensions(tmp_path):
    folder = tmp_path / "configs" / "daemon" / "services" / "telegram" / "stickers"; folder.mkdir(parents=True)
    (folder / "wrong.png").write_bytes(b"not-a-sticker")
    (folder / "catalog.toml").write_text('schema_version = 1\n\n[stickers.external]\nfile = "../outside.webp"\ncontext = "Nunca."\n\n[stickers.extension]\nfile = "wrong.png"\ncontext = "Nunca."\n\n[stickers.missing]\nfile = "missing.webp"\ncontext = "Nunca."\n', encoding="utf-8")

    catalog = load_sticker_catalog(tmp_path, 1024)

    assert catalog.stickers == ()
    assert len(catalog.issues) == 3


def test_empty_sticker_catalog_is_valid_and_is_written_with_its_table(tmp_path):
    folder = tmp_path / "configs" / "daemon" / "services" / "telegram" / "stickers"; folder.mkdir(parents=True)
    (folder / "catalog.toml").write_text("schema_version = 1\n", encoding="utf-8")

    assert load_sticker_catalog(tmp_path, 1024).stickers == ()
    catalog = save_sticker_catalog(tmp_path, {})

    assert catalog.read_text(encoding="utf-8") == "schema_version = 1\n\n[stickers]\n"
    assert load_sticker_catalog(tmp_path, 1024).issues == ()


def test_sticker_catalog_rejects_context_over_the_declared_limit_before_writing(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="Arquivo ou contexto"):
        save_sticker_catalog(tmp_path, {"celebrar": {"file": "celebrar.webp", "context": "x" * (MAX_CONTEXT_LENGTH + 1)}})

    assert not (tmp_path / "configs" / "daemon" / "services" / "telegram" / "stickers" / "catalog.toml").exists()


def test_static_sticker_conversion_creates_a_separate_512_webp(tmp_path):
    from PIL import Image

    folder = tmp_path / "configs" / "daemon" / "services" / "telegram" / "stickers"; folder.mkdir(parents=True)
    source = folder / "celebrar.png"
    Image.new("RGBA", (1000, 300), (20, 40, 80, 180)).save(source)

    assert discover_static_sources(tmp_path) == [source]
    converted = convert_static(source)

    assert source.exists() and converted.output.name == "celebrar-telegram.webp"
    assert converted.output.is_file() and converted.output_bytes == converted.output.stat().st_size
    with Image.open(converted.output) as image:
        assert image.size == (512, 512) and image.format == "WEBP"


def test_static_sticker_conversion_never_overwrites_an_existing_output(tmp_path):
    from PIL import Image

    folder = tmp_path / "configs" / "daemon" / "services" / "telegram" / "stickers"; folder.mkdir(parents=True)
    source = folder / "celebrar.png"; Image.new("RGB", (20, 20), "red").save(source)
    (folder / "celebrar-telegram.webp").write_bytes(b"keep")

    try:
        convert_static(source)
    except FileExistsError as error:
        assert "celebrar-telegram.webp" in str(error)
    else:
        raise AssertionError("A conversão não pode substituir uma saída existente.")


def test_agent_lists_and_sends_only_cataloged_stickers(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    folder = data / "stickers"; folder.mkdir(); image = folder / "celebrar.webp"; image.write_bytes(b"sticker")
    (folder / "catalog.toml").write_text('schema_version = 1\n\n[stickers.celebrar]\nfile = "celebrar.webp"\ncontext = "Comemoração por uma conquista."\n', encoding="utf-8")
    gateway = Gateway(replace(Settings.load(tmp_path, data), agent_outbound_media=True)); sent = []
    gateway.api = type("Api", (), {"send_file": lambda _self, method, chat, path, field, **values: sent.append((method, chat, path, field, values)) or {"message_id": 12}})()
    active = AppServerTurn(9, "sticker-turn", threading.Event(), conversation_id="private:9", source_trust="OWNER")
    gateway.app_turns[active.thread_id] = active
    request = {"namespace": "telegram_gateway", "threadId": active.thread_id}

    listed = gateway._app_server_request("item/tool/call", {**request, "tool": "list_stickers", "arguments": {}})
    delivered = gateway._app_server_request("item/tool/call", {**request, "tool": "send_sticker", "arguments": {"sticker_id": "celebrar"}})
    sticker_only = gateway._app_server_request("item/tool/call", {**request, "tool": "send_sticker", "arguments": {"sticker_id": "celebrar", "delivery": "only"}})

    assert json.loads(listed["contentItems"][0]["text"]) == [{"id": "celebrar", "context": "Comemoração por uma conquista."}]
    assert json.loads(delivered["contentItems"][0]["text"]) == {"sent": True, "message_id": 12, "delivery": "supplement"}
    assert json.loads(sticker_only["contentItems"][0]["text"]) == {"sent": True, "message_id": 12, "delivery": "only"}
    assert active.sticker_only_sent and sent == [("sendSticker", 9, image.resolve(), "sticker", {}), ("sendSticker", 9, image.resolve(), "sticker", {})]


def test_sticker_only_completion_does_not_create_a_fallback_text_message(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(replace(Settings.load(tmp_path, data), app_server_enabled=True)); sent = []
    gateway.api = type("Api", (), {"typing": lambda *_args, **_values: None, "send": lambda _self, *values, **_kwargs: sent.append(values)})()
    gateway._app_turn = lambda *_args: (None, "text")

    gateway._turn_serial(9, "ok", [])

    assert sent == []


def test_agent_cannot_send_unknown_sticker_or_sticker_for_untrusted_user(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(replace(Settings.load(tmp_path, data), agent_outbound_media=True))
    active = AppServerTurn(9, "sticker-turn", threading.Event(), conversation_id="private:9", source_trust="UNTRUSTED_USER")
    gateway.app_turns[active.thread_id] = active
    response = gateway._app_server_request("item/tool/call", {"namespace": "telegram_gateway", "threadId": active.thread_id, "tool": "send_sticker", "arguments": {"sticker_id": "inexistente"}})

    payload = json.loads(response["contentItems"][0]["text"])
    assert payload["sent"] is False and payload["stage"] == "authorization"


def test_agent_receives_a_structured_media_upload_failure(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(replace(Settings.load(tmp_path, data), agent_outbound_media=True))

    class Api:
        def send_file(_self, *_args, **_kwargs): raise TelegramMediaUploadError("Telegram API HTTP 400 ao executar sendPhoto: Bad Request: invalid photo", kind="not_sent")

    active = AppServerTurn(9, "turn-1", threading.Event(), conversation_id="private:9")
    gateway.api = Api(); gateway.app_turns[active.thread_id] = active; outbox = gateway._outbox(active.conversation_id, active.thread_id); active.outbox = outbox
    (outbox / "picture.png").write_bytes(b"image")

    response = gateway._app_server_request("item/tool/call", {"namespace": "telegram_gateway", "threadId": active.thread_id, "tool": "send_file", "arguments": {"file_name": "picture.png", "kind": "photo"}})

    payload = json.loads(response["contentItems"][0]["text"])
    assert response["success"] is True and payload == {"sent": False, "uncertain": False, "retry_safe": False, "stage": "telegram_api", "error": "Falha operacional registrada; confira a etapa e o código de diagnóstico."}
    state = json.loads((gateway.settings.state_dir / "gateway-status.json").read_text(encoding="utf-8"))
    assert state["diagnostics"][-1]["event"] == "media.send_failed"


def test_owner_can_send_a_work_file_without_copying_to_outbox(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    source = tmp_path / "email-attachment.pdf"; source.write_bytes(b"pdf")
    gateway = Gateway(replace(Settings.load(tmp_path, data), agent_outbound_media=True)); sent = []

    class Api:
        def send_file(_self, _method, _chat, path, _field, _caption, **_values): sent.append(path); return {"message_id": 9}

    active = AppServerTurn(9, "turn-1", threading.Event(), conversation_id="private:9", source_trust="OWNER")
    gateway.api = Api(); gateway.app_turns[active.thread_id] = active
    response = gateway._app_server_request("item/tool/call", {"namespace": "telegram_gateway", "threadId": active.thread_id, "tool": "send_file", "arguments": {"file_path": str(source), "kind": "document"}})

    assert json.loads(response["contentItems"][0]["text"])["sent"] is True
    assert sent == [source.resolve()] and active.outbox is None


def test_untrusted_user_cannot_send_a_work_file_by_direct_path(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    source = tmp_path / "private.pdf"; source.write_bytes(b"pdf")
    gateway = Gateway(replace(Settings.load(tmp_path, data), agent_outbound_media=True))
    active = AppServerTurn(9, "turn-1", threading.Event(), conversation_id="private:9", source_trust="UNTRUSTED_USER")
    gateway.app_turns[active.thread_id] = active

    response = gateway._app_server_request("item/tool/call", {"namespace": "telegram_gateway", "threadId": active.thread_id, "tool": "send_file", "arguments": {"file_path": str(source), "kind": "document"}})

    assert json.loads(response["contentItems"][0]["text"])["stage"] == "authorization"


def test_media_upload_exposes_telegram_http_description(tmp_path):
    image = tmp_path / "picture.png"; image.write_bytes(b"image")
    error = HTTPError("https://api.telegram.org/bottoken/sendPhoto", 400, "Bad Request", None, io.BytesIO(b'{"ok":false,"description":"Bad Request: invalid photo"}'))

    with patch("onmyoji_daemon.telegram.urlopen", side_effect=error):
        try:
            TelegramApi("token").send_file("sendPhoto", 9, image, "photo")
        except TelegramMediaUploadError as raised:
            assert str(raised) == "Telegram API HTTP 400 ao executar sendPhoto: Bad Request: invalid photo"
        else:
            raise AssertionError("O erro HTTP do Telegram deve ser preservado para diagnóstico.")


def test_group_dynamic_tools_do_not_publish_interactive_menu(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); calls = []

    class Client:
        def request(self, method, params):
            calls.append((method, params))
            return {"thread": {"id": "thread-1"}}

    gateway._app_thread("group:-100:1", Client(), {"conversation_kind": "group", "source_trust": "OWNER"})
    tools = calls[-1][1]["dynamicTools"][0]["tools"]
    assert "ask_menu" not in {tool["name"] for tool in tools}
    assert {"list_stickers", "send_sticker"} <= {tool["name"] for tool in tools}
    assert next(tool for tool in tools if tool["name"] == "send_sticker")["inputSchema"]["properties"]["delivery"]["enum"] == ["supplement", "only"]
    assert next(tool for tool in tools if tool["name"] == "send_message")["description"].startswith("Envia mensagem adicional à conversa")


def test_resuming_a_revised_thread_preserves_its_context_and_updates_the_audit_hash(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.database.execute("INSERT INTO conversations(chat_id, updated_at, codex_thread_id, instruction_baseline_hash) VALUES (?, ?, ?, ?)", ("9", 0, "thread-old", "outdated")); gateway.database.commit(); calls = []

    class Client:
        def request(_self, method, params): calls.append((method, params)); return {}

    assert gateway._app_thread(9, Client()) == "thread-old"
    row = gateway.database.execute("SELECT codex_thread_id, instruction_baseline_hash FROM conversations WHERE chat_id='9'").fetchone()
    assert calls[0][0] == "thread/resume" and row[0] == "thread-old" and row[1] != "outdated"


def test_transient_thread_resume_failure_preserves_the_thread_for_retry(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.database.execute("INSERT INTO conversations(chat_id, updated_at, codex_thread_id) VALUES (?, ?, ?)", ("9", 0, "thread-old")); gateway.database.commit()

    class Client:
        def request(_self, _method, _params): raise CodexProtocolError("Codex App Server excedeu o tempo de espera em thread/resume")

    try: gateway._app_thread(9, Client())
    except CodexProtocolError: pass
    else: raise AssertionError("A falha transitória precisa ser entregue para nova tentativa.")
    assert gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == ("thread-old",)


def test_missing_thread_is_recreated_only_after_resume_confirms_its_absence(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.database.execute("INSERT INTO conversations(chat_id, updated_at, codex_thread_id) VALUES (?, ?, ?)", ("9", 0, "thread-old")); gateway.database.commit(); calls = []

    class Client:
        def request(_self, method, params):
            calls.append(method)
            if method == "thread/resume": raise CodexProtocolError("Codex App Server recusou thread/resume: thread not found")
            return {"thread": {"id": "thread-new"}}

    assert gateway._app_thread(9, Client()) == "thread-new"
    assert calls == ["thread/resume", "thread/start"]
    assert gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == ("thread-new",)


def test_queue_preserves_trusted_turn_metadata_separately_from_user_content(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; write_settings(tmp_path, data)
    gateway = Gateway(Settings.load(tmp_path, data)); gateway.stop_event.set(); gateway.contacts.add_owner({"id": 9, "first_name": "owner"})
    context = gateway._turn_context("private:9", {"id": 9}, "Conteúdo privado do usuário", [])
    gateway._enqueue_turn("private:9", "Conteúdo privado do usuário", [], context)
    row = gateway.database.execute("SELECT text, turn_context FROM inbox_items WHERE chat_id=?", ("private:9",)).fetchone()
    persisted = json.loads(row[1])
    assert row[0] == "Conteúdo privado do usuário"
    assert persisted == {"conversation_kind": "private", "source_trust": "OWNER", "has_text": True, "attachment_kinds": [], "contains_untrusted_context": False, "is_context_snapshot": False}
    assert "Conteúdo privado do usuário" not in row[1]
