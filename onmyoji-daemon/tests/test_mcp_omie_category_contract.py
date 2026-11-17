"""Contratos públicos da substituição de categorias, sem credenciais ou API reais."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.omie import finance
from onmyoji_daemon.mcp.omie.contracts import build_params
from onmyoji_daemon.mcp.omie.exception import OmieError
from onmyoji_daemon.mcp.omie.finance import OmieFinanceFacade
from onmyoji_daemon.mcp.omie.service import OmieService


TOOL = "omie.financeiro.contas_pagar.definir_categorias"
CATEGORIES = [{"codigo": "2.01.03", "percentual": 100}]


def facade(root):
    return OmieFinanceFacade(SimpleNamespace(root=root))


@pytest.mark.parametrize("multiple_profiles", [False, True])
def test_category_replacement_publishes_a_closed_semantic_schema(tmp_path, multiple_profiles):
    profiles = [{"name": "empresa", "description": "ERP"}]
    if multiple_profiles:
        profiles.append({"name": "filial", "description": "Outro ERP"})
    definition = next(item for item in facade(tmp_path).definitions(profiles) if item["name"] == TOOL)
    schema = definition["input_schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    properties = {"dados", "confirmar", "incluir_dados_omie"}
    if multiple_profiles:
        properties.add("profile")
    assert set(schema["properties"]) == properties
    assert set(schema["properties"]["dados"]["properties"]) == {"id", "codigo_integracao", "categorias"}
    arguments = {"confirmar": True, "dados": {"id": 34, "categorias": CATEGORIES}}
    if multiple_profiles:
        assert not validator.is_valid(arguments)
        arguments["profile"] = "filial"
    validator.validate(arguments)
    validator.validate({**arguments, "dados": {"codigo_integracao": "TITLE-34", "categorias": CATEGORIES}})
    assert "AlterarContaPagar" in definition["description"]
    assert "ListarMovimentos" in definition["description"]
    assert "Nunca cancela nem refaz" in definition["description"]
    assert "título" in definition["description"] and "conciliação" in definition["description"]


@pytest.mark.parametrize("data", [
    None, [], "", True,
    {}, {"id": 34}, {"categorias": CATEGORIES},
    {"id": 34, "codigo_integracao": "TITLE-34", "categorias": CATEGORIES},
    {"id": "34", "categorias": CATEGORIES}, {"id": 0, "categorias": CATEGORIES},
    {"id": True, "categorias": CATEGORIES}, {"codigo_integracao": " ", "categorias": CATEGORIES},
    {"codigo_integracao": 34, "categorias": CATEGORIES},
    {"codigo_integracao": "X" * 61, "categorias": CATEGORIES},
    {"id": 34, "categorias": []}, {"id": 34, "categorias": None},
    {"id": 34, "categorias": [{"codigo": "2.01.03"}]},
    {"id": 34, "categorias": [{"codigo": " ", "percentual": 100}]},
    {"id": 34, "categorias": [{"codigo": 20103, "percentual": 100}]},
    {"id": 34, "categorias": [{"codigo": "X" * 21, "percentual": 100}]},
    {"id": 34, "categorias": [{"codigo": "2.01.03", "percentual": "100"}]},
    {"id": 34, "categorias": [{"codigo": "2.01.03", "percentual": True}]},
    {"id": 34, "categorias": [{"codigo": "2.01.03", "percentual": 0}]},
    {"id": 34, "categorias": [{"codigo": "2.01.03", "percentual": 101}]},
    {"id": 34, "categorias": [{"codigo_categoria": "2.01.03", "percentual": 100}]},
    {"id": 34, "categorias": [{"codigo": "2.01.03", "percentual": 100, "valor": 1000}]},
    {"id": 34, "categorias": CATEGORIES, "desconto": 25.57},
    {"id": 34, "categorias": CATEGORIES, "conciliado": True},
    {"id": 34, "categorias": CATEGORIES, "id_baixa": 99},
    {"id": 34, "categorias": CATEGORIES, "lancamento_conta_id": 12},
])
def test_invalid_category_arguments_fail_schema_and_runtime_before_service_access(tmp_path, monkeypatch, data):
    instance = facade(tmp_path)
    schema = next(item for item in instance.definitions([{"name": "empresa"}]) if item["name"] == TOOL)["input_schema"]
    arguments = {"confirmar": True, "dados": data}
    assert not Draft202012Validator(schema).is_valid(arguments)
    service = Mock()
    monkeypatch.setattr(finance, "OmieService", service)

    with pytest.raises(OmieError) as error:
        instance.call(TOOL, arguments, "empresa")

    assert error.value.code == "invalid_parameter"
    service.assert_not_called()


@pytest.mark.parametrize("categories", [
    [{"codigo": "2.01.03", "percentual": float("nan")}],
    [{"codigo": "2.01.03", "percentual": float("inf")}],
    [{"codigo": "2.01.03", "percentual": 99}],
    [{"codigo": "2.01.03", "percentual": 50}, {"codigo": "2.01.03", "percentual": 50}],
])
def test_nonfinite_duplicate_and_incomplete_allocations_fail_before_service_access(tmp_path, monkeypatch, categories):
    service = Mock()
    monkeypatch.setattr(finance, "OmieService", service)

    with pytest.raises(OmieError) as error:
        facade(tmp_path).call(TOOL, {"confirmar": True, "dados": {"id": 34, "categorias": categories}}, "empresa")

    assert error.value.code == "invalid_parameter"
    service.assert_not_called()


@pytest.mark.parametrize("extra", [
    {"confirmar": False}, {"confirmar": 1}, {"confirmar": None},
    {"atualizar_cache": True}, {"incluir_dados_omie": "S"},
    {"pagina": 1}, {"filtros": {"id": 34}},
])
def test_invalid_top_level_controls_are_refused_before_service_access(tmp_path, monkeypatch, extra):
    service = Mock()
    monkeypatch.setattr(finance, "OmieService", service)
    arguments = {"confirmar": True, "dados": {"id": 34, "categorias": CATEGORIES}, **extra}

    with pytest.raises(OmieError):
        facade(tmp_path).call(TOOL, arguments, "empresa")

    service.assert_not_called()


@pytest.mark.parametrize("identifier,native_identifier", [
    ({"id": 34}, {"codigo_lancamento_omie": 34}),
    ({"codigo_integracao": "000034"}, {"codigo_lancamento_integracao": "000034"}),
    ({"codigo_integracao": "X" * 60}, {"codigo_lancamento_integracao": "X" * 60}),
])
@pytest.mark.parametrize("include_native", [False, True])
def test_valid_replacement_translates_only_identifier_and_percentages(tmp_path, monkeypatch, identifier, native_identifier, include_native):
    service = Mock()
    constructor = Mock(return_value=service)
    expected = {"alvo": {"recurso": "contas_pagar", "id": 34}, "categorias": CATEGORIES, "baixas": [], "verificacao": {"titulo_confirmado": True}}
    define = Mock(return_value=expected)
    monkeypatch.setattr(finance, "OmieService", constructor)
    monkeypatch.setattr(finance, "define_categories", define)
    arguments = {"confirmar": True, "dados": {**identifier, "categorias": CATEGORIES}, "incluir_dados_omie": include_native}

    result = facade(tmp_path).call(TOOL, arguments, "empresa")

    assert result == expected
    constructor.assert_called_once_with(tmp_path)
    define.assert_called_once_with(service, "empresa", native_identifier, [{"codigo_categoria": "2.01.03", "percentual": 100}], include_native=include_native)
    service.invoke_financial.assert_not_called()


def test_general_payable_update_points_to_verified_category_replacement(tmp_path, monkeypatch):
    service = Mock()
    monkeypatch.setattr(finance, "OmieService", service)

    with pytest.raises(OmieError) as error:
        facade(tmp_path).call("omie.financeiro.contas_pagar.alterar", {"confirmar": True, "dados": {"id": 34, "categorias": CATEGORIES}}, "empresa")

    assert error.value.code == "invalid_parameter"
    assert error.value.details["use_instead"] == [TOOL]
    assert error.value.details["field"] == "dados.categorias"
    service.assert_not_called()


def test_native_movement_filters_preserve_title_and_payment_discovery_fields():
    query = {"nPagina": 1, "nRegPorPagina": 50, "nCodTitulo": 34, "cTpLancamento": "BXCP", "cNatureza": "P", "lDadosCad": True, "dDtEmisDe": "01/01/2020", "dDtEmisAte": "01/01/2020"}

    assert build_params("financial_movements_list", {"params": query}) == query


def test_native_payable_update_preserves_the_complete_category_list():
    body = {"codigo_lancamento_omie": 34, "categorias": [{"codigo_categoria": "2.01.03", "percentual": 100}]}

    assert build_params("payables_update", {"body": body}) == body


def test_replacement_reaches_native_contract_and_confirms_discounted_reconciled_payment(tmp_path, monkeypatch):
    """Preserva fachada, serviço e tradutores reais; substitui somente perfil e HTTP."""
    title_before = {
        "codigo_lancamento_omie": 34, "codigo_lancamento_integracao": "TEST-TITLE-34",
        "codigo_cliente_fornecedor": 90, "data_emissao": "01/02/2020",
        "data_vencimento": "06/02/2020", "valor_documento": 1000,
        "codigo_categoria": "2.07.01", "status_titulo": "PAGO",
        "categorias": [{"codigo_categoria": "2.01.03", "percentual": 80, "valor": 800},
                       {"codigo_categoria": "2.07.01", "percentual": 20, "valor": 200}],
    }
    financial_before = {
        "detalhes": {"nCodTitulo": 34, "cNatureza": "P", "nValorTitulo": 1000, "cCodCateg": "2.07.01"},
        "resumo": {"cLiquidado": "S", "nValPago": 1000, "nValLiquido": 950,
                   "nValAberto": 0, "nDesconto": 50, "nJuros": 0, "nMulta": 0},
        "categorias": [{"cCodCateg": "2.01.03", "nDistrPercentual": 80, "nDistrValor": 800},
                       {"cCodCateg": "2.07.01", "nDistrPercentual": 20, "nDistrValor": 200}],
    }
    payment_before = {
        "detalhes": {
            "nCodTitulo": 34, "cNatureza": "P", "nCodBaixa": 78,
            "nCodMovCC": 178, "nCodCC": 56, "dDtPagamento": "06/02/2020",
            "nValorMovCC": 950, "nDesconto": 50, "nJuros": 0, "nMulta": 0,
            "dDtConcilia": "06/02/2020", "cHrConcilia": "12:34:56", "cUsConcilia": "TESTE",
        },
        "categorias": [{"cCodCateg": "2.01.03", "nDistrPercentual": 80, "nDistrValor": 760},
                       {"cCodCateg": "2.07.01", "nDistrPercentual": 20, "nDistrValor": 190}],
    }
    title_after = deepcopy(title_before)
    title_after["codigo_categoria"] = "2.01.03"
    title_after["categorias"] = [{"codigo_categoria": "2.01.03", "percentual": 100, "valor": 1000}]
    financial_after = deepcopy(financial_before)
    financial_after["detalhes"]["cCodCateg"] = "2.01.03"
    financial_after["categorias"] = [{"cCodCateg": "2.01.03", "nDistrPercentual": 100, "nDistrValor": 1000}]
    payment_after = deepcopy(payment_before)
    payment_after["categorias"] = [{"cCodCateg": "2.01.03", "nDistrPercentual": 100, "nDistrValor": 950}]

    def one_movement(row):
        return {"nPagina": 1, "nTotPaginas": 1, "nRegistros": 1, "nTotRegistros": 1, "movimentos": [row]}

    request = Mock(side_effect=[
        title_before, one_movement(financial_before), one_movement(payment_before),
        {"codigo_status": "0", "codigo_lancamento_omie": 34},
        title_after, one_movement(financial_after), one_movement(payment_after),
    ])
    profile = Mock(return_value=({}, {}))
    monkeypatch.setattr(OmieService, "_profile", profile)
    monkeypatch.setattr(OmieService, "_request", request)

    result = facade(tmp_path).call(TOOL, {
        "confirmar": True, "dados": {"codigo_integracao": "TEST-TITLE-34", "categorias": CATEGORIES},
    }, "empresa")

    assert result["verificacao"] == {
        "titulo_confirmado": True, "titulo_financeiro_confirmado": True,
        "categorias_financeiro_disponiveis": True,
        "baixas_confirmadas": True, "campos_preservados": True, "quantidade_baixas": 1,
    }
    assert result["baixas"][0]["id_baixa"] == 78
    assert result["baixas"][0]["valor"] == 950
    assert result["baixas"][0]["desconto"] == 50
    assert result["baixas"][0]["conciliacao"] == {
        "conciliado": True, "data": "06/02/2020", "hora": "12:34:56", "usuario": "TESTE",
    }
    assert result["baixas"][0]["categorias"] == [{"codigo": "2.01.03", "percentual": 100, "valor": 950}]
    calls = request.call_args_list
    assert len(calls) == profile.call_count == 7
    assert [call.args[3] for call in calls] == [
        "ConsultarContaPagar", "ListarMovimentos", "ListarMovimentos", "AlterarContaPagar",
        "ConsultarContaPagar", "ListarMovimentos", "ListarMovimentos",
    ]
    assert calls[0].args[2:] == ("payables", "ConsultarContaPagar", {"codigo_lancamento_integracao": "TEST-TITLE-34"})
    assert calls[4].args[2:] == ("payables", "ConsultarContaPagar", {"codigo_lancamento_omie": 34})
    assert calls[3].args[2:] == ("payables", "AlterarContaPagar", {
        "codigo_lancamento_omie": 34, "codigo_categoria": "2.01.03",
        "categorias": [{"codigo_categoria": "2.01.03", "percentual": 100}],
    })
    assert calls[3].kwargs == {"retries": 0}
    for index, kind in ((1, "CP"), (2, "BXCP"), (5, "CP"), (6, "BXCP")):
        assert calls[index].args[2:] == ("financial_movements", "ListarMovimentos", {
            "nCodTitulo": 34, "cNatureza": "P", "cTpLancamento": kind,
            "dDtEmisDe": "01/02/2020", "dDtEmisAte": "01/02/2020",
            "nPagina": 1, "nRegPorPagina": 50, "lDadosCad": True,
        })
    assert all(call.kwargs == {"retries": None, "fresh": True} for index, call in enumerate(calls) if index != 3)
