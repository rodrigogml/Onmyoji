from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from onmyoji_daemon.agent_notification import EventContract, EventContractError, new_uuid7, received_at


def event(**changes):
    value = {
        "schema_version": 1,
        "event_id": new_uuid7(1_800_000_000_000),
        "prompt": "Analise este evento.",
        "response_channel": {"type": "telegram", "value": "group:-1001:0"},
        "thread_policy": "reuse",
    }
    value.update(changes)
    return value


def test_contract_accepts_telegram_and_email_and_preserves_prompt():
    contract = EventContract()
    telegram = contract.validate(event(prompt="  texto livre  "))
    assert telegram.prompt == "  texto livre  "
    assert telegram.response_channel.destination_key == '["telegram",null,"group:-1001:0"]'
    email = contract.validate(event(response_channel={"type": "email", "value": "destino@exemplo.com", "account": "principal", "subject": "Resultado"}, thread_policy="new"))
    assert email.response_channel.account == "principal"
    assert email.as_dict()["response_channel"]["subject"] == "Resultado"


@pytest.mark.parametrize("raw", [b"", b"{", b"\xff", b"[]", "null"])
def test_invalid_json_is_rejected(raw):
    with pytest.raises(EventContractError) as raised:
        EventContract().parse_json(raw)
    assert raised.value.code == "invalid_json"


@pytest.mark.parametrize("value", [{}, {"schema_version": "1"}, {"schema_version": 2}, {"schema_version": True}])
def test_version_errors_are_stable(value):
    with pytest.raises(EventContractError) as raised:
        EventContract().validate(value)
    assert raised.value.code == ("schema_version_required" if "schema_version" not in value else "unsupported_schema_version")


@pytest.mark.parametrize("identifier", [str(uuid.uuid1()), str(uuid.uuid4()), "not-a-uuid"])
def test_only_canonical_uuidv7_is_accepted(identifier):
    with pytest.raises(EventContractError) as raised:
        EventContract().validate(event(event_id=identifier))
    assert raised.value.code == "invalid_event_id"


def test_uuidv7_text_is_case_insensitive():
    identifier = new_uuid7().upper()
    assert EventContract().validate(event(event_id=identifier)).event_id == identifier


@pytest.mark.parametrize("prompt", ["", " ", "\n\t"])
def test_prompt_must_have_visible_content(prompt):
    with pytest.raises(EventContractError) as raised:
        EventContract().validate(event(prompt=prompt))
    assert raised.value.code == "event_validation_failed"


@pytest.mark.parametrize("channel", [
    {"type": "email", "value": "a@b", "account": "main"},
    {"type": "email", "value": "a@b", "subject": "s"},
    {"type": "telegram", "value": "1", "account": "main"},
    {"type": "telegram", "value": "1", "subject": "s"},
    {"type": "sms", "value": "1"},
    {"type": "telegram", "value": " "},
])
def test_channel_conditional_contract_is_closed(channel):
    with pytest.raises(EventContractError):
        EventContract().validate(event(response_channel=channel))


@pytest.mark.parametrize("change", [
    {"unexpected": True},
    {"thread_policy": "force_new"},
    {"response_channel": {"type": "telegram", "value": "1", "unexpected": True}},
])
def test_unknown_fields_and_literals_are_rejected(change):
    with pytest.raises(EventContractError):
        EventContract().validate(event(**change))


def test_uuid7_and_timestamp_helpers_follow_contract():
    first = new_uuid7(1_800_000_000_000)
    second = new_uuid7(1_800_000_000_001)
    assert uuid.UUID(first).version == 7
    assert first < second
    assert received_at(datetime(2026, 11, 11, 12, 34, 56, 987654, tzinfo=timezone.utc)) == "2026-11-11T12:34:56.987Z"
    with pytest.raises(ValueError):
        new_uuid7(-1)


def test_repeated_uuid_is_not_deduplicated_by_contract():
    contract = EventContract(); value = event()
    assert contract.validate(value) == contract.validate(value)
