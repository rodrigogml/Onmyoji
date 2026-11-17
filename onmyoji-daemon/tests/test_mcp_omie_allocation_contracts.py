"""Regressões do rateio percentual e da alteração parcial de lançamentos Omie."""
from __future__ import annotations

from copy import deepcopy

import pytest

from onmyoji_daemon.mcp.omie.contracts import build_params, normalize_percentage_departments
from onmyoji_daemon.mcp.omie.exception import OmieError


@pytest.mark.parametrize("rows,expected", [
    ([], []),
    ([{"codigo": "000123", "percentual": 100}], [{"cCodDep": "000123", "nPerDep": 100}]),
    ([{"cCodDep": "D" * 40, "nPerDep": 100}], [{"cCodDep": "D" * 40, "nPerDep": 100}]),
    ([{"codigo": "A", "percentual": 0.1}, {"codigo": "B", "percentual": 99.9}],
     [{"cCodDep": "A", "nPerDep": 0.1}, {"cCodDep": "B", "nPerDep": 99.9}]),
])
def test_percentage_allocation_preserves_codes_and_requires_no_amount(rows, expected):
    original = deepcopy(rows)

    assert normalize_percentage_departments(rows) == expected

    assert rows == original


@pytest.mark.parametrize("rows", [
    None, {}, "departamentos", False,
    [None], [False], [[]],
    [{"codigo": "A"}], [{"percentual": 100}],
    [{"codigo": "A", "nPerDep": 100}],
    [{"codigo": "A", "percentual": 100, "valor": 10}],
    [{"cCodDep": "A", "nValDep": 10}],
    [{"codigo": "A", "percentual": 50}, {"cCodDep": "B", "nPerDep": 50}],
    [{"codigo": "A", "percentual": 50}, {"codigo": "A", "percentual": 50}],
    [{"cCodDep": "A", "nPerDep": 50}, {"cCodDep": "A", "nPerDep": 50}],
])
def test_percentage_allocation_rejects_invalid_structure_and_duplicates(rows):
    with pytest.raises(OmieError) as error:
        normalize_percentage_departments(rows)

    assert error.value.code == "invalid_parameter"
    assert error.value.details["field"] == "dados.departamentos"
    assert error.value.details["retryable"] is False


@pytest.mark.parametrize("code", [None, "", " \t", 123, True, "D" * 41])
def test_percentage_allocation_rejects_invalid_department_code(code):
    with pytest.raises(OmieError) as error:
        normalize_percentage_departments([{"codigo": code, "percentual": 100}])

    assert "código do departamento" in str(error.value)


@pytest.mark.parametrize("percentage", [None, True, "100", [], {}, float("inf"), float("nan"), 0, -1, 101, 99.9])
def test_percentage_allocation_rejects_invalid_percentages_and_incomplete_total(percentage):
    with pytest.raises(OmieError) as error:
        normalize_percentage_departments([{"codigo": "A", "percentual": percentage}], field="body.distribuicao", error_code="invalid_body")

    assert error.value.code == "invalid_body"
    assert error.value.details["field"] == "body.distribuicao"


@pytest.mark.parametrize("identity,native_identity", [
    ({"id": 9000000222}, {"nCodLanc": 9000000222}),
    ({"integration_id": "000123"}, {"cCodIntLanc": "000123"}),
    ({"id": 9000000222, "integration_id": "000123"}, {"nCodLanc": 9000000222, "cCodIntLanc": "000123"}),
])
@pytest.mark.parametrize("rows", [[{"codigo": "D1", "percentual": 100}], [{"cCodDep": "D1", "nPerDep": 100}], []])
def test_transaction_allocation_update_emits_only_identity_and_allocation(identity, native_identity, rows):
    result = build_params("account_transactions_update", {"body": {**identity, "departments": rows}})

    expected_rows = [{"cCodDep": "D1", "nPerDep": 100}] if rows else []
    assert result == {**native_identity, "departamentos": expected_rows}
    assert "cabecalho" not in result
    assert "detalhes" not in result


@pytest.mark.parametrize("change,expected", [
    ({"account_id": 12}, {"cabecalho": {"nCodCC": 12}}),
    ({"date": "17/11/2026"}, {"cabecalho": {"dDtLanc": "17/11/2026"}}),
    ({"amount": 10.25}, {"cabecalho": {"nValorLanc": 10.25}}),
    ({"category_code": "2.11.97"}, {"detalhes": {"cCodCateg": "2.11.97"}}),
    ({"document_type": "FAT", "document_number": "969"}, {"detalhes": {"cTipo": "FAT", "cNumDoc": "969"}}),
    ({"customer_id": 123, "project_id": 456}, {"detalhes": {"nCodCliente": 123, "nCodProjeto": 456}}),
    ({"note": ""}, {"detalhes": {"cObs": ""}}),
])
def test_transaction_update_keeps_only_the_requested_fields(change, expected):
    assert build_params("account_transactions_update", {"body": {"id": 123, **change}}) == {"nCodLanc": 123, **expected}


@pytest.mark.parametrize("body,code", [
    ({"departments": []}, "missing_parameter"),
    ({"id": 123}, "invalid_body"),
    ({"id": 123, "integration_id": "I1"}, "invalid_body"),
    ({"id": 123, "unknown": "X"}, "invalid_body"),
])
def test_transaction_update_requires_identity_and_a_supported_change(body, code):
    with pytest.raises(OmieError) as error:
        build_params("account_transactions_update", {"body": body})

    assert error.value.code == code


@pytest.mark.parametrize("field,value", [
    ("id", True), ("id", "123"), ("account_id", 0), ("customer_id", None), ("project_id", 1.5),
    ("integration_id", ""), ("integration_id", "I" * 21),
    ("category_code", ""), ("category_code", "C" * 21),
    ("document_number", 123), ("document_number", "N" * 21),
    ("document_type", "UNKNOWN"), ("document_type", None),
    ("date", "1/11/2026"), ("date", "31/02/2026"), ("date", None),
    ("amount", True), ("amount", 0), ("amount", float("nan")), ("amount", float("inf")),
    ("note", None),
    ("departments", [{"cCodDep": "D1", "nValDep": 100}]),
])
def test_transaction_update_validates_every_supplied_field(field, value):
    with pytest.raises(OmieError) as error:
        build_params("account_transactions_update", {"body": {"id": 123, "note": "válido", field: value}})

    assert error.value.code == "invalid_body"


def test_transaction_update_with_amount_preserves_legacy_value_allocation():
    result = build_params("account_transactions_update", {"body": {
        "id": 123, "amount": 100,
        "departments": [{"cCodDep": "D1", "nValDep": 100}],
    }})

    assert result == {"nCodLanc": 123, "cabecalho": {"nValorLanc": 100}, "departamentos": [{"cCodDep": "D1", "nValDep": 100}]}


@pytest.mark.parametrize("missing", ["integration_id", "account_id", "date", "amount", "category_code", "document_type"])
def test_transaction_creation_retains_all_required_fields(missing):
    body = {"integration_id": "TEST-001", "account_id": 12, "date": "17/11/2026", "amount": 100, "category_code": "2.11.97", "document_type": "FAT"}
    del body[missing]

    with pytest.raises(OmieError) as error:
        build_params("account_transactions_create", {"body": body})

    assert error.value.code == "invalid_body"
