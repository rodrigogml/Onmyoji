"""Contratos públicos da fachada financeira da Omie."""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.omie import OmieError, OmieService
from onmyoji_daemon.mcp.omie.contracts import DIRECT_DOCUMENT_TYPES
from onmyoji_daemon.mcp.omie.finance import OmieFinanceFacade, SPECS
from onmyoji_daemon.mcp.omie.publication import input_contract


class Configuration:
    def __init__(self, root): self.root = root


PERIOD = {"data_inicial": "01/11/2026", "data_final": "30/11/2026"}
ACCOUNT_QUERY = "omie.financeiro.lancamentos_conta.listar_por_periodo"
RECONCILIATION_QUERY = "omie.financeiro.movimentos.listar_para_conciliacao"
DOCUMENT_CATALOG = "omie.financeiro.tipos_documento.listar"
DETAIL_QUERIES = [
    ("contas_correntes", {"id": 12}, "bank_accounts", "ConsultarContaCorrente", {"nCodCC": 12}),
    ("categorias", {"codigo": "1.01"}, "categories", "ConsultarCategoria", {"codigo": "1.01"}),
    ("departamentos", {"codigo": "D1"}, "departments", "ConsultarDepartamento", {"codigo": "D1"}),
    ("projetos", {"id": 12}, "projects", "ConsultarProjeto", {"codigo": 12}),
    ("contas_pagar", {"id": 12}, "payables", "ConsultarContaPagar", {"codigo_lancamento_omie": 12}),
    ("contas_receber", {"id": 12}, "receivables", "ConsultarContaReceber", {"codigo_lancamento_omie": 12}),
    ("lancamentos_conta", {"id": 12}, "account_transactions", "ConsultaLancCC", {"nCodLanc": 12}),
]
INTEGRATION_IDENTIFIERS = [
    ("contas_correntes", "cCodCCInt", 20),
    ("projetos", "codInt", 20),
    ("contas_pagar", "codigo_lancamento_integracao", 60),
    ("contas_receber", "codigo_lancamento_integracao", 60),
    ("lancamentos_conta", "cCodIntLanc", 20),
]
IDENTIFIER_METHODS = [
    (f"omie.financeiro.{resource}.consultar", "filtros", filters, {"codigo"} if resource in {"categorias", "departamentos"} else {"id", "codigo_integracao"})
    for resource, filters, *_ in DETAIL_QUERIES
] + [
    (f"omie.financeiro.{resource}.excluir", "dados", {"id": 12}, {"id", "codigo_integracao"})
    for resource in ("contas_pagar", "contas_receber", "lancamentos_conta")
]
TRANSACTION_CREATE = "omie.financeiro.lancamentos_conta.criar"
TRANSACTION_UPDATE = "omie.financeiro.lancamentos_conta.alterar"


def transaction_data():
    """Valores sintéticos; nenhum lançamento de usuário é reproduzido nos testes."""
    return {
        "codigo_integracao": "TEST-LANC-001", "conta_corrente_id": 8000000111,
        "data": "15/11/2026", "valor": 123.45, "categoria_codigo": "2.01", "tipo_documento": "99999",
    }


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


@pytest.mark.parametrize("multiple_profiles", [False, True])
def test_document_catalog_schema_requires_no_filters_or_pagination(tmp_path, multiple_profiles):
    profiles = [{"name": "empresa", "description": "ERP"}]
    if multiple_profiles:
        profiles.append({"name": "filial", "description": "ERP filial"})
    tool = next(tool for tool in OmieFinanceFacade(Configuration(tmp_path)).definitions(profiles) if tool["name"] == DOCUMENT_CATALOG)
    schema = tool["input_schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert set(schema["properties"]) == ({"incluir_dados_omie", "profile"} if multiple_profiles else {"incluir_dados_omie"})
    arguments = {"profile": "filial"} if multiple_profiles else {}
    validator.validate(arguments)
    validator.validate({**arguments, "incluir_dados_omie": True})
    assert validator.is_valid({}) is not multiple_profiles
    for extra in ({"filtros": {}}, {"pagina": 1}, {"tamanho_pagina": 50}):
        assert not validator.is_valid({**arguments, **extra})
    if multiple_profiles:
        assert not validator.is_valid({"profile": "desconhecido"})
    assert "sem paginação" in tool["description"].lower()


@pytest.mark.parametrize("arguments", [{}, {"incluir_dados_omie": True}])
def test_document_catalog_reaches_omie_once_with_only_an_empty_code(tmp_path, monkeypatch, arguments):
    rows = [{"codigo": "FAT", "descricao": "Fatura"}, {"codigo": "NFS", "descricao": "Nota Fiscal de Serviço"}]
    response = {"tipo_documento_cadastro": rows}
    profile, request = mock_remote(monkeypatch, [deepcopy(response)])

    result = OmieFinanceFacade(Configuration(tmp_path)).call(DOCUMENT_CATALOG, arguments, "empresa")

    profile.assert_called_once_with("empresa")
    request.assert_called_once_with({}, {}, "document_types", "PesquisarTipoDocumento", {"codigo": ""}, retries=None)
    assert result["dados"] == rows
    assert "paginacao" not in result
    if arguments.get("incluir_dados_omie"):
        assert result["dados_omie"] == response
    else:
        assert "dados_omie" not in result


@pytest.mark.parametrize("arguments", [{"filtros": {}}, {"filtros": {"codigo": "FAT"}}, {"params": {}}, {"codigo": "FAT"}])
def test_document_catalog_rejects_unsupported_arguments_before_access(tmp_path, monkeypatch, arguments):
    profile, request = mock_remote(monkeypatch, [])

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(DOCUMENT_CATALOG, arguments, "empresa")

    assert error.value.code == "invalid_parameter"
    profile.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize("resource,filters,endpoint,method,native", DETAIL_QUERIES, ids=[case[0] for case in DETAIL_QUERIES])
def test_individual_queries_publish_no_pagination_and_send_only_identifiers(tmp_path, monkeypatch, resource, filters, endpoint, method, native):
    name = f"omie.financeiro.{resource}.consultar"
    facade = OmieFinanceFacade(Configuration(tmp_path))
    tool = next(tool for tool in facade.definitions([{"name": "empresa", "description": "ERP"}]) if tool["name"] == name)
    validator = Draft202012Validator(tool["input_schema"])
    arguments = {"filtros": filters}
    validator.validate(arguments)
    for extra in ({"pagina": 1}, {"tamanho_pagina": 50}):
        assert not validator.is_valid({**arguments, **extra})
    response = {"codigo": 12}
    _, request = mock_remote(monkeypatch, [response])

    result = facade.call(name, arguments, "empresa")

    request.assert_called_once_with({}, {}, endpoint, method, native, retries=None)
    assert result["dados"] == [response]
    assert "paginacao" not in result


@pytest.mark.parametrize("name,arguments", [
    (DOCUMENT_CATALOG, {}),
    (RECONCILIATION_QUERY, {"filtros": {**PERIOD, "conta_corrente_id": 12}}),
] + [(f"omie.financeiro.{resource}.consultar", {"filtros": filters}) for resource, filters, *_ in DETAIL_QUERIES])
@pytest.mark.parametrize("pagination", [{"pagina": 1}, {"tamanho_pagina": 50}, {"tamanho_pagina": 100}])
def test_unpaginated_queries_reject_public_pagination_before_access(tmp_path, monkeypatch, name, arguments, pagination):
    profile, request = mock_remote(monkeypatch, [])

    with pytest.raises(OmieError, match="não aceita paginação") as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(name, {**arguments, **pagination}, "empresa")

    assert error.value.code == "invalid_parameter"
    profile.assert_not_called()
    request.assert_not_called()


def test_paginated_directory_listing_still_accepts_and_forwards_pagination(tmp_path, monkeypatch):
    name = "omie.financeiro.departamentos.listar"
    arguments = {"pagina": 2, "tamanho_pagina": 2}
    rows = [{"codigo": "D3", "descricao": "Terceiro"}, {"codigo": "D4", "descricao": "Quarto"}]
    response = {"pagina": 2, "total_de_paginas": 3, "total_de_registros": 5, "departamentos": rows}
    facade = OmieFinanceFacade(Configuration(tmp_path))
    schema = next(tool["input_schema"] for tool in facade.definitions([{"name": "empresa", "description": "ERP"}]) if tool["name"] == name)
    Draft202012Validator(schema).validate(arguments)
    _, request = mock_remote(monkeypatch, [response])

    result = facade.call(name, arguments, "empresa")

    request.assert_called_once_with({}, {}, "departments", "ListarDepartamentos", {"pagina": 2, "registros_por_pagina": 2}, retries=None)
    assert result["dados"] == rows
    assert result["paginacao"] == {"pagina": 2, "total_paginas": 3, "total_registros": 5, "proxima_pagina": 3}


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


@pytest.mark.parametrize("name,container,identifier,allowed", IDENTIFIER_METHODS)
def test_identifier_schemas_are_specific_and_require_exactly_one_key(tmp_path, name, container, identifier, allowed):
    tools = OmieFinanceFacade(Configuration(tmp_path)).definitions([{"name": "empresa", "description": "ERP"}])
    schema = next(tool["input_schema"] for tool in tools if tool["name"] == name)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    arguments = {container: identifier, **({"confirmar": True} if container == "dados" else {})}
    validator.validate(arguments)
    assert set(schema["properties"][container]["properties"]) == allowed
    assert not validator.is_valid({**arguments, container: {}})
    if allowed == {"id", "codigo_integracao"}:
        assert not validator.is_valid({**arguments, container: {"id": 12, "codigo_integracao": "TEST-12"}})
        assert not validator.is_valid({**arguments, container: {"codigo": "9000000222"}})
    else:
        assert not validator.is_valid({**arguments, container: {"id": 12}})


@pytest.mark.parametrize("name,container", [
    (name, container) for name, container, _, allowed in IDENTIFIER_METHODS if allowed == {"id", "codigo_integracao"}
])
def test_ambiguous_codigo_is_rejected_with_identifier_guidance_before_access(tmp_path, monkeypatch, name, container):
    profile, request = mock_remote(monkeypatch, [])
    arguments = {container: {"codigo": "9000000222"}, **({"confirmar": True} if container == "dados" else {})}

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(name, arguments, "empresa")

    assert error.value.code == "invalid_parameter"
    assert {f"{container}.id", f"{container}.codigo_integracao"} <= set(error.value.details["use_instead"])
    profile.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize("resource,native_key,limit", INTEGRATION_IDENTIFIERS)
def test_lookup_by_integration_code_preserves_text_and_documented_limit(tmp_path, monkeypatch, resource, native_key, limit):
    code = "0" + "X" * (limit - 1)
    name = f"omie.financeiro.{resource}.consultar"
    endpoint, method = next(case[2:4] for case in DETAIL_QUERIES if case[0] == resource)
    _, request = mock_remote(monkeypatch, [{"resultado": "encontrado"}])

    OmieFinanceFacade(Configuration(tmp_path)).call(name, {"filtros": {"codigo_integracao": code}}, "empresa")

    request.assert_called_once_with({}, {}, endpoint, method, {native_key: code}, retries=None)


def test_transaction_lookup_preserves_an_omie_id_larger_than_32_bits(tmp_path, monkeypatch):
    identifier = 9000000222
    _, request = mock_remote(monkeypatch, [{"nCodLanc": identifier}])

    result = OmieFinanceFacade(Configuration(tmp_path)).call(
        "omie.financeiro.lancamentos_conta.consultar", {"filtros": {"id": identifier}}, "empresa",
    )

    request.assert_called_once_with({}, {}, "account_transactions", "ConsultaLancCC", {"nCodLanc": identifier}, retries=None)
    assert result["dados"] == [{"nCodLanc": identifier}]


@pytest.mark.parametrize("departments", [[], [{"cCodDep": "D1", "nPerDep": 100}]])
def test_transaction_lookup_preserves_the_complete_record_with_nested_lists(tmp_path, monkeypatch, departments):
    response = {
        "nCodLanc": 9000000222,
        "cabecalho": {"nCodCC": 8000000111, "dDtLanc": "15/11/2026", "nValorLanc": 123.45},
        "detalhes": {"cCodCateg": "2.01", "cTipo": "99999"},
        "departamentos": departments,
    }
    _, request = mock_remote(monkeypatch, [deepcopy(response)])

    result = OmieFinanceFacade(Configuration(tmp_path)).call(
        "omie.financeiro.lancamentos_conta.consultar",
        {"filtros": {"id": 9000000222}, "incluir_dados_omie": True}, "empresa",
    )

    request.assert_called_once_with({}, {}, "account_transactions", "ConsultaLancCC", {"nCodLanc": 9000000222}, retries=None)
    assert result["dados"] == [response]
    assert result["dados_omie"] == response


@pytest.mark.parametrize("resource,limit", [("categorias", 20), ("departamentos", 40)])
def test_lookup_by_catalog_code_preserves_leading_zero_and_documented_limit(tmp_path, monkeypatch, resource, limit):
    code = "0" + "X" * (limit - 1)
    endpoint, method = next(case[2:4] for case in DETAIL_QUERIES if case[0] == resource)
    _, request = mock_remote(monkeypatch, [{"codigo": code}])

    OmieFinanceFacade(Configuration(tmp_path)).call(f"omie.financeiro.{resource}.consultar", {"filtros": {"codigo": code}}, "empresa")

    request.assert_called_once_with({}, {}, endpoint, method, {"codigo": code}, retries=None)


@pytest.mark.parametrize("resource,field,limit", [
    (resource, "codigo_integracao", limit) for resource, _, limit in INTEGRATION_IDENTIFIERS
] + [("categorias", "codigo", 20), ("departamentos", "codigo", 40)])
def test_identifier_length_limits_are_enforced_before_access(tmp_path, monkeypatch, resource, field, limit):
    profile, request = mock_remote(monkeypatch, [])

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(
            f"omie.financeiro.{resource}.consultar", {"filtros": {field: "X" * (limit + 1)}}, "empresa",
        )

    assert error.value.code == "invalid_parameter"
    profile.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize("name,container,identifier", [
    ("omie.financeiro.lancamentos_conta.consultar", "filtros", {}),
    ("omie.financeiro.lancamentos_conta.consultar", "filtros", {"id": 12, "codigo_integracao": "TEST-12"}),
    ("omie.financeiro.lancamentos_conta.consultar", "filtros", {"id": True}),
    ("omie.financeiro.lancamentos_conta.consultar", "filtros", {"id": "9000000222"}),
    ("omie.financeiro.lancamentos_conta.consultar", "filtros", {"id": 0}),
    ("omie.financeiro.lancamentos_conta.consultar", "filtros", {"codigo_integracao": " "}),
    ("omie.financeiro.categorias.consultar", "filtros", {"codigo": 12}),
    ("omie.financeiro.departamentos.consultar", "filtros", {"codigo": True}),
    ("omie.financeiro.lancamentos_conta.excluir", "dados", {}),
    ("omie.financeiro.lancamentos_conta.excluir", "dados", {"id": 12, "codigo_integracao": "TEST-12"}),
])
def test_invalid_identifier_type_or_cardinality_never_reaches_credentials(tmp_path, monkeypatch, name, container, identifier):
    profile, request = mock_remote(monkeypatch, [])
    arguments = {container: identifier, **({"confirmar": True} if container == "dados" else {})}

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(name, arguments, "empresa")

    assert error.value.code == "invalid_parameter"
    profile.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize("resource,endpoint,method,id_key,integration_key", [
    ("contas_pagar", "payables", "ExcluirContaPagar", "codigo_lancamento_omie", "codigo_lancamento_integracao"),
    ("contas_receber", "receivables", "ExcluirContaReceber", "codigo_lancamento_omie", "codigo_lancamento_integracao"),
    ("lancamentos_conta", "account_transactions", "ExcluirLancCC", "nCodLanc", "cCodIntLanc"),
])
@pytest.mark.parametrize("by_integration", [False, True])
def test_delete_uses_the_same_exact_identifier_as_lookup(tmp_path, monkeypatch, resource, endpoint, method, id_key, integration_key, by_integration):
    value = "TEST-LANC-001" if by_integration else 9000000222
    public_key = "codigo_integracao" if by_integration else "id"
    native_key = integration_key if by_integration else id_key
    _, request = mock_remote(monkeypatch, [{"cCodStatus": "0"}])

    OmieFinanceFacade(Configuration(tmp_path)).call(
        f"omie.financeiro.{resource}.excluir", {"confirmar": True, "dados": {public_key: value}}, "empresa",
    )

    request.assert_called_once_with({}, {}, endpoint, method, {native_key: value}, retries=0)


def test_transaction_write_schemas_publish_exact_fields_and_required_identifiers(tmp_path):
    tools = {tool["name"]: tool for tool in OmieFinanceFacade(Configuration(tmp_path)).definitions([{"name": "empresa", "description": "ERP"}])}
    base = transaction_data()
    expected_create = set(base) | {"numero_documento", "cliente_id", "projeto_id", "observacao", "departamentos"}
    create_schema = tools[TRANSACTION_CREATE]["input_schema"]
    update_schema = tools[TRANSACTION_UPDATE]["input_schema"]
    create = Draft202012Validator(create_schema)
    update = Draft202012Validator(update_schema)
    for schema in (create_schema, update_schema):
        Draft202012Validator.check_schema(schema)
        properties = schema["properties"]["dados"]["properties"]
        assert properties["codigo_integracao"]["maxLength"] == 20
        assert set(properties["tipo_documento"]["enum"]) == set(DIRECT_DOCUMENT_TYPES)
    assert set(create_schema["properties"]["dados"]["properties"]) == expected_create
    assert set(update_schema["properties"]["dados"]["properties"]) == expected_create | {"id"}
    create.validate({"confirmar": True, "dados": base})
    assert not create.is_valid({"confirmar": True, "dados": {**base, "id": 9000000222}})
    for missing in base:
        assert not create.is_valid({"confirmar": True, "dados": {key: value for key, value in base.items() if key != missing}})
    without_identifier = {key: value for key, value in base.items() if key != "codigo_integracao"}
    assert not update.is_valid({"confirmar": True, "dados": without_identifier})
    for missing in without_identifier:
        incomplete = {key: value for key, value in without_identifier.items() if key != missing}
        assert not update.is_valid({"confirmar": True, "dados": {**incomplete, "id": 9000000222}})
    for identifier in ({"id": 9000000222}, {"codigo_integracao": "TEST-LANC-001"}, {"id": 9000000222, "codigo_integracao": "TEST-LANC-001"}):
        update.validate({"confirmar": True, "dados": {**without_identifier, **identifier}})


@pytest.mark.parametrize("missing", list(transaction_data()))
def test_transaction_creation_requires_semantic_fields_before_access(tmp_path, monkeypatch, missing):
    data = transaction_data()
    del data[missing]
    profile, request = mock_remote(monkeypatch, [])

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(TRANSACTION_CREATE, {"confirmar": True, "dados": data}, "empresa")

    assert error.value.code == "invalid_parameter"
    if missing == "codigo_integracao":
        assert error.value.details["field"] == "dados.codigo_integracao"
    profile.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"codigo_integracao": ""}, {"codigo_integracao": "X" * 21},
    {"conta_corrente_id": True}, {"conta_corrente_id": 0}, {"conta_corrente_id": "8000000111"},
    {"data": "31/11/2026"}, {"valor": 0}, {"valor": -1}, {"valor": True},
    {"tipo_documento": "PAG"}, {"categoria_codigo": " "}, {"numero_documento": "X" * 21},
    {"cliente_id": True}, {"id": 9000000222}, {"data_vencimento": "15/11/2026"},
])
def test_invalid_transaction_data_never_reaches_credentials_or_http(tmp_path, monkeypatch, changes):
    profile, request = mock_remote(monkeypatch, [])

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(
            TRANSACTION_CREATE, {"confirmar": True, "dados": {**transaction_data(), **changes}}, "empresa",
        )

    assert error.value.code == "invalid_parameter"
    profile.assert_not_called()
    request.assert_not_called()


def test_valid_direct_transaction_reaches_the_native_contract_without_unrelated_fields(tmp_path, monkeypatch):
    data = {**transaction_data(), "numero_documento": "DOC-1", "cliente_id": 8000000222, "projeto_id": 8000000333, "observacao": "Teste sintético", "departamentos": []}
    response = {"nCodLanc": 9000000222, "departamentos": []}
    _, request = mock_remote(monkeypatch, [response])

    result = OmieFinanceFacade(Configuration(tmp_path)).call(TRANSACTION_CREATE, {"confirmar": True, "dados": data}, "empresa")

    request.assert_called_once_with({}, {}, "account_transactions", "IncluirLancCC", {
        "cCodIntLanc": "TEST-LANC-001",
        "cabecalho": {"nCodCC": 8000000111, "dDtLanc": "15/11/2026", "nValorLanc": 123.45},
        "detalhes": {"cCodCateg": "2.01", "cTipo": "99999", "cNumDoc": "DOC-1", "nCodCliente": 8000000222, "nCodProjeto": 8000000333, "cObs": "Teste sintético"},
        "departamentos": [],
    }, retries=0)
    assert result["dados"] == [response]


@pytest.mark.parametrize("missing", ["identifier", "conta_corrente_id", "data", "valor", "categoria_codigo", "tipo_documento"])
def test_transaction_update_requires_identity_and_complete_financial_data_before_access(tmp_path, monkeypatch, missing):
    data = {key: value for key, value in transaction_data().items() if key != "codigo_integracao"}
    if missing != "identifier":
        data["id"] = 9000000222
        del data[missing]
    profile, request = mock_remote(monkeypatch, [])

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(TRANSACTION_UPDATE, {"confirmar": True, "dados": data}, "empresa")

    assert error.value.code == "invalid_parameter"
    profile.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize("identifiers,native", [
    ({"id": 9000000222}, {"nCodLanc": 9000000222}),
    ({"codigo_integracao": "TEST-LANC-001"}, {"cCodIntLanc": "TEST-LANC-001"}),
    ({"id": 9000000222, "codigo_integracao": "TEST-LANC-001"}, {"nCodLanc": 9000000222, "cCodIntLanc": "TEST-LANC-001"}),
])
def test_transaction_update_preserves_either_or_both_identifiers(tmp_path, monkeypatch, identifiers, native):
    data = {key: value for key, value in transaction_data().items() if key != "codigo_integracao"}
    _, request = mock_remote(monkeypatch, [{"nCodLanc": 9000000222}])

    OmieFinanceFacade(Configuration(tmp_path)).call(TRANSACTION_UPDATE, {"confirmar": True, "dados": {**data, **identifiers}}, "empresa")

    request.assert_called_once_with({}, {}, "account_transactions", "AlterarLancCC", {
        **native,
        "cabecalho": {"nCodCC": 8000000111, "dDtLanc": "15/11/2026", "nValorLanc": 123.45},
        "detalhes": {"cCodCateg": "2.01", "cTipo": "99999"},
    }, retries=0)
