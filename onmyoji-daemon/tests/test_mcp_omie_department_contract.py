"""Contratos públicos que separam edição financeira de definição de rateio."""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.omie.exception import OmieError
from onmyoji_daemon.mcp.omie.finance import OmieFinanceFacade
from onmyoji_daemon.mcp.omie.service import OmieService


class Configuration:
    def __init__(self, root): self.root = root


@pytest.mark.parametrize("resource,title", [("lancamentos_conta", False), ("contas_pagar", True), ("contas_receber", True)])
def test_dedicated_tools_publish_only_identity_allocation_and_optional_link(tmp_path, resource, title):
    name = f"omie.financeiro.{resource}.definir_departamentos"
    tools = OmieFinanceFacade(Configuration(tmp_path)).definitions([{"name": "empresa"}])
    tool = next(item for item in tools if item["name"] == name)
    schema = tool["input_schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    data = {"id": 12, "departamentos": [{"codigo": "000123", "percentual": 100}]}
    validator.validate({"confirmar": True, "dados": data})
    validator.validate({"confirmar": True, "dados": {"codigo_integracao": "I-12", "departamentos": []}})
    assert set(schema["properties"]["dados"]["properties"]) == {"id", "codigo_integracao", "departamentos"} | ({"lancamento_conta_id"} if title else set())
    assert validator.is_valid({"confirmar": True, "dados": {**data, "lancamento_conta_id": 34}}) is title
    for bad in ({"id": 12}, {"departamentos": []}, {**data, "codigo_integracao": "I-12"}, {**data, "valor": 100}):
        assert not validator.is_valid({"confirmar": True, "dados": bad})
    assert "observados" in tool["description"]
    assert "processamento" in tool["description"]


@pytest.mark.parametrize("resource", ["lancamentos_conta", "contas_pagar", "contas_receber"])
def test_general_update_rejects_allocation_before_configuration_or_http(tmp_path, monkeypatch, resource):
    profile, request = Mock(), Mock()
    monkeypatch.setattr(OmieService, "_profile", profile)
    monkeypatch.setattr(OmieService, "_request", request)
    facade = OmieFinanceFacade(Configuration(tmp_path))
    name = f"omie.financeiro.{resource}.alterar"
    arguments = {"confirmar": True, "dados": {"id": 12, "observacao": "Texto existente", "departamentos": [{"codigo": "000123", "percentual": 100}]}}
    schema = next(tool["input_schema"] for tool in facade.definitions([{"name": "empresa"}]) if tool["name"] == name)
    assert "departamentos" not in schema["properties"]["dados"]["properties"]
    assert not Draft202012Validator(schema).is_valid(arguments)

    with pytest.raises(OmieError) as error:
        facade.call(name, arguments, "empresa")

    assert error.value.code == "invalid_parameter"
    assert error.value.details["use_instead"] == [f"omie.financeiro.{resource}.definir_departamentos"]
    profile.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize("resource", ["contas_pagar", "contas_receber"])
def test_title_account_field_is_the_native_expected_account_not_a_payment(tmp_path, monkeypatch, resource):
    monkeypatch.setattr(OmieService, "_profile", Mock(return_value=({}, {})))
    request = Mock(return_value={"codigo_status": "0"})
    monkeypatch.setattr(OmieService, "_request", request)
    arguments = {"confirmar": True, "dados": {"id": 12, "conta_corrente_id": 34}}
    facade = OmieFinanceFacade(Configuration(tmp_path))
    name = f"omie.financeiro.{resource}.alterar"
    schema = next(tool["input_schema"] for tool in facade.definitions([{"name": "empresa"}]) if tool["name"] == name)
    Draft202012Validator(schema).validate(arguments)

    facade.call(name, arguments, "empresa")

    request.assert_called_once_with({}, {}, "payables" if resource == "contas_pagar" else "receivables",
                                    "AlterarContaPagar" if resource == "contas_pagar" else "AlterarContaReceber",
                                    {"codigo_lancamento_omie": 12, "id_conta_corrente": 34}, retries=0)


@pytest.mark.parametrize("resource", ["contas_pagar", "contas_receber"])
@pytest.mark.parametrize("action", ["criar_lote", "incluir_ou_alterar_lote"])
def test_batch_cannot_accept_allocation_without_per_object_verification(tmp_path, monkeypatch, resource, action):
    profile, request = Mock(), Mock()
    monkeypatch.setattr(OmieService, "_profile", profile)
    monkeypatch.setattr(OmieService, "_request", request)
    item = {"codigo_integracao": "I-12", "cliente_fornecedor_id": 34, "data_vencimento": "17/11/2026",
            "valor": 100, "categoria_codigo": "2.01", "departamentos": [{"codigo": "000123", "percentual": 100}]}
    name = f"omie.financeiro.{resource}.{action}"

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call(name, {"confirmar": True, "dados": {"lote": 1, "titulos": [item]}}, "empresa")

    assert error.value.details["field"] == "dados.titulos[0].departamentos"
    assert error.value.details["use_instead"] == [f"omie.financeiro.{resource}.definir_departamentos"]
    profile.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize("data", [
    {"id": 12}, {"id": 12, "codigo_integracao": "I-12", "departamentos": []},
    {"id": True, "departamentos": []}, {"id": 12, "departamentos": None},
    {"id": 12, "departamentos": [], "conta_corrente_id": 34},
    {"id": 12, "departamentos": [], "lancamento_conta_id": False},
])
def test_invalid_dedicated_parameters_never_reach_a_remote_method(tmp_path, monkeypatch, data):
    invoke = Mock()
    monkeypatch.setattr(OmieService, "invoke_financial", invoke)

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration(tmp_path)).call("omie.financeiro.contas_pagar.definir_departamentos", {"confirmar": True, "dados": data}, "empresa")

    assert error.value.code == "invalid_parameter"
    invoke.assert_not_called()
