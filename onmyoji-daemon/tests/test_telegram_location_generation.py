"""Fronteiras de geração durante efeitos externos de localização fixa."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from onmyoji_daemon.telegram import Gateway, Settings


@pytest.fixture
def gateway(tmp_path: Path):
    """Mantém configuração, banco e workspace no temporário da suíte, sem iniciar serviços."""
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    data.mkdir(parents=True)
    (tmp_path / "configs" / "onmyoji-system.toml").write_text(
        "[codex]\nproject_directory=" + json.dumps(tmp_path.as_posix()) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "configs" / "daemon" / "daemon.toml").write_text(
        "[app_server]\nenabled=false\n", encoding="utf-8",
    )
    (data / "config.toml").write_text(
        'schema_version=1\n[telegram]\nvault_profile="fake"\ntoken_entry="fake"\n',
        encoding="utf-8",
    )
    instance = Gateway(Settings.load(tmp_path, data))
    yield instance
    instance.stop_event.set()
    instance.database.close()


def test_reset_during_confirmed_topic_creation_preserves_partial_result_without_sending_location(gateway):
    """Um tópico confirmado antes de /new não autoriza localização na geração seguinte."""
    channel = "group:-1000:12"
    gateway._channel_register(channel, "Destino", 9)
    generation = gateway._generation(channel)
    calls = []

    class ResetDuringTopicCreation:
        def call(self, method, values):
            calls.append((method, values))
            assert method == "createForumTopic"
            gateway._new_conversation(channel)
            return {"message_thread_id": 17}

    gateway.api = ResetDuringTopicCreation()
    response = gateway._outbound_send_location({
        "channel": channel, "latitude": 0, "longitude": -46.633308,
        "new_topic": "Pontos",
    })

    assert response["sent"] is False and response["topic_created"] is True
    assert response["channel"] == "group:-1000:17"
    assert response["stage"] == "gateway" and response["delivery"] == "not_sent"
    assert "não repita a criação" in response["error"]
    assert calls == [("createForumTopic", {"chat_id": -1000, "name": "Pontos"})]
    assert gateway._generation(channel) == generation + 1
    assert [item["id"] for item in gateway._channels_list()] == [channel]
    assert gateway._pending_external(channel) == ("", [])
    assert gateway._pending_external("group:-1000:17") == ("", [])
    assert gateway.database.execute("SELECT COUNT(*) FROM telegram_external_messages").fetchone() == (0,)
    assert gateway.database.execute("SELECT COUNT(*) FROM bot_messages").fetchone() == (0,)


def test_reset_during_confirmed_location_send_keeps_success_without_new_generation_history(gateway):
    """O recibo confirmado continua sendo sucesso; /new impede atribuí-lo ao contexto novo."""
    channel = "9"
    gateway._channel_register(channel, "Destino", 9)
    generation = gateway._generation(channel)
    calls = []

    class ResetDuringLocationSend:
        def call(self, method, values):
            calls.append((method, values))
            assert method == "sendLocation"
            gateway._new_conversation(channel)
            return {"message_id": 700}

    gateway.api = ResetDuringLocationSend()
    response = gateway._outbound_send_location({
        "channel": channel, "latitude": 0, "longitude": -46.633308,
    })

    assert response == {
        "sent": True, "channel": channel, "message_id": 700, "topic_created": False,
        "warning": "Telegram confirmou o envio, mas não foi possível preservar integralmente o contexto local.",
    }
    assert calls == [("sendLocation", {"chat_id": 9, "latitude": 0, "longitude": -46.633308})]
    assert gateway._generation(channel) == generation + 1
    assert gateway._pending_external(channel) == ("", [])
    assert gateway.database.execute("SELECT COUNT(*) FROM telegram_external_messages").fetchone() == (0,)
    assert gateway.database.execute("SELECT COUNT(*) FROM bot_messages").fetchone() == (0,)
