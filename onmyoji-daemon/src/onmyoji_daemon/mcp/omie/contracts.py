"""Validação e normalização dos contratos Omie, independentes do transporte."""
from __future__ import annotations

import re
from typing import Any, Mapping

from .exception import OmieError


def fail(code: str, message: str) -> None:
    raise OmieError(code, message)


OPERATION_NAMES = {
    "document_types_list": "document-types.list",
    "document_types_get": "document-types.get",
    "customers_list": "customers.list",
    "customers_get": "customers.get",
    "customers_create": "customers.create",
    "departments_list": "departments.list",
    "departments_get": "departments.get",
    "departments_create": "departments.create",
    "departments_update": "departments.update",
    "departments_delete": "departments.delete",
    "projects_list": "projects.list",
    "projects_get": "projects.get",
    "projects_create": "projects.create",
    "projects_update": "projects.update",
    "projects_upsert": "projects.upsert",
    "projects_delete": "projects.delete",
    "categories_list": "categories.list",
    "categories_get": "categories.get",
    "categories_create": "categories.create",
    "categories_update": "categories.update",
    "category_groups_create": "category-groups.create",
    "category_groups_update": "category-groups.update",
    "bank_accounts_list": "bank-accounts.list",
    "bank_accounts_get": "bank-accounts.get",
    "financial_movements_list": "financial-movements.list",
    "account_transactions_list": "account-transactions.list",
    "account_transactions_get": "account-transactions.get",
    "account_transactions_create": "account-transactions.create",
    "account_transactions_update": "account-transactions.update",
    "account_transactions_delete": "account-transactions.delete",
    "account_transfers_create": "account-transfers.create",
    "payables_list": "payables.list",
    "payables_get": "payables.get",
    "payables_create": "payables.create",
    "payables_update": "payables.update",
    "payables_upsert": "payables.upsert",
    "payables_delete": "payables.delete",
    "payables_pay": "payables.pay",
    "payables_payment_cancel": "payables.payment.cancel",
    "payables_create_batch": "payables.create-batch",
    "payables_upsert_batch": "payables.upsert-batch",
    "receivables_list": "receivables.list",
    "receivables_get": "receivables.get",
    "receivables_create": "receivables.create",
    "receivables_update": "receivables.update",
    "receivables_upsert": "receivables.upsert",
    "receivables_delete": "receivables.delete",
    "receivables_receive": "receivables.receive",
    "receivables_receipt_cancel": "receivables.receipt.cancel",
    "receivables_receipt_reconcile": "receivables.receipt.reconcile",
    "receivables_receipt_unreconcile": "receivables.receipt.unreconcile",
    "receivables_department_allocation_create": "receivables.department-allocation.create",
    "receivables_department_allocation_update": "receivables.department-allocation.update",
    "receivables_department_allocation_delete": "receivables.department-allocation.delete",
    "receivables_create_batch": "receivables.create-batch",
    "receivables_upsert_batch": "receivables.upsert-batch",
    "inbound_nfe_receipts_list": "inbound-nfe-receipts.list",
    "inbound_nfe_receipts_get": "inbound-nfe-receipts.get",
    "inbound_nfe_receipts_update": "inbound-nfe-receipts.update",
    "inbound_nfe_receipts_set_stage": "inbound-nfe-receipts.set-stage",
    "inbound_nfe_receipts_update_completed": "inbound-nfe-receipts.update-completed",
    "inbound_nfe_receipts_complete": "inbound-nfe-receipts.complete",
    "inbound_nfe_receipts_reverse": "inbound-nfe-receipts.reverse",
    "inbound_nfe_receipts_delete": "inbound-nfe-receipts.delete",
    "incoming_invoices_list": "incoming-invoices.list",
    "incoming_invoices_get": "incoming-invoices.get",
    "incoming_invoices_create": "incoming-invoices.create",
    "incoming_invoices_update": "incoming-invoices.update",
    "incoming_invoices_delete": "incoming-invoices.delete",
    "incoming_invoices_status": "incoming-invoices.status",
    "electronic_invoices_list": "electronic-invoices.list",
    "electronic_invoices_get": "electronic-invoices.get"
}


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
    unknown = set(params) - allowed - {"page", "page_size", "pagina", "registros_por_pagina"}
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


def title_body(operation: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Preserva o campo de boleto de contas a pagar; rejeita valores vazios ou maiores que 70 caracteres."""
    allowed = TITLE_FIELDS | ({"codigo_barras_ficha_compensacao"} if operation.startswith("payables.") else set())
    title = copy_fields(body, allowed)
    if "codigo_barras_ficha_compensacao" in title:
        require_string(title["codigo_barras_ficha_compensacao"], "codigo_barras_ficha_compensacao", max_length=70)
    return title


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
        title = title_body(operation, body)
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
        return {"lote": batch["lote"], key: [title_body(operation, item) for item in titles]}
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
    """Build a minimal CPF/CNPJ counterparty; reject invalid_body/invalid_request.

    Accept numeric or masked CPF/CNPJ, a legal or full personal name up to 60 characters and an
    optional integration ID up to 60 characters. Generate a stable ID from the document
    when omitted. This validates format, not tax registration or check digits.
    """
    body, params = request.get("body", {}), request.get("params", {})
    if not isinstance(body, Mapping) or not isinstance(params, Mapping) or params:
        fail("invalid_request", "customers.create exige body e não aceita params.")
    result = copy_fields(body, {"cnpj_cpf", "razao_social", "codigo_cliente_integracao"})
    document = result.get("cnpj_cpf")
    if not isinstance(document, str) or not re.fullmatch(r"(?:[0-9]{11}|[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}|[0-9]{14}|[0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2})", document):
        fail("invalid_body", "cnpj_cpf deve ser CPF com 11 dígitos ou máscara XXX.XXX.XXX-XX, ou CNPJ com 14 dígitos ou máscara XX.XXX.XXX/XXXX-XX.")
    digits = re.sub(r"[^0-9]", "", document)
    document_type = "CPF" if len(digits) == 11 else "CNPJ"
    result["cnpj_cpf"] = (
        f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
        if document_type == "CPF"
        else f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    )
    result["razao_social"] = require_string(result.get("razao_social"), "razao_social", max_length=60)
    result["codigo_cliente_integracao"] = require_string(result.get("codigo_cliente_integracao", f"{document_type}-{digits}"), "codigo_cliente_integracao", max_length=60)
    return result


def _build_params(operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
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



def build_params(operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Valida a operação MCP e aplica o contrato Omie antes de acessar o cofre."""
    legacy_name = OPERATION_NAMES.get(operation)
    if legacy_name is None:
        fail("unsupported_operation", "Operação Omie não suportada.")
    if not isinstance(arguments, Mapping):
        fail("invalid_request", "Os argumentos devem ser um objeto JSON.")
    for field in ("params", "body"):
        if field in arguments and not isinstance(arguments[field], Mapping):
            fail("invalid_request", "params e body devem ser objetos JSON.")
    return _build_params(legacy_name, arguments)
