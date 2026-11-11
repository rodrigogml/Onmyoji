"""Fachada financeira semântica da Omie.

Os nomes e os filtros publicados aqui pertencem ao MCP.  Os identificadores da
Omie ficam confinados neste adaptador para que uma mudança ou inconsistência da
API não se torne uma instrução ambígua para o agente.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

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


def _list_specs(resource: str, legacy: str, label: str) -> list[FinanceSpec]:
    reconciliation_note = (
        " Cada item informa conciliacao.conciliado: true quando a Omie retorna "
        "a data de conciliação; false quando ela está ausente."
        if legacy == "financial_movements_list" else ""
    )
    return [FinanceSpec(
        f"omie.financeiro.{resource}.listar_por_{period}", legacy,
        f"Lista {label} exclusivamente pelo período de {text}.{reconciliation_note}", period=period,
    ) for period, (_, _, text) in PERIODS.items()]


SPECS: list[FinanceSpec] = [
    FinanceSpec("omie.financeiro.contas_correntes.listar", "bank_accounts_list", "Lista contas correntes disponíveis para operações financeiras."),
    FinanceSpec("omie.financeiro.contas_correntes.consultar", "bank_accounts_get", "Consulta uma conta corrente pelo identificador público.", kind="get"),
    FinanceSpec("omie.financeiro.categorias.listar", "categories_list", "Lista categorias financeiras."),
    FinanceSpec("omie.financeiro.categorias.consultar", "categories_get", "Consulta uma categoria pelo código.", kind="get"),
    FinanceSpec("omie.financeiro.departamentos.listar", "departments_list", "Lista departamentos para rateio."),
    FinanceSpec("omie.financeiro.departamentos.consultar", "departments_get", "Consulta um departamento pelo código.", kind="get"),
    FinanceSpec("omie.financeiro.projetos.listar", "projects_list", "Lista projetos para classificação financeira."),
    FinanceSpec("omie.financeiro.projetos.consultar", "projects_get", "Consulta um projeto pelo identificador.", kind="get"),
    FinanceSpec("omie.financeiro.tipos_documento.listar", "document_types_list", "Lista tipos de documento aceitos pela Omie."),
]
SPECS += _list_specs("movimentos", "financial_movements_list", "movimentos financeiros")
SPECS += [
    FinanceSpec("omie.financeiro.lancamentos_conta.listar_por_inclusao", "account_transactions_list", "Lista lançamentos de conta corrente pela data de inclusão.", period="inclusao"),
    FinanceSpec("omie.financeiro.lancamentos_conta.listar_por_alteracao", "account_transactions_list", "Lista lançamentos de conta corrente pela data de alteração.", period="alteracao"),
    FinanceSpec("omie.financeiro.lancamentos_conta.listar_por_periodo", "account_transactions_list", "Lista lançamentos brutos de conta corrente pelo período do extrato.", period="periodo"),
    FinanceSpec("omie.financeiro.movimentos.listar_para_conciliacao", "financial_movements_list", "Consulta completa para conciliar um extrato bancário: exige conta e período, coleta todas as páginas e informa o estado de conciliação.", period="pagamento", account_required=True, collect_pages=True),
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


def _page(arguments: Mapping[str, Any], *, native: bool) -> dict[str, int]:
    page, size = arguments.get("pagina", 1), arguments.get("tamanho_pagina", 50)
    if type(page) is not int or page < 1: _invalid("pagina", "deve ser um inteiro positivo.")
    if type(size) is not int or not 1 <= size <= 50: _invalid("tamanho_pagina", "deve estar entre 1 e 50.")
    return {"nPagina": page, "nRegPorPagina": size} if native else {"page": page, "page_size": size}


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
            "cliente_id": {"type": "integer", "minimum": 1}, "departamentos": {"type": "array", "items": {"type": "object"}},
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
        properties["dados"] = data_schema
        properties["confirmar"] = {"type": "boolean", "const": True, "description": "Deve ser true para confirmar a escrita."}
        required = ["dados", "confirmar"]
    else:
        filters: dict[str, Any] = {}
        if spec.period:
            filters = {"data_inicial": date, "data_final": date}
            if spec.legacy in {"financial_movements_list", "account_transactions_list"}:
                filters["conta_corrente_id"] = {"type": "integer", "minimum": 1, "description": "Conta corrente Omie; restrinja a consulta quando conhecida."}
            required = ["filtros"]
        elif spec.kind == "get":
            filters = {"id": {"type": "integer", "minimum": 1}, "codigo_integracao": {"type": "string", "minLength": 1}, "codigo": {"type": "string", "minLength": 1}}
            required = ["filtros"]
        properties.update({"filtros": {"type": "object", "properties": filters, "required": ["data_inicial", "data_final"] if spec.period else [], "additionalProperties": False}})
        if spec.account_required:
            properties["filtros"]["required"].append("conta_corrente_id")
        if not spec.collect_pages:
            properties.update({"pagina": {"type": "integer", "minimum": 1, "default": 1}, "tamanho_pagina": {"type": "integer", "minimum": 1, "maximum": 50, "default": 50}})
    if len(profiles) > 1:
        properties["profile"] = {"type": "string", "enum": [profile["name"] for profile in profiles]}; required.append("profile")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _translate_title(data: Mapping[str, Any]) -> dict[str, Any]:
    names = {"codigo_integracao": "codigo_lancamento_integracao", "id": "codigo_lancamento_omie", "cliente_fornecedor_id": "codigo_cliente_fornecedor", "cliente_fornecedor_codigo_integracao": "codigo_cliente_fornecedor_integracao", "data_vencimento": "data_vencimento", "valor": "valor_documento", "categoria_codigo": "codigo_categoria", "projeto_id": "codigo_projeto", "observacao": "observacao", "tipo_documento": "codigo_tipo_documento", "numero_documento": "numero_documento"}
    unknown = set(data) - set(names)
    if unknown: _invalid(next(iter(unknown)), "não pertence ao contrato desta operação.")
    return {names[key]: value for key, value in data.items()}


class OmieFinanceFacade:
    """Publica e executa os contratos financeiros orientados a finalidade."""

    def __init__(self, configuration: Any): self.configuration = configuration

    def definitions(self, profiles: list[dict[str, str]]) -> list[dict[str, Any]]:
        return [{"name": spec.name, "description": spec.description + " Não use parâmetros técnicos da Omie.", "input_schema": _schema(spec, profiles)} for spec in SPECS]

    def call(self, name: str, arguments: Mapping[str, Any], profile: str) -> dict[str, Any]:
        spec = BY_NAME.get(name)
        if spec is None: raise ValueError("Tool financeira Omie indisponível.")
        if not isinstance(arguments, Mapping): raise OmieError("invalid_parameter", "Os argumentos devem ser um objeto JSON.")
        legacy = self._legacy_arguments(spec, arguments)
        service = OmieService(self.configuration.root)
        response = service.invoke_financial(profile, spec.legacy, legacy, writes=spec.writes)
        if spec.collect_pages:
            response = self._collect_pages(service, profile, spec, legacy, response)
        return self._normalize(spec, response, arguments.get("incluir_dados_omie") is True)

    def _legacy_arguments(self, spec: FinanceSpec, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if spec.writes:
            if arguments.get("confirmar") is not True: raise OmieError("confirmation_required", "Operações de escrita exigem confirmar=true.", {"retryable": False})
            data = _object(arguments, "dados")
            if spec.kind == "get":
                names = {"id": "nCodLanc", "codigo_integracao": "cCodIntLanc"} if spec.legacy == "account_transactions_delete" else {"id": "codigo_lancamento_omie", "codigo_integracao": "codigo_lancamento_integracao"}
                identifiers = self._map(data, names)
                if len(identifiers) != 1: _invalid("dados", "informe exatamente um identificador.")
                return {"params": identifiers}
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
                names = {"id": "id", "codigo_integracao": "integration_id", "conta_corrente_id": "account_id", "data": "date", "valor": "amount", "categoria_codigo": "category_code", "tipo_documento": "document_type", "numero_documento": "document_number", "cliente_id": "customer_id", "projeto_id": "project_id", "observacao": "note", "departamentos": "departments"}
                return {"body": self._map(data, names)}
            if spec.kind == "transfer":
                names = {"codigo_integracao": "integration_id", "conta_origem_id": "source_account_id", "conta_destino_id": "destination_account_id", "data": "date", "valor": "amount", "categoria_codigo": "category_code", "numero_documento": "document_number", "observacao": "note", "projeto_id": "project_id", "departamentos": "departments"}
                return {"body": self._map(data, names)}
        filters = _object(arguments, "filtros")
        native = spec.legacy in {"financial_movements_list", "account_transactions_list"}
        params = _page(arguments, native=native)
        if spec.period:
            expected = {"data_inicial", "data_final", "conta_corrente_id"}
            if not {"data_inicial", "data_final"} <= set(filters) or not set(filters) <= expected: _invalid("filtros", "exige data_inicial, data_final e, opcionalmente, conta_corrente_id.")
            start, end = _date(filters["data_inicial"], "filtros.data_inicial"), _date(filters["data_final"], "filtros.data_final")
            if datetime.strptime(start, "%d/%m/%Y") > datetime.strptime(end, "%d/%m/%Y"): _invalid("filtros", "data_inicial não pode ser posterior a data_final.")
            if spec.legacy in {"payables_list", "receivables_list"}:
                params.update({"filtrar_por_data_de": start, "filtrar_por_data_ate": end})
            else:
                prefix, suffix, _ = PERIODS[spec.period]; params.update({prefix: start, suffix: end})
            if "conta_corrente_id" in filters:
                params["nCodCC"] = filters["conta_corrente_id"]
        elif spec.kind == "get":
            if set(filters) != {next(iter(filters))} if filters else True: _invalid("filtros", "informe exatamente um identificador.")
            value = next(iter(filters.values()), None)
            if spec.legacy == "bank_accounts_get": params = {"nCodCC": value} if "id" in filters else {"cCodCCInt": value}
            elif spec.legacy == "categories_get" or spec.legacy == "departments_get": params = {"codigo": value}
            elif spec.legacy == "projects_get": params = {"codigo": value} if "id" in filters else {"codInt": value}
            elif spec.legacy in {"account_transactions_get", "account_transactions_delete"}: params = {"nCodLanc": value} if "id" in filters else {"cCodIntLanc": value}
            else: params = {"codigo_lancamento_omie": value} if "id" in filters else {"codigo_lancamento_integracao": value}
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
        rows = next((value for key, value in response.items() if key not in metadata and isinstance(value, list)), None)
        if spec.legacy == "financial_movements_list" and isinstance(rows, list):
            rows = [OmieFinanceFacade._with_reconciliation_status(row) for row in rows]
        result: dict[str, Any] = {"dados": rows if rows is not None else [dict(response)]}
        if "pagina" in response or "nPagina" in response:
            current, total = response.get("pagina", response.get("nPagina")), response.get("total_de_paginas", response.get("nTotPaginas"))
            result["paginacao"] = {"pagina": current, "total_paginas": total, "total_registros": response.get("total_de_registros", response.get("nTotRegistros")), "proxima_pagina": current + 1 if isinstance(current, int) and isinstance(total, int) and current < total else None}
        if include_native: result["dados_omie"] = dict(response)
        return result

    @staticmethod
    def _collect_pages(service: OmieService, profile: str, spec: FinanceSpec, legacy: dict[str, Any], first: Any) -> Any:
        if not isinstance(first, Mapping): return first
        total = first.get("nTotPaginas")
        current = first.get("nPagina")
        rows = first.get("movimentos")
        if not isinstance(total, int) or not isinstance(current, int) or not isinstance(rows, list): return first
        merged = dict(first); merged["movimentos"] = list(rows)
        while current < total:
            current += 1
            params = dict(legacy["params"]); params["nPagina"] = current
            page = service.invoke_financial(profile, spec.legacy, {"params": params}, writes=False)
            if not isinstance(page, Mapping) or not isinstance(page.get("movimentos"), list): break
            merged["movimentos"].extend(page["movimentos"])
        merged["nPagina"] = 1; merged["nTotPaginas"] = 1; merged["nRegistros"] = len(merged["movimentos"]); merged["nTotRegistros"] = len(merged["movimentos"])
        return merged

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
