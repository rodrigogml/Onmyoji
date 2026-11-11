"""Contratos públicos da fachada financeira da Omie."""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.omie import OmieError, OmieService
from onmyoji_daemon.mcp.omie.finance import OmieFinanceFacade, SPECS


class Configuration:
    def __init__(self, root): self.root = root


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
    with pytest.raises(Exception):
        Draft202012Validator(payment["input_schema"]).validate({"filtros": {"dtPagInicial": "01/11/2026", "dtPagFinal": "30/11/2026"}})


def test_payment_filter_translates_to_omie_and_returns_pagination(tmp_path, monkeypatch):
    request = Mock(return_value={"pagina": 1, "total_de_paginas": 2, "total_de_registros": 60, "lancamentos": [{"nCodLanc": 1}]})
    monkeypatch.setattr(OmieService, "invoke_financial", request)
    result = OmieFinanceFacade(Configuration(tmp_path)).call(
        "omie.financeiro.lancamentos_conta.listar_por_periodo",
        {"filtros": {"data_inicial": "01/11/2026", "data_final": "30/11/2026", "conta_corrente_id": 12}}, "empresa",
    )
    assert request.call_args.args[1] == "account_transactions_list"
    assert request.call_args.args[2] == {"params": {"nPagina": 1, "nRegPorPagina": 50, "dtPagInicial": "01/11/2026", "dtPagFinal": "30/11/2026", "nCodCC": 12}}
    assert result["paginacao"]["proxima_pagina"] == 2


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
    request = Mock(side_effect=[
        {"nPagina": 1, "nTotPaginas": 2, "nTotRegistros": 2, "movimentos": [{"detalhes": {}}]},
        {"nPagina": 2, "nTotPaginas": 2, "nTotRegistros": 2, "movimentos": [{"detalhes": {"dDtConcilia": "02/11/2026"}}]},
    ])
    monkeypatch.setattr(OmieService, "invoke_financial", request)

    result = OmieFinanceFacade(Configuration(tmp_path)).call(
        "omie.financeiro.movimentos.listar_para_conciliacao",
        {"filtros": {"conta_corrente_id": 12, "data_inicial": "01/11/2026", "data_final": "02/11/2026"}}, "empresa",
    )

    assert request.call_count == 2
    assert request.call_args_list[0].args[2]["params"] == {"nPagina": 1, "nRegPorPagina": 50, "dDtPagtoDe": "01/11/2026", "dDtPagtoAte": "02/11/2026", "nCodCC": 12}
    assert [row["conciliacao"]["conciliado"] for row in result["dados"]] == [False, True]


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
