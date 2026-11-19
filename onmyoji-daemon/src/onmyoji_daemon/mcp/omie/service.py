"""Base segura da integração Omie publicada pelo servidor MCP interno."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
import re
import time
import tomllib
from unicodedata import combining, normalize
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..credentials import CredentialError, KeePassCredentialResolver
from .contracts import build_params
from .customers import create_customer
from .exception import OmieError
from .response_cache import DEFAULT_TTL, ResponseCache
from .traffic import TRAFFIC_GATE, retry_after_seconds


ENDPOINTS = {
    "document_types": "https://app.omie.com.br/api/v1/geral/tiposdoc/", "customers": "https://app.omie.com.br/api/v1/geral/clientes/",
    "products": "https://app.omie.com.br/api/v1/geral/produtos/",
    "departments": "https://app.omie.com.br/api/v1/geral/departamentos/", "projects": "https://app.omie.com.br/api/v1/geral/projetos/",
    "categories": "https://app.omie.com.br/api/v1/geral/categorias/", "bank_accounts": "https://app.omie.com.br/api/v1/geral/contacorrente/",
    "financial_movements": "https://app.omie.com.br/api/v1/financas/mf/", "account_transactions": "https://app.omie.com.br/api/v1/financas/contacorrentelancamentos/",
    "payables": "https://app.omie.com.br/api/v1/financas/contapagar/", "receivables": "https://app.omie.com.br/api/v1/financas/contareceber/",
    "inbound_nfe_receipts": "https://app.omie.com.br/api/v1/produtos/recebimentonfe/", "incoming_invoices": "https://app.omie.com.br/api/v1/produtos/notaentrada/", "electronic_invoices": "https://app.omie.com.br/api/v1/produtos/nfconsultar/",
}
OPERATIONS = {
    "document_types_list": ("document_types", "PesquisarTipoDocumento", False), "document_types_get": ("document_types", "ConsultarTipoDocumento", False),
    "customers_list": ("customers", "ListarClientes", False), "customers_get": ("customers", "ConsultarCliente", False), "customers_create": ("customers", "IncluirCliente", True),
    "products_list": ("products", "ListarProdutos", False), "products_get": ("products", "ConsultarProduto", False),
    "departments_list": ("departments", "ListarDepartamentos", False), "departments_get": ("departments", "ConsultarDepartamento", False), "departments_create": ("departments", "IncluirDepartamento", True), "departments_update": ("departments", "AlterarDepartamento", True), "departments_delete": ("departments", "ExcluirDepartamento", True),
    "projects_list": ("projects", "ListarProjetos", False), "projects_get": ("projects", "ConsultarProjeto", False), "projects_create": ("projects", "IncluirProjeto", True), "projects_update": ("projects", "AlterarProjeto", True), "projects_upsert": ("projects", "UpsertProjeto", True), "projects_delete": ("projects", "ExcluirProjeto", True),
    "categories_list": ("categories", "ListarCategorias", False), "categories_get": ("categories", "ConsultarCategoria", False), "categories_create": ("categories", "IncluirCategoria", True), "categories_update": ("categories", "AlterarCategoria", True), "category_groups_create": ("categories", "IncluirGrupoCategoria", True), "category_groups_update": ("categories", "AlterarGrupoCategoria", True),
    "bank_accounts_list": ("bank_accounts", "ListarContasCorrentes", False), "bank_accounts_get": ("bank_accounts", "ConsultarContaCorrente", False),
    "financial_movements_list": ("financial_movements", "ListarMovimentos", False), "account_transactions_list": ("account_transactions", "ListarLancCC", False), "account_transactions_get": ("account_transactions", "ConsultaLancCC", False), "account_transactions_create": ("account_transactions", "IncluirLancCC", True), "account_transactions_update": ("account_transactions", "AlterarLancCC", True), "account_transactions_delete": ("account_transactions", "ExcluirLancCC", True), "account_transfers_create": ("account_transactions", "IncluirLancCC", True),
    "payables_list": ("payables", "ListarContasPagar", False), "payables_get": ("payables", "ConsultarContaPagar", False), "payables_create": ("payables", "IncluirContaPagar", True), "payables_update": ("payables", "AlterarContaPagar", True), "payables_upsert": ("payables", "UpsertContaPagar", True), "payables_delete": ("payables", "ExcluirContaPagar", True), "payables_pay": ("payables", "LancarPagamento", True), "payables_payment_cancel": ("payables", "CancelarPagamento", True), "payables_create_batch": ("payables", "IncluirContaPagarPorLote", True), "payables_upsert_batch": ("payables", "UpsertContaPagarPorLote", True),
    "receivables_list": ("receivables", "ListarContasReceber", False), "receivables_get": ("receivables", "ConsultarContaReceber", False), "receivables_create": ("receivables", "IncluirContaReceber", True), "receivables_update": ("receivables", "AlterarContaReceber", True), "receivables_upsert": ("receivables", "UpsertContaReceber", True), "receivables_delete": ("receivables", "ExcluirContaReceber", True), "receivables_receive": ("receivables", "LancarRecebimento", True), "receivables_receipt_cancel": ("receivables", "CancelarRecebimento", True), "receivables_receipt_reconcile": ("receivables", "ConciliarRecebimento", True), "receivables_receipt_unreconcile": ("receivables", "DesconciliarRecebimento", True),  "receivables_create_batch": ("receivables", "IncluirContaReceberPorLote", True), "receivables_upsert_batch": ("receivables", "UpsertContaReceberPorLote", True),
    "inbound_nfe_receipts_list": ("inbound_nfe_receipts", "ListarRecebimentos", False), "inbound_nfe_receipts_get": ("inbound_nfe_receipts", "ConsultarRecebimento", False), "inbound_nfe_receipts_update": ("inbound_nfe_receipts", "AlterarRecebimento", True), "inbound_nfe_receipts_set_stage": ("inbound_nfe_receipts", "AlterarEtapaRecebimento", True), "inbound_nfe_receipts_update_completed": ("inbound_nfe_receipts", "AlterarRecebimentoConcluido", True), "inbound_nfe_receipts_complete": ("inbound_nfe_receipts", "ConcluirRecebimento", True), "inbound_nfe_receipts_reverse": ("inbound_nfe_receipts", "ReverterRecebimento", True), "inbound_nfe_receipts_delete": ("inbound_nfe_receipts", "ExcluirRecebimento", True),
    "incoming_invoices_list": ("incoming_invoices", "ListarNotaEnt", False), "incoming_invoices_get": ("incoming_invoices", "ConsultarNotaEnt", False), "incoming_invoices_create": ("incoming_invoices", "IncluirNotaEnt", True), "incoming_invoices_update": ("incoming_invoices", "AlterarNotaEnt", True), "incoming_invoices_delete": ("incoming_invoices", "ExcluirNotaEnt", True), "incoming_invoices_status": ("incoming_invoices", "StatusNotaEnt", False), "electronic_invoices_list": ("electronic_invoices", "ListarNF", False), "electronic_invoices_get": ("electronic_invoices", "ConsultarNF", False),
}


READ_ONLY_METHODS = frozenset(method for _, method, writes in OPERATIONS.values() if not writes)
CACHE_READ_DESCRIPTION = " Leituras bem-sucedidas compartilham cache entre processos desta instalação. O retorno cache informa reutilizado, consultado_em e valido_ate. Para exigir atualização, use atualizar_cache=true; o MCP respeita a janela de repetição da Omie antes de consultar novamente."
_FINANCIAL_RESOURCES = {"payables", "receivables", "account_transactions", "financial_movements"}
_DETAIL_IDENTIFIERS = {
    "ConsultarProjeto": ("codigo", "codInt"),
    "ConsultarContaCorrente": ("nCodCC", "cCodCCInt"),
    "ConsultarCliente": ("codigo_cliente_omie", "codigo_cliente_integracao"),
    "ConsultaLancCC": ("nCodLanc", "cCodIntLanc"),
    "ConsultarContaPagar": ("codigo_lancamento_omie", "codigo_lancamento_integracao"),
    "ConsultarContaReceber": ("codigo_lancamento_omie", "codigo_lancamento_integracao"),
}


def _affected_resources(endpoint: str) -> set[str]:
    """Invalida o recurso alterado e suas representações financeiras dependentes."""
    if endpoint in _FINANCIAL_RESOURCES:
        return set(_FINANCIAL_RESOURCES)
    if endpoint in {"inbound_nfe_receipts", "incoming_invoices"}:
        return {endpoint, *_FINANCIAL_RESOURCES}
    return {endpoint}


@dataclass(frozen=True)
class _RequestPolicy:
    timeout: float
    retries: int
    interval: float
    method_limit: int
    root_limit: int
    app_limit: int | None
    day_limit: int | None
    cache_ttl: float


def _method_busy_error(description: Any) -> bool:
    """Reconhece somente o aviso conhecido de simultaneidade da Omie."""
    if not isinstance(description, str):
        return False
    folded = "".join(ch for ch in normalize("NFKD", description.casefold()) if not combining(ch))
    return "ja existe uma requisicao" in folded and "metodo" in folded and "sendo executada" in folded


def _project_lookup_error(method: str, parameters: Mapping[str, Any], details: Mapping[str, Any]) -> OmieError | None:
    """Contextualiza apenas a ausência conhecida de um identificador de projeto.

    Retorna ``project_not_found`` para ``ConsultarProjeto`` com a descrição
    ``Projeto não cadastrado``, inclusive quando recebida como HTTP 500. Mantém
    os diagnósticos remotos e orienta a descobrir o ID pelo nome, sem inferir
    que o cadastro inexiste ou que seu código de integração está vazio.
    Outras falhas permanecem com a classificação original.
    """
    description = details.get("remote_description")
    if method != "ConsultarProjeto" or not isinstance(description, str):
        return None
    folded = " ".join("".join(ch for ch in normalize("NFKD", description.casefold()) if not combining(ch)).split())
    if re.fullmatch(r"(?:(?:error|erro):\s*)?projeto nao cadastrado[.!]*", folded) is None:
        return None
    integration = "codInt" in parameters
    field = "filtros.codigo_integracao" if integration else "filtros.id"
    identifier = "código de integração" if integration else "ID numérico"
    return OmieError(
        "project_not_found",
        f"A Omie não encontrou projeto pelo {identifier} informado. "
        "Localize-o com omie.financeiro.projetos.listar e filtros.nome, confira as páginas "
        "e confirme o cadastro antes de usar dados[].codigo em filtros.id. "
        "O nome do projeto e seu código de integração são campos distintos.",
        {**details, "retryable": False, "reason": "project_not_found_for_identifier", "field": field,
         "suggested_tool": "omie.financeiro.projetos.listar", "suggested_filter": "filtros.nome"},
    )


class OmieService:
    """Valida contratos e executa operações Omie com credenciais do cofre MCP."""

    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve()
        self.path = self.root / "configs" / "mcp" / "omie.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)
        self._read_observations: list[tuple[bool, float, float]] = []

    def cache_metadata(self) -> dict[str, Any] | None:
        """Informa a idade mais antiga das páginas consultadas nesta operação."""
        if not self._read_observations:
            return None
        def utc(value: float) -> str:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        return {
            "reutilizado": all(item[0] for item in self._read_observations),
            "consultado_em": utc(min(item[1] for item in self._read_observations)),
            "valido_ate": utc(min(item[2] for item in self._read_observations)),
        }

    def _pause_after_error(self, app_key: str, method: str, seconds: float) -> str | None:
        """Preserva o erro remoto se a persistência da pausa também falhar."""
        try:
            TRAFFIC_GATE.pause(self.root, app_key, method, seconds)
        except OmieError as error:
            return error.code
        return None

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        """Executa uma listagem mínima para confirmar as duas credenciais Omie."""
        profile, defaults = self._profile(profile_name)
        data = self._request(profile, defaults, "departments", "ListarDepartamentos", {"pagina": 1, "registros_por_pagina": 1})
        if not isinstance(data, Mapping):
            raise OmieError("invalid_response", "Omie não confirmou a resposta do perfil.")
        return {"profile": profile_name, "authenticated": True}

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Executa uma operação Omie registrada sem expor app_key ou app_secret."""
        if not isinstance(arguments, Mapping):
            raise OmieError("invalid_request", "Os argumentos devem ser um objeto JSON.")
        spec = OPERATIONS.get(operation)
        if spec is None:
            raise OmieError("unsupported_operation", "Operação Omie não suportada.")
        endpoint, method, writes = spec
        refresh = arguments.get("atualizar_cache", False)
        if type(refresh) is not bool or (writes and "atualizar_cache" in arguments):
            raise OmieError("invalid_parameter", "atualizar_cache deve ser booleano e pertence somente às leituras.", {"field": "atualizar_cache", "retryable": False})
        if writes and arguments.get("confirm") is not True:
            raise OmieError("confirmation_required", "Operações de escrita exigem confirm=true.")
        parameters = build_params(operation, arguments)
        profile, defaults = self._profile(profile_name)
        if operation == "customers_create":
            def request(method: str, params: Mapping[str, Any], *, retries: int | None = None) -> Any:
                return self._request(profile, defaults, "customers", method, params, retries=retries)
            return {"data": create_customer(parameters, request)}
        result = {"data": self._request(profile, defaults, endpoint, method, parameters, retries=0 if writes else None, **({"fresh": True} if refresh else {}))}
        if not writes and (metadata := self.cache_metadata()) is not None:
            result["cache"] = metadata
        return result

    def invoke_financial(self, profile_name: str, operation: str, arguments: Mapping[str, Any], *, writes: bool) -> dict[str, Any]:
        """Executa o contrato financeiro já normalizado pela fachada pública."""
        spec = OPERATIONS.get(operation)
        if spec is None:
            raise OmieError("unsupported_operation", "Operação financeira Omie não suportada.")
        endpoint, method, _ = spec
        refresh = arguments.get("atualizar_cache", False)
        if type(refresh) is not bool or (writes and "atualizar_cache" in arguments):
            raise OmieError("invalid_parameter", "atualizar_cache deve ser booleano e pertence somente às leituras.", {"field": "atualizar_cache", "retryable": False})
        parameters = build_params(operation, arguments)
        profile, defaults = self._profile(profile_name)
        try:
            return self._request(profile, defaults, endpoint, method, parameters, retries=0 if writes else None, **({"fresh": True} if refresh else {}))
        except OmieError as error:
            if writes:
                if error.code == "omie_http_error":
                    status = error.details.get("http_status")
                    # HTTP 4xx (exceto timeout 408) é rejeição explícita, não estado incerto.
                    if type(status) is int and 400 <= status < 500 and status != 408:
                        raise
                released_after_request = error.code == "omie_local_lock_error" and error.details.get("phase") == "release"
                if error.code in {"omie_http_error", "network_error", "invalid_response"} or released_after_request:
                    details = {"operation": operation, "cause_code": error.code, "retryable": False}
                    for field in ("http_status", "remote_code", "remote_description", "retry_after_seconds", "phase"):
                        if field in error.details:
                            details[field] = error.details[field]
                    raise OmieError(
                        "outcome_unknown", "A escrita pode ter sido recebida pela Omie; consulte antes de repetir.", details,
                    ) from error
            raise

    def _profile(self, profile_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise OmieError("config_not_found", "Configuração Omie MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise OmieError("invalid_config", "Configuração Omie MCP inválida.") from error
        if document.get("schema_version") != 1:
            raise OmieError("unsupported_config", "Versão da configuração Omie MCP não suportada.")
        profile = document.get("profiles", {}).get(profile_name)
        defaults = document.get("defaults", {})
        if not isinstance(profile, dict) or not isinstance(defaults, dict):
            raise OmieError("profile_not_found", "Perfil Omie não encontrado.")
        if profile.get("enabled") is not True:
            raise OmieError("profile_disabled", "Perfil Omie está desabilitado.")
        return profile, defaults

    def _request(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], endpoint: str, method: str, parameters: Mapping[str, Any], *, retries: int | None = None, fresh: bool = False) -> Any:
        """Compartilha cache e consumo; escritas nunca reutilizam respostas anteriores."""
        key_field, secret_field = profile.get("app_key_field"), profile.get("app_secret_field")
        credential_profile, entry = profile.get("credential_profile"), profile.get("credential_entry_path")
        if not all(isinstance(value, str) and value for value in (key_field, secret_field, credential_profile, entry)):
            raise OmieError("invalid_config", "Referência de credencial Omie incompleta.")
        try:
            app_key = self.credentials.resolve(credential_profile, entry, key_field)
            app_secret = self.credentials.resolve(credential_profile, entry, secret_field)
            policy = _RequestPolicy(
                float(defaults.get("timeout_seconds", 30)),
                int(defaults.get("max_retries", 2)) if retries is None else retries,
                float(defaults.get("min_request_interval_seconds", 0.35)),
                int(defaults.get("method_requests_per_minute", 180)),
                int(defaults.get("process_requests_per_minute", 900)),
                int(defaults["app_requests_per_minute"]) if "app_requests_per_minute" in defaults else None,
                int(defaults["app_requests_per_day"]) if "app_requests_per_day" in defaults else None,
                float(defaults.get("cache_ttl_seconds", DEFAULT_TTL)),
            )
        except CredentialError as error:
            raise OmieError("credential_" + error.code, "Não foi possível obter a credencial Omie.") from error
        except (TypeError, ValueError, OverflowError) as error:
            raise OmieError("invalid_config", "Timeout, tentativas ou limites Omie inválidos.") from error
        if (not isfinite(policy.timeout) or not 0 < policy.timeout <= 3500 or policy.retries < 0
                or not isfinite(policy.interval) or policy.interval < 0
                or not 1 <= policy.method_limit <= 240 or not 1 <= policy.root_limit <= 960
                or policy.app_limit is not None and not 1 <= policy.app_limit <= 960
                or policy.day_limit is not None and policy.day_limit < 1
                or not isfinite(policy.cache_ttl) or not 61 <= policy.cache_ttl <= 300):
            raise OmieError("invalid_config", "Timeout, tentativas, cache ou limites de consumo Omie inválidos.")
        payload = json.dumps({"call": method, "app_key": app_key, "app_secret": app_secret, "param": [dict(parameters)]}, ensure_ascii=False).encode("utf-8")
        cache = ResponseCache(self.root, app_key)
        return self._send_request(cache, app_key, endpoint, method, parameters, payload, policy, fresh=fresh)

    def _remember_read(self, cache: ResponseCache, endpoint: str, method: str, parameters: Mapping[str, Any], result: Mapping[str, Any], generation: int, fingerprint: str, ttl: float) -> None:
        """Aprende aliases somente quando o detalhe comprova ID e integração do mesmo objeto."""
        queries = [dict(parameters)]
        identifiers = _DETAIL_IDENTIFIERS.get(method)
        if identifiers is not None:
            id_key, integration_key = identifiers
            identifier, integration = result.get(id_key), result.get(integration_key)
            if (type(identifier) is int and identifier > 0 and isinstance(integration, str) and integration.strip()
                    and set(parameters) <= set(identifiers)
                    and all(result.get(key) == value for key, value in parameters.items())):
                queries += [{id_key: identifier}, {integration_key: integration}]
        stored = False
        for query in queries:
            stored = cache.put(endpoint, method, query, result, generation=generation, ttl=ttl) or stored
        if stored:
            cache.register_aliases(endpoint, method, queries, fingerprint=fingerprint, generation=generation)
        hit = cache.get(endpoint, method, parameters) if stored else None
        if not cache.is_current(endpoint, generation):
            raise OmieError(
                "omie_read_invalidated", "Uma escrita concorrente invalidou a leitura Omie; consulte novamente antes de confirmar o resultado.",
                {"retryable": False, "retry_after_seconds": 61},
            )
        if hit is not None:
            self._read_observations.append((False, hit.stored_at, hit.expires_at))
        else:
            now = time.time()
            self._read_observations.append((False, now, now))

    def _send_request(self, cache: ResponseCache, app_key: str, endpoint: str, method: str, parameters: Mapping[str, Any], payload: bytes, policy: _RequestPolicy, *, fresh: bool) -> Any:
        """Executa no máximo uma escrita e protege cada tentativa remota de leitura.

        Falhas HTTP e de negócio pausam o método por pelo menos 61 segundos;
        HTTP 425 preserva o bloqueio de 30 minutos. Somente falhas de transporte
        em leituras podem repetir, após aguardar o intervalo compartilhado.
        """
        reading = method in READ_ONLY_METHODS
        if reading and not fresh:
            hit = cache.get(endpoint, method, parameters)
            if hit is not None:
                self._read_observations.append((True, hit.stored_at, hit.expires_at))
                return hit.data
        with TRAFFIC_GATE.serialize(self.root, app_key, method, wait_seconds=policy.timeout + 70.0):
            if reading and not fresh:
                hit = cache.get(endpoint, method, parameters)
                if hit is not None:
                    self._read_observations.append((True, hit.stored_at, hit.expires_at))
                    return hit.data
            generation = cache.generation(endpoint) if reading else None
            fingerprint = cache.resolve_fingerprint(endpoint, method, parameters) if reading else None
            for attempt in range((policy.retries if reading else 0) + 1):
                TRAFFIC_GATE.await_slot(
                    self.root, app_key, method, interval=policy.interval,
                    method_limit=policy.method_limit, root_limit=policy.root_limit,
                    request_fingerprint=fingerprint, app_limit=policy.app_limit, day_limit=policy.day_limit,
                    request_timeout=policy.timeout,
                )
                resources = _affected_resources(endpoint)
                token = None if reading else cache.begin_write(resources, lease_seconds=policy.timeout + 65.0)
                failed = False
                try:
                    try:
                        request = Request(ENDPOINTS[endpoint], data=payload, headers={"Content-Type": "application/json"}, method="POST")
                        with urlopen(request, timeout=policy.timeout) as response:
                            result = json.loads(response.read())
                    except HTTPError as error:
                        wait = max(1800.0 if error.code == 425 else 61.0, retry_after_seconds(error.headers) or 0.0)
                        pause_error = self._pause_after_error(app_key, method, wait)
                        details: dict[str, Any] = {"http_status": error.code, "retryable": False,
                                                   "retry_after_seconds": max(1, int(wait + 0.999))}
                        if pause_error is not None:
                            details["coordination_error"] = pause_error
                        try:
                            body = json.loads(error.read().decode("utf-8", errors="replace"))
                            if isinstance(body, Mapping):
                                description = body.get("description") or body.get("faultstring")
                                if isinstance(description, str) and description.strip():
                                    details["remote_description"] = description.strip()[:300]
                                code = body.get("code") or body.get("faultcode")
                                if isinstance(code, (str, int)) and not isinstance(code, bool):
                                    details["remote_code"] = code
                        except (OSError, ValueError, UnicodeError, AttributeError):
                            pass
                        if error.code == 500 and reading and _method_busy_error(details.get("remote_description")):
                            raise OmieError(
                                "omie_busy", "A Omie já está executando esse método; a consulta não foi concluída. "
                                "A próxima tentativa respeitará o intervalo compartilhado, sem repetição rápida.",
                                {**details, "reason": "same_method_in_progress"},
                            ) from error
                        lookup_error = _project_lookup_error(method, parameters, details)
                        if error.code == 500 and lookup_error is not None:
                            raise lookup_error from error
                        message = f"A API Omie retornou HTTP {error.code}."
                        if error.code == 425:
                            message += " Aguarde o fim do bloqueio temporário e revise os parâmetros antes de tentar novamente."
                        raise OmieError("omie_http_error", message, details) from error
                    except (URLError, TimeoutError) as error:
                        pause_error = self._pause_after_error(app_key, method, 61.0)
                        if reading and attempt < policy.retries and pause_error is None:
                            continue
                        raise OmieError("network_error", "Não foi possível consultar a API Omie.", {"retryable": False, "retry_after_seconds": 61}) from error
                    except (json.JSONDecodeError, UnicodeError) as error:
                        self._pause_after_error(app_key, method, 61.0)
                        raise OmieError("invalid_response", "A API Omie retornou uma resposta inválida.", {"retryable": False, "retry_after_seconds": 61}) from error
                    if not isinstance(result, Mapping):
                        self._pause_after_error(app_key, method, 61.0)
                        raise OmieError("invalid_response", "A API Omie retornou uma resposta inválida.", {"retryable": False, "retry_after_seconds": 61})
                    status_field = next((field for field in ("cCodStatus", "codigo_status") if field in result
                                         and (type(result[field]) not in (str, int) or result[field] not in ("0", 0))), None)
                    if result.get("faultstring") or status_field is not None:
                        pause_error = self._pause_after_error(app_key, method, 61.0)
                        details = {"retryable": False, "retry_after_seconds": 61}
                        if pause_error is not None:
                            details["coordination_error"] = pause_error
                        code = result.get("faultcode") if result.get("faultstring") else result.get(status_field)
                        description = result.get("faultstring") or result.get("cDesStatus" if status_field == "cCodStatus" else "descricao_status")
                        if isinstance(code, (str, int)) and not isinstance(code, bool):
                            details["remote_code"] = code
                        if isinstance(description, str) and description.strip():
                            details["remote_description"] = description.strip()[:300]
                        lookup_error = _project_lookup_error(method, parameters, details)
                        if lookup_error is not None:
                            raise lookup_error
                        raise OmieError("omie_api_error", "A API Omie retornou uma falha de negócio.", details)
                    if reading:
                        self._remember_read(cache, endpoint, method, parameters, result, generation, fingerprint, policy.cache_ttl)
                    return result
                except BaseException:
                    failed = True
                    raise
                finally:
                    try:
                        try:
                            if not reading:
                                cache.end_write(resources, token)
                        finally:
                            TRAFFIC_GATE.complete(self.root, app_key, method, fingerprint)
                    except OmieError as error:
                        if not failed:
                            if reading:
                                raise
                            raise OmieError(
                                "outcome_unknown", "A escrita pode ter sido recebida pela Omie; a invalidação compartilhada falhou. Consulte antes de repetir.",
                                {"cause_code": error.code, "retryable": False, "phase": "cache_invalidation"},
                            ) from error
        raise OmieError("network_error", "Não foi possível consultar a API Omie.")
