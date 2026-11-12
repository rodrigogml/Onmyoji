from copy import deepcopy
from uuid import UUID

import pytest

from onmyoji_daemon.agent_notification.contract import SCHEMA, is_uuid7, uuid7, validate_event
from onmyoji_daemon.rpc import RpcError


def event():
    return {"schema_version": 1, "event_id": uuid7(), "prompt": "Executar teste", "response_channel": {"type": "telegram", "value": "group:-1:42"}, "thread_policy": "new"}


def test_schema_and_uuid7_roundtrip():
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(SCHEMA)
    value = event()
    assert UUID(value["event_id"]).version == 7
    assert validate_event(value) == value
    validated = validate_event(value)
    validated["response_channel"]["value"] = "changed"
    assert value["response_channel"]["value"] != "changed"


def test_version_does_not_accept_json_float():
    value = event()
    value["schema_version"] = 1.0
    with pytest.raises(RpcError):
        validate_event(value)


@pytest.mark.parametrize("value", [None, [], "fixture", {}, {"schema_version": 1}])
def test_non_envelope_roots_and_incomplete_objects(value):
    with pytest.raises(RpcError) as caught:
        validate_event(value)
    assert caught.value.code == "invalid_event"


def test_channel_object_is_closed_and_uuid_is_canonical():
    value = event()
    value["response_channel"]["extra"] = "fixture"
    with pytest.raises(RpcError):
        validate_event(value)
    del value["response_channel"]["extra"]
    value["event_id"] = value["event_id"].replace("-", "")
    with pytest.raises(RpcError):
        validate_event(value)


@pytest.mark.parametrize("field,value", [("schema_version", True), ("schema_version", 2), ("event_id", "invalid"), ("event_id", "00000000-0000-4000-8000-000000000000"), ("prompt", "  \n"), ("prompt", 3), ("thread_policy", "invalid"), ("response_channel", {"type": "web", "value": "x"}), ("response_channel", {"type": "email", "value": " "}), ("extra", True)])
def test_invalid_values_do_not_echo_payload(field, value):
    request = event()
    request[field] = value
    with pytest.raises(RpcError) as caught:
        validate_event(request)
    assert caught.value.code == "invalid_event"
    assert "path" in caught.value.details
    assert str(request["prompt"]) not in str(caught.value)


@pytest.mark.parametrize("field", list(event()))
def test_missing_fields(field):
    request = event()
    del request[field]
    with pytest.raises(RpcError) as caught:
        validate_event(request)
    assert caught.value.details["path"] == "/" + field


def test_missing_nested_field_identifies_its_full_path():
    request = event()
    del request["response_channel"]["value"]
    with pytest.raises(RpcError) as caught:
        validate_event(request)
    assert caught.value.details["path"] == "/response_channel/value"
    assert str(caught.value) == "Campo obrigatório ausente."


def test_email_requires_account_and_subject_and_telegram_forbids_them():
    value = event()
    value["response_channel"] = {"type": "email", "value": "destino@example.com", "account": "operations", "subject": "Resultado agendado"}
    assert validate_event(value) == value
    for field in ("account", "subject"):
        invalid = deepcopy(value)
        del invalid["response_channel"][field]
        with pytest.raises(RpcError):
            validate_event(invalid)
    telegram = event()
    telegram["response_channel"]["account"] = "operations"
    with pytest.raises(RpcError):
        validate_event(telegram)


@pytest.mark.parametrize("value", [None, 1, "", "ffffffff-ffff-7fff-0fff-ffffffffffff"])
def test_uuid7_rejects_non_uuid_and_wrong_variant(value):
    assert not is_uuid7(value)
