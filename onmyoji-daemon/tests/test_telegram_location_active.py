"""Envio nativo de pontos fixos na conversa Telegram ativa, sem rede."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import threading

import pytest
from jsonschema import ValidationError, validate

from onmyoji_daemon.telegram import AppServerTurn, Gateway, Settings


class LocationApi:
    def __init__(self, response=None, error=None):
        self.response = {"message_id": 700} if response is None else response
        self.error = error
        self.calls = []

    def call(self, method, values):
        self.calls.append((method, values))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def gateway(tmp_path):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "onmyoji-system.toml").write_text('[codex]\nexecutable="codex"\nproject_directory="' + tmp_path.as_posix() + '"\n', encoding="utf-8")
    daemon = configs / "daemon"
    daemon.mkdir()
    (daemon / "daemon.toml").write_text('[app_server]\nenabled=true\n[instructions]\nenabled=true\nshikigami_file="instructions.md"\n', encoding="utf-8")
    data = daemon / "services" / "telegram"
    data.mkdir(parents=True)
    (data / "config.toml").write_text('schema_version=1\n[telegram]\nvault_profile="telegram"\ntoken_entry="test"\nagent_outbound_media=true\n', encoding="utf-8")
    result = Gateway(Settings.load(tmp_path, data))
    result.api = LocationApi()
    yield result
    result.database.close()


def active_turn(gateway, conversation="9", trust="OWNER", thread="thread-active"):
    chat_id, topics = gateway._telegram_address(conversation)
    active = AppServerTurn(chat_id, thread, threading.Event(), conversation_id=conversation, telegram_topic_values=topics, source_trust=trust)
    gateway.app_turns[thread] = active
    return active


def tool_call(gateway, active, name, arguments=None):
    response = gateway._app_server_request("item/tool/call", {"namespace": "telegram_gateway", "threadId": active.thread_id, "tool": name, "arguments": arguments or {}})
    assert response["success"] is True
    return json.loads(response["contentItems"][0]["text"])


def test_active_location_is_native_without_catalog_or_files_and_registers_reply_id(gateway):
    active = active_turn(gateway)
    assert gateway._channels_list() == []

    result = tool_call(gateway, active, "send_location", {"latitude": 0, "longitude": 0, "horizontal_accuracy": 0})

    assert result == {"sent": True, "message_id": 700}
    assert gateway.api.calls == [("sendLocation", {"chat_id": 9, "latitude": 0, "longitude": 0, "horizontal_accuracy": 0})]
    assert gateway.database.execute("SELECT chat_id, generation, telegram_message_id FROM bot_messages").fetchall() == [("9", 0, 700)]
    assert gateway.database.execute("SELECT COUNT(*) FROM conversation_attachments").fetchone() == (0,)
    assert gateway.database.execute("SELECT COUNT(*) FROM telegram_external_messages").fetchone() == (0,)
    assert active.outbox is None


@pytest.mark.parametrize("conversation,expected", [
    ("group:-1000:12", {"chat_id": -1000, "message_thread_id": 12}),
    ("private:9:42", {"chat_id": 9, "message_thread_id": 42}),
    ("direct:9:81", {"chat_id": 9, "direct_messages_topic_id": 81}),
])
def test_native_point_uses_only_active_topic_address_and_omits_unknown_accuracy(gateway, conversation, expected):
    active = active_turn(gateway, conversation)
    assert gateway._send_active_location(active, {"latitude": -23.55052, "longitude": -46.633308})["sent"] is True
    assert gateway.api.calls == [("sendLocation", {**expected, "latitude": -23.55052, "longitude": -46.633308})]


@pytest.mark.parametrize("trust", ["OWNER", "AUTHORIZED_USER"])
def test_authorized_origins_can_obtain_context_and_send_fixed_points(gateway, trust):
    active = active_turn(gateway, trust=trust)
    token = tool_call(gateway, active, "get_channel_state")["location_reply_context"]
    assert isinstance(token, str) and len(token) >= 32
    result = gateway._reply_send_location({"reply_context": token, "latitude": 0, "longitude": -180})
    assert result == {"sent": True, "message_id": 700}


@pytest.mark.parametrize("trust", ["UNTRUSTED_USER", "BOT", "SYSTEM_EVENT"])
def test_untrusted_origins_get_no_context_and_cannot_send(gateway, trust):
    active = active_turn(gateway, trust=trust)
    assert "location_reply_context" not in tool_call(gateway, active, "get_channel_state")
    result = gateway._send_active_location(active, {"latitude": 0, "longitude": 0})
    assert result["sent"] is False and result["stage"] == "authorization"
    assert gateway.api.calls == []


def test_disabled_media_gets_no_context_and_blocks_native_send(gateway):
    gateway.settings = replace(gateway.settings, agent_outbound_media=False)
    active = active_turn(gateway)
    assert "location_reply_context" not in tool_call(gateway, active, "get_channel_state")
    result = gateway._send_active_location(active, {"latitude": 0, "longitude": 0})
    assert result["sent"] is False and result["stage"] == "capability"
    assert gateway.api.calls == []


@pytest.mark.parametrize("arguments", [
    {"latitude": 0},
    {"latitude": True, "longitude": 0},
    {"latitude": 0, "longitude": "0"},
    {"latitude": float("nan"), "longitude": 0},
    {"latitude": 0, "longitude": 0, "horizontal_accuracy": None},
    {"latitude": 0, "longitude": 0, "chat_id": 1234},
    {"latitude": 0, "longitude": 0, "channel": "other"},
    {"latitude": 0, "longitude": 0, "live_period": 900},
])
def test_invalid_or_destination_override_arguments_never_reach_api(gateway, arguments):
    active = active_turn(gateway)
    result = tool_call(gateway, active, "send_location", arguments)
    assert result["sent"] is False and result["stage"] == "argument_validation"
    assert gateway.api.calls == []


@pytest.mark.parametrize("token", [None, False, 1, "", "x" * 129, "unknown-token", "á"])
def test_invalid_reply_context_is_rejected_without_api_call(gateway, token):
    active_turn(gateway)
    assert gateway._reply_send_location({"reply_context": token, "latitude": 0, "longitude": 0}) == {
        "sent": False, "stage": "authorization", "error": "Contexto de resposta inválido ou expirado.",
    }
    assert gateway.api.calls == []


def test_missing_reply_context_is_a_domain_failure_without_api_call(gateway):
    active_turn(gateway)
    assert gateway._reply_send_location({"latitude": 0, "longitude": 0}) == {
        "sent": False, "stage": "authorization", "error": "Contexto de resposta inválido ou expirado.",
    }
    assert gateway.api.calls == []


def test_expired_context_cannot_be_reused_by_a_different_turn(gateway):
    first = active_turn(gateway)
    old_token = tool_call(gateway, first, "get_channel_state")["location_reply_context"]
    gateway.app_turns.clear()
    second = active_turn(gateway)
    new_token = tool_call(gateway, second, "get_channel_state")["location_reply_context"]
    assert old_token != new_token
    assert gateway._reply_send_location({"reply_context": old_token, "latitude": 0, "longitude": 0}) == {
        "sent": False, "stage": "authorization", "error": "Contexto de resposta inválido ou expirado.",
    }
    assert gateway.api.calls == []


def test_completed_turn_hides_context_and_rejects_previously_issued_token(gateway):
    active = active_turn(gateway)
    token = tool_call(gateway, active, "get_channel_state")["location_reply_context"]
    active.completed.set()
    with pytest.raises(RuntimeError, match="turno ativo|contexto encerrado"):
        tool_call(gateway, active, "get_channel_state")
    result = gateway._reply_send_location({"reply_context": token, "latitude": 0, "longitude": 0})
    assert result["sent"] is False and result["stage"] == "authorization"
    assert gateway.api.calls == []


def test_context_from_previous_conversation_generation_is_never_reauthorized(gateway):
    active = active_turn(gateway)
    token = tool_call(gateway, active, "get_channel_state")["location_reply_context"]
    gateway.database.execute("UPDATE conversations SET generation=generation+1 WHERE chat_id='9'")
    gateway.database.commit()
    with pytest.raises(RuntimeError, match="turno ativo|contexto encerrado"):
        tool_call(gateway, active, "get_channel_state")
    result = gateway._reply_send_location({"reply_context": token, "latitude": 0, "longitude": 0})
    assert result["sent"] is False and result["stage"] == "authorization"
    assert gateway.api.calls == []


def test_new_conversation_during_delivery_keeps_reply_record_in_original_generation(gateway):
    active = active_turn(gateway)
    token = tool_call(gateway, active, "get_channel_state")["location_reply_context"]

    class ResetDuringSend(LocationApi):
        def call(self, method, values):
            gateway._new_conversation("9")
            return super().call(method, values)

    gateway.api = ResetDuringSend()
    assert gateway._reply_send_location({"reply_context": token, "latitude": 0, "longitude": 0}) == {"sent": True, "message_id": 700}
    assert gateway._generation("9") == 1
    assert gateway.database.execute("SELECT chat_id, generation, telegram_message_id FROM bot_messages").fetchall() == [("9", 0, 700)]
    with pytest.raises(RuntimeError, match="turno ativo|contexto encerrado"):
        tool_call(gateway, active, "get_channel_state")
    rejected = gateway._reply_send_location({"reply_context": token, "latitude": 0, "longitude": 0})
    assert rejected["sent"] is False and rejected["stage"] == "authorization"
    assert len(gateway.api.calls) == 1


@pytest.mark.parametrize("status", ["interrupted", "cancelled", "completed", "failed"])
def test_nonrunning_status_revokes_context_even_before_completed_event_is_set(gateway, status):
    active = active_turn(gateway)
    token = tool_call(gateway, active, "get_channel_state")["location_reply_context"]
    active.status = status
    assert active.completed.is_set() is False
    assert "location_reply_context" not in tool_call(gateway, active, "get_channel_state")
    result = gateway._reply_send_location({"reply_context": token, "latitude": 0, "longitude": 0})
    assert result["sent"] is False and result["stage"] == "authorization"
    assert gateway.api.calls == []


def test_missing_gateway_reports_domain_failure(gateway):
    active = active_turn(gateway)
    gateway.api = None
    assert gateway._send_active_location(active, {"latitude": 0, "longitude": 0}) == {"sent": False, "stage": "gateway", "error": "Gateway Telegram indisponível."}


def test_transport_failure_and_diagnostics_never_echo_geodata_or_api_errors(gateway):
    active = active_turn(gateway)
    gateway.api = LocationApi(error=RuntimeError("secret=private-token latitude=-23.55052 longitude=-46.633308"))
    result = tool_call(gateway, active, "send_location", {"latitude": -23.55052, "longitude": -46.633308})
    assert result["sent"] is False and result["stage"] == "telegram_api"
    assert "pode ter ocorrido" in result["error"]
    state = (gateway.settings.state_dir / "gateway-status.json").read_text(encoding="utf-8")
    recorded = json.dumps(result) + state
    assert all(value not in recorded for value in ("23.55052", "46.633308", "private-token"))


@pytest.mark.parametrize("response", [{}, [], {"message_id": True}, {"message_id": 0}, {"message_id": -1}, {"message_id": "700"}])
def test_invalid_receipt_never_claims_confirmed_delivery_or_registers_reply(gateway, response):
    active = active_turn(gateway)
    gateway.api = LocationApi(response=response)
    result = gateway._send_active_location(active, {"latitude": 0, "longitude": 0})
    assert result["sent"] is False and result["stage"] == "receipt_validation"
    assert "pode ter ocorrido" in result["error"]
    assert gateway.database.execute("SELECT COUNT(*) FROM bot_messages").fetchone() == (0,)


@pytest.mark.parametrize("diagnostic_failure", [False, True])
def test_local_record_failure_preserves_confirmed_delivery_with_warning(gateway, monkeypatch, diagnostic_failure):
    active = active_turn(gateway)
    database = gateway.database

    class FailedReplyRecord:
        def execute(self, sql, *args):
            if "INSERT OR IGNORE INTO bot_messages" in sql:
                raise sqlite3.OperationalError("local storage unavailable")
            return database.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(database, name)

    monkeypatch.setattr(gateway, "database", FailedReplyRecord())
    if diagnostic_failure:
        def unavailable_diagnostic(*_args, **_kwargs):
            raise OSError("diagnostic storage unavailable")
        monkeypatch.setattr(gateway, "_diagnostic", unavailable_diagnostic)
    result = gateway._send_active_location(active, {"latitude": 0, "longitude": 0})
    assert result == {"sent": True, "message_id": 700, "warning": "Telegram confirmou o envio, mas não foi possível preservar integralmente o contexto local."}
    assert len(gateway.api.calls) == 1


def test_diagnostic_failure_never_loses_confirmed_delivery_or_reply_record(gateway, monkeypatch):
    active = active_turn(gateway)

    def unavailable_diagnostic(*_args, **_kwargs):
        raise OSError("diagnostic storage unavailable")

    monkeypatch.setattr(gateway, "_diagnostic", unavailable_diagnostic)
    assert gateway._send_active_location(active, {"latitude": 0, "longitude": 0}) == {"sent": True, "message_id": 700}
    assert gateway.database.execute("SELECT chat_id, generation, telegram_message_id FROM bot_messages").fetchall() == [("9", 0, 700)]
    assert len(gateway.api.calls) == 1


@pytest.mark.parametrize("transport_failure", [False, True])
def test_diagnostic_storage_failure_preserves_domain_errors(gateway, monkeypatch, transport_failure):
    active = active_turn(gateway)

    def unavailable_diagnostic(*_args, **_kwargs):
        raise OSError("diagnostic storage unavailable")

    monkeypatch.setattr(gateway, "_diagnostic", unavailable_diagnostic)
    if transport_failure:
        gateway.api = LocationApi(error=OSError("transport unavailable"))
    else:
        gateway.settings = replace(gateway.settings, agent_outbound_media=False)
    result = gateway._send_active_location(active, {"latitude": 0, "longitude": 0})
    assert result["sent"] is False
    assert result["stage"] == ("telegram_api" if transport_failure else "capability")
    assert len(gateway.api.calls) == (1 if transport_failure else 0)


def test_new_thread_publishes_fixed_location_schema_with_no_destination(gateway):
    calls = []

    class Client:
        def request(self, method, params):
            calls.append((method, params))
            return {"thread": {"id": "new-thread"}}

    assert gateway._app_thread("9", Client()) == "new-thread"
    assert calls[0][0] == "thread/start"
    schema = next(tool["inputSchema"] for tool in calls[0][1]["dynamicTools"][0]["tools"] if tool["name"] == "send_location")
    validate({"latitude": 0, "longitude": 0}, schema)
    validate({"latitude": -90, "longitude": 180, "horizontal_accuracy": 0}, schema)
    with pytest.raises(ValidationError):
        validate({"latitude": 0, "longitude": 0, "channel": "other"}, schema)
    with pytest.raises(ValidationError):
        validate({"latitude": True, "longitude": 0}, schema)


def test_old_thread_keeps_id_and_existing_state_tool_issues_context_for_native_reply(gateway):
    gateway.database.execute("INSERT INTO conversations(chat_id, updated_at, codex_thread_id, instruction_baseline_hash) VALUES ('9', 0, 'old-thread', 'old-contract')")
    gateway.database.commit()
    calls = []

    class Client:
        def request(self, method, params):
            calls.append((method, params))
            return {}

    thread_id = gateway._app_thread("9", Client())
    assert thread_id == "old-thread"
    assert [method for method, _params in calls] == ["thread/resume"]
    assert "dynamicTools" not in calls[0][1]
    assert gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == ("old-thread",)
    active = active_turn(gateway, thread=thread_id)
    token = tool_call(gateway, active, "get_channel_state")["location_reply_context"]
    result = gateway._reply_send_location({"reply_context": token, "latitude": 0, "longitude": 0})
    assert result["sent"] is True
    assert gateway.api.calls == [("sendLocation", {"chat_id": 9, "latitude": 0, "longitude": 0})]


def test_location_tool_rejects_calls_without_active_turn(gateway):
    with pytest.raises(RuntimeError, match="turno ativo|contexto encerrado"):
        gateway._app_server_request("item/tool/call", {"namespace": "telegram_gateway", "threadId": "absent", "tool": "send_location", "arguments": {"latitude": 0, "longitude": 0}})
    assert gateway.api.calls == []
