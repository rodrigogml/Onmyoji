"""Substituição de categorias com auditoria financeira e respostas sintéticas."""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.omie import category_allocation as allocation
from onmyoji_daemon.mcp.omie.category_allocation import define_categories, normalize_categories
from onmyoji_daemon.mcp.omie.exception import OmieError


CATEGORIES = [{"codigo_categoria": "2.01.03", "percentual": 100}]
ACK = {"codigo_status": "0", "descricao_status": "Processado", "codigo_lancamento_omie": 34}
IDENTIFIER = {"codigo_lancamento_omie": 34}


def title(*, corrected=False):
    return {
        "codigo_lancamento_omie": 34, "codigo_lancamento_integracao": "TEST-TITLE-34",
        "codigo_cliente_fornecedor": 90, "data_vencimento": "17/11/2026",
        "data_emissao": "01/11/2026", "data_previsao": "17/11/2026",
        "valor_documento": 1000, "valor_pag": 0, "codigo_categoria": "2.01.03",
        "codigo_projeto": 91, "id_conta_corrente": 56, "status_titulo": "PAGO",
        "numero_documento": "TEST-NF-001", "numero_documento_fiscal": "TEST-001",
        "numero_parcela": "001/001", "observacao": "Preservar condição sintética",
        "id_origem": "NFEP", "distribuicao": [],
        "categorias": ([{"codigo_categoria": "2.01.03", "percentual": 100, "valor": 1000}]
                       if corrected else [{"codigo_categoria": "2.01.03", "percentual": 80, "valor": 800},
                                          {"codigo_categoria": "2.07.01", "percentual": 20, "valor": 200}]),
    }


def financial_record(*, corrected=False, payment=True, identifier=78, value=950, discount=50):
    return {
        "detalhes": {
            "nCodTitulo": 34, "nCodMovCC": identifier + 100 if payment else 0,
            "nCodBaixa": identifier if payment else 0, "nCodCC": 56,
            "nCodCliente": 90, "cNatureza": "P", "cOrigem": "BAXP" if payment else "NFEP",
            "dDtPagamento": "17/11/2026" if payment else "",
            "nValorTitulo": 1000, "nValorMovCC": value if payment else 0,
            "nDesconto": discount if payment else 0, "nJuros": 0, "nMulta": 0,
            "dDtConcilia": "17/11/2026" if payment else "",
            "cHrConcilia": "12:34:56" if payment else "", "cUsConcilia": "TESTE" if payment else "",
            "cStatus": "LIQUIDADO", "cCodCateg": "2.01.03", "cNumTitulo": "TEST-TITLE-34",
        },
        "resumo": {"cLiquidado": "S", "nValPago": 1000, "nValAberto": 0,
                   "nDesconto": 50, "nJuros": 0, "nMulta": 0, "nValLiquido": 950},
        "categorias": ([{"cCodCateg": "2.01.03", "nDistrPercentual": 100,
                         "nDistrValor": value if payment else 1000}]
                       if corrected else [{"cCodCateg": "2.01.03", "nDistrPercentual": 80,
                                          "nDistrValor": value * .8 if payment else 800},
                                         {"cCodCateg": "2.07.01", "nDistrPercentual": 20,
                                          "nDistrValor": value * .2 if payment else 200}]),
        "departamentos": [],
    }


def page(*rows, number=1, total_pages=1, total_records=None):
    return {"nPagina": number, "nTotPaginas": total_pages, "nRegistros": len(rows),
            "nTotRegistros": len(rows) if total_records is None else total_records,
            "movimentos": list(rows)}


def successful_responses():
    return [title(), page(financial_record(payment=False)), page(financial_record()), ACK,
            title(corrected=True), page(financial_record(payment=False, corrected=True)),
            page(financial_record(corrected=True))]


@pytest.mark.parametrize("categories,expected", [
    ([{"codigo": "2.01.03", "percentual": 100}], CATEGORIES),
    ([{"codigo": "000123", "percentual": 40}, {"codigo": "2.07.01", "percentual": 60}],
     [{"codigo_categoria": "000123", "percentual": 40}, {"codigo_categoria": "2.07.01", "percentual": 60}]),
    ([{"codigo": "A" * 20, "percentual": 100.0}], [{"codigo_categoria": "A" * 20, "percentual": 100.0}]),
])
def test_categories_are_translated_without_losing_textual_codes(categories, expected):
    original = deepcopy(categories)
    assert normalize_categories(categories) == expected
    assert categories == original


@pytest.mark.parametrize("categories", [
    None, [], {}, "categoria", (), [None], [[]], ["2.01.03"],
    [{}], [{"codigo": "2.01.03"}], [{"percentual": 100}],
    [{"codigo": None, "percentual": 100}], [{"codigo": 123, "percentual": 100}],
    [{"codigo": "", "percentual": 100}], [{"codigo": " ", "percentual": 100}],
    [{"codigo": "A" * 21, "percentual": 100}],
    [{"codigo": "2.01.03", "percentual": None}], [{"codigo": "2.01.03", "percentual": "100"}],
    [{"codigo": "2.01.03", "percentual": True}], [{"codigo": "2.01.03", "percentual": 0}],
    [{"codigo": "2.01.03", "percentual": -1}], [{"codigo": "2.01.03", "percentual": 101}],
    [{"codigo": "2.01.03", "percentual": float("nan")}],
    [{"codigo": "2.01.03", "percentual": float("inf")}],
    [{"codigo": "2.01.03", "percentual": float("-inf")}],
    [{"codigo": "2.01.03", "percentual": 99}],
    [{"codigo": "2.01.03", "percentual": 50}, {"codigo": "2.01.03", "percentual": 50}],
    [{"codigo": "2.01.03", "percentual": 100, "valor": 1000}],
    [{"codigo_categoria": "2.01.03", "percentual": 100}],
])
def test_invalid_or_ambiguous_categories_are_rejected(categories):
    with pytest.raises(OmieError) as error:
        normalize_categories(categories)
    assert error.value.code == "invalid_parameter"
    assert error.value.details["retryable"] is False


@pytest.mark.parametrize("by_integration", [False, True])
@pytest.mark.parametrize("include_native", [False, True])
def test_paid_title_and_discounted_reconciled_payment_are_confirmed(by_integration, include_native):
    identifier = {"codigo_lancamento_integracao": "TEST-TITLE-34"} if by_integration else IDENTIFIER
    service = Mock()
    service.invoke_financial.side_effect = successful_responses()

    result = define_categories(service, "empresa", identifier, CATEGORIES, include_native=include_native)

    assert result["alvo"] == {"recurso": "contas_pagar", "id": 34}
    assert result["verificacao"]["titulo_confirmado"] is True
    assert result["verificacao"]["baixas_confirmadas"] is True
    assert result["verificacao"]["campos_preservados"] is True
    calls = service.invoke_financial.call_args_list
    writes = [call for call in calls if call.kwargs["writes"]]
    assert len(writes) == 1
    assert writes[0].args == ("empresa", "payables_update", {"body": {
        "codigo_lancamento_omie": 34, "codigo_categoria": "2.01.03", "categorias": CATEGORIES,
    }})
    assert all(call.args[2]["atualizar_cache"] is True for call in calls if not call.kwargs["writes"])
    for call in calls:
        if call.args[1] == "financial_movements_list":
            assert call.args[2]["params"]["nCodTitulo"] == 34
            assert call.args[2]["params"]["cTpLancamento"] in {"CP", "BXCP"}
            assert call.args[2]["params"]["nRegPorPagina"] == 50
    assert ("dados_omie" in result) is include_native
    assert "app_key" not in str(result) and "app_secret" not in str(result)


def test_processing_success_does_not_confirm_unchanged_title_categories():
    responses = successful_responses()
    responses[4] = title()
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_not_persisted"
    assert error.value.details["escrita_processada"] is True
    assert error.value.details["retryable"] is False
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_corrected_title_does_not_confirm_payment_with_legacy_category():
    responses = successful_responses()
    responses[6] = page(financial_record())
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_propagation_not_confirmed"
    assert error.value.details["verificacao"]["titulo_confirmado"] is True
    assert error.value.details["escrita_processada"] is True
    assert error.value.details["retryable"] is False
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_all_payments_are_checked_for_a_title_paid_in_installments():
    first = financial_record(identifier=78, value=380, discount=20)
    second = financial_record(identifier=79, value=570, discount=30)
    first_after = financial_record(identifier=78, value=380, discount=20, corrected=True)
    second_after = financial_record(identifier=79, value=570, discount=30, corrected=True)
    service = Mock()
    service.invoke_financial.side_effect = [
        title(), page(financial_record(payment=False)), page(first, second), ACK,
        title(corrected=True), page(financial_record(payment=False, corrected=True)), page(second_after, first_after),
    ]

    result = define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert result["verificacao"]["quantidade_baixas"] == 2
    assert {item["id_baixa"] for item in result["baixas"]} == {78, 79}
    assert result["verificacao"]["campos_preservados"] is True
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_partial_payment_preserves_remaining_balance():
    before, after = title(), title(corrected=True)
    before["status_titulo"] = after["status_titulo"] = "PAGTO_PARCIAL"
    before["valor_pag"] = after["valor_pag"] = 600
    cp_before = financial_record(payment=False)
    cp_after = financial_record(payment=False, corrected=True)
    bx_before = financial_record(value=380, discount=20)
    bx_after = financial_record(value=380, discount=20, corrected=True)
    for record in (cp_before, cp_after, bx_before, bx_after):
        record["detalhes"]["cStatus"] = "PAGTO_PARCIAL"
        record["resumo"] = {"cLiquidado": "N", "nValPago": 400, "nValAberto": 600,
                            "nDesconto": 20, "nJuros": 0, "nMulta": 0, "nValLiquido": 380}
    service = Mock()
    service.invoke_financial.side_effect = [before, page(cp_before), page(bx_before), ACK,
                                           after, page(cp_after), page(bx_after)]

    result = define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert result["verificacao"]["titulo_confirmado"] is True
    assert result["verificacao"]["baixas_confirmadas"] is True
    assert result["verificacao"]["campos_preservados"] is True


def test_payment_discovery_collects_every_page_before_and_after_write():
    first = financial_record(identifier=78, value=380, discount=20)
    second = financial_record(identifier=79, value=570, discount=30)
    first_after = financial_record(identifier=78, value=380, discount=20, corrected=True)
    second_after = financial_record(identifier=79, value=570, discount=30, corrected=True)
    service = Mock()
    service.invoke_financial.side_effect = [
        title(), page(financial_record(payment=False)),
        page(first, number=1, total_pages=2, total_records=2),
        page(second, number=2, total_pages=2, total_records=2), ACK,
        title(corrected=True), page(financial_record(payment=False, corrected=True)),
        page(first_after, number=1, total_pages=2, total_records=2),
        page(second_after, number=2, total_pages=2, total_records=2),
    ]

    result = define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert result["verificacao"]["quantidade_baixas"] == 2
    native_pages = [call.args[2]["params"]["nPagina"] for call in service.invoke_financial.call_args_list
                    if call.args[1] == "financial_movements_list" and call.args[2]["params"]["cTpLancamento"] == "BXCP"]
    assert native_pages == [1, 2, 1, 2]


@pytest.mark.parametrize("stage", [0, 1, 2])
@pytest.mark.parametrize("failure", [
    RuntimeError("erro sintético"),
    OmieError("omie_cooldown", "Aguardar", {"retry_after_seconds": 61, "http_status": 429, "reason": "remote_cooldown"}),
])
def test_preflight_read_failure_prevents_any_write(stage, failure):
    responses = successful_responses()[:stage] + [failure]
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert error.value.details["escrita_processada"] is False
    assert error.value.details["retryable"] is False
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


@pytest.mark.parametrize("record", [None, [], {}, {"codigo_lancamento_omie": True},
                                   {"codigo_lancamento_omie": 0}, {"codigo_lancamento_omie": 99}])
def test_preflight_requires_the_exact_title_identity(record):
    service = Mock()
    service.invoke_financial.return_value = record

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert error.value.details["escrita_processada"] is False
    assert service.invoke_financial.call_count == 1


@pytest.mark.parametrize("field,value", [("data_emissao", None), ("data_emissao", "31/02/2026"),
                                        ("valor_documento", False), ("valor_documento", -1),
                                        ("valor_documento", float("nan"))])
def test_preflight_rejects_invalid_critical_title_fields(field, value):
    record = title()
    record[field] = value
    service = Mock()
    service.invoke_financial.return_value = record

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


@pytest.mark.parametrize("field", ["codigo_cliente_fornecedor", "data_emissao", "valor_documento"])
def test_preflight_requires_critical_title_fields(field):
    record = title()
    del record[field]
    service = Mock()
    service.invoke_financial.return_value = record

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


@pytest.mark.parametrize("records", [(), (financial_record(payment=False), financial_record(payment=False))])
def test_preflight_requires_exactly_one_financial_title_record(records):
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(*records)]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


def test_paid_title_without_discoverable_payment_is_not_written():
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(financial_record(payment=False)), page()]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert error.value.details["escrita_processada"] is False
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


@pytest.mark.parametrize("field,value", [
    ("nCodTitulo", 99), ("nCodTitulo", True), ("nCodBaixa", 0), ("nCodBaixa", "78"),
    ("nCodMovCC", 0), ("nCodCC", 0), ("cNatureza", "R"),
    ("dDtPagamento", "inválida"), ("nValorMovCC", None), ("nDesconto", float("nan")),
])
def test_preflight_refuses_unverifiable_payment_identity_and_conditions(field, value):
    record = financial_record()
    record["detalhes"][field] = value
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(financial_record(payment=False)), page(record)]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert error.value.details["escrita_processada"] is False
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


def test_duplicate_payment_ids_are_not_counted_as_distinct_payments():
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(financial_record(payment=False)),
                                           page(financial_record(), financial_record())]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


@pytest.mark.parametrize("field,new_value", [
    ("nPagina", 2), ("nTotPaginas", -1), ("nRegistros", 0), ("nTotRegistros", 0),
    ("nPagina", True), ("movimentos", None), ("movimentos", [None]),
])
def test_incomplete_or_inconsistent_pagination_prevents_writing(field, new_value):
    response = page(financial_record())
    response[field] = new_value
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(financial_record(payment=False)), response]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


@pytest.mark.parametrize("field,new_value", [
    ("id_conta_corrente", 99), ("numero_documento", "OUTRO"), ("valor_pag", 1),
    ("codigo_projeto", 99), ("status_titulo", "ABERTO"), ("distribuicao", [{"cCodDep": "D1", "nPerDep": 100}]),
])
def test_post_write_changes_outside_categories_are_detected(field, new_value):
    responses = successful_responses()
    responses[4][field] = new_value
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_side_effect_detected"
    assert error.value.details["escrita_processada"] is True
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


@pytest.mark.parametrize("field,new_value", [
    ("nCodCC", 99), ("dDtPagamento", "16/11/2026"), ("dDtConcilia", ""),
    ("cHrConcilia", "13:00:00"), ("cUsConcilia", "OUTRO"), ("cNumTitulo", "OUTRO"),
])
def test_payment_and_reconciliation_changes_are_detected(field, new_value):
    responses = successful_responses()
    responses[6]["movimentos"][0]["detalhes"][field] = new_value
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_side_effect_detected"
    assert error.value.details["escrita_processada"] is True
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


@pytest.mark.parametrize("stage", [4, 5, 6])
def test_post_write_read_failure_is_inconclusive_and_never_repeats_the_write(stage):
    responses = successful_responses()[:stage]
    responses.append(OmieError("omie_cooldown", "Aguardar", {"retry_after_seconds": 61, "http_status": 429}))
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_verification_failed"
    assert error.value.details["escrita_processada"] is True
    assert error.value.details["retryable"] is False
    assert error.value.details["retry_after_seconds"] == 61
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_transport_uncertainty_is_not_retried_or_compensated():
    service = Mock()
    service.invoke_financial.side_effect = successful_responses()[:3] + [
        OmieError("outcome_unknown", "Resultado remoto desconhecido", {"retryable": False}),
    ]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "outcome_unknown"
    assert error.value.details["retryable"] is False
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_remote_pagination_counters_can_be_decimal_text():
    responses = successful_responses()
    for index in (1, 2, 5, 6):
        for field in ("nPagina", "nTotPaginas", "nRegistros", "nTotRegistros"):
            responses[index][field] = str(responses[index][field])
    service = Mock()
    service.invoke_financial.side_effect = responses

    result = define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert result["verificacao"]["baixas_confirmadas"] is True


@pytest.mark.parametrize("field,value", [
    ("resumo", None), ("resumo.cLiquidado", "X"), ("resumo.nValPago", "inválido"),
    ("resumo.nValAberto", -1), ("detalhes.nValorTitulo", 999),
    ("resumo.nValLiquido", 949), ("resumo.nValAberto", 1),
])
def test_invalid_financial_summary_prevents_any_write(field, value):
    cp = financial_record(payment=False)
    if "." in field:
        group, key = field.split(".")
        cp[group][key] = value
    else:
        cp[field] = value
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(cp)]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


@pytest.mark.parametrize("field,value", [
    ("nDesconto", -1), ("dDtConcilia", "31/02/2026"), ("cUsConcilia", ""),
])
def test_invalid_payment_amount_or_reconciliation_is_not_assumed_preservable(field, value):
    bx = financial_record()
    bx["detalhes"][field] = value
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(financial_record(payment=False)), page(bx)]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


def test_paid_summary_with_zero_net_and_no_payment_is_rejected():
    cp = financial_record(payment=False)
    cp["resumo"].update(nValPago=0, nValLiquido=0, nDesconto=0)
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(cp), page()]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert error.value.details["reason"] == "paid_title_without_payments"


@pytest.mark.parametrize("second_page", [
    page(financial_record(identifier=79), number=2, total_pages=3, total_records=2),
    page(financial_record(identifier=79), number=2, total_pages=2, total_records=1),
    page(financial_record(identifier=79), number=2, total_pages=2, total_records=3),
])
def test_changing_or_incomplete_multi_page_population_is_rejected(second_page):
    total = second_page["nTotRegistros"]
    first_page = page(financial_record(), total_pages=2, total_records=total)
    if total == 2:
        first_page["nTotPaginas"] = 2
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(financial_record(payment=False)), first_page, second_page]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


def test_non_object_movement_page_is_rejected():
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(financial_record(payment=False)), None]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"


@pytest.mark.parametrize("categories", [
    None, [], [None], [{"codigo_categoria": "", "percentual": 100, "valor": 1000}],
    [{"codigo_categoria": "2.01.03", "percentual": 0, "valor": 0}],
    [{"codigo_categoria": "2.01.03", "percentual": 100, "valor": 999}],
    [{"codigo_categoria": "2.01.03", "percentual": 50, "valor": 500},
     {"codigo_categoria": "2.07.01", "percentual": 49, "valor": 490}],
    [{"codigo_categoria": "2.01.03", "percentual": 50, "valor": 499.99},
     {"codigo_categoria": "2.07.01", "percentual": 50, "valor": 499.99}],
])
def test_unverifiable_post_write_allocation_is_never_reported_as_confirmed(categories):
    responses = successful_responses()
    responses[4]["categorias"] = categories
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES, include_native=True)

    assert error.value.code == "category_allocation_verification_failed"
    assert error.value.details["escrita_processada"] is True
    assert error.value.details["requisicao_omie"]["call"] == "AlterarContaPagar"
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


@pytest.mark.parametrize("identifier", [None, {}, {"id": 34}, {"codigo_lancamento_omie": 34, "codigo_lancamento_integracao": "TEST"},
                                       {"codigo_lancamento_omie": True}, {"codigo_lancamento_omie": 0},
                                       {"codigo_lancamento_integracao": ""}, {"codigo_lancamento_integracao": "A" * 61}])
def test_invalid_native_identifier_is_rejected_before_reading(identifier):
    service = Mock()
    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", identifier, CATEGORIES)
    assert error.value.code == "invalid_parameter"
    service.invoke_financial.assert_not_called()


@pytest.mark.parametrize("categories", [None, {}, [None], [{"codigo": "2.01.03", "percentual": 100}],
                                       [{"codigo_categoria": "2.01.03", "percentual": 100, "valor": 1000}]])
def test_invalid_native_categories_are_rejected_before_reading(categories):
    service = Mock()
    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, categories)
    assert error.value.code == "invalid_parameter"
    service.invoke_financial.assert_not_called()


def test_native_audit_flag_requires_a_boolean():
    service = Mock()
    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES, include_native=1)
    assert error.value.code == "invalid_parameter"
    service.invoke_financial.assert_not_called()


@pytest.mark.parametrize("ack", [None, {}, {"codigo_status": "1"}, {"codigo_status": "0", "codigo_lancamento_omie": 99}])
def test_invalid_or_mismatched_acknowledgement_requires_manual_readback(ack):
    service = Mock()
    service.invoke_financial.side_effect = successful_responses()[:3] + [ack]
    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)
    assert error.value.code == "outcome_unknown"
    assert error.value.details["retryable"] is False
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_unexpected_write_exception_does_not_repeat_payment_or_title_update():
    service = Mock()
    service.invoke_financial.side_effect = successful_responses()[:3] + [RuntimeError("Falha sintética")]
    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)
    assert error.value.code == "outcome_unknown"
    assert error.value.details["retryable"] is False
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_changed_financial_balance_is_not_hidden_by_matching_categories():
    responses = successful_responses()
    responses[5]["movimentos"][0]["resumo"]["nValAberto"] = 1
    responses[5]["movimentos"][0]["resumo"]["cLiquidado"] = "N"
    service = Mock()
    service.invoke_financial.side_effect = responses
    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)
    assert error.value.code == "category_allocation_side_effect_detected"
    assert "resumo_financeiro" in error.value.details["campos_alterados"]


def test_payment_identity_changes_are_detected_even_when_amounts_are_unchanged():
    responses = successful_responses()
    responses[6]["movimentos"][0]["detalhes"]["nCodBaixa"] = 79
    service = Mock()
    service.invoke_financial.side_effect = responses
    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)
    assert error.value.code == "category_allocation_side_effect_detected"
    assert "identificadores_baixas" in error.value.details["campos_alterados"]


def test_boolean_financial_condition_is_not_equivalent_to_a_number():
    responses = successful_responses()
    responses[0]["bloqueado"] = True
    responses[4]["bloqueado"] = 1
    service = Mock()
    service.invoke_financial.side_effect = responses
    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)
    assert error.value.code == "category_allocation_side_effect_detected"


def test_unpaid_title_can_be_corrected_with_no_existing_payments():
    responses = successful_responses()
    for index in (0, 4):
        responses[index]["status_titulo"] = "ABERTO"
        responses[index]["valor_pag"] = 1000
    for index in (1, 5):
        responses[index]["movimentos"][0]["resumo"] = {
            "cLiquidado": "N", "nValPago": 0, "nValLiquido": 0, "nValAberto": 1000,
            "nDesconto": 0, "nJuros": 0, "nMulta": 0,
        }
    responses[2] = page()
    responses[6] = page()
    service = Mock()
    service.invoke_financial.side_effect = responses

    result = define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert result["baixas"] == []
    assert result["verificacao"]["quantidade_baixas"] == 0
    assert result["verificacao"]["campos_preservados"] is True


def test_legacy_scalar_category_is_not_hidden_by_corrected_distribution():
    responses = successful_responses()
    responses[4]["codigo_categoria"] = "2.07.01"
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_not_persisted"
    assert error.value.details["verificacao"]["titulo_confirmado"] is False
    assert error.value.details["verificacao"]["baixas_confirmadas"] is True


def test_legacy_category_in_financial_title_is_reported_separately():
    responses = successful_responses()
    responses[5] = page(financial_record(payment=False))
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_propagation_not_confirmed"
    flags = error.value.details["verificacao"]
    assert flags["titulo_confirmado"] is True
    assert flags["titulo_financeiro_confirmado"] is False
    assert flags["baixas_confirmadas"] is True


def test_missing_reconciliation_state_cannot_confirm_preservation():
    bx = financial_record()
    del bx["detalhes"]["dDtConcilia"]
    service = Mock()
    service.invoke_financial.side_effect = [title(), page(financial_record(payment=False)), page(bx)]

    with pytest.raises(OmieError) as error:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert error.value.code == "category_allocation_preflight_failed"
    assert error.value.details["reason"] == "missing_reconciliation_state"
    assert all(not call.kwargs["writes"] for call in service.invoke_financial.call_args_list)


@pytest.mark.parametrize("requested_second_percentage", [40, 39.999999])
def test_complete_multi_category_distribution_accepts_order_and_percentage_tolerance(requested_second_percentage):
    """Percentuais observados podem arredondar até 0,000001 sem alterar os valores auditados."""
    requested = [
        {"codigo_categoria": "2.01.03", "percentual": 60},
        {"codigo_categoria": "2.09.99", "percentual": requested_second_percentage},
    ]
    responses = successful_responses()
    responses[4]["categorias"] = [
        {"codigo_categoria": "2.09.99", "percentual": 40, "valor": 400},
        {"codigo_categoria": "2.01.03", "percentual": 60, "valor": 600},
    ]
    responses[5]["movimentos"][0]["categorias"] = [
        {"cCodCateg": "2.09.99", "nDistrPercentual": 40, "nDistrValor": 400},
        {"cCodCateg": "2.01.03", "nDistrPercentual": 60, "nDistrValor": 600},
    ]
    responses[6]["movimentos"][0]["categorias"] = [
        {"cCodCateg": "2.09.99", "nDistrPercentual": 40, "nDistrValor": 380},
        {"cCodCateg": "2.01.03", "nDistrPercentual": 60, "nDistrValor": 570},
    ]
    service = Mock()
    service.invoke_financial.side_effect = responses

    result = define_categories(service, "empresa", IDENTIFIER, requested)

    assert result["verificacao"]["titulo_confirmado"] is True
    assert result["verificacao"]["titulo_financeiro_confirmado"] is True
    assert result["verificacao"]["baixas_confirmadas"] is True
    assert result["verificacao"]["campos_preservados"] is True
    assert {row["codigo"]: row["valor"] for row in result["categorias"]} == {"2.01.03": 600, "2.09.99": 400}
    assert {row["codigo"]: row["valor"] for row in result["baixas"][0]["categorias"]} == {"2.01.03": 570, "2.09.99": 380}
    writes = [call for call in service.invoke_financial.call_args_list if call.kwargs["writes"]]
    assert len(writes) == 1
    assert writes[0].args[2] == {"body": {"codigo_lancamento_omie": 34, "categorias": requested}}
    assert "codigo_categoria" not in writes[0].args[2]["body"]


def test_negative_debit_preserves_its_sign_and_checks_allocation_by_absolute_value():
    responses = successful_responses()
    responses[2]["movimentos"][0]["detalhes"]["nValorMovCC"] = -950
    responses[6]["movimentos"][0]["detalhes"]["nValorMovCC"] = -950
    service = Mock()
    service.invoke_financial.side_effect = responses

    result = define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert result["baixas"][0]["valor"] == -950
    assert result["baixas"][0]["categorias"] == [{"codigo": "2.01.03", "percentual": 100, "valor": 950}]
    assert result["verificacao"]["campos_preservados"] is True


@pytest.mark.parametrize("group,index,operation,kind", [
    ("title", 4, "payables_get", None),
    ("CP", 5, "financial_movements_list", "CP"),
    ("BXCP", 6, "financial_movements_list", "BXCP"),
])
@pytest.mark.parametrize("collection", [None, [], "omitida"])
@pytest.mark.parametrize("include_native", [False, True])
def test_missing_category_collection_keeps_its_origin_and_still_checks_financial_preservation(group, index, operation, kind, collection, include_native):
    """Coleção ausente não impede a leitura posterior das condições financeiras."""
    responses = successful_responses()
    record = responses[index] if group == "title" else responses[index]["movimentos"][0]
    if collection == "omitida":
        del record["categorias"]
    else:
        record["categorias"] = collection
    original = deepcopy(responses)
    if group == "BXCP":
        failure = OmieError("omie_http_error", "Consulta do lançamento indisponível", {"http_status": 503})
        responses.append(failure)
        original.append(failure)
    service = Mock()
    service.invoke_financial.side_effect = responses

    if group == "CP":
        result = define_categories(service, "empresa", IDENTIFIER, CATEGORIES, include_native=include_native)
        assert result["verificacao"]["titulo_confirmado"] is True
        assert result["verificacao"]["titulo_financeiro_confirmado"] is False
        assert result["verificacao"]["categorias_financeiro_disponiveis"] is False
        assert result["verificacao"]["baixas_confirmadas"] is True
        assert result["verificacao"]["campos_preservados"] is True
        assert result["categorias_financeiro"] is None
        assert responses == original
        assert len(service.invoke_financial.call_args_list) == 7
        assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1
        assert ("dados_omie" in result) is include_native
        if include_native:
            assert result["dados_omie"]["financeiro"] == original[5]["movimentos"][0]
        return

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES, include_native=include_native)

    error = raised.value
    assert error.code == "category_allocation_verification_failed"
    assert error.details["reason"] == ("query_failed" if group == "BXCP" else "missing_categories")
    if group != "BXCP":
        assert error.details["field"] == "categorias"
    assert error.details["operation"] == ("account_transactions_get" if group == "BXCP" else operation)
    if kind is None:
        assert "tipo_movimento" not in error.details
    else:
        assert error.details["tipo_movimento"] == kind
    if group == "BXCP":
        assert error.details["id_baixa"] == 78
    else:
        assert "id_baixa" not in error.details
    assert error.details["escrita_processada"] is True
    assert error.details["retryable"] is False
    assert error.details["verificacao"]["campos_preservados"] is True
    assert len(service.invoke_financial.call_args_list) == (8 if group == "BXCP" else 7)
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1
    assert responses == original
    assert ("dados_omie" in error.details) is include_native
    if include_native:
        audit = error.details["dados_omie"]
        assert audit["registro"] == original[4]
        assert audit["financeiro"] == original[5]["movimentos"][0]
        assert audit["baixas"] == original[6]["movimentos"]
        assert error.details["requisicao_omie"]["call"] == "AlterarContaPagar"


def test_missing_collections_are_reported_together_without_claiming_any_category_confirmation():
    responses = successful_responses()
    del responses[4]["categorias"]
    responses[5]["movimentos"][0]["categorias"] = []
    responses[6]["movimentos"][0]["categorias"] = None
    responses.append(OmieError("omie_http_error", "Consulta do lançamento indisponível", {"http_status": 503}))
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES, include_native=True)

    details = raised.value.details
    assert raised.value.code == "category_allocation_verification_failed"
    failures = details["falhas_conferencia"]
    assert len(failures) == 2
    assert all(failure["code"] == "category_allocation_verification_failed" for failure in failures)
    assert [(failure["details"]["operation"], failure["details"].get("tipo_movimento")) for failure in failures] == [
        ("payables_get", None), ("account_transactions_get", "BXCP"),
    ]
    assert failures[1]["details"]["id_baixa"] == 78
    assert details["categorias_titulo"] is None
    assert details["categorias_financeiro"] is None
    assert details["baixas"][0]["categorias"] is None
    assert details["verificacao"] == {
        "titulo_confirmado": False, "titulo_financeiro_confirmado": False, "categorias_financeiro_disponiveis": False,
        "baixas_confirmadas": False, "campos_preservados": True,
    }
    assert len(service.invoke_financial.call_args_list) == 8
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


@pytest.mark.parametrize("changed_condition", ["reconciliation", "discount"])
def test_missing_title_categories_do_not_hide_changed_payment_conditions(changed_condition):
    """A preservação tem precedência sobre a classificação inconclusiva do título."""
    responses = successful_responses()
    del responses[4]["categorias"]
    payment = responses[6]["movimentos"][0]
    if changed_condition == "reconciliation":
        payment["detalhes"]["dDtConcilia"] = "18/11/2026"
    else:
        for index in (5, 6):
            responses[index]["movimentos"][0]["resumo"].update(nDesconto=25, nValLiquido=975)
        payment["detalhes"].update(nDesconto=25, nValorMovCC=975)
        payment["categorias"][0]["nDistrValor"] = 975
    original = deepcopy(responses)
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES, include_native=True)

    assert raised.value.code == "category_allocation_side_effect_detected"
    details = raised.value.details
    assert details["verificacao"]["campos_preservados"] is False
    assert details["verificacao"]["titulo_confirmado"] is False
    assert "baixa:78" in details["campos_alterados"]
    assert details["falhas_conferencia"][0]["details"]["operation"] == "payables_get"
    assert details["dados_omie"]["registro"] == original[4]
    assert details["dados_omie"]["baixas"] == original[6]["movimentos"]
    assert responses == original
    assert len(service.invoke_financial.call_args_list) == 7
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_observed_legacy_title_takes_precedence_over_missing_financial_categories():
    """A divergência comprovada no título não vira somente uma falha de leitura CP."""
    responses = successful_responses()
    responses[4] = title()
    del responses[5]["movimentos"][0]["categorias"]
    responses[6] = page(financial_record())
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    details = raised.value.details
    assert raised.value.code == "category_allocation_not_persisted"
    assert details["categorias_titulo"] == [
        {"codigo": "2.01.03", "percentual": 80, "valor": 800},
        {"codigo": "2.07.01", "percentual": 20, "valor": 200},
    ]
    assert details["categorias_financeiro"] is None
    assert details["baixas"][0]["categorias"][1]["codigo"] == "2.07.01"
    assert not details.get("falhas_conferencia")
    assert details["verificacao"]["categorias_financeiro_disponiveis"] is False
    assert details["verificacao"]["campos_preservados"] is True
    assert len(service.invoke_financial.call_args_list) == 7
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_observed_legacy_financial_title_takes_precedence_over_missing_payment_categories():
    responses = successful_responses()
    responses[5] = page(financial_record(payment=False))
    del responses[6]["movimentos"][0]["categorias"]
    responses.append(OmieError("omie_http_error", "Consulta do lançamento indisponível", {"http_status": 503}))
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    details = raised.value.details
    assert raised.value.code == "category_allocation_propagation_not_confirmed"
    assert details["verificacao"]["titulo_confirmado"] is True
    assert details["verificacao"]["titulo_financeiro_confirmado"] is False
    assert details["verificacao"]["baixas_confirmadas"] is False
    assert details["verificacao"]["campos_preservados"] is True
    assert details["categorias_financeiro"][1]["codigo"] == "2.07.01"
    assert details["baixas"][0]["categorias"] is None
    assert details["falhas_conferencia"][0]["details"]["id_baixa"] == 78
    assert len(service.invoke_financial.call_args_list) == 8
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_missing_title_categories_do_not_hide_observed_legacy_categories_in_movements():
    responses = successful_responses()
    del responses[4]["categorias"]
    responses[5] = page(financial_record(payment=False))
    responses[6] = page(financial_record())
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    details = raised.value.details
    assert raised.value.code == "category_allocation_verification_failed"
    assert details["categorias_titulo"] is None
    assert details["categorias_financeiro"][1]["codigo"] == "2.07.01"
    assert details["baixas"][0]["categorias"][1]["codigo"] == "2.07.01"
    assert details["verificacao"]["titulo_confirmado"] is False
    assert details["verificacao"]["campos_preservados"] is True
    assert len(service.invoke_financial.call_args_list) == 7


def test_one_unreadable_payment_category_does_not_prevent_checking_other_payments():
    first_before = financial_record(identifier=78, value=380, discount=20)
    second_before = financial_record(identifier=79, value=570, discount=30)
    first_after = financial_record(identifier=78, value=380, discount=20, corrected=True)
    second_after = financial_record(identifier=79, value=570, discount=30)
    del first_after["categorias"]
    responses = [title(), page(financial_record(payment=False)), page(first_before, second_before), ACK,
                 title(corrected=True), page(financial_record(payment=False, corrected=True)), page(first_after, second_after)]
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    details = raised.value.details
    assert raised.value.code == "category_allocation_propagation_not_confirmed"
    assert details["baixas"][0]["id_baixa"] == 78
    assert details["baixas"][0]["categorias"] is None
    assert details["baixas"][1]["id_baixa"] == 79
    assert details["baixas"][1]["categorias"][1]["codigo"] == "2.07.01"
    assert details["falhas_conferencia"][0]["details"]["id_baixa"] == 78
    assert details["verificacao"]["titulo_confirmado"] is True
    assert details["verificacao"]["campos_preservados"] is True
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_post_write_payment_query_failure_keeps_the_title_and_financial_record_already_observed():
    """Auditoria parcial distingue o CP consultado da listagem de baixas que falhou."""
    responses = successful_responses()
    observed_title = deepcopy(responses[4])
    observed_financial = deepcopy(responses[5]["movimentos"][0])
    responses[6] = OmieError("omie_http_error", "Falha HTTP sintética", {"http_status": 503})
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES, include_native=True)

    details = raised.value.details
    assert raised.value.code == "category_allocation_verification_failed"
    assert details["reason"] == "query_failed"
    assert details["cause_code"] == "omie_http_error"
    assert details["http_status"] == 503
    assert details["escrita_processada"] is True
    assert details["verificacao"]["campos_preservados"] is False
    assert details["verificacao"]["baixas_confirmadas"] is False
    assert details["dados_omie"] == {"registro": observed_title, "financeiro": observed_financial}
    assert "baixas" not in details["dados_omie"]
    assert len(service.invoke_financial.call_args_list) == 7
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


def test_post_write_invalid_payment_number_keeps_all_observed_native_records_without_claiming_preservation():
    """Uma baixa identificada permanece auditável mesmo quando seu valor não é válido."""
    responses = successful_responses()
    responses[6]["movimentos"][0]["detalhes"]["nValorMovCC"] = None
    original = deepcopy(responses)
    service = Mock()
    service.invoke_financial.side_effect = responses

    with pytest.raises(OmieError) as raised:
        define_categories(service, "empresa", IDENTIFIER, CATEGORIES, include_native=True)

    details = raised.value.details
    assert raised.value.code == "category_allocation_verification_failed"
    assert details["reason"] == "invalid_number"
    assert details["field"] == "detalhes.nValorMovCC"
    assert details["tipo_movimento"] == "BXCP"
    assert details["number_issue"] == "null"
    assert details["escrita_processada"] is True
    assert details["verificacao"]["campos_preservados"] is False
    assert details["verificacao"]["baixas_confirmadas"] is False
    assert details["dados_omie"] == {
        "registro": original[4], "financeiro": original[5]["movimentos"][0],
        "baixas": original[6]["movimentos"],
    }
    assert responses == original
    assert len(service.invoke_financial.call_args_list) == 7
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1
