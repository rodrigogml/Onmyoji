"""Criação idempotente de clientes sem acesso a uma conta Omie real."""
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.omie.contracts import build_params
from onmyoji_daemon.mcp.omie.customers import create_customer
from onmyoji_daemon.mcp.omie.exception import OmieError


def page(rows, number=1, pages=1, total=None):
    return {"pagina": number, "total_de_paginas": pages, "total_de_registros": len(rows) if total is None else total, "clientes_cadastro": rows}


@pytest.mark.parametrize("document", ["52438909000120", "01234567890"])
def test_reuses_unique_active_customer_after_scanning_all_pages(document):
    body = build_params("customers_create", {"body": {"cnpj_cpf": document, "razao_social": "Nome completo"}})
    rows = [{"codigo_cliente_omie": identifier, "cnpj_cpf": "", "inativo": "N"} for identifier in range(1, 51)]
    match = {"codigo_cliente_omie": 51, "cnpj_cpf": body["cnpj_cpf"], "inativo": "N", "razao_social": "Nome existente"}
    request = Mock(side_effect=[page(rows, pages=2, total=51), page([match], number=2, pages=2, total=51)])

    assert create_customer(body, request) == {"codigo_cliente_omie": 51, "created": False, "customer": match}
    assert request.call_count == 2
    for number, call in enumerate(request.call_args_list, 1):
        assert call.args == ("ListarClientes", {"pagina": number, "registros_por_pagina": 50, "apenas_importado_api": "N"})


def test_includes_only_after_complete_absence_without_automatic_retry():
    body = build_params("customers_create", {"body": {"cnpj_cpf": "01234567890", "razao_social": "Nome completo"}})
    request = Mock(side_effect=[page([], pages=0), {"codigo_status": "0", "codigo_cliente_omie": 123}])

    assert create_customer(body, request) == {"codigo_status": "0", "codigo_cliente_omie": 123, "created": True}
    assert request.call_args.args == ("IncluirCliente", body)
    assert request.call_args.kwargs == {"retries": 0}


MATCH = {"codigo_cliente_omie": 1, "cnpj_cpf": "52438909000120", "inativo": "N"}


@pytest.mark.parametrize("responses,code", [
    ([page([MATCH, {**MATCH, "codigo_cliente_omie": 2}])], "ambiguous_customer"),
    ([page([{**MATCH, "inativo": "S"}])], "customer_inactive"),
    ([page([{**MATCH, "inativo": None}])], "invalid_response"),
    ([{}], "invalid_response"),
    ([page([MATCH], total=2)], "invalid_response"),
    ([page([MATCH, MATCH])], "invalid_response"),
    ([page([], pages=2, total=1)], "invalid_response"),
    ([OmieError("network_error", "Falha de rede")], "network_error"),
    ([page([MATCH] * 51, pages=2, total=51)], "invalid_response"),
    ([page([{**MATCH, "codigo_cliente_omie": True}])], "invalid_response"),
    ([page([None])], "invalid_response"),
    ([page([{**MATCH, "cnpj_cpf": None}])], "invalid_response"),
])
def test_ambiguous_inactive_or_incomplete_results_prevent_inclusion(responses, code):
    request = Mock(side_effect=responses)
    body = build_params("customers_create", {"body": {"cnpj_cpf": "52438909000120", "razao_social": "Nome"}})
    with pytest.raises(OmieError) as error:
        create_customer(body, request)
    assert error.value.code == code
    assert all(call.args[0] == "ListarClientes" for call in request.call_args_list)


@pytest.mark.parametrize("response,code", [
    ({"codigo_status": "1", "descricao_status": "sensitive"}, "omie_api_error"),
    ({"codigo_status": "0"}, "invalid_response"),
    ({"codigo_status": "0", "codigo_cliente_omie": True}, "invalid_response"),
])
def test_inclusion_requires_success_status_and_valid_identifier(response, code):
    request = Mock(side_effect=[page([]), response])
    with pytest.raises(OmieError) as error:
        create_customer({"cnpj_cpf": "01234567890"}, request)
    assert error.value.code == code
    assert "sensitive" not in str(error.value)
