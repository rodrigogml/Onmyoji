"""Diagnósticos de números remotos sem gravar quando a pré-consulta é inválida."""
from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.omie.category_allocation import define_categories
from onmyoji_daemon.mcp.omie.exception import OmieError


IDENTIFIER = {"codigo_lancamento_omie": 34}
CATEGORIES = [{"codigo_categoria": "2.01.03", "percentual": 100}]
MISSING = object()
PRIVATE_VALUE = "private-numeric-value-app-secret"


def title(*, corrected=False):
    return {
        "codigo_lancamento_omie": 34, "codigo_cliente_fornecedor": 90,
        "data_emissao": "01/11/2026", "data_vencimento": "17/11/2026",
        "valor_documento": 1000, "codigo_categoria": "2.01.03", "status_titulo": "PAGO",
        "categorias": ([{"codigo_categoria": "2.01.03", "percentual": 100, "valor": 1000}]
                       if corrected else [{"codigo_categoria": "2.01.03", "percentual": 80, "valor": 800},
                                          {"codigo_categoria": "2.07.01", "percentual": 20, "valor": 200}]),
    }


def movement(*, payment, corrected=False):
    total = 950 if payment else 1000
    return {
        "detalhes": {
            "nCodTitulo": 34, "cNatureza": "P", "nValorTitulo": 1000,
            "nCodBaixa": 78 if payment else 0, "nCodMovCC": 178 if payment else 0,
            "nCodCC": 56, "dDtPagamento": "17/11/2026" if payment else "",
            "nValorMovCC": 950 if payment else 0, "nDesconto": 50 if payment else 0,
            "nJuros": 0, "nMulta": 0, "dDtConcilia": "17/11/2026" if payment else "",
            "cHrConcilia": "12:34:56" if payment else "", "cUsConcilia": "TESTE" if payment else "",
        },
        "resumo": {"cLiquidado": "S", "nValPago": 1000, "nValLiquido": 950,
                   "nValAberto": 0, "nDesconto": 50, "nJuros": 0, "nMulta": 0},
        "categorias": ([{"cCodCateg": "2.01.03", "nDistrPercentual": 100, "nDistrValor": total}]
                       if corrected else [{"cCodCateg": "2.01.03", "nDistrPercentual": 80, "nDistrValor": total * .8},
                                          {"cCodCateg": "2.07.01", "nDistrPercentual": 20, "nDistrValor": total * .2}]),
    }


def page(record):
    return {"nPagina": 1, "nTotPaginas": 1, "nRegistros": 1, "nTotRegistros": 1, "movimentos": [record]}


def responses():
    return [title(), page(movement(payment=False)), page(movement(payment=True)),
            {"codigo_status": "0", "codigo_lancamento_omie": 34},
            title(corrected=True), page(movement(payment=False, corrected=True)),
            page(movement(payment=True, corrected=True))]


REMOTE_FIELDS = [
    ("title", "valor_documento", None),
    ("CP", "resumo.nValPago", "CP"),
    ("CP", "resumo.nValLiquido", "CP"),
    ("CP", "resumo.nValAberto", "CP"),
    ("CP", "resumo.nDesconto", "CP"),
    ("CP", "resumo.nJuros", "CP"),
    ("CP", "resumo.nMulta", "CP"),
    ("CP", "detalhes.nValorTitulo", "CP"),
    ("BXCP", "detalhes.nValorMovCC", "BXCP"),
    ("BXCP", "detalhes.nDesconto", "BXCP"),
    ("BXCP", "detalhes.nJuros", "BXCP"),
    ("BXCP", "detalhes.nMulta", "BXCP"),
]
INVALID_VALUES = [
    pytest.param(MISSING, "missing", "missing", id="missing"),
    pytest.param(None, "null", "null", id="null"),
    pytest.param(True, "invalid_type", "boolean", id="boolean"),
    pytest.param([PRIVATE_VALUE], "invalid_type", "array", id="array"),
    pytest.param({"private": PRIVATE_VALUE}, "invalid_type", "object", id="object"),
    pytest.param((PRIVATE_VALUE,), "invalid_type", "unsupported", id="unsupported"),
    pytest.param("", "invalid_value", "string", id="empty-string"),
    pytest.param(PRIVATE_VALUE, "invalid_value", "string", id="malformed-string"),
    pytest.param("50,00", "invalid_value", "string", id="locale-string"),
    pytest.param(float("nan"), "invalid_value", "number", id="nan"),
    pytest.param(float("inf"), "invalid_value", "number", id="infinity"),
]


@pytest.mark.parametrize("processed", [False, True], ids=["preflight", "verification"])
@pytest.mark.parametrize("group,field,kind", REMOTE_FIELDS)
@pytest.mark.parametrize("value,issue,received_type", INVALID_VALUES)
def test_remote_numeric_failure_identifies_the_field_without_exposing_its_value(processed, group, field, kind, value, issue, received_type):
    """Ausência, nulo e formato inválido continuam inconclusivos, mesmo para ajustes zerados."""
    records = responses()
    index = {"title": 0, "CP": 1, "BXCP": 2}[group] + (4 if processed else 0)
    record = records[index] if group == "title" else records[index]["movimentos"][0]
    path = field.split(".")
    parent = record if len(path) == 1 else record[path[0]]
    if value is MISSING:
        del parent[path[-1]]
    else:
        parent[path[-1]] = value
    service = Mock()
    service.invoke_financial.side_effect = records

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    error = raised.value
    assert error.code == ("category_allocation_verification_failed" if processed else "category_allocation_preflight_failed")
    assert error.details["reason"] == "invalid_number"
    assert error.details["field"] == field
    assert error.details["operation"] == ("payables_get" if group == "title" else "financial_movements_list")
    assert error.details["number_issue"] == issue
    assert error.details["received_type"] == received_type
    assert error.details["escrita_processada"] is processed
    assert error.details["retryable"] is False
    if kind is None:
        assert "tipo_movimento" not in error.details
    else:
        assert error.details["tipo_movimento"] == kind
    public_error = json.dumps(error.as_dict(), ensure_ascii=False, allow_nan=False)
    assert PRIVATE_VALUE not in public_error
    assert not {"received_value", "valor_recebido", "snippet", "dados_omie", "registro"} & set(error.details)
    calls = service.invoke_financial.call_args_list
    assert sum(call.kwargs["writes"] for call in calls) == int(processed)
    assert len(calls) == index + 1


def test_decimal_text_from_remote_financial_fields_remains_accepted():
    records = responses()
    for index in (0, 4):
        records[index]["valor_documento"] = "1000.00"
    for index in (1, 2, 5, 6):
        record = records[index]["movimentos"][0]
        for key in ("nValPago", "nValLiquido", "nValAberto", "nDesconto", "nJuros", "nMulta"):
            record["resumo"][key] = f'{record["resumo"][key]:.2f}'
        for key in ("nValorTitulo", "nValorMovCC", "nDesconto", "nJuros", "nMulta"):
            record["detalhes"][key] = f'{record["detalhes"][key]:.2f}'
    original = deepcopy(records)
    service = Mock()
    service.invoke_financial.side_effect = records

    result = define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert result["verificacao"]["campos_preservados"] is True
    assert result["verificacao"]["baixas_confirmadas"] is True
    assert result["baixas"][0]["valor"] == 950
    assert result["baixas"][0]["desconto"] == 50
    assert records == original
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


@pytest.mark.parametrize("group,key", [
    ("title", "percentual"), ("CP", "nDistrPercentual"), ("CP", "nDistrValor"),
    ("BXCP", "nDistrPercentual"), ("BXCP", "nDistrValor"),
])
@pytest.mark.parametrize("value,issue,received_type", INVALID_VALUES)
def test_post_write_category_numbers_keep_the_exact_field_and_origin(group, key, value, issue, received_type):
    records = responses()
    index = {"title": 4, "CP": 5, "BXCP": 6}[group]
    record = records[index] if group == "title" else records[index]["movimentos"][0]
    category = record["categorias"][0]
    if value is MISSING:
        del category[key]
    else:
        category[key] = value
    service = Mock()
    service.invoke_financial.side_effect = records

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    details = raised.value.details
    assert raised.value.code == "category_allocation_verification_failed"
    assert details["reason"] == "invalid_number"
    assert details["field"] == f"categorias[0].{key}"
    assert details["operation"] == ("payables_get" if group == "title" else "financial_movements_list")
    assert details["number_issue"] == issue
    assert details["received_type"] == received_type
    assert details["escrita_processada"] is True
    assert details["retryable"] is False
    if group == "title":
        assert "tipo_movimento" not in details
    else:
        assert details["tipo_movimento"] == group
    assert PRIVATE_VALUE not in json.dumps(raised.value.as_dict(), ensure_ascii=False, allow_nan=False)
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_null_optional_category_amount_is_invalid_when_the_remote_field_is_present():
    records = responses()
    records[4]["categorias"][0]["valor"] = None
    service = Mock()
    service.invoke_financial.side_effect = records

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert raised.value.details["field"] == "categorias[0].valor"
    assert raised.value.details["operation"] == "payables_get"
    assert raised.value.details["number_issue"] == "null"
    assert raised.value.details["escrita_processada"] is True
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1
