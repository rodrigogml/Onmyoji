"""Contratos públicos da fachada financeira da Omie."""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.omie import OmieError, OmieService
from onmyoji_daemon.mcp.omie.finance import OmieFinanceFacade, SPECS
from onmyoji_daemon.mcp.omie.publication import input_contract


class Configuration:
    def __init__(self, root): self.root = root


PERIOD = {"data_inicial": "01/11/2026", "data_final": "30/11/2026"}
ACCOUNT_QUERY = "omie.financeiro.lancamentos_conta.listar_por_periodo"
RECONCILIATION_QUERY = "omie.financeiro.movimentos.listar_para_conciliacao"


def transaction(identifier, account=12):
    return {"nCodLanc": identifier, "cabecalho": {"nCodCC": account}}


def movement(identifier, account=12):
    return {"detalhes": {"nCodTitulo": identifier, "nCodCC": account}}


def native_page(number, total_pages, total_records, rows, collection="listaLancamentos"):
    return {
        "nPagina": number,
        "nTotPaginas": total_pages,
        "nRegistros": len(rows),
        "nTotRegistros": total_records,
        collection: rows,
    }


def mock_remote(monkeypatch, responses):
    """Preserva invoke_financial/build_params e substitui somente o acesso externo."""
    profile = Mock(return_value=({}, {}))
    request = Mock(side_effect=responses)
    monkeypatch.setattr(OmieService, "_profile", profile)
    monkeypatch.setattr(OmieService, "_request", request)
    return profile, request


def test_finance_tools_publish_closed_schemas_in_portuguese(tmp_path):
    facade = OmieFinanceFacade(Configuration(tmp_path))
    tools = facade.definitions([{"name": "empresa", "description": "ERP"}])
    assert len(tools) == len(SPECS)
    assert all(tool["name"].startswith("omie.financeiro.") for tool in tools)
    assert "omie.financeiro.lancamentos_conta.listar_por_periodo" in {tool["name"] for tool in tools}
    payment = next(tool for tool in tools if tool["name"].endswith("lancamentos_conta.listar_por_periodo"))
    Draft202012Validator.check_schema(payment["input_schema"])
    Draft202012Validator(payment["input_schema"]).validate({"filtros": {"data_inicial": "01/11/2026", "data_final": "30/11/2026", "conta_corrente_id": 12}})
    movements = next(tool for tool in tools if tool["name"].endswith("movimentos.listar_por_pagamento"))
    assert "conciliacao.conciliado" in movements["description"]
    assert "omie.financeiro.movimentos.listar_por_periodo" not in {tool["name"] for tool in tools}
    with pytest.raises(Exception):
        Draft202012Validator(payment["input_schema"]).validate({"filtros": {"dtPagInicial": "01/11/2026", "dtPagFinal": "30/11/2026"}})


def test_account_filter_collects_native_pages_before_public_pagination(tmp_path, monkeypatch):
    rows = [transaction(identifier, 99) for identifier in range(1, 103)]
    for index in (50, 99, 101):
        rows[index]["cabecalho"]["nCodCC"] = 12
    pages = [native_page(number, 3, 102, rows[start:start + 50]) for number, start in enumerate(range(0, 102, 50), 1)]
    # Lista independente de campos do request oficial de ListarLancCC.
    accepted_fields = {
        "nPagina", "nRegPorPagina", "cOrdenarPor", "cOrdemDecrescente", "cOrigem",
        "dDtAltAte", "dDtAltDe", "dDtIncAte", "dDtIncDe", "dtPagFinal", "dtPagInicial",
    }

    def remote(profile, defaults, endpoint, method, params, **kwargs):
        assert endpoint == "account_transactions"
        assert method == "ListarLancCC"
        assert set(params) <= accepted_fields
        assert params["nRegPorPagina"] == 50
        assert params["dtPagInicial"] == PERIOD["data_inicial"]
        assert params["dtPagFinal"] == PERIOD["data_final"]
        return deepcopy(pages[params["nPagina"] - 1])

    _, request = mock_remote(monkeypatch, remote)
    result = OmieFinanceFacade(Configuration(tmp_path)).call(
        ACCOUNT_QUERY,
        {"filtros": {**PERIOD, "conta_corrente_id": 12}, "pagina": 2, "tamanho_pagina": 1, "incluir_dados_omie": True},
        "empresa",
    )

    assert [call.args[4]["nPagina"] for call in request.call_args_list] == [1, 2, 3]
    assert result["dados"] == [rows[99]]
    assert result["paginacao"] == {"pagina": 2, "total_paginas": 3, "total_registros": 3, "proxima_pagina": 3}
    assert result["dados_omie"]["listaLancamentos"] == [rows[99]]


def test_transactions_without_account_fetch_only_the_requested_page(tmp_path, monkeypatch):
    rows = [transaction(3), transaction(4, 99)]
    _, request = mock_remote(monkeypatch, [native_page(2, 3, 5, rows)])

    result = OmieFinanceFacade(Configuration(tmp_path)).call(
        ACCOUNT_QUERY, {"filtros": PERIOD, "pagina": 2, "tamanho_pagina": 2}, "empresa",
    )

    request.assert_called_once()
    assert request.call_args.args[3] == "ListarLancCC"
    assert request.call_args.args[4] == {"nPagina": 2, "nRegPorPagina": 2, "dtPagInicial": "01/11/2026", "dtPagFinal": "30/11/2026"}
    assert result["dados"] == rows
    assert result["paginacao"] == {"pagina": 2, "total_paginas": 3, "total_registros": 5, "proxima_pagina": 3}


@pytest.mark.parametrize("response", [
    native_page(1, 1, 2, [transaction(1, 99), transaction(2, 98)]),
    native_page(1, 0, 0, []),
    native_page(1, 1, 0, []),
])
def test_empty_account_result_never_returns_other_accounts(tmp_path, monkeypatch, response):
    _, request = mock_remote(monkeypatch, [deepcopy(response)])

    result = OmieFinanceFacade(Configuration(tmp_path)).call(
        ACCOUNT_QUERY, {"filtros": {**PERIOD, "conta_corrente_id": 12}, "incluir_dados_omie": True}, "empresa",
    )

    request.assert_called_once()
    assert result["dados"] == []
    assert result["dados_omie"]["listaLancamentos"] == []
    assert result["paginacao"]["total_registros"] == 0
    assert result["paginacao"]["proxima_pagina"] is None


def test_native_transaction_contract_rejects_unsupported_account_filter_before_access(tmp_path, monkeypatch):
    profile, request = mock_remote(monkeypatch, [])

    with pytest.raises(OmieError) as error:
        OmieService(tmp_path).invoke_financial(
            "empresa", "account_transactions_list", {"params": {"nPagina": 1, "nRegPorPagina": 50, "nCodCC": 12}}, writes=False,
        )

    assert error.value.code == "invalid_body"
    profile.assert_not_called()
    request.assert_not_called()


def test_native_list_schemas_keep_account_and_period_fields_in_the_correct_endpoint():
    _, transactions = input_contract("account_transactions_list")
    _, movements = input_contract("financial_movements_list")
    assert "nCodCC" not in transactions["properties"]
    assert "dDtPagtoDe" not in transactions["properties"]
    assert "dtPagInicial" in transactions["properties"]
    assert "nCodCC" in movements["properties"]
    assert "dDtPagtoDe" in movements["properties"]
    assert "dtPagInicial" not in movements["properties"]


def test_account_query_rejects_overlapping_pages_instead_of_counting_a_transaction_twice(tmp_path, monkeypatch):
    mock_remote(monkeypatch, [
        native_page(1, 2, 51, [transaction(identifier) for identifier in range(1, 51)]),
        native_page(2, 2, 51, [transaction(25)]),
    ])

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(
            ACCOUNT_QUERY, {"filtros": {**PERIOD, "conta_corrente_id": 12}}, "empresa",
        )

    assert error.value.code == "invalid_response"
    assert error.value.details["field"] == "nCodLanc"


def test_financial_movements_expose_an_unambiguous_reconciliation_status(tmp_path, monkeypatch):
    request = Mock(return_value={"movimentos": [
        {"detalhes": {"dDtConcilia": "10/11/2026", "cHrConcilia": "14:35:00", "cUsConcilia": "rodrigo"}},
        {"detalhes": {}},
        {},
    ]})
    monkeypatch.setattr(OmieService, "invoke_financial", request)

    result = OmieFinanceFacade(Configuration(tmp_path)).call(
        "omie.financeiro.movimentos.listar_por_pagamento",
        {"filtros": {"data_inicial": "01/11/2026", "data_final": "30/11/2026"}}, "empresa",
    )

    assert result["dados"][0]["conciliacao"] == {
        "conciliado": True, "data": "10/11/2026", "hora": "14:35:00", "usuario": "rodrigo",
    }
    assert result["dados"][1]["conciliacao"] == {
        "conciliado": False, "data": None, "hora": None, "usuario": None,
    }
    assert result["dados"][2]["conciliacao"]["conciliado"] is False


def test_reconciliation_query_filters_the_account_and_collects_all_pages(tmp_path, monkeypatch):
    first_rows = [movement(identifier) for identifier in range(1, 51)]
    last_row = movement(51)
    last_row["detalhes"]["dDtConcilia"] = "02/11/2026"
    _, request = mock_remote(monkeypatch, [
        native_page(1, 2, 51, first_rows, "movimentos"),
        native_page(2, 2, 51, [last_row], "movimentos"),
    ])

    result = OmieFinanceFacade(Configuration(tmp_path)).call(
        "omie.financeiro.movimentos.listar_para_conciliacao",
        {"filtros": {"conta_corrente_id": 12, "data_inicial": "01/11/2026", "data_final": "02/11/2026"}}, "empresa",
    )

    assert request.call_count == 2
    for number, call in enumerate(request.call_args_list, 1):
        assert call.args[2:4] == ("financial_movements", "ListarMovimentos")
        assert call.args[4] == {"nPagina": number, "nRegPorPagina": 50, "dDtPagtoDe": "01/11/2026", "dDtPagtoAte": "02/11/2026", "nCodCC": 12}
    assert [row["conciliacao"]["conciliado"] for row in result["dados"]] == [False] * 50 + [True]
    assert result["paginacao"]["total_registros"] == 51
    assert result["paginacao"]["proxima_pagina"] is None


@pytest.mark.parametrize("name,collection,make_row", [
    (ACCOUNT_QUERY, "listaLancamentos", transaction),
    (RECONCILIATION_QUERY, "movimentos", movement),
])
@pytest.mark.parametrize("problem", [
    "missing_initial_metadata", "missing_later_collection", "repeated_page",
    "inconsistent_page_count", "changed_total", "incomplete_total", "missing_account",
])
def test_complete_queries_reject_incomplete_or_unidentifiable_results(
    tmp_path, monkeypatch, name, collection, make_row, problem,
):
    pages = [
        native_page(1, 2, 51, [make_row(identifier) for identifier in range(1, 51)], collection),
        native_page(2, 2, 51, [make_row(51)], collection),
    ]
    if problem == "missing_initial_metadata":
        del pages[0]["nTotPaginas"]
    elif problem == "missing_later_collection":
        del pages[1][collection]
    elif problem == "repeated_page":
        pages[1]["nPagina"] = 1
    elif problem == "inconsistent_page_count":
        pages[1]["nRegistros"] = 2
    elif problem == "changed_total":
        pages[1]["nTotRegistros"] = 52
    elif problem == "incomplete_total":
        pages[1][collection] = []
        pages[1]["nRegistros"] = 0
    elif problem == "missing_account":
        container = "cabecalho" if collection == "listaLancamentos" else "detalhes"
        del pages[1][collection][0][container]["nCodCC"]
    pages[1]["provider_private_note"] = "PRIVATE-RESPONSE-CONTENT"
    mock_remote(monkeypatch, pages)

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(name, {"filtros": {**PERIOD, "conta_corrente_id": 12}}, "empresa")

    assert error.value.code == "invalid_response"
    assert "PRIVATE-RESPONSE-CONTENT" not in str(error.value.as_dict())


@pytest.mark.parametrize("name,collection,make_row", [
    (ACCOUNT_QUERY, "listaLancamentos", transaction),
    (RECONCILIATION_QUERY, "movimentos", movement),
])
def test_complete_queries_propagate_later_network_failure(tmp_path, monkeypatch, name, collection, make_row):
    failure = OmieError("network_error", "Não foi possível consultar a API Omie.")
    _, request = mock_remote(monkeypatch, [
        native_page(1, 2, 51, [make_row(identifier) for identifier in range(1, 51)], collection),
        failure,
    ])

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(name, {"filtros": {**PERIOD, "conta_corrente_id": 12}}, "empresa")

    assert error.value is failure
    assert request.call_count == 2


def test_reconciliation_rejects_an_account_ignored_by_the_provider(tmp_path, monkeypatch):
    mock_remote(monkeypatch, [native_page(1, 1, 1, [movement(1, 99)], "movimentos")])

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(
            RECONCILIATION_QUERY, {"filtros": {**PERIOD, "conta_corrente_id": 12}}, "empresa",
        )

    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("name", [ACCOUNT_QUERY, RECONCILIATION_QUERY])
@pytest.mark.parametrize("account", [None, True, False, 0, -1, "12", 12.0])
def test_invalid_account_is_rejected_before_credentials_or_http(tmp_path, monkeypatch, name, account):
    profile, request = mock_remote(monkeypatch, [])

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(name, {"filtros": {**PERIOD, "conta_corrente_id": account}}, "empresa")

    assert error.value.code == "invalid_parameter"
    profile.assert_not_called()
    request.assert_not_called()


def test_reconciliation_requires_account_at_runtime(tmp_path, monkeypatch):
    profile, request = mock_remote(monkeypatch, [])

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(RECONCILIATION_QUERY, {"filtros": PERIOD}, "empresa")

    assert error.value.code == "invalid_parameter"
    profile.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize("filters", [
    {"dtPagInicial": "01/11/2026", "dtPagFinal": "30/11/2026"},
    {"data_inicial": "31/11/2026", "data_final": "30/12/2026"},
    {"data_inicial": "30/12/2026", "data_final": "01/12/2026"},
])
def test_invalid_period_never_reaches_omie(tmp_path, monkeypatch, filters):
    request = Mock()
    monkeypatch.setattr(OmieService, "invoke_financial", request)
    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call("omie.financeiro.lancamentos_conta.listar_por_periodo", {"filtros": filters}, "empresa")
    assert error.value.code == "invalid_parameter"
    request.assert_not_called()


def test_write_requires_confirmation_and_uses_semantic_fields(tmp_path, monkeypatch):
    request = Mock(return_value={"codigo_lancamento_omie": 8})
    monkeypatch.setattr(OmieService, "invoke_financial", request)
    facade = OmieFinanceFacade(Configuration(tmp_path))
    data = {"codigo_integracao": "P-1", "cliente_fornecedor_id": 2, "data_vencimento": "30/11/2026", "valor": 10, "categoria_codigo": "1.01"}
    with pytest.raises(OmieError, match="confirmar"):
        facade.call("omie.financeiro.contas_pagar.criar", {"dados": data}, "empresa")
    facade.call("omie.financeiro.contas_pagar.criar", {"dados": data, "confirmar": True}, "empresa")
    assert request.call_args.args[2]["body"]["codigo_lancamento_integracao"] == "P-1"
    assert request.call_args.kwargs["writes"] is True


def test_omie_error_is_structured():
    assert OmieError("invalid_parameter", "Filtro inválido", {"field": "x"}).as_dict() == {"code": "invalid_parameter", "message": "Filtro inválido", "details": {"field": "x"}}
