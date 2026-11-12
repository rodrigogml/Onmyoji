"""Fachada financeira semântica da Omie.

Os nomes e os filtros publicados aqui pertencem ao MCP.  Os identificadores da
Omie ficam confinados neste adaptador para que uma mudança ou inconsistência da
API não se torne uma instrução ambígua para o agente.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
from threading import RLock
from time import monotonic
from datetime import datetime
from math import isfinite
from typing import Any, Mapping

from .contracts import DIRECT_DOCUMENT_TYPES, normalize_account_departments
from .exception import OmieError
from .service import OmieService


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
    FinanceSpec("omie.financeiro.projetos.listar", "projects_list", "Lista projetos para classificação financeira."),
    FinanceSpec("omie.financeiro.projetos.consultar", "projects_get", "Consulta um projeto pelo identificador.", kind="get"),
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
    FinanceSpec("omie.financeiro.contas_receber.criar_rateio_departamento", "receivables_department_allocation_create", "Cria o rateio por departamento de uma conta a receber.", True, kind="allocation"),
    FinanceSpec("omie.financeiro.contas_receber.alterar_rateio_departamento", "receivables_department_allocation_update", "Altera o rateio por departamento de uma conta a receber.", True, kind="allocation"),
    FinanceSpec("omie.financeiro.contas_receber.excluir_rateio_departamento", "receivables_department_allocation_delete", "Exclui o rateio por departamento de uma conta a receber.", True, kind="allocation"),
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


def _invalid(field: str, message: str, *, use_instead: list[str] | None = None) -> None:
    details: dict[str, Any] = {"field": field, "reason": message, "retryable": False}
    if use_instead:
        details["use_instead"] = use_instead
    raise OmieError("invalid_parameter", f"{field}: {message}", details)


def _date(value: Any, field: str) -> str:
    if not isinstance(value, str): _invalid(field, "deve ser uma data no formato dd/mm/aaaa.")
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
    return {key: value for key, value in TRANSACTION_NAMES.items() if key != "id" or spec.legacy.endswith("_update")}


def _transaction_required(spec: FinanceSpec) -> tuple[str, ...]:
    return ("codigo_integracao", *TRANSACTION_REQUIRED) if spec.legacy.endswith("_create") else TRANSACTION_REQUIRED


def _transaction_body(spec: FinanceSpec, data: Mapping[str, Any]) -> dict[str, Any]:
    names = _transaction_names(spec)
    unknown = set(data) - set(names)
    if unknown:
        _invalid(f"dados.{sorted(unknown)[0]}", "não pertence ao contrato desta operação.")
    for field in _transaction_required(spec):
        if field not in data:
            _invalid(f"dados.{field}", "é obrigatório nesta operação.")
    if spec.legacy.endswith("_update") and not {"id", "codigo_integracao"} & set(data):
        _invalid("dados", "informe id ou codigo_integracao para identificar o lançamento existente.", use_instead=["dados.id", "dados.codigo_integracao"])
    for field in ("id", "conta_corrente_id", "cliente_id", "projeto_id"):
        if field in data:
            _positive_id(data[field], f"dados.{field}")
    for field in ("codigo_integracao", "categoria_codigo", "numero_documento"):
        if field in data:
            _text(data[field], f"dados.{field}", max_length=20)
    if not isinstance(data["data"], str) or len(data["data"]) != 10:
        _invalid("dados.data", "deve ser uma data no formato dd/mm/aaaa.")
    _date(data["data"], "dados.data")
    amount = data["valor"]
    if type(amount) not in (int, float) or amount <= 0 or (isinstance(amount, float) and not isfinite(amount)):
        _invalid("dados.valor", "deve ser um número finito maior que zero.")
    if not isinstance(data["tipo_documento"], str) or data["tipo_documento"] not in DIRECT_DOCUMENT_TYPES:
        _invalid("dados.tipo_documento", "deve ser um dos tipos de documento publicados no contrato.")
    if "observacao" in data and not isinstance(data["observacao"], str):
        _invalid("dados.observacao", "deve ser um texto.")
    translated = {names[key]: value for key, value in data.items()}
    if "departamentos" in data:
        translated["departments"] = normalize_account_departments(
            data["departamentos"], amount, field="dados.departamentos", error_code="invalid_parameter",
        )
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
            "Um departamento recebe percentual 100; com vários, a soma deve ser 100. "
            "Não use cCodDep/nPerDep/nValDep nem valor nos itens; o MCP calcula os valores."
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
    required: list[str] = []
    if spec.writes:
        data_properties: dict[str, Any] = {
            "id": {"type": "integer", "minimum": 1}, "codigo_integracao": {"type": "string", "minLength": 1, "maxLength": 60},
            "cliente_fornecedor_id": {"type": "integer", "minimum": 1}, "cliente_fornecedor_codigo_integracao": {"type": "string", "minLength": 1},
            "data_vencimento": date, "valor": {"type": "number", "exclusiveMinimum": 0}, "categoria_codigo": {"type": "string", "minLength": 1},
            "projeto_id": {"type": "integer", "minimum": 1}, "observacao": {"type": "string"}, "tipo_documento": {"type": "string", "minLength": 1}, "numero_documento": {"type": "string", "maxLength": 20},
            "lote": {"type": "integer", "minimum": 1}, "titulos": {"type": "array", "minItems": 1, "items": {"type": "object"}},
            "conta_corrente_id": {"type": "integer", "minimum": 1}, "data": date, "id_baixa": {"type": "integer", "minimum": 1},
            "chave": {"type": "integer", "minimum": 1}, "distribuicao": {"type": "array", "minItems": 1, "items": {"type": "object"}},
            "cliente_id": {"type": "integer", "minimum": 1}, "departamentos": _department_schema(),
            "conta_origem_id": {"type": "integer", "minimum": 1}, "conta_destino_id": {"type": "integer", "minimum": 1},
        }
        data_schema: dict[str, Any] = {"type": "object", "properties": data_properties, "description": "Dados semânticos da operação; campos fora do contrato são recusados.", "additionalProperties": False}
        if spec.kind == "title" and spec.legacy.endswith("_create"):
            data_schema["required"] = ["codigo_integracao", "cliente_fornecedor_id", "data_vencimento", "valor", "categoria_codigo"]
        elif spec.kind == "payment":
            data_schema["required"] = ["conta_corrente_id", "valor", "data"]
            data_schema["anyOf"] = [{"required": ["id"]}, {"required": ["codigo_integracao"]}]
        elif spec.kind == "receipt": data_schema["required"] = ["id_baixa"]
        elif spec.kind == "transfer": data_schema["required"] = ["codigo_integracao", "conta_origem_id", "conta_destino_id", "data", "valor", "categoria_codigo"]
        elif spec.kind == "batch": data_schema["required"] = ["lote", "titulos"]
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
        properties["filtros"] = _identifier_schema(spec) if spec.kind == "get" else {"type": "object", "properties": filters, "required": ["data_inicial", "data_final"] if spec.period else [], "additionalProperties": False}
        if spec.account_required:
            properties["filtros"]["required"].append("conta_corrente_id")
        if spec.paginated:
            properties.update({"pagina": {"type": "integer", "minimum": 1, "default": 1}, "tamanho_pagina": {"type": "integer", "minimum": 1, "maximum": 50, "default": 50}})
    if len(profiles) > 1:
        properties["profile"] = {"type": "string", "enum": [profile["name"] for profile in profiles]}; required.append("profile")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _translate_title(data: Mapping[str, Any]) -> dict[str, Any]:
    names = {"codigo_integracao": "codigo_lancamento_integracao", "id": "codigo_lancamento_omie", "cliente_fornecedor_id": "codigo_cliente_fornecedor", "cliente_fornecedor_codigo_integracao": "codigo_cliente_fornecedor_integracao", "data_vencimento": "data_vencimento", "valor": "valor_documento", "categoria_codigo": "codigo_categoria", "projeto_id": "codigo_projeto", "observacao": "observacao", "tipo_documento": "codigo_tipo_documento", "numero_documento": "numero_documento"}
    unknown = set(data) - set(names)
    if unknown: _invalid(next(iter(unknown)), "não pertence ao contrato desta operação.")
    return {names[key]: value for key, value in data.items()}


# O cache guarda páginas nativas completas, nunca credenciais, somente em memória.
# A chave inclui a instância/perfil; mudar a página pública não refaz a coleta.
_ACCOUNT_CACHE_TTL = 300.0
_ACCOUNT_CACHE_MAX_ENTRIES = 6
_ACCOUNT_CACHE_MAX_ROWS = 25000
_ACCOUNT_CACHE_LOCK = RLock()
_ACCOUNT_CACHE: OrderedDict[tuple[str, str, str], tuple[float, dict[str, Any]]] = OrderedDict()
_ACCOUNT_PROFILE_LOCKS: dict[tuple[str, str], RLock] = {}


def _profile_cache_lock(root: str, profile: str) -> RLock:
    with _ACCOUNT_CACHE_LOCK:
        return _ACCOUNT_PROFILE_LOCKS.setdefault((root, profile), RLock())


def _cache_get(key: tuple[str, str, str]) -> dict[str, Any] | None:
    with _ACCOUNT_CACHE_LOCK:
        item = _ACCOUNT_CACHE.get(key)
        if item is None:
            return None
        expiration, data = item
        if expiration <= monotonic():
            del _ACCOUNT_CACHE[key]
            return None
        _ACCOUNT_CACHE.move_to_end(key)
        return data


def _cache_put(key: tuple[str, str, str], response: dict[str, Any]) -> None:
    # Não manter históricos desproporcionalmente grandes na memória do daemon.
    if len(response["listaLancamentos"]) > _ACCOUNT_CACHE_MAX_ROWS:
        return
    with _ACCOUNT_CACHE_LOCK:
        _ACCOUNT_CACHE[key] = (monotonic() + _ACCOUNT_CACHE_TTL, response)
        _ACCOUNT_CACHE.move_to_end(key)
        while len(_ACCOUNT_CACHE) > _ACCOUNT_CACHE_MAX_ENTRIES:
            _ACCOUNT_CACHE.popitem(last=False)


def _cache_invalidate(root: str, profile: str) -> None:
    with _ACCOUNT_CACHE_LOCK:
        for key in list(_ACCOUNT_CACHE):
            if key[0] == root and key[1] == profile:
                del _ACCOUNT_CACHE[key]


class OmieFinanceFacade:
    """Publica e executa os contratos financeiros orientados a finalidade."""

    def __init__(self, configuration: Any): self.configuration = configuration

    def definitions(self, profiles: list[dict[str, str]]) -> list[dict[str, Any]]:
        tools = []
        for spec in SPECS:
            description = spec.description
            if spec.legacy == "account_transactions_list":
                description += " Aceita conta_corrente_id; o MCP consulta todas as páginas do período, filtra a conta e só então aplica pagina e tamanho_pagina (máximo 50). Se a consulta falhar, a ausência de duplicidade NÃO está confirmada. Resultados bem-sucedidos podem permanecer em cache por 5 minutos."
            if spec.kind == "get":
                fields = list(IDENTIFIER_NAMES[spec.legacy.rsplit("_", 1)[0]])
                description += " Informe exatamente um identificador em " + ("dados" if spec.writes else "filtros") + ": " + " ou ".join(fields) + "."
            if spec.legacy == "account_transactions_create":
                description += " Exige codigo_integracao (texto de até 20 caracteres), conta_corrente_id, data, valor, categoria_codigo e tipo_documento em dados. O id é gerado pela Omie."
            if spec.kind in {"transaction", "transfer"}:
                description += (
                    ' Para dados.departamentos use somente [{"codigo":"8532594895","percentual":100}]. '
                    'codigo é TEXTO mesmo quando contém apenas dígitos (sempre entre aspas); '
                    'percentual é NÚMERO e deve totalizar 100. '
                    'Não use chaves técnicas cCodDep/nPerDep/nValDep ou valor no rateio; '
                    'o MCP calcula o valor de cada departamento.'
                )
            tools.append({"name": spec.name, "description": description + " Não use parâmetros técnicos da Omie.", "input_schema": _schema(spec, profiles)})
        return tools

    def call(self, name: str, arguments: Mapping[str, Any], profile: str) -> dict[str, Any]:
        spec = BY_NAME.get(name)
        if spec is None: raise ValueError("Tool financeira Omie indisponível.")
        if not isinstance(arguments, Mapping): raise OmieError("invalid_parameter", "Os argumentos devem ser um objeto JSON.")
        legacy = self._legacy_arguments(spec, arguments)
        account = _object(arguments, "filtros").get("conta_corrente_id") if spec.legacy == "account_transactions_list" else None
        if spec.collect_pages or account is not None:
            legacy = {"params": {**legacy["params"], "nPagina": 1, "nRegPorPagina": 50}}
        service = OmieService(self.configuration.root)
        root = str(self.configuration.root.resolve())
        if account is not None:
            # A mesma consulta nativa é usada por todas as páginas/contas públicas.
            cache_key = (root, profile, json.dumps(legacy["params"], sort_keys=True))
            with _profile_cache_lock(root, profile):
                response = _cache_get(cache_key)
                if response is None:
                    response = service.invoke_financial(profile, spec.legacy, legacy, writes=False)
                    response = self._collect_pages(service, profile, spec, legacy, response)
                    _cache_put(cache_key, response)
                response = self._filter_account_transactions(response, account, arguments)
        elif spec.writes:
            # Não reutilizar respostas antigas em escritas (inclusive incertas).
            with _profile_cache_lock(root, profile):
                _cache_invalidate(root, profile)
                try:
                    response = service.invoke_financial(profile, spec.legacy, legacy, writes=True)
                finally:
                    _cache_invalidate(root, profile)
        else:
            response = service.invoke_financial(profile, spec.legacy, legacy, writes=False)
            if spec.collect_pages:
                response = self._collect_pages(service, profile, spec, legacy, response)
        return self._normalize(spec, response, arguments.get("incluir_dados_omie") is True)

    def _legacy_arguments(self, spec: FinanceSpec, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if spec.writes:
            if arguments.get("confirmar") is not True: raise OmieError("confirmation_required", "Operações de escrita exigem confirmar=true.", {"retryable": False})
            data = _object(arguments, "dados")
            if spec.kind == "get":
                return {"params": _identifier_params(spec, data, "dados")}
            if spec.kind == "title": return {"body": _translate_title(data)}
            if spec.kind == "batch":
                titles = data.get("titulos")
                if type(data.get("lote")) is not int or not isinstance(titles, list) or not titles: _invalid("dados", "exige lote e titulos não vazios.")
                if not all(isinstance(item, Mapping) for item in titles): _invalid("dados.titulos", "deve conter apenas objetos JSON.")
                return {"body": {"lote": data["lote"], "titles": [_translate_title(item) for item in titles]}}
            if spec.kind == "payment":
                names = {"id": "codigo_lancamento", "codigo_integracao": "codigo_lancamento_integracao", "conta_corrente_id": "codigo_conta_corrente", "valor": "valor", "data": "data", "observacao": "observacao"}
                return {"body": self._map(data, names)}
            if spec.kind == "receipt": return {"body": self._map(data, {"id_baixa": "codigo_baixa"})}
            if spec.kind == "allocation": return {"body": self._map(data, {"id": "codigo_lancamento_omie", "chave": "chave_lancamento", "distribuicao": "distribuicao"})}
            if spec.kind == "transaction":
                return {"body": _transaction_body(spec, data)}
            if spec.kind == "transfer":
                names = {"codigo_integracao": "integration_id", "conta_origem_id": "source_account_id", "conta_destino_id": "destination_account_id", "data": "date", "valor": "amount", "categoria_codigo": "category_code", "numero_documento": "document_number", "observacao": "note", "projeto_id": "project_id", "departamentos": "departments"}
                body = self._map(data, names)
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
            unknown = set(arguments) - {"profile", "incluir_dados_omie"}
            if unknown:
                _invalid(sorted(unknown)[0], "este catálogo não aceita filtros; chame com {} ou somente os controles publicados de perfil e auditoria.")
            return {"params": {}}
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
                page = service.invoke_financial(profile, spec.legacy, {"params": params}, writes=False)
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
