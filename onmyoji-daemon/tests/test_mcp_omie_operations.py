"""Exercita os 72 contratos Omie pelo serviço e pela publicação MCP."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.omie.contracts import OPERATION_NAMES, build_params
from onmyoji_daemon.mcp.omie.service import OPERATIONS, OmieError, OmieService
from onmyoji_daemon.mcp.omie.tools import OmieToolRegistry


def operation_cases():
    cases = {}
    def add(names, field, payload):
        for name in names.split():
            cases[name] = {field: payload}
    add("document_types_list", "params", {})
    add("document_types_get", "params", {"codigo": "FAT"})
    add("customers_list", "params", {"page": 2, "page_size": 10, "clientesFiltro": {"cnpj_cpf": "01234567890"}})
    add("customers_get", "params", {"codigo_cliente_omie": 1})
    add("customers_create", "body", {"cnpj_cpf": "01234567890", "razao_social": "Nome completo"})
    add("departments_list projects_list categories_list bank_accounts_list payables_list receivables_list", "params", {"page": 2, "page_size": 10})
    add("departments_get departments_delete categories_get", "params", {"codigo": "D1"})
    add("departments_create departments_update", "body", {"codigo": "D1", "descricao": "Departamento"})
    add("projects_get projects_delete", "params", {"codInt": "P1"})
    add("projects_create projects_update projects_upsert", "body", {"codInt": "P1", "nome": "Projeto"})
    add("categories_create", "body", {"categoria_superior": "1000", "descricao": "Categoria", "tipo_categoria": "001"})
    add("categories_update", "body", {"codigo": "C1", "descricao": "Categoria"})
    add("category_groups_create", "body", {"descricao": "Grupo", "tipo_grupo": "R"})
    add("category_groups_update", "body", {"codigo": "G1", "descricao": "Grupo"})
    add("bank_accounts_get", "params", {"nCodCC": 1})
    add("financial_movements_list account_transactions_list", "params", {"nPagina": 1, "nRegPorPagina": 10})
    add("account_transactions_get account_transactions_delete", "params", {"nCodLanc": 1})
    add("account_transactions_create account_transactions_update", "body", {"integration_id": "L1", "account_id": 1, "date": "25/10/2026", "amount": 100, "category_code": "2.01", "document_type": "NFS", "document_number": "25", "customer_id": 3})
    add("account_transfers_create", "body", {"integration_id": "TR1", "source_account_id": 1, "destination_account_id": 2, "date": "25/10/2026", "amount": 100})
    title = {"codigo_lancamento_integracao": "T1", "codigo_cliente_fornecedor": 1, "data_vencimento": "25/10/2026", "valor_documento": 100, "codigo_categoria": "2.01"}
    add("payables_create payables_update payables_upsert receivables_create receivables_update receivables_upsert", "body", title)
    add("payables_get payables_delete receivables_get receivables_delete", "params", {"codigo_lancamento_omie": 1})
    add("payables_pay receivables_receive", "body", {"codigo_lancamento": 1, "codigo_conta_corrente": 2, "valor": 100, "data": "25/10/2026"})
    add("payables_payment_cancel receivables_receipt_cancel receivables_receipt_reconcile receivables_receipt_unreconcile", "body", {"codigo_baixa": 1})
    add("payables_create_batch payables_upsert_batch receivables_create_batch receivables_upsert_batch", "body", {"lote": 1, "titles": [title]})
    add("receivables_department_allocation_create receivables_department_allocation_update receivables_department_allocation_delete", "body", {"chave_lancamento": 1, "distribuicao": [{"cCodDep": "D1", "nPerDep": 100}]})
    add("inbound_nfe_receipts_list", "params", {"nPagina": 1, "nRegistrosPorPagina": 10})
    add("inbound_nfe_receipts_get", "params", {"receipt_id": 1})
    add("inbound_nfe_receipts_delete", "body", {"receipt_id": 1})
    add("inbound_nfe_receipts_update", "body", {"receipt_id": 1, "items": {}})
    add("inbound_nfe_receipts_set_stage inbound_nfe_receipts_complete inbound_nfe_receipts_reverse", "body", {"receipt_id": 1, "stage": "50"})
    add("inbound_nfe_receipts_update_completed", "body", {"receipt_id": 1, "purchase_category_code": "2.01", "bank_account_id": 2, "registration_date": "25/10/2026", "departments": []})
    add("incoming_invoices_list electronic_invoices_list", "params", {"nPagina": 1, "nRegistrosPorPagina": 10})
    add("incoming_invoices_get incoming_invoices_status", "params", {"id": 1})
    add("incoming_invoices_delete", "body", {"id": 1})
    add("incoming_invoices_create incoming_invoices_update", "body", {"cabec": {}, "infAdic": {}, "produtos": []})
    add("electronic_invoices_get", "params", {"nCodNF": 1})
    return cases


CASES = operation_cases()
WRITES = [name for name, spec in OPERATIONS.items() if spec[2]]


def test_catalog_and_validation_cover_the_same_72_operations():
    assert len(OPERATIONS) == 72
    assert len(WRITES) == 46
    assert set(CASES) == set(OPERATIONS) == set(OPERATION_NAMES)


@pytest.mark.parametrize("operation", sorted(CASES))
def test_service_routes_normalized_parameters_to_registered_endpoint(operation, tmp_path):
    service = OmieService(tmp_path)
    service._profile = Mock(return_value=({}, {}))
    response = {"pagina": 1, "total_de_paginas": 0, "total_de_registros": 0, "clientes_cadastro": []}
    service._request = Mock(side_effect=[response, {"codigo_status": "0", "codigo_cliente_omie": 42}]) if operation == "customers_create" else Mock(return_value={"id": 42})
    arguments = {**CASES[operation], "confirm": True}

    result = service.invoke("example", operation, arguments)
    if operation == "customers_create":
        assert result["data"]["created"] is True
        assert service._request.call_args.kwargs == {"retries": 0}
    else:
        assert result == {"data": {"id": 42}}
        endpoint, method, _ = OPERATIONS[operation]
        assert service._request.call_args.args == ({}, {}, endpoint, method, build_params(operation, arguments))


@pytest.mark.parametrize("operation", WRITES)
@pytest.mark.parametrize("confirmation", [None, False, "true", 1])
def test_every_write_requires_boolean_confirmation_before_vault_or_network(operation, confirmation, tmp_path):
    service = OmieService(tmp_path)
    service._profile = Mock(side_effect=AssertionError("A configuração não deve ser lida"))
    with pytest.raises(OmieError) as error:
        service.invoke("example", operation, {**CASES[operation], "confirm": confirmation})
    assert error.value.code == "confirmation_required"


class Configuration:
    def __init__(self, profiles, root=None): self.profiles, self.root = profiles, root
    def enabled_omie_profiles(self): return self.profiles


def test_publication_selects_one_profile_and_publishes_context():
    assert OmieToolRegistry(Configuration([])).definitions() == []
    tools = OmieToolRegistry(Configuration([{"name": "empresa", "description": "ERP da empresa"}])).definitions()
    assert len(tools) == 72
    for tool in tools:
        assert "profile" not in tool["input_schema"]["properties"]
        assert "ERP da empresa" in tool["description"]
    creation = next(tool for tool in tools if tool["name"] == "omie.customers_create")
    assert creation["input_schema"]["properties"]["body"]["required"] == ["cnpj_cpf", "razao_social"]
    assert "duplicados/inativos" in creation["description"]


def test_multiple_profiles_require_selection_and_expose_context():
    profiles = [{"name": "a", "description": "ERP A"}, {"name": "b", "description": "ERP B"}]
    registry = OmieToolRegistry(Configuration(profiles))
    tools = registry.definitions()
    assert len(tools) == 73
    assert registry.call("omie.list_profiles", {}) == {"profiles": profiles}
    for tool in tools[1:]:
        assert "profile" in tool["input_schema"]["required"]
    with pytest.raises(ValueError, match="Perfil Omie indisponível"):
        registry.call("omie.projects_get", {"params": {"codInt": "P1"}})


@pytest.mark.parametrize("operation", sorted(CASES))
def test_each_published_schema_accepts_its_valid_example(operation):
    from jsonschema import Draft202012Validator
    registry = OmieToolRegistry(Configuration([{"name": "empresa", "description": "ERP"}]))
    tool = next(item for item in registry.definitions() if item["name"] == "omie." + operation)
    schema = tool["input_schema"]
    Draft202012Validator.check_schema(schema)
    arguments = {**CASES[operation], **({"confirm": True} if operation in WRITES else {})}
    Draft202012Validator(schema).validate(arguments)


@pytest.mark.parametrize("operation,arguments", [
    ("departments_get", {"params": {}}),
    ("departments_update", {"body": {"codigo": "D1"}}),
    ("projects_update", {"body": {"codigo": 1}}),
    ("projects_create", {"body": {"codInt": "P1", "nome": "N", "extra": True}}),
    ("categories_update", {"body": {"codigo": "C1"}}),
    ("category_groups_create", {"body": {"descricao": "Grupo", "tipo_grupo": "RR"}}),
    ("bank_accounts_get", {"params": {}}),
    ("account_transactions_delete", {"params": {}}),
    ("account_transactions_create", {"body": {"integration_id": "L1"}}),
    ("account_transfers_create", {"body": {"integration_id": "TR1", "source_account_id": 1, "destination_account_id": 1, "date": "25/10/2026", "amount": 100}}),
    ("payables_create", {"body": {"codigo_lancamento_integracao": "T1"}}),
    ("receivables_get", {"params": {}}),
    ("payables_pay", {"body": {"valor": 100, "data": "25/10/2026", "codigo_conta_corrente": 1}}),
    ("receivables_receipt_cancel", {"body": {"codigo_baixa": "1"}}),
    ("payables_create_batch", {"body": {"lote": 1, "titles": []}}),
    ("receivables_upsert_batch", {"body": {"lote": 1, "titles": [None]}}),
    ("receivables_department_allocation_update", {"body": {"chave_lancamento": 1}}),
    ("receivables_department_allocation_delete", {"body": {"distribuicao": []}}),
    ("inbound_nfe_receipts_get", {"params": {}}),
    ("inbound_nfe_receipts_complete", {"body": {"receipt_id": 1}}),
    ("inbound_nfe_receipts_delete", {"body": {"receipt_id": "1"}}),
    ("inbound_nfe_receipts_update_completed", {"body": {"receipt_id": 1}}),
    ("incoming_invoices_get", {"params": {}}),
    ("incoming_invoices_create", {"body": {"cabec": {}}}),
    ("electronic_invoices_get", {"params": {"endpoint": "https://example.invalid"}}),
])
def test_invalid_family_contracts_prevent_configuration_vault_and_network(operation, arguments, tmp_path):
    service = OmieService(tmp_path)
    service._profile = Mock(side_effect=AssertionError("A configuração não deve ser lida"))
    with pytest.raises(OmieError):
        service.invoke("example", operation, {**arguments, "confirm": True})


def test_registry_dispatches_selected_profile_without_legacy_scripts(tmp_path, monkeypatch):
    profiles = [{"name": "a", "description": "ERP A"}, {"name": "b", "description": "ERP B"}]
    invocation = Mock(return_value={"data": {"codigo": 1}})
    monkeypatch.setattr(OmieService, "invoke", invocation)
    registry = OmieToolRegistry(Configuration(profiles, tmp_path))
    arguments = {"profile": "b", "params": {"codInt": "P1"}}
    assert registry.call("omie.projects_get", arguments) == {"data": {"codigo": 1}}
    invocation.assert_called_once_with("b", "projects_get", arguments)
