"""Conferência de categorias por fontes vinculadas, sem repetir gravações."""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.omie import category_allocation as allocation
from onmyoji_daemon.mcp.omie.exception import OmieError


IDENTIFIER = {"codigo_lancamento_omie": 34}
CATEGORIES = [{"codigo_categoria": "2.01.03", "percentual": 100}]
MISSING = object()


def title(*, corrected=True):
    return {
        "codigo_lancamento_omie": 34, "codigo_lancamento_integracao": "TEST-TITLE-34",
        "codigo_cliente_fornecedor": 90, "valor_documento": 1000,
        "data_emissao": "01/11/2026", "data_vencimento": "17/11/2026",
        "codigo_categoria": "2.01.03", "status_titulo": "PAGO",
        "categorias": ([{"codigo_categoria": "2.01.03", "percentual": 100, "valor": 1000}]
                       if corrected else [{"codigo_categoria": "2.01.03", "percentual": 80, "valor": 800},
                                          {"codigo_categoria": "2.07.01", "percentual": 20, "valor": 200}]),
    }


def movement(*, payment, categories=MISSING):
    record = {
        "detalhes": {
            "nCodTitulo": 34, "cNatureza": "P", "nValorTitulo": 1000,
            "nCodBaixa": 78 if payment else 0, "nCodMovCC": 178 if payment else 0,
            "nCodCC": 56, "dDtPagamento": "17/11/2026" if payment else "",
            "nValorMovCC": 950 if payment else 0, "nDesconto": 50 if payment else 0,
            "nJuros": 0, "nMulta": 0,
            "dDtConcilia": "17/11/2026" if payment else "",
            "cHrConcilia": "12:34:56" if payment else "", "cUsConcilia": "TESTE" if payment else "",
        },
        "resumo": {"cLiquidado": "S", "nValPago": 1000, "nValLiquido": 950,
                   "nValAberto": 0, "nDesconto": 50, "nJuros": 0, "nMulta": 0},
    }
    if categories is not MISSING:
        record["categorias"] = deepcopy(categories)
    return record


def account(*, corrected=True, categories=MISSING):
    """A data de lançamento pode diferir do pagamento sem romper o vínculo por IDs."""
    record = {
        "nCodLanc": 178, "cCodIntLanc": "TEST-MOV-178",
        "cabecalho": {"nCodCC": 56, "nValorLanc": 950, "dDtLanc": "18/11/2026"},
        "detalhes": {"cCodCateg": "2.01.03", "cTipo": "TRA"},
        "diversos": {"nCodLancCP": 34, "nCodLancCR": 0, "cNatureza": "P", "cOrigem": "NFEP",
                     "dDtConc": "17/11/2026", "cHrConc": "12:34:56", "cUsConc": "TESTE"},
    }
    if categories is MISSING:
        record["detalhes"]["aCodCateg"] = (
            [{"cCodCateg": "2.01.03", "nValor": 950}]
            if corrected else [{"cCodCateg": "2.01.03", "nValor": 760}, {"cCodCateg": "2.07.01", "nValor": 190}]
        )
    else:
        record["detalhes"]["aCodCateg"] = deepcopy(categories)
    return record


def page(*records):
    return {"nPagina": 1, "nTotPaginas": 1, "nRegistros": len(records),
            "nTotRegistros": len(records), "movimentos": list(records)}


def read_responses(*, corrected=True, cp_categories=MISSING, bx_categories=MISSING):
    return [title(corrected=corrected), page(movement(payment=False, categories=cp_categories)),
            page(movement(payment=True, categories=bx_categories)), account(corrected=corrected)]


def check(records, *, expected=CATEGORIES, include_native=False, identifier=IDENTIFIER):
    service = Mock()
    service.invoke_financial.side_effect = records
    result = allocation.check_categories(service, "empresa", identifier, expected, include_native=include_native)
    return result, service


def assert_only_fresh_reads(service):
    assert all(call.kwargs["writes"] is False for call in service.invoke_financial.call_args_list)
    assert all(call.args[2]["atualizar_cache"] is True for call in service.invoke_financial.call_args_list)


def has_native_record(value, expected):
    """A localização da auditoria pode variar, mas o registro nativo deve permanecer intacto."""
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(has_native_record(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(has_native_record(child, expected) for child in value)
    return False


@pytest.mark.parametrize("cp_categories", [MISSING, None, []], ids=["missing", "null", "empty"])
@pytest.mark.parametrize("bx_categories", [MISSING, None, []], ids=["missing", "null", "empty"])
@pytest.mark.parametrize("include_native", [False, True])
def test_missing_movement_categories_are_checked_from_the_linked_account_entry(cp_categories, bx_categories, include_native):
    records = read_responses(cp_categories=cp_categories, bx_categories=bx_categories)
    original = deepcopy(records)

    result, service = check(records, include_native=include_native)

    assert result["conforme_esperado"] is True
    assert result["alvo"] == {"recurso": "contas_pagar", "id": 34}
    flags = result["verificacao"]
    assert flags["titulo_confirmado"] is True
    assert flags["titulo_financeiro_confirmado"] is False
    assert flags["categorias_financeiro_disponiveis"] is False
    assert flags["baixas_confirmadas"] is True
    assert "campos_preservados" not in flags
    assert result["categorias_financeiro"] is None
    assert result["falhas_conferencia"] == []
    payment = result["baixas"][0]
    assert payment["id_baixa"] == 78
    assert payment["lancamento_conta_id"] == 178
    assert payment["fonte_categorias"] == "account_transactions_get"
    assert payment["categorias"] == [{"codigo": "2.01.03", "valor": 950}]
    assert payment["desconto"] == 50
    assert payment["conciliacao"]["conciliado"] is True
    calls = service.invoke_financial.call_args_list
    assert [call.args[1] for call in calls] == ["payables_get", "financial_movements_list", "financial_movements_list", "account_transactions_get"]
    assert calls[-1].args[2]["params"] == {"nCodLanc": 178}
    assert_only_fresh_reads(service)
    assert records == original
    assert ("dados_omie" in result) is include_native
    if include_native:
        assert has_native_record(result["dados_omie"], records[-1])


def test_available_movement_categories_do_not_require_an_account_fallback():
    cp = [{"cCodCateg": "2.01.03", "nDistrPercentual": 100, "nDistrValor": 1000}]
    bx = [{"cCodCateg": "2.01.03", "nDistrPercentual": 100, "nDistrValor": 950}]
    records = read_responses(cp_categories=cp, bx_categories=bx)[:3]

    result, service = check(records)

    assert result["conforme_esperado"] is True
    assert result["verificacao"]["titulo_financeiro_confirmado"] is True
    assert result["verificacao"]["categorias_financeiro_disponiveis"] is True
    assert result["baixas"][0]["fonte_categorias"] == "financial_movements_list"
    assert len(service.invoke_financial.call_args_list) == 3
    assert_only_fresh_reads(service)


def test_read_only_check_reports_legacy_distributions_without_attempting_a_write():
    records = read_responses(corrected=False)

    result, service = check(records)

    assert result["conforme_esperado"] is False
    assert result["verificacao"]["titulo_confirmado"] is False
    assert result["verificacao"]["baixas_confirmadas"] is False
    assert {row["codigo"] for row in result["categorias"]} == {"2.01.03", "2.07.01"}
    assert {row["codigo"] for row in result["baixas"][0]["categorias"]} == {"2.01.03", "2.07.01"}
    assert_only_fresh_reads(service)


def test_value_only_account_categories_are_compared_with_monetary_rounding():
    expected = [{"codigo_categoria": "CAT-A", "percentual": 33.33}, {"codigo_categoria": "CAT-B", "percentual": 66.67}]
    records = read_responses()
    records[0]["categorias"] = [{"codigo_categoria": "CAT-A", "percentual": 33.33, "valor": 333.30},
                                {"codigo_categoria": "CAT-B", "percentual": 66.67, "valor": 666.70}]
    records[-1]["detalhes"]["aCodCateg"] = [{"cCodCateg": "CAT-B", "nValor": 633.36}, {"cCodCateg": "CAT-A", "nValor": 316.64}]

    result, service = check(records, expected=expected)

    assert result["conforme_esperado"] is True
    assert result["verificacao"]["baixas_confirmadas"] is True
    assert all("percentual" not in row for row in result["baixas"][0]["categorias"])
    assert_only_fresh_reads(service)


def test_percentage_only_account_categories_remain_verifiable():
    records = read_responses()
    records[-1]["detalhes"]["aCodCateg"] = [{"cCodCateg": "2.01.03", "nPerc": 100}]

    result, service = check(records)

    assert result["conforme_esperado"] is True
    assert result["baixas"][0]["categorias"] == [{"codigo": "2.01.03", "percentual": 100}]
    assert_only_fresh_reads(service)


def test_every_payment_uses_its_own_linked_entry_when_movement_categories_are_omitted():
    first = movement(payment=True)
    second = movement(payment=True)
    first["detalhes"].update(nValorMovCC=380, nDesconto=20)
    second["detalhes"].update(nCodBaixa=79, nCodMovCC=179, nValorMovCC=570, nDesconto=30)
    first_account = account()
    first_account["cabecalho"]["nValorLanc"] = 380
    first_account["detalhes"]["aCodCateg"][0]["nValor"] = 380
    second_account = account()
    second_account["nCodLanc"] = 179
    second_account["cabecalho"]["nValorLanc"] = 570
    second_account["detalhes"]["aCodCateg"][0]["nValor"] = 570
    records = [title(), page(movement(payment=False)), page(first, second), first_account, second_account]

    result, service = check(records)

    assert result["conforme_esperado"] is True
    assert {row["id_baixa"] for row in result["baixas"]} == {78, 79}
    assert {row["lancamento_conta_id"] for row in result["baixas"]} == {178, 179}
    linked_calls = [call for call in service.invoke_financial.call_args_list if call.args[1] == "account_transactions_get"]
    assert [call.args[2]["params"] for call in linked_calls] == [{"nCodLanc": 178}, {"nCodLanc": 179}]
    assert_only_fresh_reads(service)


@pytest.mark.parametrize("field,value", [
    ("nCodLanc", 179), ("nCodLanc", "178"), ("nCodLanc", True),
    ("diversos.nCodLancCP", 35), ("diversos.nCodLancCP", "34"),
    ("diversos.nCodLancCR", 45), ("cabecalho.nCodCC", 57),
    ("diversos.cNatureza", "R"),
    ("cabecalho.nValorLanc", 949), ("cabecalho.nValorLanc", None),
    ("cabecalho.dDtLanc", "31/02/2026"),
    ("diversos.dDtConc", "18/11/2026"),
    ("diversos.cHrConc", "12:34:57"), ("diversos.cUsConc", "OUTRO"),
])
def test_account_fallback_rejects_unproven_identity_or_conflicting_financial_conditions(field, value):
    records = read_responses()
    path = field.split(".")
    parent = records[-1] if len(path) == 1 else records[-1][path[0]]
    parent[path[-1]] = value

    result, service = check(records)

    assert result["conforme_esperado"] is False
    assert result["verificacao"]["baixas_confirmadas"] is False
    assert result["baixas"][0]["categorias"] is None
    assert result["falhas_conferencia"]
    assert_only_fresh_reads(service)


@pytest.mark.parametrize("field", ["nCodLanc", "cabecalho.nCodCC", "cabecalho.nValorLanc", "cabecalho.dDtLanc", "diversos.nCodLancCP"])
def test_account_fallback_requires_its_essential_link_fields(field):
    records = read_responses()
    path = field.split(".")
    parent = records[-1] if len(path) == 1 else records[-1][path[0]]
    del parent[path[-1]]

    result, service = check(records)

    assert result["conforme_esperado"] is False
    assert result["baixas"][0]["categorias"] is None
    assert result["falhas_conferencia"]
    assert_only_fresh_reads(service)


def test_absent_account_reconciliation_fields_do_not_invent_additional_evidence():
    records = read_responses()
    for key in ("dDtConc", "cHrConc", "cUsConc"):
        del records[-1]["diversos"][key]

    result, service = check(records)

    assert result["conforme_esperado"] is True
    assert result["baixas"][0]["conciliacao"] == {
        "conciliado": True, "data": "17/11/2026", "hora": "12:34:56", "usuario": "TESTE",
    }
    assert_only_fresh_reads(service)


@pytest.mark.parametrize("categories", [None, [], {}, [{"cCodCateg": "2.01.03", "nValor": None}],
                                       [None], [{"cCodCateg": "2.01.03", "nPerc": 0}],
                                       [{"cCodCateg": "2.01.03"}],
                                       [{"cCodCateg": "2.01.03", "nPerc": 99}],
                                       [{"cCodCateg": "CAT-A", "nValor": 475}, {"cCodCateg": "CAT-B", "nPerc": 50}],
                                       [{"cCodCateg": "2.01.03", "nValor": 949}],
                                       [{"cCodCateg": "2.01.03", "nValor": 950, "nPerc": 99}],
                                       [{"cCodCateg": "2.01.03", "nValor": 475}, {"cCodCateg": "2.01.03", "nValor": 475}]])
def test_invalid_account_category_arrays_are_not_replaced_by_the_scalar_category(categories):
    records = read_responses()
    records[-1]["detalhes"]["aCodCateg"] = categories

    result, service = check(records)

    assert result["conforme_esperado"] is False
    assert result["verificacao"]["baixas_confirmadas"] is False
    assert result["falhas_conferencia"]
    assert_only_fresh_reads(service)


@pytest.mark.parametrize("group", ["cabecalho", "diversos", "detalhes"])
def test_non_object_account_sections_cannot_establish_a_link(group):
    records = read_responses()
    records[-1][group] = None

    result, service = check(records)

    assert result["conforme_esperado"] is False
    assert result["falhas_conferencia"]
    assert result["baixas"][0]["categorias"] is None
    assert_only_fresh_reads(service)


@pytest.mark.parametrize("values_only", [False, True], ids=["percentage", "amount"])
def test_matching_category_codes_do_not_hide_an_incorrect_distribution(values_only):
    expected = [{"codigo_categoria": "CAT-A", "percentual": 60}, {"codigo_categoria": "CAT-B", "percentual": 40}]
    records = read_responses()
    records[0]["categorias"] = [{"codigo_categoria": "CAT-A", "percentual": 60, "valor": 600},
                                {"codigo_categoria": "CAT-B", "percentual": 40, "valor": 400}]
    records[-1]["detalhes"]["aCodCateg"] = (
        [{"cCodCateg": "CAT-A", "nValor": 380}, {"cCodCateg": "CAT-B", "nValor": 570}]
        if values_only else [{"cCodCateg": "CAT-A", "nPerc": 40}, {"cCodCateg": "CAT-B", "nPerc": 60}]
    )

    result, service = check(records, expected=expected)

    assert result["verificacao"]["titulo_confirmado"] is True
    assert result["verificacao"]["baixas_confirmadas"] is False
    assert result["conforme_esperado"] is False
    assert_only_fresh_reads(service)


def test_zero_value_only_categories_do_not_prove_a_multiple_percentage_distribution():
    expected = [{"codigo_categoria": "CAT-A", "percentual": 60}, {"codigo_categoria": "CAT-B", "percentual": 40}]
    records = read_responses()
    records[0]["categorias"] = [{"codigo_categoria": "CAT-A", "percentual": 60, "valor": 600},
                                {"codigo_categoria": "CAT-B", "percentual": 40, "valor": 400}]
    for index in (1, 2):
        row = records[index]["movimentos"][0]
        row["resumo"].update(nValLiquido=0, nDesconto=1000)
    records[2]["movimentos"][0]["detalhes"].update(nValorMovCC=0, nDesconto=1000)
    records[-1]["cabecalho"]["nValorLanc"] = 0
    records[-1]["detalhes"]["aCodCateg"] = [{"cCodCateg": "CAT-A", "nValor": 0}, {"cCodCateg": "CAT-B", "nValor": 0}]

    result, service = check(records, expected=expected)

    assert result["verificacao"]["titulo_confirmado"] is True
    assert result["verificacao"]["baixas_confirmadas"] is False
    assert result["conforme_esperado"] is False
    assert_only_fresh_reads(service)


@pytest.mark.parametrize("group,operation", [("title", "payables_get"), ("CP", "financial_movements_list")])
def test_read_only_check_collects_category_errors_and_still_reads_the_linked_payment(group, operation):
    records = read_responses()
    record = records[0] if group == "title" else records[1]["movimentos"][0]
    if group == "title":
        del record["categorias"]
    else:
        record["categorias"] = {"invalida": True}

    result, service = check(records)

    assert result["conforme_esperado"] is False
    assert result["falhas_conferencia"][0]["details"]["operation"] == operation
    assert result["verificacao"]["baixas_confirmadas"] is True
    assert service.invoke_financial.call_args_list[-1].args[1] == "account_transactions_get"
    assert_only_fresh_reads(service)


def test_missing_account_category_array_is_not_inferred_from_a_matching_scalar():
    records = read_responses()
    del records[-1]["detalhes"]["aCodCateg"]

    result, service = check(records)

    assert result["conforme_esperado"] is False
    assert result["baixas"][0]["categorias"] is None
    assert result["falhas_conferencia"]
    assert_only_fresh_reads(service)


@pytest.mark.parametrize("invalid_categories", [{"category": "2.01.03"}, "2.01.03", False])
def test_invalid_movement_category_type_is_not_silently_bypassed_by_a_fallback(invalid_categories):
    records = read_responses(bx_categories=invalid_categories)[:3]

    result, service = check(records)

    assert result["conforme_esperado"] is False
    assert result["falhas_conferencia"]
    assert len(service.invoke_financial.call_args_list) == 3
    assert_only_fresh_reads(service)


def test_account_query_failure_is_reported_without_writing_or_repeating_the_query():
    records = read_responses()[:3] + [OmieError("omie_cooldown", "Aguardar", {"retry_after_seconds": 61})]

    result, service = check(records)

    assert result["conforme_esperado"] is False
    assert result["falhas_conferencia"]
    assert result["baixas"][0]["categorias"] is None
    assert len(service.invoke_financial.call_args_list) == 4
    assert_only_fresh_reads(service)


def test_essential_title_read_failure_remains_an_error_with_no_writes():
    service = Mock()
    service.invoke_financial.side_effect = OmieError("omie_http_error", "Indisponível", {"http_status": 503})

    with pytest.raises(OmieError) as raised:
        allocation.check_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert raised.value.details["escrita_processada"] is False
    assert_only_fresh_reads(service)


def test_write_flow_uses_the_linked_read_after_a_single_title_update():
    before = read_responses(corrected=False)[:3]
    after = read_responses()
    records = before + [{"codigo_status": "0", "codigo_lancamento_omie": 34}] + after
    service = Mock()
    service.invoke_financial.side_effect = records

    result = allocation.define_categories(service, "empresa", IDENTIFIER, CATEGORIES)

    assert result["verificacao"]["titulo_confirmado"] is True
    assert result["verificacao"]["baixas_confirmadas"] is True
    assert result["verificacao"]["campos_preservados"] is True
    assert result["baixas"][0]["fonte_categorias"] == "account_transactions_get"
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1
    assert service.invoke_financial.call_args_list[-1].args[1] == "account_transactions_get"
