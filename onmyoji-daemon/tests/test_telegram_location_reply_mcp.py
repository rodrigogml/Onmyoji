"""Compatibilidade MCP de respostas nativas em threads Telegram já existentes."""
from __future__ import annotations

from dataclasses import replace
import json
import threading

import pytest
from jsonschema import ValidationError, validate

from onmyoji_daemon.mcp.telegram import tools as telegram_tools
from onmyoji_daemon.mcp.telegram.service import TelegramMcpError, TelegramMcpService
from onmyoji_daemon.mcp.telegram.tools import TelegramToolRegistry
from onmyoji_daemon.telegram import AppServerTurn, Gateway, Settings


TOKEN = "temporary-reply-context-for-test"


@pytest.fixture
def gateway(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    data.mkdir(parents=True)
    (tmp_path / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory="' + tmp_path.as_posix() + '"\n', encoding="utf-8")
    (tmp_path / "configs" / "daemon" / "daemon.toml").write_text('[app_server]\nenabled=true\n', encoding="utf-8")
    (data / "config.toml").write_text('schema_version=1\n[telegram]\nvault_profile="telegram"\ntoken_entry="test"\nagent_outbound_media=true\n', encoding="utf-8")
    result = Gateway(Settings.load(tmp_path, data))
    calls = []

    class Api:
        def call(self, method, values):
            calls.append((method, values))
            return {"message_id": 700}

    result.api = Api()
    result.test_api_calls = calls
    yield result
    result.database.close()


def active_turn(gateway, conversation="group:-1000:12", trust="OWNER", thread="old-thread"):
    chat_id, topics = gateway._telegram_address(conversation)
    active = AppServerTurn(chat_id, thread, threading.Event(), conversation_id=conversation, telegram_topic_values=topics, source_trust=trust)
    gateway.app_turns[thread] = active
    return active


def reply_token(gateway, active):
    response = gateway._app_server_request("item/tool/call", {"namespace": "telegram_gateway", "threadId": active.thread_id, "tool": "get_channel_state", "arguments": {}})
    return json.loads(response["contentItems"][0]["text"])["location_reply_context"]


def service_with_result(tmp_path, result, calls):
    def rpc(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(result, Exception):
            raise result
        return result
    return TelegramMcpService(tmp_path, rpc, lambda _root: ("127.0.0.1", 1, "private-rpc-token"))


def connected_service(gateway, calls):
    def rpc(_host, _port, _token, method, params, **kwargs):
        calls.append((method, params, kwargs))
        return gateway.handle(method, params)
    return TelegramMcpService(gateway.settings.root, rpc, lambda _root: ("127.0.0.1", 1, "private-rpc-token"))


def test_schema_allows_exactly_one_destination_mode(tmp_path, monkeypatch):
    registry = TelegramToolRegistry(tmp_path)
    monkeypatch.setattr(registry, "available", lambda: True)
    schema = next(item["input_schema"] for item in registry.definitions() if item["name"] == "telegram.send_location")
    validate({"reply_context": TOKEN, "latitude": 0, "longitude": 0, "confirm": True}, schema)
    validate({"channel": "9", "latitude": 0, "longitude": 0, "new_topic": "Novo", "confirm": True}, schema)
    for arguments in (
        {"latitude": 0, "longitude": 0, "confirm": True},
        {"reply_context": TOKEN, "channel": "9", "latitude": 0, "longitude": 0, "confirm": True},
        {"reply_context": TOKEN, "new_topic": "", "latitude": 0, "longitude": 0, "confirm": True},
        {"reply_context": TOKEN, "latitude": 0, "longitude": 0, "confirm": False},
        {"reply_context": TOKEN, "latitude": 0, "longitude": 0},
        {"reply_context": TOKEN, "latitude": 0, "longitude": 0, "confirm": True, "chat_id": 9},
        {"reply_context": "", "latitude": 0, "longitude": 0, "confirm": True},
        {"reply_context": "x" * 129, "latitude": 0, "longitude": 0, "confirm": True},
    ):
        with pytest.raises(ValidationError):
            validate(arguments, schema)


@pytest.mark.parametrize("extra", [{"channel": "9"}, {"channel": None}, {"new_topic": ""}, {"new_topic": "Novo"}, {"chat_id": 9}, {"live_period": 60}])
def test_registry_rejects_token_destination_overrides_before_rpc(tmp_path, monkeypatch, extra):
    calls = []
    service = service_with_result(tmp_path, {}, calls)
    monkeypatch.setattr(telegram_tools, "TelegramMcpService", lambda _root: service)
    registry = TelegramToolRegistry(tmp_path)
    monkeypatch.setattr(registry, "available", lambda: True)
    with pytest.raises(TelegramMcpError):
        registry.call("telegram.send_location", {"reply_context": TOKEN, "latitude": 0, "longitude": 0, "confirm": True, **extra})
    assert calls == []


@pytest.mark.parametrize("precision", [None, 0, 15])
def test_service_uses_reply_rpc_without_channel_or_fabricated_accuracy(tmp_path, precision):
    calls = []
    service = service_with_result(tmp_path, {"sent": True, "message_id": 700, "reply_context": TOKEN}, calls)
    kwargs = {} if precision is None else {"horizontal_accuracy": precision}
    result = service.send_location(None, -23.55052, -46.633308, reply_context=TOKEN, confirm=True, **kwargs)
    assert result == {"sent": True, "message_id": 700}
    assert calls[0][0][3:] == ("telegram.reply.send_location", {"reply_context": TOKEN, "latitude": -23.55052, "longitude": -46.633308, **kwargs})
    assert calls[0][1] == {"timeout": 120}
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("token", [None, False, 1, "", "x" * 129, "á"])
def test_invalid_reply_token_is_rejected_locally_without_echoing_it(tmp_path, token):
    calls = []
    service = service_with_result(tmp_path, {}, calls)
    with pytest.raises(TelegramMcpError, match="inválido ou expirado"):
        service.send_location(None, 0, 0, reply_context=token, confirm=True)
    assert calls == []


@pytest.mark.parametrize("stage,expected", [("authorization", "não está autorizado ou expirou"), ("capability", "desabilitado"), ("gateway", "indisponível"), ("telegram_api", "Não repita automaticamente"), ("receipt_validation", "indeterminada")])
def test_domain_failures_retain_safe_meaning_without_echoing_reply_token(tmp_path, stage, expected):
    calls = []
    service = service_with_result(tmp_path, {"sent": False, "stage": stage, "error": TOKEN + " private-rpc-token latitude=-23.55052"}, calls)
    with pytest.raises(TelegramMcpError, match=expected) as raised:
        service.send_location(None, -23.55052, 0, reply_context=TOKEN, confirm=True)
    assert all(value not in str(raised.value) for value in (TOKEN, "private-rpc-token", "-23.55052"))
    assert len(calls) == 1


@pytest.mark.parametrize("result", [None, False, [], {}, {"sent": True}, {"sent": True, "message_id": True}, {"sent": True, "message_id": 0}, {"sent": True, "message_id": "700"}, TimeoutError(TOKEN + " private-rpc-token")])
def test_invalid_reply_results_and_transport_errors_never_confirm_delivery(tmp_path, result):
    calls = []
    service = service_with_result(tmp_path, result, calls)
    with pytest.raises(TelegramMcpError, match="Não repita automaticamente") as raised:
        service.send_location(None, 0, 0, reply_context=TOKEN, confirm=True)
    assert TOKEN not in str(raised.value) and "private-rpc-token" not in str(raised.value)
    assert len(calls) == 1


def test_resumed_thread_reply_flows_from_existing_state_tool_through_mcp(gateway, monkeypatch):
    conversation = "group:-1000:12"
    gateway._ensure_conversation(conversation)
    gateway.database.execute("UPDATE conversations SET codex_thread_id='old-thread', instruction_baseline_hash='old-contract' WHERE chat_id=?", (conversation,))
    gateway.database.commit()
    app_requests = []

    class Client:
        def request(self, method, params):
            app_requests.append((method, params))
            return {}

    thread = gateway._app_thread(conversation, Client())
    assert thread == "old-thread"
    assert [method for method, _params in app_requests] == ["thread/resume"]
    active = active_turn(gateway, conversation, thread=thread)
    token = reply_token(gateway, active)
    calls = []
    service = connected_service(gateway, calls)
    monkeypatch.setattr(telegram_tools, "TelegramMcpService", lambda _root: service)
    registry = TelegramToolRegistry(gateway.settings.root)
    monkeypatch.setattr(registry, "available", lambda: True)
    result = registry.call("telegram.send_location", {"reply_context": token, "latitude": 0, "longitude": 0, "confirm": True})
    assert result == {"sent": True, "message_id": 700}
    assert calls == [("telegram.reply.send_location", {"reply_context": token, "latitude": 0, "longitude": 0}, {"timeout": 120})]
    assert gateway.test_api_calls == [("sendLocation", {"chat_id": -1000, "message_thread_id": 12, "latitude": 0, "longitude": 0})]
    assert gateway._channels_list() == []
    assert gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id=?", (conversation,)).fetchone()[0] == thread
    assert gateway.database.execute("SELECT COUNT(*) FROM telegram_external_messages").fetchone()[0] == 0
    diagnostics = (gateway.settings.state_dir / "gateway-status.json").read_text(encoding="utf-8")
    assert token not in diagnostics


@pytest.mark.parametrize("expiration", ["completed", "failed", "interrupted", "new", "removed"])
def test_reply_token_expires_with_turn_or_conversation_reset(gateway, expiration):
    active = active_turn(gateway)
    token = reply_token(gateway, active)
    if expiration == "completed":
        active.completed.set()
    elif expiration in {"failed", "interrupted"}:
        active.status = expiration
    elif expiration == "new":
        gateway._new_conversation(active.conversation_id)
    else:
        gateway.app_turns.pop(active.thread_id)
    service = connected_service(gateway, [])
    with pytest.raises(TelegramMcpError, match="não está autorizado ou expirou") as raised:
        service.send_location(None, 0, 0, reply_context=token, confirm=True)
    assert token not in str(raised.value)
    assert gateway.test_api_calls == []


def test_each_reply_token_remains_bound_to_its_own_topic_and_turn(gateway):
    first = active_turn(gateway, conversation="group:-1000:12", thread="thread-first")
    second = active_turn(gateway, conversation="direct:9:81", thread="thread-second")
    first_token = reply_token(gateway, first)
    second_token = reply_token(gateway, second)
    assert first_token != second_token
    service = connected_service(gateway, [])
    service.send_location(None, 0, 0, reply_context=first_token, confirm=True)
    service.send_location(None, 0, 0, reply_context=second_token, confirm=True)
    assert gateway.test_api_calls == [
        ("sendLocation", {"chat_id": -1000, "message_thread_id": 12, "latitude": 0, "longitude": 0}),
        ("sendLocation", {"chat_id": 9, "direct_messages_topic_id": 81, "latitude": 0, "longitude": 0}),
    ]
    gateway.app_turns.pop(first.thread_id)
    replacement = active_turn(gateway, conversation="group:-1000:12", thread="thread-first")
    assert reply_token(gateway, replacement) != first_token
    with pytest.raises(TelegramMcpError, match="não está autorizado ou expirou"):
        service.send_location(None, 0, 0, reply_context=first_token, confirm=True)
    assert len(gateway.test_api_calls) == 2


@pytest.mark.parametrize("changed", ["trust", "capability"])
def test_reply_context_rechecks_authorization_and_capability_at_send(gateway, changed):
    active = active_turn(gateway)
    token = reply_token(gateway, active)
    if changed == "trust":
        active.source_trust = "UNTRUSTED_USER"
    else:
        gateway.settings = replace(gateway.settings, agent_outbound_media=False)
    service = connected_service(gateway, [])
    with pytest.raises(TelegramMcpError, match="não está autorizado|desabilitado"):
        service.send_location(None, 0, 0, reply_context=token, confirm=True)
    assert gateway.test_api_calls == []


def test_confirmed_reply_is_preserved_if_diagnostic_write_fails(gateway, monkeypatch):
    active = active_turn(gateway)
    token = reply_token(gateway, active)
    def fail(*args, **kwargs):
        raise OSError("unavailable diagnostic disk")
    monkeypatch.setattr(gateway, "_diagnostic", fail)
    result = connected_service(gateway, []).send_location(None, 0, 0, reply_context=token, confirm=True)
    assert result["sent"] is True and result["message_id"] == 700
    assert len(gateway.test_api_calls) == 1
