"""Fachada financeira semântica da Omie.

Os nomes e os filtros publicados aqui pertencem ao MCP.  Os identificadores da
Omie são traduzidos nas entradas; os registros retornados preservam os campos
nativos, cujo uso é explicado nos contratos publicados.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Mapping

from .allocation import verify_department_allocation
from .department_allocation import define_departments
from .contracts import DIRECT_DOCUMENT_TYPES, build_params, normalize_account_departments, normalize_percentage_departments
from .exception import OmieError
from .service import CACHE_READ_DESCRIPTION, OmieService


DATE_PATTERN = r"^\d{2}/\d{2}/\d{4}$"
PERIODS = {
    "inclusao": ("dDtIncDe", "dDtIncAte", "inclusão"),
    "alteracao": ("dDtAltDe", "dDtAltAte", "alteração"),
    "pagamento": ("dDtPagtoDe", "dDtPagtoAte", "pagamento"),
    "vencimento": ("dDtVencDe", "dDtVencAte", "vencimento"),
    "previsao": ("dDtPrevDe", "dDtPrevAte", "previsão"),
    "periodo": ("dtPagInicial", "dtPagFinal", "período do extrato"),
}


@dataclass(frozen=True)
class FinanceSpec:
    name: str
    legacy: str
    description: str
    writes: bool = False
    period: str | None = None
    kind: str = "list"
    account_required: bool = False
    collect_pages: bool = False

    @property
    def paginated(self) -> bool:
        """Somente listagens por página recebem paginação do agente."""
        return not self.writes and self.kind == "list" and not self.collect_pages


def _list_specs(resource: str, legacy: str, label: str) -> list[FinanceSpec]:
    reconciliation_note = (
        " Cada item informa conciliacao.conciliado: true quando a Omie retorna "
        "a data de conciliação; false quando ela está ausente."
        if legacy == "financial_movements_list" else ""
    )
    return [FinanceSpec(
        f"omie.financeiro.{resource}.listar_por_{period}", legacy,
        f"Lista {label} exclusivamente pelo período de {text}.{reconciliation_note}", period=period,
    ) for period, (_, _, text) in PERIODS.items() if period != "periodo"]


SPECS: list[FinanceSpec] = [
    FinanceSpec("omie.financeiro.contas_correntes.listar", "bank_accounts_list", "Lista contas correntes disponíveis para operações financeiras."),
    FinanceSpec("omie.financeiro.contas_correntes.consultar", "bank_accounts_get", "Consulta uma conta corrente pelo identificador público.", kind="get"),
    FinanceSpec("omie.financeiro.categorias.listar", "categories_list", "Lista categorias financeiras."),
    FinanceSpec("omie.financeiro.categorias.consultar", "categories_get", "Consulta uma categoria pelo código.", kind="get"),
    FinanceSpec("omie.financeiro.departamentos.listar", "departments_list", "Lista departamentos para rateio."),
    FinanceSpec("omie.financeiro.departamentos.consultar", "departments_get", "Consulta um departamento pelo código.", kind="get"),
    FinanceSpec("omie.financeiro.projetos.listar", "projects_list", "Lista projetos para classificação financeira, com filtro opcional em filtros.nome. Percorra paginacao.proxima_pagina e confirme o projeto pelo nome e demais dados; nomes semelhantes não identificam um projeto com segurança."),
    FinanceSpec("omie.financeiro.projetos.consultar", "projects_get", "Consulta um projeto pelo ID Omie ou por um código de integração confirmado. Para descobrir o ID por nome, use omie.financeiro.projetos.listar com filtros.nome.", kind="get"),
    FinanceSpec("omie.financeiro.tipos_documento.listar", "document_types_list", "Retorna o catálogo completo de tipos de documento em uma única chamada, sem paginação e sem filtros. Chame com {} quando houver um único perfil; não envie pagina nem tamanho_pagina.", kind="catalog"),
]
SPECS += _list_specs("movimentos", "financial_movements_list", "movimentos financeiros")
SPECS += [
    FinanceSpec("omie.financeiro.lancamentos_conta.listar_por_inclusao", "account_transactions_list", "Lista lançamentos de conta corrente pela data de inclusão.", period="inclusao"),
    FinanceSpec("omie.financeiro.lancamentos_conta.listar_por_alteracao", "account_transactions_list", "Lista lançamentos de conta corrente pela data de alteração.", period="alteracao"),
    FinanceSpec("omie.financeiro.lancamentos_conta.listar_por_periodo", "account_transactions_list", "Lista lançamentos brutos de conta corrente pelo período do extrato.", period="periodo"),
    FinanceSpec("omie.financeiro.movimentos.listar_para_conciliacao", "financial_movements_list", "Consulta movimentos para apoiar a conciliação: exige conta e período de pagamento, coleta todas as páginas retornadas e informa o estado de conciliação.", period="pagamento", account_required=True, collect_pages=True),
]

for resource, legacy, label in (("contas_pagar", "payables", "contas a pagar"), ("contas_receber", "receivables", "contas a receber")):
    SPECS += [
        FinanceSpec(f"omie.financeiro.{resource}.listar_por_vencimento", f"{legacy}_list", f"Lista {label} pelo período de vencimento.", period="vencimento"),
        FinanceSpec(f"omie.financeiro.{resource}.consultar", f"{legacy}_get", f"Consulta uma {label[:-1]} pelo identificador.", kind="get"),
        FinanceSpec(f"omie.financeiro.{resource}.criar", f"{legacy}_create", f"Cria uma {label[:-1]}.", True, kind="title"),
        FinanceSpec(f"omie.financeiro.{resource}.alterar", f"{legacy}_update", f"Altera uma {label[:-1]}.", True, kind="title"),
        FinanceSpec(f"omie.financeiro.{resource}.incluir_ou_alterar", f"{legacy}_upsert", f"Inclui ou altera uma {label[:-1]}.", True, kind="title"),
        FinanceSpec(f"omie.financeiro.{resource}.excluir", f"{legacy}_delete", f"Exclui uma {label[:-1]}.", True, kind="get"),
        FinanceSpec(f"omie.financeiro.{resource}.criar_lote", f"{legacy}_create_batch", f"Cria um lote de {label}.", True, kind="batch"),
        FinanceSpec(f"omie.financeiro.{resource}.incluir_ou_alterar_lote", f"{legacy}_upsert_batch", f"Inclui ou altera um lote de {label}.", True, kind="batch"),
    ]

SPECS += [
    FinanceSpec("omie.financeiro.contas_pagar.registrar_pagamento", "payables_pay", "Registra o pagamento de uma conta a pagar.", True, kind="payment"),
    FinanceSpec("omie.financeiro.contas_pagar.cancelar_pagamento", "payables_payment_cancel", "Cancela um pagamento pelo identificador da baixa.", True, kind="receipt"),
    FinanceSpec("omie.financeiro.contas_receber.registrar_recebimento", "receivables_receive", "Registra o recebimento de uma conta a receber.", True, kind="payment"),
    FinanceSpec("omie.financeiro.contas_receber.cancelar_recebimento", "receivables_receipt_cancel", "Cancela um recebimento pelo identificador da baixa.", True, kind="receipt"),
    FinanceSpec("omie.financeiro.contas_receber.conciliar_recebimento", "receivables_receipt_reconcile", "Concilia um recebimento pelo identificador da baixa.", True, kind="receipt"),
    FinanceSpec("omie.financeiro.contas_receber.desconciliar_recebimento", "receivables_receipt_unreconcile", "Desconcilia um recebimento pelo identificador da baixa.", True, kind="receipt"),
    FinanceSpec("omie.financeiro.lancamentos_conta.definir_departamentos", "account_transactions_update", "Substitui o rateio de um lançamento direto e retorna os departamentos efetivamente consultados. Para baixa vinculada a título, orienta o método do título antes de escrever.", True, kind="department_set"),
    FinanceSpec("omie.financeiro.contas_pagar.definir_departamentos", "payables_update", "Substitui o rateio de uma conta a pagar, inclusive paga, sem alterar o pagamento. Pode conferir também um lançamento vinculado com lancamento_conta_id.", True, kind="department_set"),
    FinanceSpec("omie.financeiro.contas_receber.definir_departamentos", "receivables_update", "Substitui o rateio de uma conta a receber sem refazer o recebimento. Pode conferir também um lançamento vinculado com lancamento_conta_id.", True, kind="department_set"),
    FinanceSpec("omie.financeiro.lancamentos_conta.consultar", "account_transactions_get", "Consulta um lançamento de conta corrente.", kind="get"),
    FinanceSpec("omie.financeiro.lancamentos_conta.criar", "account_transactions_create", "Cria um lançamento direto de conta corrente.", True, kind="transaction"),
    FinanceSpec("omie.financeiro.lancamentos_conta.alterar", "account_transactions_update", "Altera um lançamento direto de conta corrente.", True, kind="transaction"),
    FinanceSpec("omie.financeiro.lancamentos_conta.excluir", "account_transactions_delete", "Exclui um lançamento de conta corrente.", True, kind="get"),
    FinanceSpec("omie.financeiro.transferencias.criar", "account_transfers_create", "Cria uma transferência entre contas correntes diferentes.", True, kind="transfer"),
]
BY_NAME = {spec.name: spec for spec in SPECS}

# A chave pública identifica a origem do código; não inferir pelo seu conteúdo.
IDENTIFIER_NAMES = {
    "bank_accounts": {"id": "nCodCC", "codigo_integracao": "cCodCCInt"},
    "categories": {"codigo": "codigo"},
    "departments": {"codigo": "codigo"},
    "projects": {"id": "codigo", "codigo_integracao": "codInt"},
    "payables": {"id": "codigo_lancamento_omie", "codigo_integracao": "codigo_lancamento_integracao"},
    "receivables": {"id": "codigo_lancamento_omie", "codigo_integracao": "codigo_lancamento_integracao"},
    "account_transactions": {"id": "nCodLanc", "codigo_integracao": "cCodIntLanc"},
}
IDENTIFIER_LENGTHS = {"bank_accounts": 20, "categories": 20, "departments": 40, "projects": 20, "payables": 60, "receivables": 60, "account_transactions": 20}
TRANSACTION_NAMES = {
    "id": "id", "codigo_integracao": "integration_id", "conta_corrente_id": "account_id",
    "data": "date", "valor": "amount", "categoria_codigo": "category_code",
    "tipo_documento": "document_type", "numero_documento": "document_number",
    "cliente_id": "customer_id", "projeto_id": "project_id", "observacao": "note",
    "departamentos": "departments",
}
TRANSACTION_REQUIRED = ("conta_corrente_id", "data", "valor", "categoria_codigo", "tipo_documento")
TITLE_NAMES = {
    "codigo_integracao": "codigo_lancamento_integracao", "id": "codigo_lancamento_omie",
    "cliente_fornecedor_id": "codigo_cliente_fornecedor", "cliente_fornecedor_codigo_integracao": "codigo_cliente_fornecedor_integracao",
    "data_vencimento": "data_vencimento", "valor": "valor_documento", "categoria_codigo": "codigo_categoria",
    "projeto_id": "codigo_projeto", "observacao": "observacao", "tipo_documento": "codigo_tipo_documento",
    "numero_documento": "numero_documento", "conta_corrente_id": "id_conta_corrente", "departamentos": "distribuicao",
}
TITLE_REQUIRED = ("codigo_integracao", "cliente_fornecedor_id", "data_vencimento", "valor", "categoria_codigo")
PAYMENT_NAMES = {"id": "codigo_lancamento", "codigo_integracao": "codigo_lancamento_integracao", "conta_corrente_id": "codigo_conta_corrente", "valor": "valor", "data": "data", "observacao": "observacao"}
TRANSFER_NAMES = {"codigo_integracao": "integration_id", "conta_origem_id": "source_account_id", "conta_destino_id": "destination_account_id", "data": "date", "valor": "amount", "categoria_codigo": "category_code", "numero_documento": "document_number", "observacao": "note", "projeto_id": "project_id", "departamentos": "departments"}


def _invalid(field: str, message: str, *, use_instead: list[str] | None = None) -> None:
    details: dict[str, Any] = {"field": field, "reason": message, "retryable": False}
    if use_instead:
        details["use_instead"] = use_instead
    raise OmieError("invalid_parameter", f"{field}: {message}", details)


def _date(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 10: _invalid(field, "deve ser uma data no formato dd/mm/aaaa.")
    try: datetime.strptime(value, "%d/%m/%Y")
    except ValueError: _invalid(field, "deve ser uma data válida no formato dd/mm/aaaa.")
    return value


def _object(arguments: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = arguments.get(field, {})
    if not isinstance(value, Mapping): _invalid(field, "deve ser um objeto JSON.")
    return value


def _positive_id(value: Any, field: str) -> None:
    if type(value) is not int or value < 1:
        _invalid(field, "deve ser um inteiro positivo, sem aspas.")


def _text(value: Any, field: str, *, max_length: int | None = None) -> None:
    if not isinstance(value, str) or not value.strip():
        _invalid(field, "deve ser um texto não vazio.")
    if max_length is not None and len(value) > max_length:
        _invalid(field, f"deve ter no máximo {max_length} caracteres.")


def _identifier_schema(spec: FinanceSpec) -> dict[str, Any]:
    resource = spec.legacy.rsplit("_", 1)[0]
    properties: dict[str, Any] = {}
    for field in IDENTIFIER_NAMES[resource]:
        if field == "id":
            properties[field] = {"type": "integer", "minimum": 1, "description": "ID numérico gerado pela Omie. Informe como inteiro, sem aspas."}
        else:
            properties[field] = {"type": "string", "minLength": 1, "pattern": r"\S"}
            if resource in IDENTIFIER_LENGTHS:
                properties[field]["maxLength"] = IDENTIFIER_LENGTHS[resource]
            properties[field]["description"] = "Código definido pela integração na inclusão." if field == "codigo_integracao" else "Código do cadastro retornado pela Omie."
    if resource == "projects":
        properties["id"]["description"] = "ID numérico Omie retornado em dados[].codigo por projetos.listar ou consultar. Informe como inteiro, sem aspas."
        properties["codigo_integracao"]["description"] = "Valor confirmado no campo codInt do projeto. codInt pode estar vazio; o nome, mesmo quando parece um código como STRB15, não é código de integração. Para descobrir por nome, use projetos.listar com filtros.nome."
    return {"type": "object", "properties": properties, "oneOf": [{"required": [field]} for field in properties], "additionalProperties": False}


def _identifier_params(spec: FinanceSpec, data: Mapping[str, Any], field: str) -> dict[str, Any]:
    resource = spec.legacy.rsplit("_", 1)[0]
    names = IDENTIFIER_NAMES[resource]
    options = [f"{field}.{name}" for name in names]
    unknown = set(data) - set(names)
    if unknown:
        _invalid(f"{field}.{sorted(unknown)[0]}", "não pertence a esta operação; use exatamente um dos identificadores publicados: " + " ou ".join(options) + ".", use_instead=options)
    if len(data) != 1:
        _invalid(field, "informe exatamente um identificador: " + " ou ".join(options) + ".", use_instead=options)
    key, value = next(iter(data.items()))
    if key == "id":
        _positive_id(value, f"{field}.{key}")
    else:
        _text(value, f"{field}.{key}", max_length=IDENTIFIER_LENGTHS.get(resource))
    return {names[key]: value}


def _transaction_names(spec: FinanceSpec) -> dict[str, str]:
    return {key: value for key, value in TRANSACTION_NAMES.items()
            if (key != "id" or spec.legacy.endswith("_update"))
            and (key != "departamentos" or not spec.legacy.endswith("_update"))}


def _rateio_tool(spec: FinanceSpec) -> str:
    resource = spec.name.split(".")[2]
    return f"omie.financeiro.{resource}.definir_departamentos"


def _separate_allocation(spec: FinanceSpec, data: Mapping[str, Any], *, field: str = "dados.departamentos") -> None:
    if "departamentos" in data:
        _invalid(field, "rateio de registros existentes tem operação própria; use " + _rateio_tool(spec) + " com identificador e departamentos. Nenhuma alteração foi enviada.", use_instead=[_rateio_tool(spec)])


def _transaction_required(spec: FinanceSpec) -> tuple[str, ...]:
    return ("codigo_integracao", *TRANSACTION_REQUIRED) if spec.legacy.endswith("_create") else ()


def _transaction_body(spec: FinanceSpec, data: Mapping[str, Any]) -> dict[str, Any]:
    names = _transaction_names(spec)
    unknown = set(data) - set(names)
    if unknown:
        if "conciliado" in unknown:
            _invalid("dados.conciliado", "esta operação não altera conciliação; remova conciliado. Consulte movimentos.listar_para_conciliacao para conferir o estado.")
        if "observacoes" in unknown:
            _invalid("dados.observacoes", "use observacao, no singular, para alterar somente o texto.", use_instead=["dados.observacao"])
        _invalid(f"dados.{sorted(unknown)[0]}", "não pertence ao contrato desta operação.")
    for field in _transaction_required(spec):
        if field not in data:
            _invalid(f"dados.{field}", "é obrigatório nesta operação.")
    if spec.legacy.endswith("_update") and not {"id", "codigo_integracao"} & set(data):
        _invalid("dados", "informe id ou codigo_integracao para identificar o lançamento existente.", use_instead=["dados.id", "dados.codigo_integracao"])
    if spec.legacy.endswith("_update") and not set(data) - {"id", "codigo_integracao"}:
        _invalid("dados", "informe ao menos um campo a alterar além do identificador.")
    for field in ("id", "conta_corrente_id", "cliente_id", "projeto_id"):
        if field in data:
            _positive_id(data[field], f"dados.{field}")
    for field in ("codigo_integracao", "categoria_codigo", "numero_documento"):
        if field in data:
            _text(data[field], f"dados.{field}", max_length=20)
    if "data" in data:
        if not isinstance(data["data"], str) or len(data["data"]) != 10:
            _invalid("dados.data", "deve ser uma data no formato dd/mm/aaaa.")
        _date(data["data"], "dados.data")
    amount = data.get("valor")
    if "valor" in data and (type(amount) not in (int, float) or amount <= 0 or (isinstance(amount, float) and not isfinite(amount))):
        _invalid("dados.valor", "deve ser um número finito maior que zero.")
    if "tipo_documento" in data and (not isinstance(data["tipo_documento"], str) or data["tipo_documento"] not in DIRECT_DOCUMENT_TYPES):
        _invalid("dados.tipo_documento", "deve ser um dos tipos de documento publicados no contrato.")
    if "observacao" in data and not isinstance(data["observacao"], str):
        _invalid("dados.observacao", "deve ser um texto.")
    translated = {names[key]: value for key, value in data.items()}
    if "departamentos" in data:
        if "valor" in data:
            translated["departments"] = normalize_account_departments(
                data["departamentos"], amount, field="dados.departamentos", error_code="invalid_parameter",
            )
        else:
            translated["departments"] = normalize_percentage_departments(data["departamentos"])
    return translated


def _page(arguments: Mapping[str, Any], *, native: bool) -> dict[str, int]:
    page, size = arguments.get("pagina", 1), arguments.get("tamanho_pagina", 50)
    if type(page) is not int or page < 1: _invalid("pagina", "deve ser um inteiro positivo.")
    if type(size) is not int or not 1 <= size <= 50: _invalid("tamanho_pagina", "deve estar entre 1 e 50.")
    return {"nPagina": page, "nRegPorPagina": size} if native else {"page": page, "page_size": size}



def _department_schema() -> dict[str, Any]:
    """Expõe só o formato semântico preferencial; o normalizador mantém legado."""
    example = {"codigo": "8532594895", "percentual": 100}
    return {
        "type": "array",
        "description": (
            "Rateio por departamentos. Envie apenas objetos com codigo (texto) e "
            "percentual (número), por exemplo [{\"codigo\":\"8532594895\",\"percentual\":100}]. "
            "Mesmo se conter apenas dígitos, codigo precisa estar entre aspas. "
            "Um departamento recebe percentual 100; com vários, a soma deve ser 100 e cada código aparece uma única vez. "
            "Não use cCodDep/nPerDep/nValDep nem valor nos itens; envie somente o percentual. "
            "Uma lista vazia solicita remover o rateio."
        ),
        "examples": [[example]],
        "items": {
            "type": "object",
            "description": "Formato único da ferramenta: codigo textual e percentual numérico.",
            "properties": {
                "codigo": {
                    "type": "string", "minLength": 1, "maxLength": 40, "pattern": r"\S",
                    "description": (
                        "Código do departamento SEMPRE como string, inclusive quando só contém números. "
                        "Correto: \"8532594895\". Incorreto: 8532594895 (número)."
                    ),
                    "examples": ["8532594895"],
                },
                "percentual": {
                    "type": "number", "exclusiveMinimum": 0, "maximum": 100,
                    "description": (
                        "Percentual destinado ao departamento. Para departamento único, use 100; "
                        "com múltiplos departamentos, os percentuais devem somar 100."
                    ),
                    "examples": [100],
                },
            },
            "required": ["codigo", "percentual"],
            "additionalProperties": False,
            "examples": [example],
        },
    }


def _schema(spec: FinanceSpec, profiles: list[dict[str, str]]) -> dict[str, Any]:
    date = {"type": "string", "pattern": DATE_PATTERN, "description": "Data no formato dd/mm/aaaa.", "examples": ["01/11/2026"]}
    properties: dict[str, Any] = {"incluir_dados_omie": {"type": "boolean", "description": "Inclui dados nativos apenas quando forem necessários para auditoria."}}
    if not spec.writes:
        properties["atualizar_cache"] = {"type": "boolean", "description": "Exige leitura atualizada após aguardar a janela remota; nunca remove um cooldown da Omie."}
    required: list[str] = []
    if spec.writes:
        data_properties: dict[str, Any] = {
            "id": {"type": "integer", "minimum": 1}, "codigo_integracao": {"type": "string", "minLength": 1, "maxLength": 60},
            "cliente_fornecedor_id": {"type": "integer", "minimum": 1}, "cliente_fornecedor_codigo_integracao": {"type": "string", "minLength": 1},
            "data_vencimento": date, "valor": {"type": "number", "exclusiveMinimum": 0}, "categoria_codigo": {"type": "string", "minLength": 1},
            "projeto_id": {"type": "integer", "minimum": 1}, "observacao": {"type": "string"}, "tipo_documento": {"type": "string", "minLength": 1}, "numero_documento": {"type": "string", "maxLength": 20},
            "lote": {"type": "integer", "minimum": 1}, "titulos": {"type": "array", "minItems": 1, "items": {"type": "object"}},
            "conta_corrente_id": {"type": "integer", "minimum": 1}, "data": date, "id_baixa": {"type": "integer", "minimum": 1},
            "cliente_id": {"type": "integer", "minimum": 1}, "departamentos": _department_schema(),
            "conta_origem_id": {"type": "integer", "minimum": 1}, "conta_destino_id": {"type": "integer", "minimum": 1},
        }
        data_schema: dict[str, Any] = {"type": "object", "properties": data_properties, "description": "Dados semânticos da operação; campos fora do contrato são recusados.", "additionalProperties": False}
        fields = {
            "title": TITLE_NAMES, "payment": PAYMENT_NAMES, "receipt": {"id_baixa": "codigo_baixa"},
            "transfer": TRANSFER_NAMES, "batch": {"lote": "lote", "titulos": "titles"},
        }.get(spec.kind)
        if fields is not None:
            data_schema["properties"] = {field: data_properties[field] for field in fields}
        if spec.kind == "title" and spec.legacy.endswith("_update"):
            data_schema["properties"].pop("departamentos")
        if spec.kind == "title":
            data_schema["properties"]["conta_corrente_id"] = {
                "type": "integer", "minimum": 1,
                "description": "Conta prevista do título (id_conta_corrente). Não altera a conta de uma baixa já executada.",
            }
        if spec.kind == "department_set":
            identifiers = _identifier_schema(spec)
            data_schema = {
                "type": "object", "properties": {**identifiers["properties"], "departamentos": _department_schema()},
                "required": ["departamentos"], "oneOf": identifiers["oneOf"], "additionalProperties": False,
                "description": "Substituição integral do rateio; [] remove todos os departamentos. Envie somente o identificador e o rateio desejado.",
            }
            if spec.legacy != "account_transactions_update":
                data_schema["properties"]["lancamento_conta_id"] = {
                    "type": "integer", "minimum": 1,
                    "description": "Lançamento de conta corrente a conferir depois da alteração do título. O vínculo é validado antes da escrita. Sem esse campo, a confirmação abrange somente o título.",
                }
        if spec.kind == "title" and spec.legacy.endswith("_create"):
            data_schema["required"] = list(TITLE_REQUIRED)
        elif spec.kind == "title":
            data_schema["anyOf"] = [{"required": ["id"]}, {"required": ["codigo_integracao"]}]
            data_schema["allOf"] = [{"anyOf": [{"required": [field]} for field in data_schema["properties"] if field not in {"id", "codigo_integracao"}]}]
        elif spec.kind == "payment":
            data_schema["required"] = ["conta_corrente_id", "valor", "data"]
            data_schema["anyOf"] = [{"required": ["id"]}, {"required": ["codigo_integracao"]}]
        elif spec.kind == "receipt": data_schema["required"] = ["id_baixa"]
        elif spec.kind == "transfer": data_schema["required"] = ["codigo_integracao", "conta_origem_id", "conta_destino_id", "data", "valor", "categoria_codigo"]
        elif spec.kind == "batch":
            data_schema["required"] = ["lote", "titulos"]
            item = {"type": "object", "properties": {field: data_properties[field] for field in TITLE_NAMES if field != "departamentos"}, "additionalProperties": False}
            if spec.legacy.endswith("_create_batch"):
                item["required"] = list(TITLE_REQUIRED)
            else:
                item["anyOf"] = [{"required": ["id"]}, {"required": ["codigo_integracao"]}]
                item["allOf"] = [{"anyOf": [{"required": [field]} for field in item["properties"] if field not in {"id", "codigo_integracao"}]}]
            data_schema["properties"]["titulos"] = {"type": "array", "minItems": 1, "items": item}
        elif spec.kind == "get":
            data_schema = _identifier_schema(spec)
        elif spec.kind == "transaction":
            data_schema["properties"] = {field: data_properties[field] for field in _transaction_names(spec)}
            data_schema["properties"]["codigo_integracao"] = {"type": "string", "minLength": 1, "maxLength": 20, "pattern": r"\S", "description": "Código estável definido pela integração. Obrigatório na criação pelo MCP; até 20 caracteres. Guarde-o para consultar o resultado da inclusão."}
            data_schema["properties"]["tipo_documento"] = {"type": "string", "enum": sorted(DIRECT_DOCUMENT_TYPES), "description": "Tipo de documento aceito pelo MCP, por exemplo 99999 para Outros."}
            for field in ("categoria_codigo", "numero_documento"):
                data_schema["properties"][field].update({"minLength": 1, "maxLength": 20, "pattern": r"\S"})
            data_schema["required"] = list(_transaction_required(spec))
            if spec.legacy.endswith("_update"):
                data_schema["properties"]["id"] = {"type": "integer", "minimum": 1, "description": "ID do lançamento existente, gerado pela Omie."}
                data_schema["anyOf"] = [{"required": ["id"]}, {"required": ["codigo_integracao"]}]
                data_schema["allOf"] = [{"anyOf": [{"required": [field]} for field in _transaction_names(spec) if field not in {"id", "codigo_integracao"}]}]
        properties["dados"] = data_schema
        properties["confirmar"] = {"type": "boolean", "const": True, "description": "Deve ser true para confirmar a escrita."}
        required = ["dados", "confirmar"]
    elif spec.kind != "catalog":
        filters: dict[str, Any] = {}
        if spec.period:
            filters = {"data_inicial": date, "data_final": date}
            if spec.legacy in {"financial_movements_list", "account_transactions_list"}:
                account_description = "ID da conta corrente Omie, obtido em omie.financeiro.contas_correntes.listar."
                if spec.legacy == "account_transactions_list":
                    account_description += " O MCP filtra a conta após consultar todas as páginas do período e pagina apenas os lançamentos dessa conta."
                else:
                    account_description += " Restringe a consulta à conta informada."
                filters["conta_corrente_id"] = {"type": "integer", "minimum": 1, "description": account_description}
            required = ["filtros"]
        elif spec.kind == "get":
            required = ["filtros"]
        elif spec.legacy == "projects_list":
            filters["nome"] = {
                "type": "string", "minLength": 1, "maxLength": 100, "pattern": r"\S",
                "description": "Nome enviado ao filtro de projetos da Omie. Confira os nomes e demais dados retornados; não presuma busca parcial, nome único nem correspondência com codInt.",
                "examples": ["STRB15"],
            }
        properties["filtros"] = _identifier_schema(spec) if spec.kind == "get" else {"type": "object", "properties": filters, "required": ["data_inicial", "data_final"] if spec.period else [], "additionalProperties": False}
        if spec.account_required:
            properties["filtros"]["required"].append("conta_corrente_id")
        if spec.paginated:
            properties.update({"pagina": {"type": "integer", "minimum": 1, "default": 1}, "tamanho_pagina": {"type": "integer", "minimum": 1, "maximum": 50, "default": 50}})
    if len(profiles) > 1:
        properties["profile"] = {"type": "string", "enum": [profile["name"] for profile in profiles]}; required.append("profile")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _translate_title(data: Mapping[str, Any], *, create: bool) -> dict[str, Any]:
    """Traduz apenas os campos solicitados do título, inclusive rateio percentual."""
    unknown = set(data) - set(TITLE_NAMES)
    if unknown: _invalid(f"dados.{sorted(unknown)[0]}", "não pertence ao contrato desta operação.")
    if create:
        for field in TITLE_REQUIRED:
            if field not in data:
                _invalid(f"dados.{field}", "é obrigatório na criação do título.")
    else:
        if not {"id", "codigo_integracao"} & set(data):
            _invalid("dados", "informe id ou codigo_integracao para identificar o título.")
        if not set(data) - {"id", "codigo_integracao"}:
            _invalid("dados", "informe ao menos um campo a alterar além do identificador.")
    for field in ("id", "cliente_fornecedor_id", "projeto_id", "conta_corrente_id"):
        if field in data:
            _positive_id(data[field], f"dados.{field}")
    for field in ("codigo_integracao", "cliente_fornecedor_codigo_integracao", "categoria_codigo", "tipo_documento", "numero_documento"):
        if field in data:
            limit = 60 if field == "codigo_integracao" else 20 if field == "numero_documento" else None
            _text(data[field], f"dados.{field}", max_length=limit)
    if "data_vencimento" in data:
        _date(data["data_vencimento"], "dados.data_vencimento")
    if "valor" in data and (type(data["valor"]) not in (int, float) or data["valor"] <= 0 or (isinstance(data["valor"], float) and not isfinite(data["valor"]))):
        _invalid("dados.valor", "deve ser um número finito maior que zero.")
    if "observacao" in data and not isinstance(data["observacao"], str):
        _invalid("dados.observacao", "deve ser um texto.")
    translated = {TITLE_NAMES[key]: value for key, value in data.items()}
    if "departamentos" in data:
        translated["distribuicao"] = normalize_percentage_departments(data["departamentos"])
    return translated


class OmieFinanceFacade:
    """Publica e executa os contratos financeiros orientados a finalidade."""

    def __init__(self, configuration: Any): self.configuration = configuration

    def definitions(self, profiles: list[dict[str, str]]) -> list[dict[str, Any]]:
        tools = []
        for spec in SPECS:
            description = spec.description
            if spec.legacy == "account_transactions_list":
                description += " Aceita conta_corrente_id; o MCP consulta todas as páginas do período, filtra a conta e só então aplica pagina e tamanho_pagina (máximo 50). Se a consulta falhar, a ausência de duplicidade NÃO está confirmada. As páginas nativas usam o cache compartilhado da instalação."
            if spec.kind == "get":
                fields = list(IDENTIFIER_NAMES[spec.legacy.rsplit("_", 1)[0]])
                description += " Informe exatamente um identificador em " + ("dados" if spec.writes else "filtros") + ": " + " ou ".join(fields) + "."
            if spec.legacy in {"projects_list", "projects_get"}:
                description += " Em dados, codigo é o ID numérico (use filtros.id), codInt é o código de integração e nome é o nome do projeto. Textos como STRB15 podem estar somente em nome; use filtros.codigo_integracao apenas quando o valor estiver confirmado em codInt."
            if spec.legacy == "account_transactions_create":
                description += " Exige codigo_integracao (texto de até 20 caracteres), conta_corrente_id, data, valor, categoria_codigo e tipo_documento em dados. O id é gerado pela Omie."
            if spec.kind == "transaction" and spec.legacy == "account_transactions_update":
                description += " Aceita alteração parcial: informe id ou codigo_integracao e somente os campos a alterar. Não reenvie conta, data, valor ou tipo_documento que não pretende alterar."
            general_update = spec.kind in {"transaction", "title"} and spec.legacy.endswith("_update")
            if general_update:
                description += " Para trocar departamentos, use " + _rateio_tool(spec) + "; esta alteração geral não aceita rateio."
            if spec.kind == "title" and spec.legacy.endswith("_upsert"):
                description += " Para definir somente o rateio de um título existente, prefira " + _rateio_tool(spec) + ". A conferência desta inclusão/alteração abrange o título, sem confirmar propagação para baixas."
            if spec.kind == "department_set":
                description += " Retorna alvo, departamentos observados e verificacao, nunca somente o código de processamento. Departamentos são uma substituição completa, não uma inclusão incremental. Não cancele nem refaça uma baixa."
                if spec.legacy == "account_transactions_update":
                    description += " Para BAXP/BAXR, prefira a tool definir_departamentos do título de origem; a recusa fornece o ID e os argumentos quando o vínculo é conhecido."
                else:
                    description += " Informe lancamento_conta_id quando o objetivo também abranger uma baixa já existente; sem ele, a confirmação cobre apenas o título."
            if spec.kind in {"transaction", "transfer", "title", "department_set"} and not general_update:
                description += (
                    ' Para dados.departamentos use somente [{"codigo":"8532594895","percentual":100}]. '
                    'codigo é TEXTO mesmo quando contém apenas dígitos (sempre entre aspas); '
                    'percentual é NÚMERO e deve totalizar 100. '
                    'Não use chaves técnicas cCodDep/nPerDep/nValDep ou valor no rateio; '
                    'a Omie calcula os valores a partir dos percentuais. Uma lista vazia solicita remover o rateio.'
                )
            if not spec.writes:
                description += CACHE_READ_DESCRIPTION
            tools.append({"name": spec.name, "description": description + " Não use parâmetros técnicos da Omie.", "input_schema": _schema(spec, profiles)})
        return tools

    def call(self, name: str, arguments: Mapping[str, Any], profile: str) -> dict[str, Any]:
        spec = BY_NAME.get(name)
        if name in {f"omie.financeiro.contas_receber.{action}_rateio_departamento" for action in ("criar", "alterar", "excluir")}:
            raise OmieError(
                "tool_unavailable", "Os métodos nativos antigos de distribuição foram descontinuados. Use omie.financeiro.contas_receber.definir_departamentos com a lista completa desejada; [] remove o rateio.",
                {"retryable": False, "use_instead": ["omie.financeiro.contas_receber.definir_departamentos"]},
            )
        if spec is None: raise ValueError("Tool financeira Omie indisponível.")
        if not isinstance(arguments, Mapping): raise OmieError("invalid_parameter", "Os argumentos devem ser um objeto JSON.")
        if "incluir_dados_omie" in arguments and type(arguments["incluir_dados_omie"]) is not bool:
            _invalid("incluir_dados_omie", "deve ser booleano.")
        if spec.writes:
            unknown = set(arguments) - {"profile", "dados", "confirmar", "incluir_dados_omie", "atualizar_cache"}
            if unknown:
                _invalid(sorted(unknown)[0], "não pertence ao contrato desta escrita; campos do objeto devem estar em dados.")
        refresh = arguments.get("atualizar_cache", False)
        if type(refresh) is not bool or (spec.writes and "atualizar_cache" in arguments):
            _invalid("atualizar_cache", "deve ser booleano e pertence somente às leituras.")
        legacy = self._legacy_arguments(spec, arguments)
        if refresh:
            legacy["atualizar_cache"] = True
        account = _object(arguments, "filtros").get("conta_corrente_id") if spec.legacy == "account_transactions_list" else None
        if spec.collect_pages or account is not None:
            legacy = {**legacy, "params": {**legacy["params"], "nPagina": 1, "nRegPorPagina": 50}}
        service = OmieService(self.configuration.root)
        verification = None
        verified_record = None
        if spec.kind == "department_set":
            return define_departments(
                service, profile, spec.legacy.rsplit("_", 1)[0], legacy["identifier"], legacy["departments"],
                movement_id=legacy.get("movement_id"), include_native=arguments.get("incluir_dados_omie") is True,
            )
        if spec.writes:
            verify_allocation = spec.kind in {"transaction", "title", "transfer"} and "departamentos" in _object(arguments, "dados")
            parameters = build_params(spec.legacy, legacy) if verify_allocation else None
            response = service.invoke_financial(profile, spec.legacy, legacy, writes=True)
            if parameters is not None:
                verification = verify_department_allocation(service, profile, spec.legacy, parameters, include_observed=True)
                verified_record = verification.pop("registro")
        else:
            response = service.invoke_financial(profile, spec.legacy, legacy, writes=False)
            if spec.collect_pages or account is not None:
                response = self._collect_pages(service, profile, spec, legacy, response)
            if account is not None:
                response = self._filter_account_transactions(response, account, arguments)
        result = self._normalize(spec, response, arguments.get("incluir_dados_omie") is True)
        if verification is not None:
            result["departamentos"] = verification.pop("departamentos")
            result["verificacao"] = verification
            if arguments.get("incluir_dados_omie") is True:
                result["dados_omie"] = {"requisicao": parameters, "processamento": response, "registro": verified_record}
        if not spec.writes and (metadata := service.cache_metadata()) is not None:
            result["cache"] = metadata
        return result

    def _legacy_arguments(self, spec: FinanceSpec, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if spec.writes:
            if arguments.get("confirmar") is not True: raise OmieError("confirmation_required", "Operações de escrita exigem confirmar=true.", {"retryable": False})
            data = _object(arguments, "dados")
            if spec.kind == "department_set":
                allowed = {"id", "codigo_integracao", "departamentos"}
                if spec.legacy != "account_transactions_update":
                    allowed.add("lancamento_conta_id")
                unknown = set(data) - allowed
                if unknown:
                    _invalid(f"dados.{sorted(unknown)[0]}", "esta operação altera somente o rateio; não envie outros dados do lançamento ou pagamento.")
                if "departamentos" not in data:
                    _invalid("dados.departamentos", "informe a lista completa desejada; [] solicita remover o rateio.")
                identifier = _identifier_params(spec, {key: value for key, value in data.items() if key in {"id", "codigo_integracao"}}, "dados")
                departments = normalize_percentage_departments(data["departamentos"])
                if "lancamento_conta_id" in data:
                    _positive_id(data["lancamento_conta_id"], "dados.lancamento_conta_id")
                return {"identifier": identifier, "departments": departments, **({"movement_id": data["lancamento_conta_id"]} if "lancamento_conta_id" in data else {})}
            if spec.kind in {"transaction", "title"} and spec.legacy.endswith("_update"):
                _separate_allocation(spec, data)
            if spec.kind == "get":
                return {"params": _identifier_params(spec, data, "dados")}
            if spec.kind == "title":
                return {"body": _translate_title(data, create=spec.legacy.endswith("_create"))}
            if spec.kind == "batch":
                self._map(data, {"lote": "lote", "titulos": "titles"})
                titles = data.get("titulos")
                if type(data.get("lote")) is not int or not isinstance(titles, list) or not titles: _invalid("dados", "exige lote e titulos não vazios.")
                if not all(isinstance(item, Mapping) for item in titles): _invalid("dados.titulos", "deve conter apenas objetos JSON.")
                for index, item in enumerate(titles):
                    _separate_allocation(spec, item, field=f"dados.titulos[{index}].departamentos")
                return {"body": {"lote": data["lote"], "titles": [_translate_title(item, create=spec.legacy.endswith("_create_batch")) for item in titles]}}
            if spec.kind == "payment":
                return {"body": self._map(data, PAYMENT_NAMES)}
            if spec.kind == "receipt": return {"body": self._map(data, {"id_baixa": "codigo_baixa"})}
            if spec.kind == "transaction":
                return {"body": _transaction_body(spec, data)}
            if spec.kind == "transfer":
                body = self._map(data, TRANSFER_NAMES)
                if "departamentos" in data:
                    body["departments"] = normalize_account_departments(
                        data["departamentos"], data.get("valor"), field="dados.departamentos", error_code="invalid_parameter",
                    )
                return {"body": body}
        if not spec.paginated:
            for field in ("pagina", "tamanho_pagina"):
                if field in arguments:
                    _invalid(field, "esta consulta não aceita paginação; remova pagina e tamanho_pagina.")
        if spec.kind == "catalog":
            unknown = set(arguments) - {"profile", "incluir_dados_omie", "atualizar_cache"}
            if unknown:
                _invalid(sorted(unknown)[0], "este catálogo não aceita filtros; chame com {} ou somente os controles publicados de perfil e auditoria.")
            return {"params": {}}
        allowed = {"profile", "incluir_dados_omie", "atualizar_cache", "filtros"}
        if spec.paginated:
            allowed.update({"pagina", "tamanho_pagina"})
        unknown = set(arguments) - allowed
        if unknown:
            _invalid(sorted(unknown)[0], "não pertence ao contrato desta consulta; use somente os parâmetros publicados.")
        filters = _object(arguments, "filtros")
        native = spec.legacy in {"financial_movements_list", "account_transactions_list"}
        params = _page(arguments, native=native) if spec.paginated else {}
        if spec.period:
            expected = {"data_inicial", "data_final"}
            if native:
                expected.add("conta_corrente_id")
            if not {"data_inicial", "data_final"} <= set(filters) or not set(filters) <= expected:
                _invalid("filtros", "informe data_inicial, data_final e somente os filtros publicados para esta operação.")
            if spec.account_required and "conta_corrente_id" not in filters:
                _invalid("filtros.conta_corrente_id", "é obrigatório nesta consulta.")
            if "conta_corrente_id" in filters and (type(filters["conta_corrente_id"]) is not int or filters["conta_corrente_id"] < 1):
                _invalid("filtros.conta_corrente_id", "deve ser um inteiro positivo.")
            start, end = _date(filters["data_inicial"], "filtros.data_inicial"), _date(filters["data_final"], "filtros.data_final")
            if datetime.strptime(start, "%d/%m/%Y") > datetime.strptime(end, "%d/%m/%Y"): _invalid("filtros", "data_inicial não pode ser posterior a data_final.")
            if spec.legacy in {"payables_list", "receivables_list"}:
                params.update({"filtrar_por_data_de": start, "filtrar_por_data_ate": end})
            else:
                prefix, suffix, _ = PERIODS[spec.period]; params.update({prefix: start, suffix: end})
            if spec.legacy == "financial_movements_list" and "conta_corrente_id" in filters:
                params["nCodCC"] = filters["conta_corrente_id"]
        elif spec.kind == "get":
            params = _identifier_params(spec, filters, "filtros")
        elif spec.kind == "list":
            allowed_filters = {"nome"} if spec.legacy == "projects_list" else set()
            unknown = set(filters) - allowed_filters
            if unknown:
                message = "esta listagem aceita somente filtros.nome." if allowed_filters else "esta listagem não aceita filtros; remova os campos de filtros."
                _invalid(f"filtros.{sorted(unknown)[0]}", message, use_instead=["filtros.nome"] if allowed_filters else None)
            if "nome" in filters:
                _text(filters["nome"], "filtros.nome", max_length=100)
                params["nome_projeto"] = filters["nome"]
        return {"params": params}

    @staticmethod
    def _map(data: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, Any]:
        unknown = set(data) - set(names)
        if unknown: _invalid(next(iter(unknown)), "não pertence ao contrato desta operação.")
        return {names[key]: value for key, value in data.items()}

    @staticmethod
    def _normalize(spec: FinanceSpec, response: Any, include_native: bool) -> dict[str, Any]:
        if not isinstance(response, Mapping): return {"dados": response}
        metadata = {"pagina", "total_de_paginas", "total_de_registros", "registros_por_pagina", "nPagina", "nTotPaginas", "nRegistros", "nTotRegistros"}
        row_key = {"account_transactions_list": "listaLancamentos", "financial_movements_list": "movimentos"}.get(spec.legacy)
        rows = None
        if spec.kind in {"list", "catalog"}:
            rows = response.get(row_key) if row_key else next((value for key, value in response.items() if key not in metadata and isinstance(value, list)), None)
        if spec.legacy == "financial_movements_list" and isinstance(rows, list):
            rows = [OmieFinanceFacade._with_reconciliation_status(row) for row in rows]
        result: dict[str, Any] = {"dados": rows if rows is not None else [dict(response)]}
        if "pagina" in response or "nPagina" in response:
            current, total = response.get("pagina", response.get("nPagina")), response.get("total_de_paginas", response.get("nTotPaginas"))
            result["paginacao"] = {"pagina": current, "total_paginas": total, "total_registros": response.get("total_de_registros", response.get("nTotRegistros")), "proxima_pagina": current + 1 if isinstance(current, int) and isinstance(total, int) and current < total else None}
        if include_native: result["dados_omie"] = dict(response)
        return result

    @staticmethod
    def _invalid_response(field: str, **diagnostics: Any) -> None:
        raise OmieError(
            "invalid_response", "A Omie retornou uma listagem incompleta ou inconsistente; nenhum resultado parcial foi entregue.",
            {"field": field, "retryable": False, **diagnostics},
        )

    @classmethod
    def _financial_page(cls, response: Any, row_key: str, expected_page: int) -> tuple[list[Mapping[str, Any]], int, int]:
        if not isinstance(response, Mapping):
            cls._invalid_response("response")
        rows = response.get(row_key)
        if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
            cls._invalid_response(row_key)
        # Algumas respostas JSON representam contadores como texto decimal.
        # Aceitar somente representações exatas evita rejeições artificiais,
        # sem transformar metadados ausentes ou inválidos em resultados vazios.
        metadata: dict[str, int] = {}
        for field in ("nPagina", "nTotPaginas", "nRegistros", "nTotRegistros"):
            value = response.get(field)
            if type(value) is int and value >= 0:
                metadata[field] = value
            elif isinstance(value, str) and 1 <= len(value) <= 12 and value.isascii() and value.isdecimal():
                metadata[field] = int(value)
            else:
                cls._invalid_response(
                    field, reason="missing" if field not in response else "invalid_integer",
                    requested_page=expected_page,
                )
        current_page = metadata["nPagina"]
        total_pages, total_records = metadata["nTotPaginas"], metadata["nTotRegistros"]
        if current_page != expected_page:
            # Zero páginas e zero registros podem ser representados por nPagina=0.
            # Só é seguro aceitar esse caso na primeira página inteiramente vazia.
            empty_first_page = (
                expected_page == 1 and current_page == 0 and total_records == 0
                and total_pages in (0, 1) and metadata["nRegistros"] == 0 and not rows
            )
            if not empty_first_page:
                cls._invalid_response(
                    "nPagina", reason="page_mismatch",
                    requested_page=expected_page, returned_page=current_page,
                )
        if metadata["nRegistros"] != len(rows) or len(rows) > 50:
            cls._invalid_response("nRegistros")
        if total_records == 0:
            if expected_page != 1 or total_pages not in (0, 1) or rows:
                cls._invalid_response("nTotRegistros")
        elif total_pages < expected_page or not rows or len(rows) > total_records:
            cls._invalid_response("nTotPaginas")
        return rows, total_pages, total_records

    @classmethod
    def _row_account(cls, row: Mapping[str, Any], group: str) -> int:
        fields = row.get(group)
        account = fields.get("nCodCC") if isinstance(fields, Mapping) else None
        if type(account) is not int or account < 1:
            cls._invalid_response(group + ".nCodCC")
        return account

    @classmethod
    def _collect_pages(cls, service: OmieService, profile: str, spec: FinanceSpec, legacy: dict[str, Any], first: Any) -> dict[str, Any]:
        transactions = spec.legacy == "account_transactions_list"
        row_key = "listaLancamentos" if transactions else "movimentos"
        group = "cabecalho" if transactions else "detalhes"
        first_rows, total_pages, total_records = cls._financial_page(first, row_key, 1)
        rows: list[Mapping[str, Any]] = []
        transaction_ids: set[int] = set()
        page_rows = first_rows
        for current in range(1, max(1, total_pages) + 1):
            if current > 1:
                params = {**legacy["params"], "nPagina": current}
                page = service.invoke_financial(profile, spec.legacy, {**legacy, "params": params}, writes=False)
                page_rows, page_total, page_count = cls._financial_page(page, row_key, current)
                if page_total != total_pages or page_count != total_records:
                    cls._invalid_response("paginacao")
            for row in page_rows:
                account = cls._row_account(row, group)
                if transactions:
                    identifier = row.get("nCodLanc")
                    if type(identifier) is not int or identifier < 1 or identifier in transaction_ids:
                        cls._invalid_response("nCodLanc")
                    transaction_ids.add(identifier)
                if not transactions and account != legacy["params"].get("nCodCC"):
                    cls._invalid_response("detalhes.nCodCC")
            rows.extend(page_rows)
            if len(rows) > total_records:
                cls._invalid_response("nTotRegistros")
        if len(rows) != total_records:
            cls._invalid_response("nTotRegistros")
        return {row_key: rows, "nPagina": 1, "nTotPaginas": 1, "nRegistros": len(rows), "nTotRegistros": len(rows)}

    @staticmethod
    def _filter_account_transactions(response: Mapping[str, Any], account: int, arguments: Mapping[str, Any]) -> dict[str, Any]:
        # ListarLancCC não possui filtro de conta. Preserve a população desse
        # endpoint e aplique a página pública somente após o filtro local.
        rows = [row for row in response["listaLancamentos"] if row["cabecalho"]["nCodCC"] == account]
        pagination = _page(arguments, native=True)
        current, size = pagination["nPagina"], pagination["nRegPorPagina"]
        start = (current - 1) * size
        selected = rows[start:start + size]
        return {
            "listaLancamentos": selected, "nPagina": current,
            "nTotPaginas": max(1, (len(rows) + size - 1) // size),
            "nRegistros": len(selected), "nTotRegistros": len(rows),
        }

    @staticmethod
    def _with_reconciliation_status(row: Any) -> Any:
        """Expõe o estado de conciliação sem exigir inferência de campos nativos."""
        if not isinstance(row, Mapping): return row
        movement = dict(row)
        details = movement.get("detalhes")
        if not isinstance(details, Mapping): details = {}
        date = details.get("dDtConcilia")
        reconciled = isinstance(date, str) and bool(date.strip())
        movement["conciliacao"] = {
            "conciliado": reconciled,
            "data": date if reconciled else None,
            "hora": details.get("cHrConcilia") if reconciled else None,
            "usuario": details.get("cUsConcilia") if reconciled else None,
        }
        return movement
