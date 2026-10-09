#!/usr/bin/env python3
"""Secure JSON wrapper for registered Omie ERP operations."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping
import tomllib


class OmieError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Settings:
    profile: str
    timeout: float
    retries: int
    vault_command: tuple[str, ...]
    vault_config: str
    entry_template: str
    username_field: str
    password_field: str
    vault_auth: Mapping[str, Any]


ENDPOINTS = {
    "document_types": "https://app.omie.com.br/api/v1/geral/tiposdoc/",
    "customers": "https://app.omie.com.br/api/v1/geral/clientes/",
    "departments": "https://app.omie.com.br/api/v1/geral/departamentos/",
    "projects": "https://app.omie.com.br/api/v1/geral/projetos/",
    "categories": "https://app.omie.com.br/api/v1/geral/categorias/",
    "bank_accounts": "https://app.omie.com.br/api/v1/geral/contacorrente/",
    "account_transactions": "https://app.omie.com.br/api/v1/financas/contacorrentelancamentos/",
    "payables": "https://app.omie.com.br/api/v1/financas/contapagar/",
    "receivables": "https://app.omie.com.br/api/v1/financas/contareceber/",
    "financial_movements": "https://app.omie.com.br/api/v1/financas/mf/",
    "inbound_nfe_receipts": "https://app.omie.com.br/api/v1/produtos/recebimentonfe/",
    "incoming_invoices": "https://app.omie.com.br/api/v1/produtos/notaentrada/",
    "electronic_invoices": "https://app.omie.com.br/api/v1/produtos/nfconsultar/",
}

OPERATIONS = {
    "document-types.list": ("document_types", "PesquisarTipoDocumento", False),
    "document-types.get": ("document_types", "ConsultarTipoDocumento", False),
    "customers.list": ("customers", "ListarClientes", False),
    "customers.get": ("customers", "ConsultarCliente", False),
    "customers.create": ("customers", "IncluirCliente", True),
    "departments.list": ("departments", "ListarDepartamentos", False),
    "departments.get": ("departments", "ConsultarDepartamento", False),
    "departments.create": ("departments", "IncluirDepartamento", True),
    "departments.update": ("departments", "AlterarDepartamento", True),
    "departments.delete": ("departments", "ExcluirDepartamento", True),
    "projects.list": ("projects", "ListarProjetos", False),
    "projects.get": ("projects", "ConsultarProjeto", False),
    "projects.create": ("projects", "IncluirProjeto", True),
    "projects.update": ("projects", "AlterarProjeto", True),
    "projects.upsert": ("projects", "UpsertProjeto", True),
    "projects.delete": ("projects", "ExcluirProjeto", True),
    "categories.list": ("categories", "ListarCategorias", False),
    "categories.get": ("categories", "ConsultarCategoria", False),
    "categories.create": ("categories", "IncluirCategoria", True),
    "categories.update": ("categories", "AlterarCategoria", True),
    "category-groups.create": ("categories", "IncluirGrupoCategoria", True),
    "category-groups.update": ("categories", "AlterarGrupoCategoria", True),
    "bank-accounts.list": ("bank_accounts", "ListarContasCorrentes", False),
    "bank-accounts.get": ("bank_accounts", "ConsultarContaCorrente", False),
    "financial-movements.list": ("financial_movements", "ListarMovimentos", False),
    "account-transactions.list": ("account_transactions", "ListarLancCC", False),
    "account-transactions.get": ("account_transactions", "ConsultaLancCC", False),
    "account-transactions.create": ("account_transactions", "IncluirLancCC", True),
    "account-transactions.update": ("account_transactions", "AlterarLancCC", True),
    "account-transactions.delete": ("account_transactions", "ExcluirLancCC", True),
    "account-transfers.create": ("account_transactions", "IncluirLancCC", True),
    "payables.list": ("payables", "ListarContasPagar", False),
    "payables.get": ("payables", "ConsultarContaPagar", False),
    "payables.create": ("payables", "IncluirContaPagar", True),
    "payables.update": ("payables", "AlterarContaPagar", True),
    "payables.upsert": ("payables", "UpsertContaPagar", True),
    "payables.delete": ("payables", "ExcluirContaPagar", True),
    "payables.pay": ("payables", "LancarPagamento", True),
    "payables.payment.cancel": ("payables", "CancelarPagamento", True),
    "payables.create-batch": ("payables", "IncluirContaPagarPorLote", True),
    "payables.upsert-batch": ("payables", "UpsertContaPagarPorLote", True),
    "receivables.list": ("receivables", "ListarContasReceber", False),
    "receivables.get": ("receivables", "ConsultarContaReceber", False),
    "receivables.create": ("receivables", "IncluirContaReceber", True),
    "receivables.update": ("receivables", "AlterarContaReceber", True),
    "receivables.upsert": ("receivables", "UpsertContaReceber", True),
    "receivables.delete": ("receivables", "ExcluirContaReceber", True),
    "receivables.receive": ("receivables", "LancarRecebimento", True),
    "receivables.receipt.cancel": ("receivables", "CancelarRecebimento", True),
    "receivables.receipt.reconcile": ("receivables", "ConciliarRecebimento", True),
    "receivables.receipt.unreconcile": ("receivables", "DesconciliarRecebimento", True),
    "receivables.department-allocation.create": ("receivables", "IncluirDistribuicaoDepartamento", True),
    "receivables.department-allocation.update": ("receivables", "AlterarDistribuicaoDepartamento", True),
    "receivables.department-allocation.delete": ("receivables", "ExcluirDistribuicaoDepartamento", True),
    "receivables.create-batch": ("receivables", "IncluirContaReceberPorLote", True),
    "receivables.upsert-batch": ("receivables", "UpsertContaReceberPorLote", True),
    "inbound-nfe-receipts.list": ("inbound_nfe_receipts", "ListarRecebimentos", False),
    "inbound-nfe-receipts.get": ("inbound_nfe_receipts", "ConsultarRecebimento", False),
    "inbound-nfe-receipts.update": ("inbound_nfe_receipts", "AlterarRecebimento", True),
    "inbound-nfe-receipts.set-stage": ("inbound_nfe_receipts", "AlterarEtapaRecebimento", True),
    "inbound-nfe-receipts.update-completed": ("inbound_nfe_receipts", "AlterarRecebimentoConcluido", True),
    "inbound-nfe-receipts.complete": ("inbound_nfe_receipts", "ConcluirRecebimento", True),
    "inbound-nfe-receipts.reverse": ("inbound_nfe_receipts", "ReverterRecebimento", True),
    "inbound-nfe-receipts.delete": ("inbound_nfe_receipts", "ExcluirRecebimento", True),
    "incoming-invoices.list": ("incoming_invoices", "ListarNotaEnt", False),
    "incoming-invoices.get": ("incoming_invoices", "ConsultarNotaEnt", False),
    "incoming-invoices.create": ("incoming_invoices", "IncluirNotaEnt", True),
    "incoming-invoices.update": ("incoming_invoices", "AlterarNotaEnt", True),
    "incoming-invoices.delete": ("incoming_invoices", "ExcluirNotaEnt", True),
    "incoming-invoices.status": ("incoming_invoices", "StatusNotaEnt", False),
    "electronic-invoices.list": ("electronic_invoices", "ListarNF", False),
    "electronic-invoices.get": ("electronic_invoices", "ConsultarNF", False),
}


def fail(code: str, message: str, status: int | None = None) -> None:
    raise OmieError(code, message, status)


def load_settings(path: str, profile_name: str) -> Settings:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        fail("invalid_config", "Não foi possível ler o perfil Omie.")
    omie, vault = data.get("defaults", {}), data.get("profiles", {}).get(profile_name, {})
    if not isinstance(omie, dict) or not isinstance(vault, dict): fail("invalid_config", "Perfil Omie não encontrado.")
    try:
        timeout = float(omie.get("timeout_seconds", "30"))
        retries = int(omie.get("max_retries", "2"))
    except ValueError:
        fail("invalid_config", "timeout_seconds e max_retries devem ser numéricos.")
    if timeout <= 0 or retries < 0:
        fail("invalid_config", "Configuração Omie incompleta ou inválida.")
    entry_template = vault.get("vault_entry_path", "").strip()
    username_field = vault.get("app_key_field", "username").strip()
    password_field = vault.get("app_secret_field", "password").strip()
    if not entry_template or not vault.get("vault_profile") or username_field not in {"username", "password", "url", "notes"} or password_field not in {"username", "password", "url", "notes"}:
        fail("invalid_config", "Configuração KeePassVault incompleta.")
    script = Path(__file__).resolve().parents[2] / "keepass-vault" / "scripts" / "keepass_vault.py"
    return Settings(str(vault["vault_profile"]), timeout, retries, (sys.executable, str(script)), str(Path(path).parent / "keepass.toml"), entry_template, username_field, password_field, {"mode": "configured"})


def vault_read(settings: Settings, field: str) -> str:
    entry = settings.entry_template
    request = {"operation": "read", "path": entry, "field": field, "auth": dict(settings.vault_auth)}
    try:
        result = subprocess.run([*settings.vault_command, "--config", settings.vault_config, "--profile", settings.profile], input=json.dumps(request), text=True, capture_output=True, timeout=settings.timeout, check=False)
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        fail("vault_unavailable", "Não foi possível consultar a KeePassVault.")
    if result.returncode != 0 or payload.get("ok") is not True:
        fail("vault_operation_failed", "A operação na KeePassVault falhou.")
    value = payload.get("result", {}).get("value")
    if not isinstance(value, str) or not value:
        fail("credential_missing", "Credencial Omie ausente na KeePassVault.")
    return value


def credentials(settings: Settings) -> tuple[str, str]:
    return vault_read(settings, settings.username_field), vault_read(settings, settings.password_field)


def require_string(value: Any, field: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip() or (max_length is not None and len(value) > max_length):
        fail("invalid_body", f"{field} é obrigatório e inválido.")
    return value


def copy_fields(source: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    unknown = set(source) - allowed
    if unknown:
        fail("invalid_body", "body contém campos não permitidos para esta operação.")
    return dict(source)


def pagination(params: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    unknown = set(params) - allowed - {"page", "page_size"}
    if unknown:
        fail("invalid_request", "params contém campos não permitidos para esta operação.")
    try:
        page = int(params.get("page", params.get("pagina", 1)))
        page_size = int(params.get("page_size", params.get("registros_por_pagina", 50)))
    except (TypeError, ValueError):
        fail("invalid_request", "pagina e registros_por_pagina devem ser inteiros.")
    if page < 1 or page_size < 1:
        fail("invalid_request", "pagina e registros_por_pagina devem ser positivos.")
    result = {"pagina": page, "registros_por_pagina": page_size}
    result.update({key: value for key, value in params.items() if key not in {"page", "page_size", "pagina", "registros_por_pagina"}})
    return result


def project_identifier(source: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "codigo" in source:
        if not isinstance(source["codigo"], int) or source["codigo"] < 1:
            fail("invalid_body", "codigo deve ser um inteiro positivo.")
        result["codigo"] = source["codigo"]
    if "codInt" in source:
        result["codInt"] = require_string(source["codInt"], "codInt", max_length=20)
    if not result:
        fail("missing_parameter", "codigo ou codInt é obrigatório.")
    return result


TITLE_FIELDS = {"codigo_lancamento_omie", "codigo_lancamento_integracao", "codigo_cliente_fornecedor", "codigo_cliente_fornecedor_integracao", "data_vencimento", "valor_documento", "codigo_categoria", "data_previsao", "categorias", "id_conta_corrente", "numero_documento", "numero_parcela", "codigo_tipo_documento", "numero_documento_fiscal", "numero_pedido", "chave_nfe", "observacao", "data_emissao", "codigo_projeto", "distribuicao", "bloqueado", "bloquear_baixa", "baixar_documento", "conciliar_documento", "acao", "operacao"}
TITLE_KEYS = {"codigo_lancamento_omie", "codigo_lancamento_integracao", "chave_lancamento"}
PAYMENT_FIELDS = {"codigo_lancamento", "codigo_lancamento_integracao", "codigo_baixa", "codigo_baixa_integracao", "codigo_conta_corrente", "valor", "desconto", "juros", "multa", "data", "observacao", "conciliar_documento"}
LIST_FIELDS = {"pagina", "registros_por_pagina", "apenas_importado_api", "ordenar_por", "ordem_descrescente", "filtrar_por_data_de", "filtrar_por_data_ate", "filtrar_apenas_inclusao", "filtrar_apenas_alteracao", "filtrar_por_emissao_de", "filtrar_por_emissao_ate", "filtrar_por_registro_de", "filtrar_por_registro_ate", "filtrar_conta_corrente", "filtrar_cliente", "filtrar_por_cpf_cnpj", "filtrar_por_status", "filtrar_por_projeto", "exibir_obs"}


def financial_params(operation: str, params: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    if operation.endswith(".list"):
        return pagination(params, LIST_FIELDS)
    if operation.endswith(".get") or operation.endswith(".delete"):
        if operation.startswith("receivables.department-allocation."):
            allocation = copy_fields(body, {"chave_lancamento", "codigo_lancamento_omie", "distribuicao"})
            if not (set(allocation) & {"chave_lancamento", "codigo_lancamento_omie"}):
                fail("invalid_body", "A chave do título é obrigatória.")
            return allocation
        key = copy_fields(params, TITLE_KEYS)
        if not key:
            fail("missing_parameter", "A chave do título é obrigatória.")
        return key
    if operation.startswith("receivables.department-allocation."):
        allocation = copy_fields(body, {"chave_lancamento", "codigo_lancamento_omie", "distribuicao"})
        if not (set(allocation) & {"chave_lancamento", "codigo_lancamento_omie"}) or not isinstance(allocation.get("distribuicao"), list):
            fail("invalid_body", "A chave do título e distribuicao são obrigatórias.")
        return allocation
    if operation.endswith(".create") or operation.endswith(".update") or operation.endswith(".upsert"):
        title = copy_fields(body, TITLE_FIELDS)
        if operation.endswith(".create") and not {"codigo_lancamento_integracao", "codigo_cliente_fornecedor", "data_vencimento", "valor_documento", "codigo_categoria"} <= set(title):
            fail("invalid_body", "Título exige código de integração, cliente/fornecedor, vencimento, valor e categoria.")
        return title
    if operation.endswith("-batch"):
        batch = copy_fields(body, {"lote", "titles"})
        titles = batch.get("titles")
        if not isinstance(batch.get("lote"), int) or not isinstance(titles, list) or not titles:
            fail("invalid_body", "lote e titles não vazios são obrigatórios.")
        if not all(isinstance(item, Mapping) for item in titles):
            fail("invalid_body", "titles deve conter apenas objetos JSON.")
        key = "conta_pagar_cadastro" if operation.startswith("payables.") else "conta_receber_cadastro"
        return {"lote": batch["lote"], key: [copy_fields(item, TITLE_FIELDS) for item in titles]}
    if operation in {"payables.pay", "receivables.receive"}:
        payment = copy_fields(body, PAYMENT_FIELDS)
        if not {"valor", "data", "codigo_conta_corrente"} <= set(payment) or not (set(payment) & {"codigo_lancamento", "codigo_lancamento_integracao"}):
            fail("invalid_body", "Baixa exige título, conta corrente, valor e data.")
        return payment
    if operation in {"payables.payment.cancel", "receivables.receipt.cancel", "receivables.receipt.reconcile", "receivables.receipt.unreconcile"}:
        cancellation = copy_fields(body, {"codigo_baixa"})
        if not isinstance(cancellation.get("codigo_baixa"), int):
            fail("invalid_body", "codigo_baixa é obrigatório.")
        return cancellation
    fail("unsupported_operation", "Operação financeira não permitida.")


def nfe_params(operation: str, params: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    if operation == "inbound-nfe-receipts.list":
        return copy_fields(params, {"nPagina", "nRegistrosPorPagina", "cOrdenarPor", "dtAltDe", "dtAltAte", "hrAltDe", "hrAltAte", "dtEmissaoDe", "dtEmissaoAte", "nIdFornecedor", "cEtapa", "cExibirDetalhes"})
    if operation == "inbound-nfe-receipts.delete":
        receipt_id = body.get("receipt_id")
        if not isinstance(receipt_id, int): fail("invalid_body", "receipt_id é obrigatório.")
        return {"nIdReceb": receipt_id}
    if operation.startswith("inbound-nfe-receipts."):
        identifier = {"nIdReceb": body["receipt_id"]} if isinstance(body.get("receipt_id"), int) else ({"cChaveNfe": body["access_key"]} if isinstance(body.get("access_key"), str) else {})
        if operation.endswith(".get"):
            identifier = {"nIdReceb": params["receipt_id"]} if isinstance(params.get("receipt_id"), int) else ({"cChaveNfe": params["access_key"]} if isinstance(params.get("access_key"), str) else {})
        if not identifier: fail("missing_parameter", "receipt_id ou access_key é obrigatório.")
        if operation in {"inbound-nfe-receipts.get", "inbound-nfe-receipts.complete", "inbound-nfe-receipts.reverse", "inbound-nfe-receipts.set-stage"}:
            if operation != "inbound-nfe-receipts.get":
                stage = body.get("stage")
                if not isinstance(stage, str) or not stage: fail("invalid_body", "stage é obrigatório.")
                identifier["cEtapa"] = stage
            return identifier
        if operation == "inbound-nfe-receipts.update-completed":
            for field in ("purchase_category_code", "bank_account_id", "registration_date"):
                if field not in body: fail("invalid_body", f"{field} é obrigatório.")
            result = {"ide": identifier, "infoAdicionais": {"cCategCompra": body["purchase_category_code"], "nIdConta": body["bank_account_id"], "dRegistro": body["registration_date"]}}
            if "project_id" in body: result["infoAdicionais"]["nIdProjeto"] = body["project_id"]
            if "departments" in body: result["departamentos"] = body["departments"]
            if "categories" in body: result["categorias"] = body["categories"]
            return result
        if operation == "inbound-nfe-receipts.update":
            return {"ide": identifier, "itensRecebimentoEditar": body.get("items", {})}
    if operation in {"incoming-invoices.list", "electronic-invoices.list"}:
        return copy_fields(params, {"nPagina", "nRegistrosPorPagina", "pagina", "registros_por_pagina", "ordenar_por", "dDataEmissaoInicial", "dDataEmissaoFinal"})
    if operation in {"incoming-invoices.get", "incoming-invoices.status", "incoming-invoices.delete"}:
        source = params if operation != "incoming-invoices.delete" else body
        result = {"nCodNotaEnt": source["id"]} if isinstance(source.get("id"), int) else ({"cCodIntNotaEnt": source["integration_id"]} if isinstance(source.get("integration_id"), str) else {})
        if not result: fail("missing_parameter", "id ou integration_id é obrigatório.")
        return result
    if operation in {"incoming-invoices.create", "incoming-invoices.update"}:
        note = copy_fields(body, {"cabec", "frete", "infAdic", "email", "obs", "totais", "produtos", "departamentos"})
        if not {"cabec", "infAdic", "produtos"} <= set(note): fail("invalid_body", "cabec, infAdic e produtos são obrigatórios.")
        return note
    if operation == "electronic-invoices.get":
        return copy_fields(params, {"nCodNF", "nNF"})
    fail("unsupported_operation", "Operação de NF-e não permitida.")


CUSTOMER_KEYS = {"codigo_cliente_omie", "codigo_cliente_integracao"}
CUSTOMER_FILTER_FIELDS = CUSTOMER_KEYS | {"cnpj_cpf", "razao_social", "nome_fantasia", "inativo", "tags"}
CUSTOMER_LIST_FIELDS = {"pagina", "registros_por_pagina", "apenas_importado_api", "filtrar_por_data_de", "filtrar_por_data_ate", "filtrar_por_hora_de", "filtrar_por_hora_ate", "filtrar_apenas_inclusao", "filtrar_apenas_alteracao", "exibir_caracteristicas", "exibir_obs", "clientesFiltro"}


def customer_filter(source: Any, *, identifier: bool = False) -> dict[str, Any]:
    """Validate supported customer/supplier filters without changing API matching semantics.

    Raise invalid_request for unsupported fields or invalid types/lengths and
    missing_parameter when a detail lookup has no Omie or integration identifier.
    """
    allowed = CUSTOMER_KEYS if identifier else CUSTOMER_FILTER_FIELDS
    if not isinstance(source, Mapping) or set(source) - allowed:
        fail("invalid_request", "Filtro de cliente/fornecedor deve ser um objeto com campos permitidos.")
    result = dict(source)
    if identifier and not result:
        fail("missing_parameter", "codigo_cliente_omie ou codigo_cliente_integracao é obrigatório.")
    for field, value in result.items():
        if field == "codigo_cliente_omie":
            if type(value) is not int or value < 1:
                fail("invalid_request", "codigo_cliente_omie deve ser um inteiro positivo.")
        elif field == "tags":
            if not isinstance(value, list) or not value:
                fail("invalid_request", "tags deve ser uma lista não vazia de objetos com tag.")
            for tag in value:
                if not isinstance(tag, Mapping) or set(tag) != {"tag"} or not isinstance(tag["tag"], str) or not tag["tag"].strip():
                    fail("invalid_request", "Cada tag deve conter somente uma string tag não vazia.")
        elif field == "inativo":
            if value not in ("S", "N"):
                fail("invalid_request", "inativo deve ser S ou N.")
        else:
            limit = {"codigo_cliente_integracao": 60, "cnpj_cpf": 20, "razao_social": 60, "nome_fantasia": 100}[field]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                fail("invalid_request", f"{field} deve ser uma string não vazia de até {limit} caracteres.")
    return result


def customer_params(operation: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Build read-only shared customer/supplier requests, limited to 50 rows per page.

    Raise invalid_request for invalid pagination, flags or filters. Detail lookups
    accept identifiers only; CNPJ/CPF and names belong in list.clientesFiltro.
    """
    if operation == "customers.get":
        return customer_filter(params, identifier=True)
    for field in ("page", "page_size", "pagina", "registros_por_pagina"):
        if field in params and (type(params[field]) is not int or params[field] < 1):
            fail("invalid_request", f"{field} deve ser um inteiro positivo.")
    result = pagination(params, CUSTOMER_LIST_FIELDS)
    if result["registros_por_pagina"] > 50:
        fail("invalid_request", "registros_por_pagina deve ser no máximo 50.")
    if "clientesFiltro" in result:
        result["clientesFiltro"] = customer_filter(result["clientesFiltro"])
    for field in ("apenas_importado_api", "filtrar_apenas_inclusao", "filtrar_apenas_alteracao", "exibir_caracteristicas", "exibir_obs"):
        if field in result and result[field] not in ("S", "N"):
            fail("invalid_request", f"{field} deve ser S ou N.")
    for field in ("filtrar_por_data_de", "filtrar_por_data_ate", "filtrar_por_hora_de", "filtrar_por_hora_ate"):
        if field in result and (not isinstance(result[field], str) or not result[field].strip()):
            fail("invalid_request", f"{field} deve ser uma string não vazia.")
    return result


def document_type_params(operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the title document catalog's native codigo (up to five characters).

    Search defaults to an empty code for the catalog; detail requires a code.
    Raise invalid_request for unknown fields, bodies, invalid types or lengths,
    and missing_parameter for absent/blank detail codes. No pagination exists.
    These codes do not define the cTipo contract of direct account transactions.
    """
    params = request.get("params", {})
    body = request.get("body", {})
    if not isinstance(params, Mapping) or set(params) - {"codigo"} or not isinstance(body, Mapping) or body:
        fail("invalid_request", "Use somente params.codigo; esta leitura não aceita body ou paginação.")
    code = params.get("codigo", "")
    if not isinstance(code, str) or len(code) > 5 or (code and not code.strip()):
        fail("invalid_request", "codigo deve ser uma string de até 5 caracteres.")
    if operation == "document-types.get" and not code:
        fail("missing_parameter", "codigo é obrigatório para consultar o tipo de documento.")
    return {"codigo": code}


FINANCIAL_DOCUMENT_TYPES = frozenset({"99999", "NFE", "FAT", "NFS"})
DIRECT_DOCUMENT_TYPES = FINANCIAL_DOCUMENT_TYPES | frozenset({"ADI", "BOL", "CRT", "CHQ", "CON", "CRE", "DRF", "DAS", "DEB", "DIN", "DOC", "GUIA", "PROT", "REC", "RPA", "TED", "TRA"})


def customer_create_params(request: Mapping[str, Any]) -> dict[str, Any]:
    """Build a minimal CNPJ counterparty; reject invalid_body/invalid_request.

    Accept numeric or masked CNPJ, a legal name up to 60 characters and an
    optional integration ID up to 60 characters. Generate a stable ID from CNPJ
    when omitted. This validates format, not tax registration or check digits.
    """
    body, params = request.get("body", {}), request.get("params", {})
    if not isinstance(body, Mapping) or not isinstance(params, Mapping) or params:
        fail("invalid_request", "customers.create exige body e não aceita params.")
    result = copy_fields(body, {"cnpj_cpf", "razao_social", "codigo_cliente_integracao"})
    cnpj = result.get("cnpj_cpf")
    if not isinstance(cnpj, str) or not re.fullmatch(r"(?:[0-9]{14}|[0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2})", cnpj):
        fail("invalid_body", "cnpj_cpf deve ser CNPJ com 14 dígitos ou máscara XX.XXX.XXX/XXXX-XX.")
    digits = re.sub(r"[^0-9]", "", cnpj)
    result["cnpj_cpf"] = f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    result["razao_social"] = require_string(result.get("razao_social"), "razao_social", max_length=60)
    result["codigo_cliente_integracao"] = require_string(result.get("codigo_cliente_integracao", f"CNPJ-{digits}"), "codigo_cliente_integracao", max_length=60)
    return result


def create_customer(settings: Settings, key: str, secret: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Reuse a unique active CNPJ before IncluirCliente; never update a match.

    Scan every unfiltered page, including inactive/non-API customers. Raise
    invalid_response on incomplete/inconsistent results, ambiguous_customer on
    multiple CNPJ matches, customer_inactive on an inactive match and
    omie_api_error on a nonzero inclusion status. Network/API failures propagate.
    Inclusion has no automatic retries; a stable integration ID helps reconcile
    uncertain outcomes, but this read-before-write is not a transactional lock.
    """
    wanted = re.sub(r"[^0-9]", "", body["cnpj_cpf"])
    matches: list[Mapping[str, Any]] = []
    seen: set[int] = set()
    page, totals = 1, None
    while True:
        response = call_api(settings, key, secret, ENDPOINTS["customers"], "ListarClientes", {"pagina": page, "registros_por_pagina": 50, "apenas_importado_api": "N"})
        if not isinstance(response, Mapping):
            fail("invalid_response", "Consulta prévia de clientes incompleta.")
        rows = response.get("clientes_cadastro")
        pages, count = response.get("total_de_paginas"), response.get("total_de_registros")
        if not isinstance(rows, list) or type(pages) is not int or pages < 0 or type(count) is not int or count < 0 or type(response.get("pagina")) is not int or response["pagina"] != page or len(rows) > 50:
            fail("invalid_response", "Paginação de clientes inválida; inclusão não executada.")
        if (count > 0 and pages != (count + 49) // 50) or (count == 0 and pages not in (0, 1)):
            fail("invalid_response", "Totais da consulta prévia de clientes são inconsistentes.")
        if totals is None:
            totals = (pages, count)
        if totals != (pages, count) or (not rows and count > 0):
            fail("invalid_response", "Consulta de clientes mudou ou está incompleta; repita a consulta.")
        for row in rows:
            if not isinstance(row, Mapping) or type(row.get("codigo_cliente_omie")) is not int or row["codigo_cliente_omie"] < 1 or row["codigo_cliente_omie"] in seen or not isinstance(row.get("cnpj_cpf"), str):
                fail("invalid_response", "Cadastro retornado na consulta prévia é inválido.")
            seen.add(row["codigo_cliente_omie"])
            if re.sub(r"[^0-9]", "", row["cnpj_cpf"]) == wanted:
                matches.append(row)
        if page >= pages:
            break
        page += 1
    if len(seen) != totals[1]:
        fail("invalid_response", "Consulta prévia não recuperou todos os clientes.")
    if len(matches) > 1:
        fail("ambiguous_customer", "Mais de um cadastro possui o CNPJ; resolva a duplicidade antes de usar o ID.")
    if matches:
        match = matches[0]
        if match.get("inativo") not in ("S", "N"):
            fail("invalid_response", "Situação do cadastro existente não foi informada.")
        if match["inativo"] == "S":
            fail("customer_inactive", "O CNPJ já existe em cadastro inativo; não foi criada duplicata.")
        return {"codigo_cliente_omie": match["codigo_cliente_omie"], "created": False, "customer": dict(match)}
    response = call_api(replace(settings, retries=0), key, secret, ENDPOINTS["customers"], "IncluirCliente", body)
    if not isinstance(response, Mapping) or type(response.get("codigo_status")) not in (str, int) or response["codigo_status"] not in ("0", 0):
        fail("omie_api_error", "A inclusão do cliente não retornou status de sucesso.")
    if type(response.get("codigo_cliente_omie")) is not int or response["codigo_cliente_omie"] < 1:
        fail("invalid_response", "A inclusão não retornou codigo_cliente_omie válido; consulte antes de repetir.")
    return {**response, "created": True}


def build_params(operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
    if operation == "customers.create":
        return customer_create_params(request)
    if operation in {"document-types.list", "document-types.get"}:
        return document_type_params(operation, request)
    params = request.get("params") or {}
    body = request.get("body") or {}
    if not isinstance(params, Mapping) or not isinstance(body, Mapping):
        fail("invalid_request", "params e body devem ser objetos JSON.")
    if operation in {"customers.list", "customers.get"}:
        return customer_params(operation, params)
    if operation == "departments.list":
        return pagination(params, set())
    if operation in {"departments.get", "departments.delete"}:
        code = params.get("codigo", params.get("code"))
        if not isinstance(code, str) or not code.strip():
            fail("missing_parameter", "codigo é obrigatório.")
        return {"codigo": code}
    if operation in {"departments.create", "departments.update"}:
        code, description = body.get("codigo"), body.get("descricao")
        if not isinstance(code, str) or not isinstance(description, str) or not description.strip():
            fail("invalid_body", "body deve conter codigo e descricao.")
        return {"codigo": code, "descricao": description}
    if operation == "projects.list":
        return pagination(params, {"apenas_importado_api", "ordenar_por", "ordem_descrescente", "filtrar_por_data_de", "filtrar_por_data_ate", "filtrar_apenas_inclusao", "filtrar_apenas_alteracao", "nome_projeto"})
    if operation in {"projects.get", "projects.delete"}:
        return project_identifier(params)
    if operation == "projects.create":
        project = copy_fields(body, {"codInt", "nome", "inativo"})
        project["codInt"] = require_string(project.get("codInt"), "codInt", max_length=20)
        project["nome"] = require_string(project.get("nome"), "nome", max_length=70)
        return project
    if operation in {"projects.update", "projects.upsert"}:
        project = copy_fields(body, {"codigo", "codInt", "nome", "inativo"})
        identifiers = project_identifier(project)
        project.update(identifiers)
        if "nome" not in project and "inativo" not in project:
            fail("invalid_body", "body deve conter nome ou inativo para alteração.")
        if "nome" in project:
            project["nome"] = require_string(project["nome"], "nome", max_length=70)
        return project
    if operation == "categories.list":
        return pagination(params, {"filtrar_apenas_ativo", "filtrar_por_tipo", "descricao"})
    if operation == "categories.get":
        return {"codigo": require_string(params.get("codigo"), "codigo", max_length=20)}
    if operation == "categories.create":
        category = copy_fields(body, {"categoria_superior", "descricao", "natureza", "tipo_categoria", "codigo_dre"})
        for field, length in (("categoria_superior", None), ("descricao", 50), ("tipo_categoria", 3)):
            category[field] = require_string(category.get(field), field, max_length=length)
        return category
    if operation == "categories.update":
        category = copy_fields(body, {"codigo", "descricao", "natureza", "tipo_categoria", "codigo_dre", "conta_inativa"})
        category["codigo"] = require_string(category.get("codigo"), "codigo", max_length=20)
        if len(category) == 1:
            fail("invalid_body", "body deve conter ao menos um campo para alteração.")
        return category
    if operation == "category-groups.create":
        group = copy_fields(body, {"descricao", "tipo_grupo", "natureza"})
        group["descricao"] = require_string(group.get("descricao"), "descricao", max_length=50)
        group["tipo_grupo"] = require_string(group.get("tipo_grupo"), "tipo_grupo", max_length=1)
        return group
    if operation == "category-groups.update":
        group = copy_fields(body, {"codigo", "descricao", "natureza"})
        group["codigo"] = require_string(group.get("codigo"), "codigo", max_length=20)
        if len(group) == 1:
            fail("invalid_body", "body deve conter ao menos um campo para alteração.")
        return group
    if operation == "bank-accounts.list":
        return pagination(params, {"apenas_importado_api", "ordenar_por", "ordem_descrescente"})
    if operation == "bank-accounts.get":
        account = copy_fields(params, {"nCodCC", "cCodCCInt"})
        if not account:
            fail("missing_parameter", "nCodCC ou cCodCCInt é obrigatório.")
        return account
    if operation in {"financial-movements.list", "account-transactions.list"}:
        allowed = {"nPagina", "nRegPorPagina", "cOrdenarPor", "cOrdemDecrescente", "cOrigem", "dDtIncDe", "dDtIncAte", "dDtAltDe", "dDtAltAte", "dtPagInicial", "dtPagFinal", "nCodCC", "cStatus", "cNatureza", "cTipo", "dDtVencDe", "dDtVencAte", "dDtPagtoDe", "dDtPagtoAte", "dDtPrevDe", "dDtPrevAte", "lDadosCad"}
        return copy_fields(params, allowed)
    if operation == "account-transactions.get" or operation == "account-transactions.delete":
        transaction = copy_fields(params, {"nCodLanc", "cCodIntLanc"})
        if not transaction:
            fail("missing_parameter", "nCodLanc ou cCodIntLanc é obrigatório.")
        return transaction
    if operation in {"account-transactions.create", "account-transactions.update"}:
        transaction = copy_fields(body, {"integration_id", "id", "account_id", "date", "amount", "category_code", "document_type", "document_number", "customer_id", "project_id", "note", "departments"})
        if operation == "account-transactions.update":
            if "id" not in transaction and "integration_id" not in transaction:
                fail("missing_parameter", "id ou integration_id é obrigatório.")
        else:
            transaction["integration_id"] = require_string(transaction.get("integration_id"), "integration_id", max_length=20)
        for field in ("account_id", "date", "amount", "category_code", "document_type"):
            if field not in transaction:
                fail("invalid_body", f"{field} é obrigatório.")
        if not isinstance(transaction["document_type"], str) or transaction["document_type"] not in DIRECT_DOCUMENT_TYPES:
            fail("invalid_body", "document_type não está entre os tipos disponibilizados pelo wrapper.")
        if "document_number" in transaction:
            require_string(transaction["document_number"], "document_number", max_length=20)
        result = {"cabecalho": {"nCodCC": transaction["account_id"], "dDtLanc": transaction["date"], "nValorLanc": transaction["amount"]}, "detalhes": {"cCodCateg": transaction["category_code"], "cTipo": transaction["document_type"]}}
        if "integration_id" in transaction:
            result["cCodIntLanc"] = transaction["integration_id"]
        if "id" in transaction:
            result["nCodLanc"] = transaction["id"]
        for source, target in (("document_number", "cNumDoc"), ("customer_id", "nCodCliente"), ("project_id", "nCodProjeto"), ("note", "cObs")):
            if source in transaction:
                result["detalhes"][target] = transaction[source]
        if "departments" in transaction:
            result["departamentos"] = transaction["departments"]
        return result
    if operation == "account-transfers.create":
        transfer = copy_fields(body, {"integration_id", "source_account_id", "destination_account_id", "date", "amount", "document_number", "note", "project_id", "departments"})
        for field in ("integration_id", "source_account_id", "destination_account_id", "date", "amount"):
            if field not in transfer:
                fail("invalid_body", f"{field} é obrigatório.")
        if transfer["source_account_id"] == transfer["destination_account_id"]:
            fail("invalid_body", "A conta de origem deve ser diferente da conta de destino.")
        result = {"cCodIntLanc": require_string(transfer["integration_id"], "integration_id", max_length=20), "cabecalho": {"nCodCC": transfer["source_account_id"], "dDtLanc": transfer["date"], "nValorLanc": transfer["amount"]}, "detalhes": {"cTipo": "TRA"}, "transferencia": {"nCodCCDestino": transfer["destination_account_id"]}}
        for source, target in (("document_number", "cNumDoc"), ("note", "cObs"), ("project_id", "nCodProjeto")):
            if source in transfer:
                result["detalhes"][target] = transfer[source]
        if "departments" in transfer:
            result["departamentos"] = transfer["departments"]
        return result
    if operation.startswith("payables.") or operation.startswith("receivables."):
        return financial_params(operation, params, body)
    if operation.startswith("inbound-nfe-receipts.") or operation.startswith("incoming-invoices.") or operation.startswith("electronic-invoices."):
        return nfe_params(operation, params, body)
    fail("unsupported_operation", "Operação Omie não permitida.")


def call_api(settings: Settings, app_key: str, app_secret: str, endpoint: str, method: str, params: Mapping[str, Any]) -> Any:
    payload = {"call": method, "app_key": app_key, "app_secret": app_secret, "param": [dict(params)]}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(settings.retries + 1):
        request = urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=settings.timeout) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code in {429, 500, 502, 503, 504} and attempt < settings.retries:
                time.sleep(min(2 ** attempt, 10))
                continue
            fail("omie_http_error", f"A API Omie retornou HTTP {error.code}.", error.code)
        except (urllib.error.URLError, TimeoutError):
            if attempt < settings.retries:
                time.sleep(0.25 * (attempt + 1))
                continue
            fail("network_error", "Não foi possível conectar à API Omie.")
        except (json.JSONDecodeError, UnicodeError):
            fail("invalid_response", "A API Omie retornou uma resposta inválida.")
        if isinstance(result, Mapping) and result.get("faultstring"):
            fail("omie_api_error", "A API Omie retornou um erro de negócio.")
        if isinstance(result, Mapping) and "cCodStatus" in result and (type(result["cCodStatus"]) not in (str, int) or result["cCodStatus"] not in ("0", 0)):
            fail("omie_api_error", "A API Omie retornou uma falha no processamento do lançamento.")
        return result
    fail("request_failed", "A requisição Omie não foi concluída.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, Mapping) or request.get("version") != 1:
            fail("unsupported_version", "Somente requisições com version=1 são aceitas.")
        operation = str(request.get("operation", ""))
        if operation not in OPERATIONS:
            fail("unsupported_operation", "Operação Omie não permitida.")
        endpoint_name, method, writes = OPERATIONS[operation]
        if writes and request.get("confirm") is not True:
            fail("confirmation_required", "Operações de escrita exigem confirm=true.")
        settings = load_settings(args.config, args.profile)
        params = build_params(operation, request)
        key, secret = credentials(settings)
        if operation == "customers.create":
            data = create_customer(settings, key, secret, params)
        else:
            data = call_api(settings, key, secret, ENDPOINTS[endpoint_name], method, params)
        print(json.dumps({"version": 1, "ok": True, "operation": operation, "data": data}, ensure_ascii=True))
        return 0
    except json.JSONDecodeError:
        error = {"code": "invalid_json", "message": "A entrada não contém JSON válido."}
    except OmieError as exc:
        error = {"code": exc.code, "message": exc.message}
    except Exception:
        error = {"code": "internal_error", "message": "Falha interna ao processar a solicitação."}
    print(json.dumps({"version": 1, "ok": False, "error": error}, ensure_ascii=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
