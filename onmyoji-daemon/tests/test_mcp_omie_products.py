"""Regressões de consulta de produtos e associação segura no recebimento Omie."""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator, ValidationError

from onmyoji_daemon.mcp.omie.contracts import build_params
from onmyoji_daemon.mcp.omie.exception import OmieError
from onmyoji_daemon.mcp.omie.service import ENDPOINTS, OPERATIONS, OmieService
from onmyoji_daemon.mcp.omie.tools import OmieToolRegistry


class Configuration:
    def __init__(self, root):
        self.root = root

    def enabled_omie_profiles(self):
        return [{"name": "empresa", "description": "ERP de teste"}]


@pytest.fixture
def schemas(tmp_path):
    tools = OmieToolRegistry(Configuration(tmp_path)).definitions()
    return {tool["name"]: tool["input_schema"] for tool in tools}


def test_product_tools_are_read_only_and_use_the_official_endpoint(schemas):
    assert ENDPOINTS["products"] == "https://app.omie.com.br/api/v1/geral/produtos/"
    assert OPERATIONS["products_list"] == ("products", "ListarProdutos", False)
    assert OPERATIONS["products_get"] == ("products", "ConsultarProduto", False)
    for name in ("omie.products_list", "omie.products_get"):
        assert name in schemas
        assert "confirm" not in schemas[name]["properties"]
        Draft202012Validator.check_schema(schemas[name])


def test_product_list_defaults_and_ean_preserves_leading_zeros(schemas):
    assert build_params("products_list", {"params": {}}) == {
        "pagina": 1, "registros_por_pagina": 50,
        "filtrar_apenas_omiepdv": "N", "apenas_importado_api": "N",
    }
    params = {"ean": "0012345678901", "pagina": 3, "registros_por_pagina": 100,
              "filtrar_apenas_descricao": "%PISO%"}
    normalized = build_params("products_list", {"params": params})
    assert normalized["ean"] == "0012345678901"
    assert normalized["pagina"] == 3
    assert normalized["registros_por_pagina"] == 100
    assert normalized["filtrar_apenas_descricao"] == "%PISO%"
    assert normalized["filtrar_apenas_omiepdv"] == "N"
    Draft202012Validator(schemas["omie.products_list"]).validate({"params": params})


@pytest.mark.parametrize("params", [
    {"ean": 1234567890123}, {"ean": "123X"}, {"ean": "1" * 15},
    {"inativo": True}, {"filtrar_apenas_omiepdv": "X"},
    {"pagina": True}, {"registros_por_pagina": 501}, {"page": 1, "pagina": 2},
    {"filtrar_apenas_descricao": ""}, {"ncm": "1" * 14},
    {"codigo": "SKU-1"},
])
def test_product_list_invalid_filters_stop_before_configuration(params, tmp_path):
    service = OmieService(tmp_path)
    service._profile = Mock(side_effect=AssertionError("Não abrir o cofre"))
    with pytest.raises(OmieError):
        service.invoke("empresa", "products_list", {"params": params})
    service._profile.assert_not_called()


@pytest.mark.parametrize("params,expected", [
    ({"codigo_produto": 1234}, {"codigo_produto": 1234}),
    ({"codigo": "SKU-001"}, {"codigo": "SKU-001"}),
    ({"codigo_produto_integracao": "INT-01"}, {"codigo_produto_integracao": "INT-01"}),
])
def test_product_get_accepts_exactly_one_identifier(params, expected, schemas):
    assert build_params("products_get", {"params": params}) == expected
    Draft202012Validator(schemas["omie.products_get"]).validate({"params": params})


@pytest.mark.parametrize("params", [
    {}, {"codigo_produto": 0}, {"codigo_produto": True}, {"codigo_produto": "123"},
    {"codigo": ""}, {"codigo": "X" * 61}, {"codigo_produto_integracao": " "},
    {"codigo": "ABC", "codigo_produto": 1}, {"ean": "1234567890123"},
])
def test_product_get_rejects_missing_ambiguous_or_invalid_identifiers(params, schemas, tmp_path):
    service = OmieService(tmp_path)
    service._profile = Mock(side_effect=AssertionError("Não abrir o cofre"))
    with pytest.raises(OmieError):
        service.invoke("empresa", "products_get", {"params": params})
    service._profile.assert_not_called()
    # O schema pode ser mais restritivo que a tipagem Python, mas nunca pode
    # permitir identificadores múltiplos ou campos alheios ao contrato.
    if len(params) != 1 or "ean" in params or "codigo_produto" in params and (
            type(params["codigo_produto"]) is not int or params["codigo_produto"] < 1):
        with pytest.raises(ValidationError):
            Draft202012Validator(schemas["omie.products_get"]).validate({"params": params})


def association(sequence=1, product_id=123456):
    return {"itensIde": {"nSequencia": sequence, "cAcao": "ASSOCIAR-PRODUTO",
                         "nIdProdutoExistente": product_id}}


def test_receipt_association_preserves_official_payload_and_schemas(schemas):
    items = [association(1, 123456), association(2, 654321)]
    body = {"receipt_id": 8881614346, "items": items}
    expected = {"ide": {"nIdReceb": 8881614346}, "itensRecebimentoEditar": items}
    assert build_params("inbound_nfe_receipts_update", {"body": body}) == expected
    Draft202012Validator(schemas["omie.inbound_nfe_receipts_update"]).validate({
        "confirm": True, "body": body,
    })
    # Compatibilidade com o formato nativo e o envelope de item único.
    native = {"ide": {"nIdReceb": 8881614346}, "itensRecebimentoEditar": association()}
    assert build_params("inbound_nfe_receipts_update", {"body": native}) == {
        "ide": {"nIdReceb": 8881614346},
        "itensRecebimentoEditar": association(),
    }
    Draft202012Validator(schemas["omie.inbound_nfe_receipts_update"]).validate({
        "confirm": True, "body": native,
    })


@pytest.mark.parametrize("items", [
    [association(1, 0)], [association(1, True)],
    [{"itensIde": {"nSequencia": 1, "cAcao": "ASSOCIAR-PRODUTO"}}],
    [{"itensIde": {"nSequencia": 1, "cAcao": "NOVO", "nIdProdutoExistente": 123}}],
    [association(1), association(1, 999)],
    [{"itensIde": {"nSequencia": 0, "cAcao": "ASSOCIAR-PRODUTO", "nIdProdutoExistente": 123}}],
])
def test_receipt_association_invalid_payloads_are_blocked(items, schemas, tmp_path):
    body = {"receipt_id": 8881614346, "items": items}
    service = OmieService(tmp_path)
    service._profile = Mock(side_effect=AssertionError("Não abrir o cofre"))
    with pytest.raises(OmieError) as exc:
        service.invoke("empresa", "inbound_nfe_receipts_update", {"confirm": True, "body": body})
    assert exc.value.code == "invalid_body"
    service._profile.assert_not_called()
    if len(items) == 1:
        with pytest.raises(ValidationError):
            Draft202012Validator(schemas["omie.inbound_nfe_receipts_update"]).validate({
                "confirm": True, "body": body,
            })


def test_product_reads_route_without_writing(tmp_path):
    service = OmieService(tmp_path)
    service._profile = Mock(return_value=({}, {}))
    service._request = Mock(return_value={"produto_servico_cadastro": [
        {"codigo_produto": 123456, "codigo": "SKU", "ean": "0012345678901"}]})
    result = service.invoke("empresa", "products_list", {"params": {"ean": "0012345678901"}})
    assert result["data"]["produto_servico_cadastro"][0]["codigo_produto"] == 123456
    assert service._request.call_args.args[2:4] == ("products", "ListarProdutos")
    assert service._request.call_args.kwargs == {"retries": None}


def test_product_tools_use_existing_profile_and_are_not_writes(tmp_path, schemas):
    registry = OmieToolRegistry(Configuration(tmp_path))
    names = {tool["name"] for tool in registry.definitions()}
    assert {"omie.products_list", "omie.products_get"} <= names
    for name in ("omie.products_list", "omie.products_get"):
        assert not schemas[name].get("required") or "confirm" not in schemas[name]["required"]
