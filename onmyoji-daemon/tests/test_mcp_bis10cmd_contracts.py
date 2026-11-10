"""Contratos públicos e respostas estruturadas das operações financeiras novas."""
from __future__ import annotations

import copy
import json

import pytest

from onmyoji_daemon.mcp.bis10cmd.bills import normalize_request, operation_schema, validate_resource
from onmyoji_daemon.mcp.bis10cmd.exception import Bis10CmdError
from onmyoji_daemon.mcp.bis10cmd.service import parse_output


CODE = "0" + "1" * 43


def draft(**changes):
    return {"code": CODE, "company_id": 2, "profile": "bis", **changes}


@pytest.mark.parametrize("changes", [
    {"args": []}, {"code": None}, {"code": 123}, {"code": "A" * 44},
    {"code": "١" * 44}, {"code": "1" * 43}, {"code": "1" * 45}, {"code": "1" * 46},
    {"recipient_person_id": True}, {"recipient_person_id": 0}, {"recipient_person_id": 9223372036854775808},
    {"payee_person_id": "10"}, {"original_value": 12.50}, {"original_value": "NaN"},
    {"original_value": "-1"}, {"original_value": "1e3"}, {"original_value": "1.001"},
    {"due_date": "2026-02-30"}, {"due_date": "07/11/2026"},
    {"entry_date": "2026-13-01T11:00:00"}, {"entry_date": "2026-11-07T25:00:00"},
    {"entry_date": "2026-11-07T11:00:00Z"}, {"allow_past_due": "true"}, {"notes": "x" * 1001},
    {"payment_state": "PAYED"}, {"account_id": 5}, {"categories": []},
])
def test_invalid_public_draft_is_rejected(changes):
    with pytest.raises(Bis10CmdError) as caught:
        normalize_request("analyze_bill_code", draft(**changes))
    assert caught.value.code == "invalid_request"


@pytest.mark.parametrize("centers", [
    [{"cost_center_id": True, "ratio": "100"}],
    [{"cost_center_id": 9, "ratio": "99"}],
    [{"cost_center_id": 9, "ratio": "101"}],
    [{"cost_center_id": 9, "ratio": "0"}, {"cost_center_id": 10, "ratio": "100"}],
    [{"cost_center_id": 9, "ratio": "50"}, {"cost_center_id": 9, "ratio": "50"}],
    [{"cost_center_id": 9, "ratio": 100}], [],
])
def test_invalid_cost_center_distribution_is_rejected(centers):
    with pytest.raises(Bis10CmdError) as caught:
        normalize_request("analyze_bill_code", draft(categories=[{"category_id": 7, "value": "1", "cost_centers": centers}]))
    assert caught.value.code == "invalid_request"


def test_normalization_preserves_input_and_exact_decimals():
    source = draft(code=CODE[:10] + ". -\t" + CODE[10:], original_value="000125.5",
                   categories=[{"category_id": 7, "value": "125.5", "cost_centers": [
                       {"cost_center_id": 9, "ratio": "33.33"}, {"cost_center_id": 10, "ratio": "66.67"}]}])
    original = copy.deepcopy(source)
    payload = normalize_request("analyze_bill_code", source)
    assert source == original
    assert payload["code"] == CODE and payload["original_value"] == "125.50"
    assert payload["categories"][0]["value"] == "125.50"
    assert "company_id" not in payload and "profile" not in payload
    assert payload["categories"][0]["cost_centers"][0]["ratio"] == "33.33"


@pytest.mark.parametrize("size", [44, 47, 48])
def test_supported_code_lengths_keep_leading_zeros(size):
    assert normalize_request("analyze_bill_code", draft(code="0" * size))["code"] == "0" * size


@pytest.mark.parametrize("operation,arguments", [
    ("get_bill", {"bill_id": True}), ("get_bill", {"bill_id": 0}), ("get_bill", {}),
    ("get_bill_request", {"request_id": "bad key"}), ("list_people", {"id": False}),
    ("list_people", {"limit": True}), ("list_bills", {"offset": -1}),
    ("list_bills", {"limit": 1001}), ("list_bills", {"state": "OTHER"}),
    ("list_bills", {"date_from": "2026-12-10", "date_to": "2026-12-09"}),
    ("create_bill_from_code", {"code": CODE, "request_id": "key", "confirm": False}),
    ("create_bill_from_code", {"code": CODE, "request_id": "key"}),
])
def test_other_operation_contracts_reject_wrong_types_and_ranges(operation, arguments):
    with pytest.raises(Bis10CmdError) as caught:
        normalize_request(operation, arguments)
    assert caught.value.code == "invalid_request"


def test_schemas_are_independent_and_reject_unknown_operations():
    first = operation_schema("analyze_bill_code")
    first["properties"]["categories"]["items"]["properties"]["value"]["pattern"] = "changed"
    assert operation_schema("analyze_bill_code")["properties"]["categories"]["items"]["properties"]["value"]["pattern"] != "changed"
    with pytest.raises(Bis10CmdError) as caught:
        operation_schema("unknown")
    assert caught.value.code == "unsupported_operation"


def test_structurally_valid_large_classification_still_obeys_transport_size_limit():
    """O limite global de stdin vale mesmo quando cada lista atende ao próprio schema."""
    centers = [{"cost_center_id": index, "ratio": "1"} for index in range(1, 101)]
    categories = [{"category_id": index, "value": "1", "cost_centers": copy.deepcopy(centers)} for index in range(1, 51)]
    with pytest.raises(Bis10CmdError) as caught:
        normalize_request("analyze_bill_code", draft(categories=categories))
    assert caught.value.code == "invalid_request"
    assert "128 KiB" in str(caught.value)


@pytest.mark.parametrize("mutation", ["missing_bool", "wrong_bool", "no_barcode", "bad_numeric", "contradiction", "duplicates"])
def test_contradictory_or_incomplete_analysis_response_is_rejected(mutation):
    item = {"code_valid": True, "supported": True, "ready_to_create": True,
            "barcode": CODE, "numeric_code": "1" * 47, "pending_fields": [], "warnings": [], "duplicates": []}
    if mutation == "missing_bool": del item["supported"]
    elif mutation == "wrong_bool": item["code_valid"] = 1
    elif mutation == "no_barcode": del item["barcode"]
    elif mutation == "bad_numeric": item["numeric_code"] = "1" * 46
    elif mutation == "contradiction": item["pending_fields"] = [{"field": "categories"}]
    elif mutation == "duplicates": item["duplicates"] = [{"id": 10}]
    with pytest.raises(Bis10CmdError) as caught:
        validate_resource({"resource": "billCodeAnalysis", "items": [item]})
    assert caught.value.code == "invalid_bill_response"


def test_capabilities_does_not_accept_boolean_api_version():
    with pytest.raises(Bis10CmdError) as caught:
        validate_resource({"resource": "billCapabilities", "items": [{"api_version": True, "actions": ["analyzeCode", "createFromCode", "get", "list"]}]})
    assert caught.value.code == "bill_client_incompatible"


@pytest.mark.parametrize("page", [
    {"resource": "billCapabilities", "items": [{"api_version": 1, "actions": [{}]}]},
    {"resource": "billError", "items": [{"code": "invalid", "message": "Inválido.", "outcome": []}]},
])
def test_malformed_enum_values_raise_functional_errors(page):
    with pytest.raises(Bis10CmdError) as caught:
        validate_resource(page)
    assert caught.value.code in {"bill_client_incompatible", "invalid_bill_response"}


@pytest.mark.parametrize("change", [{"id": True}, {"display_name": None}, {"id": 0}])
def test_people_responses_require_unambiguous_ids_and_names(change):
    item = {"id": 10, "display_name": "Fornecedor", **change}
    with pytest.raises(Bis10CmdError) as caught:
        validate_resource({"resource": "people", "items": [item], "offset": 0, "limit": 1, "has_more": False})
    assert caught.value.code == "invalid_bill_response"


@pytest.mark.parametrize("changes", [
    {"offset": True}, {"limit": True}, {"has_more": 1}, {"has_more": True},
    {"has_more": False, "next_offset": 1}, {"limit": 0}, {"limit": 1001},
])
def test_people_pagination_cannot_imply_invalid_continuation(changes):
    page = {"resource": "people", "items": [{"id": 10, "display_name": "Fornecedor"}], "offset": 0, "limit": 1, "has_more": False, **changes}
    with pytest.raises(Bis10CmdError) as caught:
        validate_resource(page)
    assert caught.value.code == "invalid_bill_response"


def test_redaction_preserves_numeric_ids_and_json_shape():
    page = {"version": 1, "resource": "people", "items": [{"id": 123, "display_name": "secret-123"}], "offset": 0, "limit": 1, "has_more": False}
    parsed = parse_output(json.dumps(page), "secret-123", ["secret-123", "123"])
    assert parsed["lookups"][0]["items"][0] == {"id": 123, "display_name": "[REDACTED]"}
    assert parsed["stderr"] == ["[REDACTED]"]


def test_functional_error_retains_code_field_and_sanitized_details():
    error = Bis10CmdError("invalid_due_date", "Vencimento inválido.", {"field": "due_date", "outcome": "not_submitted"})
    assert error.as_dict() == {"code": "invalid_due_date", "message": "Vencimento inválido.", "data": {"field": "due_date", "outcome": "not_submitted"}}
    assert Bis10CmdError("missing", "Ausente.").as_dict() == {"code": "missing", "message": "Ausente."}
