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
    with pytest.raises(RpcError):
        validate_event(request)


@pytest.mark.parametrize("value", [None, 1, "", "ffffffff-ffff-7fff-0fff-ffffffffffff"])
def test_uuid7_rejects_non_uuid_and_wrong_variant(value):
    assert not is_uuid7(value)
