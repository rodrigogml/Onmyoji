"""Contratos da definição de rateio por objeto, sem API ou cofre reais."""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.omie import department_allocation as allocation
from onmyoji_daemon.mcp.omie.department_allocation import define_departments
from onmyoji_daemon.mcp.omie.exception import OmieError
from onmyoji_daemon.state_layout import SETUP_VERSION


DEPARTMENTS = [{"cCodDep": "000123", "nPerDep": 100}]
ACK = {"cCodStatus": "0", "cDesStatus": "Processado"}


def account(*, origin="EXTP", payable=0, receivable=0, assigned=False):
    return {
        "nCodLanc": 12, "cCodIntLanc": "MOV-12",
        "cabecalho": {"nCodCC": 56, "dDtLanc": "17/11/2026", "nValorLanc": 1000},
        "detalhes": {"cCodCateg": "2.01", "cTipo": "FAT", "nCodProjeto": 78, "nCodCliente": 90, "cObs": "Preservar"},
        "diversos": {"cOrigem": origin, "nCodLancCP": payable, "nCodLancCR": receivable, "dDtConc": ""},
        "departamentos": [{"cCodDep": "000123", "nPerDep": 100, "nValDep": 1000}] if assigned else [],
        "info": {"dAlt": "17/11/2026"},
    }


def title(*, assigned=False):
    return {
        "codigo_lancamento_omie": 34, "codigo_lancamento_integracao": "TITLE-34",
        "codigo_cliente_fornecedor": 90, "data_vencimento": "17/11/2026", "valor_documento": 1000,
        "codigo_categoria": "2.01", "categorias": [{"codigo_categoria": "2.01", "percentual": 100}],
        "codigo_projeto": 78, "id_conta_corrente": 56, "numero_documento": "DOC-SINTETICO",
        "status_titulo": "PAGO", "id_origem": "BARP", "observacao": "Preservar",
        "distribuicao": [{"cCodDep": "000123", "nPerDep": 100, "nValDep": 1000}] if assigned else [],
        "info": {"data_alt": "17/11/2026"},
    }


@pytest.mark.parametrize("resource,query,expected_body,public", [
    ("account_transactions", {"nCodLanc": 12}, {"id": 12, "departments": DEPARTMENTS}, "lancamentos_conta"),
    ("account_transactions", {"cCodIntLanc": "MOV-12"}, {"id": 12, "departments": DEPARTMENTS}, "lancamentos_conta"),
    ("payables", {"codigo_lancamento_omie": 34}, {"codigo_lancamento_omie": 34, "distribuicao": DEPARTMENTS}, "contas_pagar"),
    ("receivables", {"codigo_lancamento_integracao": "TITLE-34"}, {"codigo_lancamento_omie": 34, "distribuicao": DEPARTMENTS}, "contas_receber"),
])
@pytest.mark.parametrize("include_native", [False, True])
def test_definition_writes_only_the_allocation_and_returns_the_observed_state(resource, query, expected_body, public, include_native):
    before, after = (account(), account(assigned=True)) if resource == "account_transactions" else (title(), title(assigned=True))
    after["info"] = {"alterado": "metadado sem relação com o contrato"}
    service = Mock()
    service.invoke_financial.side_effect = [before, ACK, after]

    result = define_departments(service, "empresa", resource, query, DEPARTMENTS, include_native=include_native)

    assert result["alvo"] == {"recurso": public, "id": 12 if resource == "account_transactions" else 34}
    assert result["departamentos"] == [{"codigo": "000123", "percentual": 100, "valor": 1000}]
    assert result["verificacao"]["rateio_confirmado"] is True
    assert result["verificacao"]["campos_preservados"] is True
    assert "cabecalho.nValorLanc" in result["verificacao"]["campos_verificados"] if resource == "account_transactions" else "valor_documento" in result["verificacao"]["campos_verificados"]
    assert result["versao_mcp"] == SETUP_VERSION
    assert [call.args[1] for call in service.invoke_financial.call_args_list] == [resource + "_get", resource + "_update", resource + "_get"]
    assert service.invoke_financial.call_args_list[1].args[2] == {"body": expected_body}
    assert service.invoke_financial.call_args_list[2].args[2] == {"params": {"nCodLanc": 12} if resource == "account_transactions" else {"codigo_lancamento_omie": 34}}
    assert [call.kwargs["writes"] for call in service.invoke_financial.call_args_list] == [False, True, False]
    if include_native:
        assert result["dados_omie"]["processamento"] == ACK
        assert result["dados_omie"]["registro"] == after
        audit = result["dados_omie"]["requisicao"]
        assert set(audit) == {"call", "param"}
        assert set(audit["param"][0]) == ({"nCodLanc", "departamentos"} if resource == "account_transactions" else {"codigo_lancamento_omie", "distribuicao"})
        assert "app_key" not in str(audit) and "app_secret" not in str(audit)
    else:
        assert "dados_omie" not in result
        assert "cDesStatus" not in str(result)


@pytest.mark.parametrize("resource,query", [("account_transactions", {"nCodLanc": 12}), ("payables", {"codigo_lancamento_omie": 34})])
def test_empty_allocation_removal_is_confirmed_from_the_post_write_record(resource, query):
    before = account(assigned=True) if resource == "account_transactions" else title(assigned=True)
    after = account() if resource == "account_transactions" else title()
    service = Mock()
    service.invoke_financial.side_effect = [before, ACK, after]

    result = define_departments(service, "empresa", resource, query, [])

    assert result["departamentos"] == []
    assert result["verificacao"]["rateio_confirmado"] is True


@pytest.mark.parametrize("record,resource,identifier", [
    (account(origin="BAXP", payable=34), "contas_pagar", 34),
    (account(origin="BAXR", receivable=35), "contas_receber", 35),
    (account(origin="MANP", payable=34), "contas_pagar", 34),
])
def test_linked_movements_are_refused_before_write_with_the_complete_next_call(record, resource, identifier):
    service = Mock()
    service.invoke_financial.return_value = record

    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "account_transactions", {"nCodLanc": 12}, DEPARTMENTS, include_native=True)

    assert error.value.code == "allocation_requires_source_title"
    assert error.value.details["suggested_tool"] == f"omie.financeiro.{resource}.definir_departamentos"
    assert error.value.details["suggested_arguments"] == {
        "confirmar": True, "dados": {"id": identifier, "departamentos": [{"codigo": "000123", "percentual": 100}], "lancamento_conta_id": 12},
    }
    assert error.value.details["escrita_processada"] is False
    assert "requisicao_omie" not in error.value.details
    assert "proteção do MCP" in str(error.value)
    service.invoke_financial.assert_called_once_with("empresa", "account_transactions_get", {"params": {"nCodLanc": 12}}, writes=False)


@pytest.mark.parametrize("source", [
    None, [], {}, {"cOrigem": None}, {"cOrigem": "áBXP"}, {"cOrigem": "LONGO"},
    {"cOrigem": "BAXP"}, {"cOrigem": "BAXR", "nCodLancCR": 0},
    {"cOrigem": "BAXP", "nCodLancCP": "34"}, {"cOrigem": "EXTP", "nCodLancCP": True},
    {"cOrigem": "EXTP", "nCodLancCP": -1},
    {"cOrigem": "BAXP", "nCodLancCP": 34, "nCodLancCR": 35},
    {"cOrigem": "BAXP", "nCodLancCR": 35}, {"cOrigem": "BAXR", "nCodLancCP": 34},
])
def test_unknown_or_inconsistent_movement_origins_are_never_guessed(source):
    record = account()
    record["diversos"] = source
    service = Mock()
    service.invoke_financial.return_value = record

    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "account_transactions", {"nCodLanc": 12}, DEPARTMENTS)

    assert error.value.code == "allocation_origin_unresolved"
    assert error.value.details["escrita_processada"] is False
    assert service.invoke_financial.call_count == 1


@pytest.mark.parametrize("resource,link_key,origin", [("payables", "nCodLancCP", "BAXP"), ("receivables", "nCodLancCR", "BAXR")])
@pytest.mark.parametrize("include_native", [False, True])
def test_paid_title_and_linked_movement_can_be_confirmed_without_repeating_the_payment(resource, link_key, origin, include_native):
    movement_before = account(origin=origin)
    movement_before["diversos"][link_key] = 34
    movement_after = deepcopy(movement_before)
    movement_after["departamentos"] = [{"cCodDep": "000123", "nPerDep": 100, "nValDep": 1000}]
    service = Mock()
    service.invoke_financial.side_effect = [title(), movement_before, ACK, title(assigned=True), movement_after]

    result = define_departments(service, "empresa", resource, {"codigo_lancamento_omie": 34}, DEPARTMENTS, movement_id=12, include_native=include_native)

    assert result["verificacao"]["rateio_confirmado"] is True
    assert result["verificacao"]["campos_preservados"] is True
    assert result["verificacao"]["movimento_confirmado"] is True
    assert "status_titulo" in result["verificacao"]["campos_verificados"]
    assert result["movimento"] == {"id": 12, "departamentos": [{"codigo": "000123", "percentual": 100, "valor": 1000}]}
    assert [call.args[1] for call in service.invoke_financial.call_args_list] == [resource + "_get", "account_transactions_get", resource + "_update", resource + "_get", "account_transactions_get"]
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1
    if include_native:
        assert result["dados_omie"]["movimento"] == movement_after


@pytest.mark.parametrize("movement", [account(origin="BAXP", payable=99), account(origin="BAXR", receivable=34), account(origin="EXTP")])
def test_movement_must_prove_its_relationship_to_the_title_before_any_write(movement):
    service = Mock()
    service.invoke_financial.side_effect = [title(), movement]

    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS, movement_id=12)

    assert error.value.code == "allocation_movement_mismatch"
    assert error.value.details["escrita_processada"] is False
    assert service.invoke_financial.call_count == 2


@pytest.mark.parametrize("readback", [
    account(origin="BAXP", payable=34),
    OmieError("omie_cooldown", "Aguarde.", {"retry_after_seconds": 295, "reason": "remote_cooldown", "http_status": 425}),
])
def test_confirmed_title_does_not_imply_confirmed_propagation_to_a_paid_movement(readback):
    service = Mock()
    service.invoke_financial.side_effect = [title(), account(origin="BAXP", payable=34), ACK, title(assigned=True), readback]

    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS, movement_id=12, include_native=True)

    assert error.value.code == "allocation_propagation_not_confirmed"
    assert error.value.details["titulo_confirmado"] is True
    assert error.value.details["departamentos_titulo"] == [{"codigo": "000123", "percentual": 100, "valor": 1000}]
    if isinstance(readback, OmieError):
        assert error.value.details["retry_after_seconds"] == 295
        assert error.value.details["cause_reason"] == "remote_cooldown"
        assert error.value.details["http_status"] == 425
    else:
        assert error.value.details["departamentos_movimento"] == []
    assert error.value.details["requisicao_omie"]["call"] == "AlterarContaPagar"
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1


@pytest.mark.parametrize("resource,path,new_value", [
    ("account_transactions", ("cabecalho", "nValorLanc"), 999),
    ("account_transactions", ("detalhes", "nCodProjeto"), 999),
    ("account_transactions", ("diversos", "dDtConc"), "17/11/2026"),
    ("payables", ("status_titulo",), "ABERTO"),
    ("payables", ("categorias",), [{"codigo_categoria": "2.99", "percentual": 100}]),
    ("payables", ("id_conta_corrente",), 999),
])
def test_definition_detects_changes_outside_the_allocation(resource, path, new_value):
    before, after = (account(), account(assigned=True)) if resource == "account_transactions" else (title(), title(assigned=True))
    target = after
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = new_value
    if path == ("cabecalho", "nValorLanc"):
        after["departamentos"][0]["nValDep"] = new_value
    service = Mock()
    service.invoke_financial.side_effect = [before, ACK, after]
    identifier = {"nCodLanc": 12} if resource == "account_transactions" else {"codigo_lancamento_omie": 34}

    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", resource, identifier, DEPARTMENTS, include_native=True)

    assert error.value.code == "allocation_side_effect_detected"
    assert error.value.details["rateio_confirmado"] is True
    assert error.value.details["campos_alterados"][0]["campo"] == ".".join(path)
    assert error.value.details["campos_alterados"][0]["depois"] == new_value
    assert "requisicao_omie" in error.value.details


def test_missing_critical_post_write_fields_are_not_assumed_preserved():
    before, after = title(), title(assigned=True)
    del after["observacao"]
    service = Mock()
    service.invoke_financial.side_effect = [before, ACK, after]

    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS)

    assert error.value.code == "allocation_side_effect_detected"
    assert error.value.details["campos_alterados"] == [{"campo": "observacao", "antes": "Preservar", "depois": None, "presente_depois": False}]


@pytest.mark.parametrize("record", [None, [], {}, {"nCodLanc": True}, {"nCodLanc": 0}, {"nCodLanc": 99}])
def test_preflight_requires_the_exact_object_identity(record):
    service = Mock()
    service.invoke_financial.return_value = record
    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "account_transactions", {"nCodLanc": 12}, DEPARTMENTS)
    assert error.value.code == "allocation_preflight_failed"
    assert error.value.details["escrita_processada"] is False
    assert service.invoke_financial.call_count == 1


@pytest.mark.parametrize("cause", [
    RuntimeError("SECRET"), OmieError("network_error", "SECRET"),
    OmieError("omie_cooldown", "SECRET", {"retry_after_seconds": 295, "reason": "remote_cooldown", "http_status": 425, "body": "SECRET"}),
    OmieError("invalid_response", "SECRET", ["SECRET"]),
    OmieError("invalid_response", "SECRET", {"retry_after_seconds": True, "reason": " ", "http_status": True}),
    OmieError("invalid_response", "SECRET", {"retry_after_seconds": -1, "reason": "X" * 129, "http_status": 600}),
    OmieError("invalid_response", "SECRET", {"retry_after_seconds": "10", "reason": "line\nbreak", "http_status": "500"}),
])
def test_preflight_failures_preserve_only_safe_diagnostics(cause):
    service = Mock()
    service.invoke_financial.side_effect = cause
    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS, include_native=True)
    assert error.value.code == "allocation_preflight_failed"
    assert "SECRET" not in str(error.value.as_dict())
    assert "requisicao_omie" not in error.value.details
    assert service.invoke_financial.call_count == 1


@pytest.mark.parametrize("resource,identifier,departments,movement_id,include_native", [
    (None, {"nCodLanc": 12}, DEPARTMENTS, None, False),
    ("unknown", {"nCodLanc": 12}, DEPARTMENTS, None, False),
    ("account_transactions", None, DEPARTMENTS, None, False),
    ("account_transactions", {}, DEPARTMENTS, None, False),
    ("account_transactions", {"nCodLanc": 12, "cCodIntLanc": "I1"}, DEPARTMENTS, None, False),
    ("account_transactions", {"id": 12}, DEPARTMENTS, None, False),
    ("account_transactions", {"nCodLanc": True}, DEPARTMENTS, None, False),
    ("account_transactions", {"nCodLanc": -1}, DEPARTMENTS, None, False),
    ("account_transactions", {"cCodIntLanc": " "}, DEPARTMENTS, None, False),
    ("account_transactions", {"cCodIntLanc": "I" * 21}, DEPARTMENTS, None, False),
    ("payables", {"codigo_lancamento_integracao": "I" * 61}, DEPARTMENTS, None, False),
    ("account_transactions", {"nCodLanc": 12}, None, None, False),
    ("account_transactions", {"nCodLanc": 12}, DEPARTMENTS, 99, False),
    ("payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS, True, False),
    ("payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS, 0, False),
    ("payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS, None, "true"),
])
def test_invalid_inputs_never_read_or_write_the_api(resource, identifier, departments, movement_id, include_native):
    service = Mock()
    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", resource, identifier, departments, movement_id=movement_id, include_native=include_native)
    assert error.value.code == "invalid_parameter"
    service.invoke_financial.assert_not_called()


@pytest.mark.parametrize("cause,include_request", [
    (OmieError("omie_api_error", "Recusado."), True),
    (OmieError("outcome_unknown", "Resultado incerto."), True),
    (OmieError("omie_cooldown", "Não enviado."), False),
])
def test_write_failure_does_not_repeat_and_audits_only_remote_or_uncertain_requests(cause, include_request):
    service = Mock()
    service.invoke_financial.side_effect = [title(), cause]
    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS, include_native=True)
    assert error.value.code == cause.code
    assert ("requisicao_omie" in error.value.details) is include_request
    assert service.invoke_financial.call_count == 2


def test_unknown_failure_during_write_is_sanitized_and_never_repeated():
    service = Mock()
    service.invoke_financial.side_effect = [title(), RuntimeError("SECRET")]
    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS, include_native=True)
    assert error.value.code == "outcome_unknown"
    assert "SECRET" not in str(error.value.as_dict())
    assert service.invoke_financial.call_count == 2


@pytest.mark.parametrize("resource,record", [("account_transactions", {"nCodLanc": 12}), ("payables", {"codigo_lancamento_omie": 34})])
def test_preflight_requires_a_minimum_preservation_baseline(resource, record):
    query = {"nCodLanc": 12} if resource == "account_transactions" else {"codigo_lancamento_omie": 34}
    service = Mock()
    service.invoke_financial.return_value = record
    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", resource, query, DEPARTMENTS)
    assert error.value.code == "allocation_preflight_failed"
    assert error.value.details["reason"] == "missing_core_fields"
    assert error.value.details["escrita_processada"] is False
    assert service.invoke_financial.call_count == 1


@pytest.mark.parametrize("resource,path,value", [
    ("account_transactions", ("cabecalho", "nCodCC"), True),
    ("account_transactions", ("cabecalho", "nValorLanc"), float("inf")),
    ("account_transactions", ("cabecalho", "nValorLanc"), 0),
    ("account_transactions", ("cabecalho", "nValorLanc"), 10.25),
    ("account_transactions", ("cabecalho", "dDtLanc"), "1/1/2026"),
    ("account_transactions", ("cabecalho", "dDtLanc"), "31/02/2026"),
    ("account_transactions", ("detalhes",), None),
    ("account_transactions", ("detalhes",), []),
    ("payables", ("codigo_cliente_fornecedor",), None),
    ("payables", ("valor_documento",), True),
])
def test_invalid_core_fields_cannot_be_used_as_a_preservation_baseline(resource, path, value):
    record = account() if resource == "account_transactions" else title()
    target = record
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    service = Mock()
    service.invoke_financial.return_value = record
    query = {"nCodLanc": 12} if resource == "account_transactions" else {"codigo_lancamento_omie": 34}
    if value == 10.25:
        # Um total decimal positivo é válido; interrompemos no write para não exigir um pós fictício.
        service.invoke_financial.side_effect = [record, OmieError("omie_cooldown", "Não enviado.")]
    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", resource, query, DEPARTMENTS)
    if value == 10.25:
        assert error.value.code == "omie_cooldown"
    else:
        assert error.value.code == "allocation_preflight_failed"
        assert error.value.details["reason"] == "invalid_core_fields"
        assert service.invoke_financial.call_count == 1


@pytest.mark.parametrize("ack", [None, {"codigo_lancamento_omie": 99, "codigo_status": "0"}, {"codigo_lancamento_omie": True}])
def test_write_ack_for_a_different_object_never_confirms_the_requested_title(ack):
    service = Mock()
    service.invoke_financial.side_effect = [title(), ack]
    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "payables", {"codigo_lancamento_integracao": "TITLE-34"}, DEPARTMENTS, include_native=True)
    assert error.value.code == "outcome_unknown"
    assert error.value.details["reason"] == "write_response_identifier_mismatch"
    assert error.value.details["escrita_processada"] is True
    assert service.invoke_financial.call_count == 2
    assert service.invoke_financial.call_args_list[1].args[2]["body"] == {"codigo_lancamento_omie": 34, "distribuicao": DEPARTMENTS}


@pytest.mark.parametrize("include_native", [False, True])
def test_unknown_post_ack_failure_preserves_that_the_write_was_processed(monkeypatch, include_native):
    service = Mock()
    service.invoke_financial.side_effect = [title(), ACK]
    monkeypatch.setattr(allocation, "verify_department_allocation", Mock(side_effect=RuntimeError("SECRET")))
    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS, include_native=include_native)
    assert error.value.code == "outcome_unknown"
    assert error.value.details["escrita_processada"] is True
    assert ("requisicao_omie" in error.value.details) is include_native
    assert "SECRET" not in str(error.value.as_dict())


@pytest.mark.parametrize("resource", ["account_transactions", "payables", "receivables"])
@pytest.mark.parametrize("single_category", [None, ""])
def test_optional_category_and_document_type_do_not_block_multiple_category_records(resource, single_category):
    before = account() if resource == "account_transactions" else title()
    if resource == "account_transactions":
        before["detalhes"] = {"aCodCateg": [{"cCodCateg": "2.01", "nPerc": 40}, {"cCodCateg": "2.02", "nPerc": 60}]}
        if single_category is not None:
            before["detalhes"].update({"cCodCateg": single_category, "cTipo": ""})
        field, query = "departamentos", {"nCodLanc": 12}
    else:
        before["categorias"] = [{"codigo_categoria": "2.01", "percentual": 40}, {"codigo_categoria": "2.02", "percentual": 60}]
        if single_category is None:
            del before["codigo_categoria"]
        else:
            before["codigo_categoria"] = single_category
        field, query = "distribuicao", {"codigo_lancamento_omie": 34}
    after = deepcopy(before)
    after[field] = [{"cCodDep": "000123", "nPerDep": 100, "nValDep": 1000}]
    service = Mock()
    service.invoke_financial.side_effect = [before, ACK, after]

    result = define_departments(service, "empresa", resource, query, DEPARTMENTS)

    assert result["verificacao"]["rateio_confirmado"] is True
    assert result["verificacao"]["campos_preservados"] is True


@pytest.mark.parametrize("field,before_value,after_value,preserved", [
    ("categorias", [{"codigo_categoria": "A", "percentual": 40}, {"codigo_categoria": "B", "percentual": 60}], [{"codigo_categoria": "B", "percentual": 60}, {"codigo_categoria": "A", "percentual": 40}], True),
    ("categorias", [{"codigo_categoria": "A", "percentual": 100}], [{"codigo_categoria": "B", "percentual": 100}], False),
    ("categorias", [], [], True),
    ("pagamento", {"valor": 1, "data": "17/11/2026"}, {"data": "17/11/2026", "valor": 1.0}, True),
    ("pagamento", {"valor": 1}, {"valor": True}, False),
    ("pagamento", {"valor": 1}, {"valor": 1, "outro": 2}, False),
    ("pagamento", {"valor": 1}, [], False),
    ("pagamentos", [{"id": 1}, {"id": 2}], [{"id": 1}, {"id": 2}], True),
    ("pagamentos", [1], [1, 2], False),
    ("pagamentos", [1], {}, False),
    ("observacao", None, False, False),
    ("observacao", False, False, True),
    ("codigo_cliente_fornecedor", 1, True, False),
])
def test_snapshot_comparison_preserves_category_order_semantics_and_json_types(field, before_value, after_value, preserved):
    before, after = title(), title(assigned=True)
    before[field], after[field] = before_value, after_value
    service = Mock()
    service.invoke_financial.side_effect = [before, ACK, after]

    if preserved:
        result = define_departments(service, "empresa", "payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS)
        assert result["verificacao"]["campos_preservados"] is True
    else:
        with pytest.raises(OmieError) as error:
            define_departments(service, "empresa", "payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS)
        assert error.value.code == "allocation_side_effect_detected"
        assert error.value.details["campos_alterados"][0]["campo"] == field


@pytest.mark.parametrize("origin", [None, ""])
def test_proven_single_title_link_still_guides_the_next_tool_when_origin_is_missing(origin):
    service = Mock()
    service.invoke_financial.return_value = account(origin=origin, payable=34)
    with pytest.raises(OmieError) as error:
        define_departments(service, "empresa", "account_transactions", {"nCodLanc": 12}, DEPARTMENTS)
    assert error.value.code == "allocation_requires_source_title"
    assert error.value.details["suggested_tool"] == "omie.financeiro.contas_pagar.definir_departamentos"
    assert error.value.details["suggested_arguments"]["dados"]["id"] == 34
    assert service.invoke_financial.call_count == 1


@pytest.mark.parametrize("origin", [None, ""])
def test_proven_title_relationship_allows_movement_confirmation_without_an_origin_label(origin):
    before, after = account(origin=origin, payable=34), account(origin=origin, payable=34, assigned=True)
    service = Mock()
    service.invoke_financial.side_effect = [title(), before, ACK, title(assigned=True), after]

    result = define_departments(service, "empresa", "payables", {"codigo_lancamento_omie": 34}, DEPARTMENTS, movement_id=12)

    assert result["verificacao"]["movimento_confirmado"] is True
    assert sum(call.kwargs["writes"] for call in service.invoke_financial.call_args_list) == 1
