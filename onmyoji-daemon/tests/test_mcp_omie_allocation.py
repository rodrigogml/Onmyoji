"""Confirmação de persistência do rateio sem repetir escritas financeiras."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.omie.allocation import verify_department_allocation
from onmyoji_daemon.mcp.omie.exception import OmieError


PERCENTAGE = [{"cCodDep": "000123", "nPerDep": 100}]
AMOUNT = [{"cCodDep": "000123", "nValDep": 1000}]


@pytest.mark.parametrize("operation,read_operation,field,id_key,integration_key", [
    ("account_transactions_update", "account_transactions_get", "departamentos", "nCodLanc", "cCodIntLanc"),
    ("payables_update", "payables_get", "distribuicao", "codigo_lancamento_omie", "codigo_lancamento_integracao"),
    ("receivables_update", "receivables_get", "distribuicao", "codigo_lancamento_omie", "codigo_lancamento_integracao"),
])
@pytest.mark.parametrize("by_integration", [False, True])
def test_verification_reads_the_same_object_once_and_prefers_omie_id(operation, read_operation, field, id_key, integration_key, by_integration):
    parameters = {field: deepcopy(PERCENTAGE), integration_key: "000-INT"}
    if not by_integration:
        parameters[id_key] = 9000000222
    query = {integration_key: "000-INT"} if by_integration else {id_key: 9000000222}
    record = {**query, field: [{"cCodDep": "000123", "nPerDep": "100.000000", "nValDep": 1000, "cDesDep": "Sintético"}]}
    service = Mock()
    service.invoke_financial.return_value = record

    assert verify_department_allocation(service, "empresa", operation, parameters) == {"rateio_confirmado": True}

    service.invoke_financial.assert_called_once_with("empresa", read_operation, {"params": query}, writes=False)
    assert parameters[field] == PERCENTAGE


@pytest.mark.parametrize("expected,actual", [
    ([], []),
    ([{"cCodDep": "D1", "nPerDep": 40}, {"cCodDep": "D2", "nPerDep": 60}],
     [{"cCodDep": "D2", "nPerDep": 60}, {"cCodDep": "D1", "nPerDep": 40}]),
    (PERCENTAGE, [{"cCodDep": "000123", "nPerDep": Decimal("99.999999")}]),
    (AMOUNT, [{"cCodDep": "000123", "nValDep": Decimal("999.99")}]),
])
def test_equivalent_allocations_accept_order_and_documented_tolerances(expected, actual):
    service = Mock()
    service.invoke_financial.return_value = {"nCodLanc": 12, "departamentos": actual}
    assert verify_department_allocation(service, "empresa", "account_transactions_update", {
        "nCodLanc": 12, "departamentos": expected,
    }) == {"rateio_confirmado": True}


@pytest.mark.parametrize("operation,field,total_fields", [
    ("account_transactions_update", "departamentos", {"cabecalho": {"nValorLanc": 1000}}),
    ("payables_update", "distribuicao", {"valor_documento": 1000}),
    ("receivables_update", "distribuicao", {"valor_documento": 1000}),
])
@pytest.mark.parametrize("actual_amount", [0, 999.98])
def test_matching_percentage_with_amount_contradicting_total_is_inconclusive(operation, field, total_fields, actual_amount):
    """O percentual sozinho não confirma um retorno que contradiz o próprio valor do objeto."""
    service = Mock()
    id_key = "nCodLanc" if field == "departamentos" else "codigo_lancamento_omie"
    service.invoke_financial.return_value = {
        **total_fields, id_key: 12, field: [{"cCodDep": "000123", "nPerDep": 100, "nValDep": actual_amount}],
    }

    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", operation, {id_key: 12, field: PERCENTAGE})

    assert error.value.code == "write_verification_failed"
    assert error.value.details["reason"] == "invalid_response_allocation"
    assert error.value.details["cause_code"] == "inconsistent_department_values"
    service.invoke_financial.assert_called_once()


@pytest.mark.parametrize("total_fields", [{"cabecalho": {"nValorLanc": 1000}}, {"cabecalho": {}}])
@pytest.mark.parametrize("actual", [PERCENTAGE, [{"cCodDep": "000123", "nPerDep": 100, "nValDep": 999.99}]])
def test_consistent_amounts_or_percentage_without_amount_can_be_confirmed(total_fields, actual):
    service = Mock()
    service.invoke_financial.return_value = {**total_fields, "nCodLanc": 12, "departamentos": actual}
    assert verify_department_allocation(service, "empresa", "account_transactions_update", {
        "nCodLanc": 12, "departamentos": PERCENTAGE,
    }) == {"rateio_confirmado": True}


@pytest.mark.parametrize("total", [None, -1, "1e1000000"])
def test_invalid_total_prevents_confirming_paired_percentage_and_amount(total):
    service = Mock()
    service.invoke_financial.return_value = {
        "nCodLanc": 12,
        "cabecalho": {"nValorLanc": total},
        "departamentos": [{"cCodDep": "000123", "nPerDep": 100, "nValDep": 1000}],
    }
    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", "account_transactions_update", {
            "nCodLanc": 12, "departamentos": PERCENTAGE,
        })
    assert error.value.code == "write_verification_failed"
    assert error.value.details["reason"] == "invalid_response_allocation"


@pytest.mark.parametrize("expected,actual", [
    (PERCENTAGE, []), ([], PERCENTAGE),
    (PERCENTAGE, [{"cCodDep": "123", "nPerDep": 100}]),
    (PERCENTAGE, [{"cCodDep": "000123", "nPerDep": 99.999998}]),
    (AMOUNT, [{"cCodDep": "000123", "nValDep": 999.98}]),
])
def test_different_valid_allocation_is_not_reported_as_success(expected, actual):
    service = Mock()
    service.invoke_financial.return_value = {"nCodLanc": 12, "departamentos": actual}
    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", "account_transactions_update", {
            "nCodLanc": 12, "departamentos": expected,
        })
    assert error.value.code == "allocation_not_persisted"
    assert error.value.details == {
        "operation": "account_transactions_update", "field": "dados.departamentos",
        "retryable": False, "escrita_processada": True, "reason": "allocation_mismatch",
    }
    service.invoke_financial.assert_called_once()


@pytest.mark.parametrize("native_key,public_key,resource", [
    ("nCodLancCP", "conta_pagar_id", "contas_pagar"),
    ("nCodLancCR", "conta_receber_id", "contas_receber"),
])
def test_mismatch_explains_the_linked_title_without_writing_it(native_key, public_key, resource):
    service = Mock()
    service.invoke_financial.return_value = {
        "nCodLanc": 12, "departamentos": [], "diversos": {"cOrigem": "BAXP", native_key: 9000000333},
    }
    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", "account_transactions_update", {
            "nCodLanc": 12, "departamentos": PERCENTAGE,
        })
    assert error.value.code == "allocation_not_persisted"
    assert error.value.details[public_key] == 9000000333
    assert error.value.details["origem"] == "BAXP"
    assert error.value.details["suggested_tool"] == f"omie.financeiro.{resource}.alterar"
    assert error.value.details["suggested_title_id"] == 9000000333
    assert "dados.id=9000000333" in str(error.value)
    assert "Não cancele nem repita a baixa" in str(error.value)
    assert service.invoke_financial.call_count == 1
    assert service.invoke_financial.call_args.kwargs == {"writes": False}


def test_two_linked_titles_are_diagnostic_without_choosing_one():
    service = Mock()
    service.invoke_financial.return_value = {
        "nCodLanc": 14, "departamentos": [], "diversos": {"cOrigem": "BAXR", "nCodLancCP": 12, "nCodLancCR": 13},
    }
    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", "account_transactions_update", {
            "nCodLanc": 14, "departamentos": PERCENTAGE,
        })
    assert "suggested_tool" not in error.value.details
    assert error.value.details["conta_pagar_id"] == 12
    assert error.value.details["conta_receber_id"] == 13
    assert "Confira os títulos vinculados" in str(error.value)


@pytest.mark.parametrize("source", [None, [], {"cOrigem": "invalid sensitive", "nCodLancCP": True, "nCodLancCR": "13"},
                                       {"cOrigem": "ÁBCD", "nCodLancCP": 0, "nCodLancCR": -1}])
def test_invalid_source_metadata_does_not_create_title_guidance(source):
    service = Mock()
    service.invoke_financial.return_value = {"nCodLanc": 12, "departamentos": [], "diversos": source}
    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", "account_transactions_update", {
            "nCodLanc": 12, "departamentos": PERCENTAGE,
        })
    assert set(error.value.details) == {"operation", "field", "retryable", "escrita_processada", "reason"}
    assert "sensitive" not in str(error.value.as_dict())


@pytest.mark.parametrize("failure,cause", [
    (OmieError("network_error", "sensitive response", {"private": "SECRET"}), "network_error"),
    (ValueError("SECRET"), "unexpected_error"),
])
def test_failed_readback_is_inconclusive_and_preserves_only_the_safe_cause(failure, cause):
    service = Mock()
    service.invoke_financial.side_effect = failure
    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", "account_transactions_update", {
            "nCodLanc": 12, "departamentos": PERCENTAGE,
        })
    assert error.value.code == "write_verification_failed"
    assert error.value.details["cause_code"] == cause
    assert error.value.details["escrita_processada"] is True
    assert error.value.details["retryable"] is False
    assert "SECRET" not in str(error.value.as_dict())
    service.invoke_financial.assert_called_once()


@pytest.mark.parametrize("record,reason", [
    (None, "invalid_response"), ([], "invalid_response"), ({}, "response_identifier_missing"),
    ({"nCodLanc": 12}, "missing_allocation"),
    ({"nCodLanc": 13, "departamentos": PERCENTAGE}, "response_identifier_mismatch"),
    ({"nCodLanc": True, "departamentos": PERCENTAGE}, "response_identifier_mismatch"),
    ({"nCodLanc": 12, "departamentos": [{"cCodDep": "000123", "nValDep": 1000}]}, "missing_allocation_value"),
    ({"nCodLanc": 12, "departamentos": [{"cCodDep": "000123", "nPerDep": "1e-9999999999999999999"}]}, "invalid_response_allocation"),
])
def test_readback_that_cannot_confirm_the_request_is_inconclusive(record, reason):
    service = Mock()
    service.invoke_financial.return_value = record
    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", "account_transactions_update", {
            "nCodLanc": 12, "departamentos": PERCENTAGE,
        })
    assert error.value.code == "write_verification_failed"
    assert error.value.details["reason"] == reason


def test_out_of_range_decimal_arithmetic_is_an_inconclusive_readback():
    service = Mock()
    service.invoke_financial.return_value = {"nCodLanc": 12, "departamentos": [{"cCodDep": "000123", "nValDep": "1e1000000"}]}
    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", "account_transactions_update", {
            "nCodLanc": 12, "departamentos": AMOUNT,
        })
    assert error.value.code == "write_verification_failed"
    assert error.value.details["reason"] == "invalid_allocation_value"


@pytest.mark.parametrize("rows", [
    None, {}, [None], [{"cCodDep": 123, "nPerDep": 100}], [{"cCodDep": "", "nPerDep": 100}],
    [{"cCodDep": " " * 41, "nPerDep": 100}], [{"cCodDep": "D" * 41, "nPerDep": 100}],
    [{"cCodDep": "000123"}], [{"cCodDep": "000123", "nPerDep": 101}],
    [{"cCodDep": "000123", "nPerDep": True}], [{"cCodDep": "000123", "nPerDep": None}],
    [{"cCodDep": "000123", "nPerDep": []}], [{"cCodDep": "000123", "nPerDep": "bad"}],
    [{"cCodDep": "000123", "nPerDep": float("nan")}], [{"cCodDep": "000123", "nPerDep": float("inf")}],
    [{"cCodDep": "000123", "nPerDep": -1}], [*PERCENTAGE, *PERCENTAGE],
])
@pytest.mark.parametrize("invalid_expected", [False, True])
def test_malformed_or_duplicate_allocations_never_confirm_persistence(rows, invalid_expected):
    service = Mock()
    service.invoke_financial.return_value = {"nCodLanc": 12, "departamentos": rows}
    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", "account_transactions_update", {
            "nCodLanc": 12, "departamentos": rows if invalid_expected else PERCENTAGE,
        })
    assert error.value.code == "write_verification_failed"
    assert error.value.details["reason"] == ("invalid_expected_allocation" if invalid_expected else "invalid_response_allocation")
    assert service.invoke_financial.call_count == (0 if invalid_expected else 1)


@pytest.mark.parametrize("operation,parameters,reason", [
    ("unsupported", {}, "unsupported_operation"),
    ("account_transactions_update", None, "invalid_write_parameters"),
    ("account_transactions_update", {"departamentos": PERCENTAGE}, "missing_identifier"),
    ("account_transactions_update", {"departamentos": PERCENTAGE, "nCodLanc": True, "cCodIntLanc": " "}, "missing_identifier"),
])
def test_unverifiable_write_parameters_do_not_make_a_query(operation, parameters, reason):
    service = Mock()
    with pytest.raises(OmieError) as error:
        verify_department_allocation(service, "empresa", operation, parameters)
    assert error.value.code == "write_verification_failed"
    assert error.value.details["reason"] == reason
    service.invoke_financial.assert_not_called()
