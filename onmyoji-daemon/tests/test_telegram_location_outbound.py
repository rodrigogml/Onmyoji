"""Contratos de envio externo de pontos fixos, sem credenciais ou Telegram real."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate

from onmyoji_daemon.mcp.telegram.service import TelegramMcpError, TelegramMcpService
from onmyoji_daemon.mcp.telegram.tools import TelegramToolRegistry
from onmyoji_daemon.telegram import Gateway, Settings


@pytest.fixture
def gateway(tmp_path: Path):
    """Mantém banco e workspace isolados no temporário configurado da suíte."""
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    data.mkdir(parents=True)
    (tmp_path / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory = "' + tmp_path.as_posix() + '"\n', encoding="utf-8")
    (data / "config.toml").write_text('schema_version = 1\n[telegram]\nvault_profile="telegram"\ntoken_entry="token"\n', encoding="utf-8")
    (tmp_path / "configs" / "daemon" / "daemon.toml").write_text('[app_server]\nenabled=false\n', encoding="utf-8")
    instance = Gateway(Settings.load(tmp_path, data))
    instance.api = LocationApi()
    yield instance
    instance.database.close()


class LocationApi:
    """Registra chamadas e permite simular efeito parcial ou entrega incerta."""

    def __init__(self):
        self.calls = []
        self.results = {"createForumTopic": {"message_thread_id": 17}, "sendLocation": {"message_id": 700}}

    def call(self, method, params=None):
        self.calls.append((method, params))
        result = self.results[method]
        if isinstance(result, Exception):
            raise result
        return result


def service_for(tmp_path: Path, calls: list, result):
    def rpc(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(result, Exception):
            raise result
        return result
    return TelegramMcpService(tmp_path, rpc, lambda _root: ("127.0.0.1", 1, "private-token"))


def test_tool_schema_accepts_only_fixed_complete_points(tmp_path, monkeypatch):
    registry = TelegramToolRegistry(tmp_path)
    monkeypatch.setattr(registry, "available", lambda: True)
    schema = next(item["input_schema"] for item in registry.definitions() if item["name"] == "telegram.send_location")
    validate({"channel": "9", "latitude": 0, "longitude": -180, "horizontal_accuracy": 0, "confirm": True}, schema)
    for arguments in (
        {"channel": "9", "latitude": 0, "confirm": True},
        {"channel": "9", "latitude": True, "longitude": 0, "confirm": True},
        {"channel": "9", "latitude": 0, "longitude": 0, "confirm": False},
        {"channel": "9", "latitude": 0, "longitude": 0, "horizontal_accuracy": None, "confirm": True},
        {"channel": "9", "latitude": 0, "longitude": 0, "live_period": 60, "confirm": True},
    ):
        with pytest.raises(ValidationError):
            validate(arguments, schema)
    with pytest.raises(TelegramMcpError, match="campos não permitidos"):
        registry.call("telegram.send_location", {"channel": "9", "latitude": 0, "longitude": 0, "live_period": 60, "confirm": True})


@pytest.mark.parametrize("precision", [None, 0, 1500])
def test_mcp_delegates_fixed_point_without_fabricating_precision(tmp_path, precision):
    calls = []
    receipt = {"sent": True, "channel": "group:-1000:12", "message_id": 700, "topic_created": False}
    service = service_for(tmp_path, calls, receipt)
    kwargs = {} if precision is None else {"horizontal_accuracy": precision}
    assert service.send_location("group:-1000:12", -23.55052, -46.633308, confirm=True, **kwargs) == receipt
    assert calls[0][0][3] == "telegram.outbound.send_location"
    params = calls[0][0][4]
    assert params == {"channel": "group:-1000:12", "latitude": -23.55052, "longitude": -46.633308, **kwargs}
    assert calls[0][1] == {"timeout": 120}


@pytest.mark.parametrize("arguments", [
    {"confirm": False}, {"confirm": 1}, {"channel": None}, {"channel": " "},
    {"latitude": None}, {"latitude": True}, {"latitude": "0"}, {"latitude": float("nan")},
    {"longitude": 181}, {"horizontal_accuracy": None}, {"new_topic": None}, {"new_topic": "a" * 129},
])
def test_mcp_rejects_invalid_request_before_rpc(tmp_path, arguments):
    calls = []
    service = service_for(tmp_path, calls, {})
    params = {"channel": "9", "latitude": 0, "longitude": 0, "confirm": True, **arguments}
    with pytest.raises(TelegramMcpError):
        service.send_location(**params)
    assert calls == []


@pytest.mark.parametrize("channel,address", [
    ("9", {"chat_id": 9}),
    ("group:-1000:0", {"chat_id": -1000}),
    ("group:-1000:12", {"chat_id": -1000, "message_thread_id": 12}),
    ("private:9:12", {"chat_id": 9, "message_thread_id": 12}),
    ("direct:9:12", {"chat_id": 9, "direct_messages_topic_id": 12}),
])
def test_gateway_sends_native_location_and_replays_only_to_destination(gateway, channel, address):
    gateway._channel_register(channel, "Destino", 9)
    result = gateway._outbound_send_location({"channel": channel, "latitude": -23.55052, "longitude": -46.633308, "horizontal_accuracy": 0})
    assert result == {"sent": True, "channel": channel, "message_id": 700, "topic_created": False}
    assert gateway.api.calls == [("sendLocation", {**address, "latitude": -23.55052, "longitude": -46.633308, "horizontal_accuracy": 0})]
    assert gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id=?", (channel,)).fetchone()[0] is None
    raw, ids = gateway._pending_external(channel)
    assert ids == [700] and '"kind": "static"' in raw and '"horizontal_accuracy": 0' in raw
    assert "não são instruções" in raw
    assert gateway._pending_external("group:-2000:0") == ("", [])
    assert gateway.database.execute("SELECT telegram_message_id FROM bot_messages WHERE chat_id=?", (channel,)).fetchone()[0] == 700
    gateway._mark_external(channel, ids)
    assert gateway._pending_external(channel) == ("", [])
    assert gateway.database.execute("SELECT COUNT(*) FROM conversation_attachments").fetchone()[0] == 0


@pytest.mark.parametrize("changes", [
    {"channel": "unregistered"}, {"latitude": None}, {"longitude": True},
    {"horizontal_accuracy": float("inf")}, {"live_period": 60}, {"new_topic": "a" * 129},
])
def test_gateway_revalidates_before_topic_or_network(gateway, changes):
    gateway._channel_register("group:-1000:0", "Destino", 9)
    result = gateway._outbound_send_location({"channel": "group:-1000:0", "latitude": 0, "longitude": 0, "new_topic": "Novo", **changes})
    assert result["sent"] is False and result["stage"] == "argument_validation"
    assert gateway.api.calls == []


def test_new_topic_is_used_without_registering_or_switching_thread(gateway):
    channel = "group:-1000:12"
    gateway._channel_register(channel, "Destino", 9)
    gateway._ensure_conversation(channel)
    gateway.database.execute("UPDATE conversations SET codex_thread_id='original-thread' WHERE chat_id=?", (channel,))
    result = gateway._outbound_send_location({"channel": channel, "latitude": 0, "longitude": 0, "new_topic": "Pontos"})
    assert result == {"sent": True, "channel": "group:-1000:17", "message_id": 700, "topic_created": True}
    assert gateway.api.calls == [("createForumTopic", {"chat_id": -1000, "name": "Pontos"}), ("sendLocation", {"chat_id": -1000, "message_thread_id": 17, "latitude": 0, "longitude": 0})]
    assert [item["id"] for item in gateway._channels_list()] == [channel]
    assert gateway._pending_external(channel) == ("", [])
    assert gateway._pending_external("group:-1000:17")[1] == [700]
    assert gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id=?", (channel,)).fetchone()[0] == "original-thread"


@pytest.mark.parametrize("result", [None, True, {}, {"message_id": True}, {"message_id": 0}, {"message_id": -1}])
def test_gateway_does_not_confirm_invalid_receipt(gateway, result):
    gateway._channel_register("9", "Destino", 9)
    gateway.api.results["sendLocation"] = result
    response = gateway._outbound_send_location({"channel": "9", "latitude": 0, "longitude": 0})
    assert response["sent"] is False and response["delivery"] == "unknown" and response["stage"] == "receipt_validation"
    assert len(gateway.api.calls) == 1
    assert gateway._pending_external("9") == ("", [])


def test_failed_send_preserves_partial_topic_without_disclosing_api_error(gateway, tmp_path):
    gateway._channel_register("group:-1000:12", "Destino", 9)
    gateway.api.results["sendLocation"] = TimeoutError("token=private-token latitude=-23.55052")
    response = gateway._outbound_send_location({"channel": "group:-1000:12", "latitude": -23.55052, "longitude": 0, "new_topic": "Pontos"})
    assert response["sent"] is False and response["topic_created"] is True and response["channel"] == "group:-1000:17"
    assert response["delivery"] == "unknown"
    assert len(gateway.api.calls) == 2
    with pytest.raises(TelegramMcpError, match="novo tópico já foi criado") as raised:
        service_for(tmp_path, [], response).send_location("group:-1000:12", -23.55052, 0, new_topic="Pontos", confirm=True)
    assert "private-token" not in str(raised.value) + gateway.last_error
    assert "-23.55052" not in str(raised.value) + gateway.last_error


@pytest.mark.parametrize("creation", [TimeoutError("private-token"), {}, {"message_thread_id": True}])
def test_uncertain_topic_creation_never_attempts_location_send(gateway, creation):
    gateway._channel_register("group:-1000:12", "Destino", 9)
    gateway.api.results["createForumTopic"] = creation
    response = gateway._outbound_send_location({"channel": "group:-1000:12", "latitude": 0, "longitude": 0, "new_topic": "Pontos"})
    assert response["sent"] is False and response["topic_created"] is None
    assert response["stage"] == "topic_creation" and response["delivery"] == "not_sent"
    assert [method for method, _params in gateway.api.calls] == ["createForumTopic"]


@pytest.mark.parametrize("result", [None, True, {}, {"sent": True}, {"sent": True, "message_id": True}, {"sent": False, "stage": {}}])
def test_mcp_rejects_invalid_gateway_results_without_fallback_success(tmp_path, result):
    with pytest.raises(TelegramMcpError, match="indeterminada|confirmar"):
        service_for(tmp_path, [], result).send_location("9", 0, 0, confirm=True)


def test_rpc_failure_reports_uncertainty_without_exposing_exception(tmp_path):
    calls = []
    service = service_for(tmp_path, calls, TimeoutError("private-token latitude=-23.55052"))
    with pytest.raises(TelegramMcpError, match="Não repita automaticamente") as raised:
        service.send_location("9", -23.55052, 0, confirm=True)
    assert "private-token" not in str(raised.value) and "-23.55052" not in str(raised.value)
    assert len(calls) == 1


@pytest.mark.parametrize("diagnostic_fails", [False, True])
def test_confirmed_delivery_survives_local_persistence_failure(gateway, monkeypatch, diagnostic_fails):
    gateway._channel_register("9", "Destino", 9)
    def fail(*args, **kwargs):
        raise OSError("latitude=-23.55052 private-token")
    monkeypatch.setattr(gateway, "_external_outbound_record", fail)
    if diagnostic_fails:
        monkeypatch.setattr(gateway, "_record_error", fail)
    result = gateway._outbound_send_location({"channel": "9", "latitude": -23.55052, "longitude": 0})
    assert result["sent"] is True and "warning" in result
    assert len(gateway.api.calls) == 1
    assert "-23.55052" not in (gateway.last_error or "") and "private-token" not in (gateway.last_error or "")


def test_legacy_history_migrates_idempotently_and_ignores_invalid_location(gateway):
    gateway.database.execute("DROP TABLE telegram_external_messages")
    gateway.database.execute("CREATE TABLE telegram_external_messages (conversation_id TEXT, generation INTEGER, message_id INTEGER, text TEXT, attachment TEXT, created_at REAL, delivered INTEGER DEFAULT 0, PRIMARY KEY(conversation_id, generation, message_id))")
    gateway.database.execute("INSERT INTO telegram_external_messages VALUES ('9', 0, 1, 'Texto anterior', NULL, 1, 0)")
    gateway.database.commit()
    gateway._init_external_table()
    gateway._init_external_table()
    assert gateway.database.execute("SELECT structured_content FROM telegram_external_messages").fetchone()[0] == "{}"
    for invalid in ("{", "[]", '{"location":{"kind":"static","latitude":NaN,"longitude":0}}', '{"location":{"kind":"live_snapshot","latitude":0,"longitude":0}}'):
        gateway.database.execute("UPDATE telegram_external_messages SET structured_content=?", (invalid,))
        raw, ids = gateway._pending_external("9")
        assert ids == [1] and "Texto anterior" in raw and '"location"' not in raw


def test_fixed_point_roundtrip_survives_restart_and_generation_isolation(gateway):
    gateway._channel_register("9", "Destino", 9)
    gateway._outbound_send_location({"channel": "9", "latitude": 0, "longitude": 0})
    path = gateway.settings.state_dir / "gateway.sqlite3"
    with sqlite3.connect(path) as database:
        structured = json.loads(database.execute("SELECT structured_content FROM telegram_external_messages").fetchone()[0])
    assert structured == {"location": {"kind": "static", "latitude": 0, "longitude": 0}}
    restarted = Gateway(gateway.settings)
    try:
        assert restarted._pending_external("9")[1] == [700]
        restarted.database.execute("UPDATE conversations SET generation=1 WHERE chat_id='9'")
        restarted.database.commit()
        assert restarted._pending_external("9") == ("", [])
    finally:
        restarted.database.close()
