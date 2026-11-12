"""Contratos de entrada publicados ao agente para cada operação Omie."""
from __future__ import annotations

from typing import Any

from .contracts import (
    ACCOUNT_TRANSACTION_LIST_FIELDS, FINANCIAL_MOVEMENT_LIST_FIELDS,
    CUSTOMER_FILTER_FIELDS, CUSTOMER_LIST_FIELDS, DIRECT_DOCUMENT_TYPES,
    LIST_FIELDS, PAYMENT_FIELDS, TITLE_FIELDS, TITLE_KEYS,
)


def obj(fields: set[str] | dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    properties = {name: {} for name in sorted(fields)} if isinstance(fields, set) else fields
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


def identifiers(fields: set[str]) -> dict[str, Any]:
    schema = obj(fields)
    schema["anyOf"] = [{"required": [field]} for field in sorted(fields)]
    return schema


def pagination(fields: set[str]) -> dict[str, Any]:
    properties = {field: {} for field in sorted(fields)}
    for field in ("page", "page_size", "pagina", "registros_por_pagina"):
        properties[field] = {"type": "integer", "minimum": 1}
    return obj(properties)


def input_contract(operation: str) -> tuple[str, dict[str, Any]]:
    """Retorna params ou body com os campos aceitos pelo validador interno."""
    if operation.startswith("document_types_"):
        return "params", obj({"codigo": {"type": "string", "maxLength": 5}}, ("codigo",) if operation.endswith("_get") else ())
    if operation == "customers_list":
        schema = pagination(CUSTOMER_LIST_FIELDS)
        schema["properties"]["clientesFiltro"] = obj(CUSTOMER_FILTER_FIELDS)
        for key in ("page_size", "registros_por_pagina"):
            schema["properties"][key]["maximum"] = 50
        return "params", schema
    if operation == "customers_get":
        return "params", identifiers({"codigo_cliente_omie", "codigo_cliente_integracao"})
    if operation == "customers_create":
        return "body", obj({
            "cnpj_cpf": {"type": "string", "description": "CPF de 11 dígitos ou CNPJ de 14 dígitos, com ou sem máscara; preserve zeros à esquerda."},
            "razao_social": {"type": "string", "minLength": 1, "maxLength": 60},
            "codigo_cliente_integracao": {"type": "string", "minLength": 1, "maxLength": 60, "description": "Opcional; padrão estável CPF-<dígitos> ou CNPJ-<dígitos>."},
        }, ("cnpj_cpf", "razao_social"))
    if operation.startswith("departments_"):
        if operation.endswith("_list"):
            return "params", pagination(set())
        if operation.endswith(("_get", "_delete")):
            return "params", identifiers({"codigo", "code"})
        return "body", obj({"codigo": {"type": "string"}, "descricao": {"type": "string", "minLength": 1}}, ("codigo", "descricao"))
    if operation.startswith("projects_"):
        if operation.endswith("_list"):
            return "params", pagination({"apenas_importado_api", "ordenar_por", "ordem_descrescente", "filtrar_por_data_de", "filtrar_por_data_ate", "filtrar_apenas_inclusao", "filtrar_apenas_alteracao", "nome_projeto"})
        if operation.endswith(("_get", "_delete")):
            return "params", identifiers({"codigo", "codInt"})
        fields = {"codigo": {"type": "integer", "minimum": 1}, "codInt": {"type": "string", "maxLength": 20}, "nome": {"type": "string", "maxLength": 70}, "inativo": {"type": "string"}}
        if operation.endswith("_create"):
            fields.pop("codigo")
            return "body", obj(fields, ("codInt", "nome"))
        schema = obj(fields)
        schema["allOf"] = [{"anyOf": [{"required": [key]} for key in ("codigo", "codInt")]}, {"anyOf": [{"required": [key]} for key in ("nome", "inativo")]}]
        return "body", schema
    if operation == "categories_list":
        return "params", pagination({"filtrar_apenas_ativo", "filtrar_por_tipo", "descricao"})
    if operation == "categories_get":
        return "params", obj({"codigo": {"type": "string", "maxLength": 20}}, ("codigo",))
    if operation == "categories_create":
        return "body", obj({"categoria_superior", "descricao", "natureza", "tipo_categoria", "codigo_dre"}, ("categoria_superior", "descricao", "tipo_categoria"))
    if operation == "categories_update":
        return "body", obj({"codigo", "descricao", "natureza", "tipo_categoria", "codigo_dre", "conta_inativa"}, ("codigo",))
    if operation == "category_groups_create":
        return "body", obj({"descricao", "tipo_grupo", "natureza"}, ("descricao", "tipo_grupo"))
    if operation == "category_groups_update":
        return "body", obj({"codigo", "descricao", "natureza"}, ("codigo",))
    if operation == "bank_accounts_list":
        return "params", pagination({"apenas_importado_api", "ordenar_por", "ordem_descrescente"})
    if operation == "bank_accounts_get":
        return "params", identifiers({"nCodCC", "cCodCCInt"})
    if operation == "financial_movements_list":
        return "params", obj(FINANCIAL_MOVEMENT_LIST_FIELDS)
    if operation == "account_transactions_list":
        return "params", obj(ACCOUNT_TRANSACTION_LIST_FIELDS)
    if operation in {"account_transactions_get", "account_transactions_delete"}:
        return "params", identifiers({"nCodLanc", "cCodIntLanc"})
    if operation in {"account_transactions_create", "account_transactions_update"}:
        fields = {name: {} for name in ("integration_id", "id", "account_id", "date", "amount", "category_code", "document_number", "customer_id", "project_id", "note", "departments")}
        fields["document_type"] = {"type": "string", "enum": sorted(DIRECT_DOCUMENT_TYPES), "description": "Tipo preservado como detalhes.cTipo; NFE/NFS/FAT/99999 são aceitos localmente, sujeitos à validação do serviço Omie."}
        required = ("account_id", "date", "amount", "category_code", "document_type")
        if operation.endswith("_create"):
            required += ("integration_id",)
        schema = obj(fields, required)
        if operation.endswith("_update"):
            schema["anyOf"] = [{"required": [key]} for key in ("id", "integration_id")]
        return "body", schema
    if operation == "account_transfers_create":
        return "body", obj({
            "integration_id": {"type": "string", "minLength": 1, "maxLength": 20},
            "source_account_id": {"type": "integer", "minimum": 1},
            "destination_account_id": {"type": "integer", "minimum": 1},
            "date": {"type": "string", "description": "Data da transferência no formato dd/mm/aaaa."},
            "amount": {"type": "number", "exclusiveMinimum": 0},
            "category_code": {"type": "string", "minLength": 1, "maxLength": 20, "description": "Categoria de saída da transferência na empresa; enviada como detalhes.cCodCateg."},
            "document_number": {"type": "string", "maxLength": 20},
            "note": {"type": "string"},
            "project_id": {"type": "integer", "minimum": 1},
            "departments": {"type": "array", "items": {"type": "object"}},
        }, ("integration_id", "source_account_id", "destination_account_id", "date", "amount", "category_code"))
    if operation.startswith(("payables_", "receivables_")):
        if operation.endswith("_list"):
            return "params", pagination(LIST_FIELDS)
        if operation.startswith("receivables_department_allocation_"):
            schema = obj({"chave_lancamento", "codigo_lancamento_omie", "distribuicao"}, () if operation.endswith("_delete") else ("distribuicao",))
            schema["anyOf"] = [{"required": [key]} for key in ("chave_lancamento", "codigo_lancamento_omie")]
            return "body", schema
        if operation.endswith(("_get", "_delete")):
            return "params", identifiers(TITLE_KEYS)
        if operation.endswith(("_payment_cancel", "_receipt_cancel", "_receipt_reconcile", "_receipt_unreconcile")):
            return "body", obj({"codigo_baixa": {"type": "integer"}}, ("codigo_baixa",))
        if operation in {"payables_pay", "receivables_receive"}:
            schema = obj(PAYMENT_FIELDS, ("valor", "data", "codigo_conta_corrente"))
            schema["anyOf"] = [{"required": [key]} for key in ("codigo_lancamento", "codigo_lancamento_integracao")]
            return "body", schema
        fields = TITLE_FIELDS | ({"codigo_barras_ficha_compensacao"} if operation.startswith("payables_") else set())
        if operation.endswith("_batch"):
            return "body", obj({"lote": {"type": "integer"}, "titles": {"type": "array", "minItems": 1, "items": obj(fields)}}, ("lote", "titles"))
        required = ("codigo_lancamento_integracao", "codigo_cliente_fornecedor", "data_vencimento", "valor_documento", "codigo_categoria") if operation.endswith("_create") else ()
        return "body", obj(fields, required)
    if operation == "inbound_nfe_receipts_list":
        return "params", obj({"nPagina", "nRegistrosPorPagina", "cOrdenarPor", "dtAltDe", "dtAltAte", "hrAltDe", "hrAltAte", "dtEmissaoDe", "dtEmissaoAte", "nIdFornecedor", "cEtapa", "cExibirDetalhes"})
    if operation.startswith("inbound_nfe_receipts_"):
        schema = identifiers({"receipt_id", "access_key"})
        if operation.endswith("_get"):
            return "params", schema
        if operation.endswith("_delete"):
            return "body", obj({"receipt_id": {"type": "integer"}}, ("receipt_id",))
        if operation.endswith(("_complete", "_reverse", "_set_stage")):
            schema["properties"]["stage"] = {"type": "string", "minLength": 1}
            schema["required"] = ["stage"]
        elif operation.endswith(("_update_completed", "_update")):
            info_fields = {
                "purchase_category_code": {"type": "string", "minLength": 1, "maxLength": 20, "description": "Código Omie da categoria; infoAdicionais.cCategCompra."},
                "bank_account_id": {"type": "integer", "minimum": 1, "description": "ID da conta corrente; infoAdicionais.nIdConta."},
                "project_id": {"type": "integer", "minimum": 1, "description": "ID do projeto; infoAdicionais.nIdProjeto."},
                "registration_date": {"type": "string", "pattern": "^[0-9]{2}/[0-9]{2}/[0-9]{4}$", "description": "Data de registro dd/mm/aaaa."},
                "buyer_id": {"type": "integer", "minimum": 1, "description": "ID do comprador."},
            }
            schema["properties"].update(info_fields)
            available = list(info_fields)
            if operation.endswith("_update"):
                department = obj({
                    "cCodDepartamento": {"type": "string", "minLength": 1, "maxLength": 40, "description": "Código do departamento (texto, preserve zeros à esquerda)."},
                    "pDepartamento": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                    "vDepartamento": {"type": "number", "minimum": 0, "description": "Valor em reais; sem símbolo monetário."},
                }, ("cCodDepartamento", "pDepartamento"))
                item_id = obj({
                    "nSequencia": {"type": "integer", "minimum": 1},
                    "cAcao": {"type": "string", "enum": ["NOVO", "ASSOCIAR-PRODUTO", "ASSOCIAR-PEDIDO", "IGNORAR", "EDITAR"]},
                }, ("nSequencia", "cAcao"))
                item = obj({
                    "itensIde": item_id,
                    "itensInfoAdicEditar": obj({"cCategoriaItem": {"type": "string", "minLength": 1, "maxLength": 20}}, ("cCategoriaItem",)),
                }, ("itensIde",))
                schema["properties"]["departments"] = {"type": "array", "minItems": 1, "items": department, "description": "Rateio; soma dos percentuais deve ser 100%. Somente AlterarRecebimento (não concluído)."}
                schema["properties"]["items"] = {"anyOf": [item, {"type": "array", "minItems": 1, "items": item}], "description": "Itens (itensRecebimentoEditar). Não informe items vazio para alterar dados financeiros."}
                available.extend(["departments", "items"])
            schema["allOf"] = [{"anyOf": [{"required": [key]} for key in available]}]
        return "body", schema
    if operation in {"incoming_invoices_list", "electronic_invoices_list"}:
        return "params", obj({"nPagina", "nRegistrosPorPagina", "pagina", "registros_por_pagina", "ordenar_por", "dDataEmissaoInicial", "dDataEmissaoFinal"})
    if operation in {"incoming_invoices_get", "incoming_invoices_status", "incoming_invoices_delete"}:
        return ("body" if operation.endswith("_delete") else "params"), identifiers({"id", "integration_id"})
    if operation in {"incoming_invoices_create", "incoming_invoices_update"}:
        return "body", obj({"cabec", "frete", "infAdic", "email", "obs", "totais", "produtos", "departamentos"}, ("cabec", "infAdic", "produtos"))
    if operation == "electronic_invoices_get":
        return "params", obj({"nCodNF", "nNF"})
    raise ValueError("Contrato Omie não registrado.")


NOTES = {
    "customers_list": "Cadastro compartilhado de clientes/fornecedores. Filtros em clientesFiltro; percorra total_de_paginas. Para parte do nome, percorra páginas sem filtro de nome e compare localmente; não suponha suporte a curingas.",
    "customers_get": "Consulta somente por ID Omie ou código de integração; CPF/CNPJ e nomes usam customers_list.",
    "customers_create": "Valida formato de CPF/CNPJ, percorre o cadastro completo e reutiliza um único ativo. Bloqueia duplicados/inativos e consulta incompleta. Gera código de integração estável; inclusão sem retry automático. Em resposta incerta, consulte antes de repetir.",
    "account_transactions_create": "Cria lançamento direto (não cria conta a pagar). Converte account_id/date/amount para cabecalho e category_code/document_type para detalhes. Não solicita conciliação.",
    "account_transactions_update": "Altera lançamento direto preservando document_type e document_number; exige id ou integration_id.",
    "account_transfers_create": "Cria uma transferência única entre contas diferentes por IncluirLancCC; não produza dois lançamentos independentes. category_code é a categoria de saída e será enviada em detalhes.cCodCateg; o tipo TRA e a conta de destino são definidos pelo conector.",
    "payables_update": "Para corrigir tipo de título pago, preserve os demais campos e consulte após alteração. Não recrie título nem cancele/repita baixa para trocar o tipo.",
    "inbound_nfe_receipts_update": "Para recebimento não concluído, consulte primeiro omie.inbound_nfe_receipts_get e confira fornecedor e valor. Envie body.receipt_id e ao menos um campo de alteração: purchase_category_code, project_id, bank_account_id, registration_date, buyer_id, departments ou items. departments usa cCodDepartamento (código textual), pDepartamento e opcional vDepartamento. Não envie fornecedor nem valor como campos de edição. Omie exige infoAdicionais/departamentos/itens válidos, não um objeto items vazio.",
    "inbound_nfe_receipts_update_completed": "Somente para recebimentos já concluídos; permite infoAdicionais (purchase_category_code, project_id, bank_account_id, registration_date ou buyer_id). A API AlterarRecebimentoConcluido não aceita departments: consulte o recebimento e não altere o rateio por esta operação.",
}


def description(operation: str) -> str:
    """Descreve ação e contexto sem exigir leitura de arquivos da antiga skill."""
    verbs = {"list": "Lista", "get": "Consulta", "create": "Cria", "update": "Altera", "upsert": "Inclui ou altera", "delete": "Exclui", "pay": "Registra pagamento de", "receive": "Registra recebimento de", "cancel": "Cancela", "reconcile": "Concilia", "unreconcile": "Desconcilia", "complete": "Conclui", "reverse": "Reverte", "status": "Consulta situação de"}
    resource, action = operation.rsplit("_", 1)
    text = f"{verbs.get(action, 'Executa')} {resource.replace('_', ' ')} na Omie."
    if operation in NOTES:
        text += " " + NOTES[operation]
    if operation.startswith("payables_") and operation.endswith(("_create", "_update", "_upsert", "_batch")):
        text += " Linha digitável em codigo_barras_ficha_compensacao: texto de até 70 caracteres, sem reformatar."
    return text
