"""Base segura da integração Omie publicada pelo servidor MCP interno."""
from __future__ import annotations

import json
from pathlib import Path
import time
import tomllib
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..credentials import CredentialError, KeePassCredentialResolver


ENDPOINTS = {
    "document_types": "https://app.omie.com.br/api/v1/geral/tiposdoc/", "customers": "https://app.omie.com.br/api/v1/geral/clientes/",
    "departments": "https://app.omie.com.br/api/v1/geral/departamentos/", "projects": "https://app.omie.com.br/api/v1/geral/projetos/",
    "categories": "https://app.omie.com.br/api/v1/geral/categorias/", "bank_accounts": "https://app.omie.com.br/api/v1/geral/contacorrente/",
    "financial_movements": "https://app.omie.com.br/api/v1/financas/mf/", "account_transactions": "https://app.omie.com.br/api/v1/financas/contacorrentelancamentos/",
    "payables": "https://app.omie.com.br/api/v1/financas/contapagar/", "receivables": "https://app.omie.com.br/api/v1/financas/contareceber/",
}
OPERATIONS = {
    "document_types_list": ("document_types", "PesquisarTipoDocumento", False), "document_types_get": ("document_types", "ConsultarTipoDocumento", False),
    "customers_list": ("customers", "ListarClientes", False), "customers_get": ("customers", "ConsultarCliente", False), "customers_create": ("customers", "IncluirCliente", True),
    "departments_list": ("departments", "ListarDepartamentos", False), "departments_get": ("departments", "ConsultarDepartamento", False), "departments_create": ("departments", "IncluirDepartamento", True), "departments_update": ("departments", "AlterarDepartamento", True), "departments_delete": ("departments", "ExcluirDepartamento", True),
    "projects_list": ("projects", "ListarProjetos", False), "projects_get": ("projects", "ConsultarProjeto", False), "projects_create": ("projects", "IncluirProjeto", True), "projects_update": ("projects", "AlterarProjeto", True), "projects_upsert": ("projects", "UpsertProjeto", True), "projects_delete": ("projects", "ExcluirProjeto", True),
    "categories_list": ("categories", "ListarCategorias", False), "categories_get": ("categories", "ConsultarCategoria", False), "categories_create": ("categories", "IncluirCategoria", True), "categories_update": ("categories", "AlterarCategoria", True), "category_groups_create": ("categories", "IncluirGrupoCategoria", True), "category_groups_update": ("categories", "AlterarGrupoCategoria", True),
    "bank_accounts_list": ("bank_accounts", "ListarContasCorrentes", False), "bank_accounts_get": ("bank_accounts", "ConsultarContaCorrente", False),
    "financial_movements_list": ("financial_movements", "ListarMovimentos", False), "account_transactions_list": ("account_transactions", "ListarLancCC", False), "account_transactions_get": ("account_transactions", "ConsultaLancCC", False), "account_transactions_create": ("account_transactions", "IncluirLancCC", True), "account_transactions_update": ("account_transactions", "AlterarLancCC", True), "account_transactions_delete": ("account_transactions", "ExcluirLancCC", True), "account_transfers_create": ("account_transactions", "IncluirLancCC", True),
    "payables_list": ("payables", "ListarContasPagar", False), "payables_get": ("payables", "ConsultarContaPagar", False), "payables_create": ("payables", "IncluirContaPagar", True), "payables_update": ("payables", "AlterarContaPagar", True), "payables_upsert": ("payables", "UpsertContaPagar", True), "payables_delete": ("payables", "ExcluirContaPagar", True), "payables_pay": ("payables", "LancarPagamento", True), "payables_payment_cancel": ("payables", "CancelarPagamento", True), "payables_create_batch": ("payables", "IncluirContaPagarPorLote", True), "payables_upsert_batch": ("payables", "UpsertContaPagarPorLote", True),
    "receivables_list": ("receivables", "ListarContasReceber", False), "receivables_get": ("receivables", "ConsultarContaReceber", False), "receivables_create": ("receivables", "IncluirContaReceber", True), "receivables_update": ("receivables", "AlterarContaReceber", True), "receivables_upsert": ("receivables", "UpsertContaReceber", True), "receivables_delete": ("receivables", "ExcluirContaReceber", True), "receivables_receive": ("receivables", "LancarRecebimento", True), "receivables_receipt_cancel": ("receivables", "CancelarRecebimento", True), "receivables_receipt_reconcile": ("receivables", "ConciliarRecebimento", True), "receivables_receipt_unreconcile": ("receivables", "DesconciliarRecebimento", True), "receivables_department_allocation_create": ("receivables", "IncluirDistribuicaoDepartamento", True), "receivables_department_allocation_update": ("receivables", "AlterarDistribuicaoDepartamento", True), "receivables_department_allocation_delete": ("receivables", "ExcluirDistribuicaoDepartamento", True), "receivables_create_batch": ("receivables", "IncluirContaReceberPorLote", True), "receivables_upsert_batch": ("receivables", "UpsertContaReceberPorLote", True),
}


class OmieError(ValueError):
    """Erro funcional sanitizado da integração Omie."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class OmieService:
    """Obtém credenciais Omie do cofre MCP e confirma a conectividade."""

    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve()
        self.path = self.root / "configs" / "mcp" / "omie.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        """Executa uma listagem mínima para confirmar as duas credenciais Omie."""
        profile, defaults = self._profile(profile_name)
        data = self._request(profile, defaults, "departments", "ListarDepartamentos", {"pagina": 1, "registros_por_pagina": 1})
        if not isinstance(data, Mapping):
            raise OmieError("invalid_response", "Omie não confirmou a resposta do perfil.")
        return {"profile": profile_name, "authenticated": True}

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Executa uma operação Omie registrada sem expor app_key ou app_secret."""
        profile, defaults = self._profile(profile_name)
        spec = OPERATIONS.get(operation)
        if spec is None:
            raise OmieError("unsupported_operation", "Operação Omie não suportada.")
        endpoint, method, writes = spec
        if writes and arguments.get("confirm") is not True:
            raise OmieError("confirmation_required", "Operações de escrita exigem confirm=true.")
        parameters = arguments.get("body", arguments.get("params", {}))
        if not isinstance(parameters, Mapping):
            raise OmieError("invalid_arguments", "body ou params deve ser um objeto.")
        return {"data": self._request(profile, defaults, endpoint, method, parameters)}

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

    def _request(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], endpoint: str, method: str, parameters: Mapping[str, Any]) -> Any:
        key_field, secret_field = profile.get("app_key_field"), profile.get("app_secret_field")
        credential_profile, entry = profile.get("credential_profile"), profile.get("credential_entry_path")
        if not all(isinstance(value, str) and value for value in (key_field, secret_field, credential_profile, entry)):
            raise OmieError("invalid_config", "Referência de credencial Omie incompleta.")
        try:
            app_key = self.credentials.resolve(credential_profile, entry, key_field)
            app_secret = self.credentials.resolve(credential_profile, entry, secret_field)
            timeout, retries = float(defaults.get("timeout_seconds", 30)), int(defaults.get("max_retries", 2))
        except CredentialError as error:
            raise OmieError("credential_" + error.code, "Não foi possível obter a credencial Omie.") from error
        except (TypeError, ValueError) as error:
            raise OmieError("invalid_config", "Timeout ou tentativas Omie inválidos.") from error
        if timeout <= 0 or retries < 0:
            raise OmieError("invalid_config", "Timeout ou tentativas Omie inválidos.")
        payload = json.dumps({"call": method, "app_key": app_key, "app_secret": app_secret, "param": [dict(parameters)]}, ensure_ascii=False).encode("utf-8")
        for attempt in range(retries + 1):
            try:
                request = Request(ENDPOINTS[endpoint], data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(request, timeout=timeout) as response:
                    result = json.loads(response.read())
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == retries:
                    raise OmieError("omie_http_error", f"A API Omie retornou HTTP {error.code}.") from error
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                if attempt == retries:
                    raise OmieError("network_error", "Não foi possível consultar a API Omie.") from error
            else:
                if isinstance(result, Mapping) and (result.get("faultstring") or result.get("cCodStatus") not in (None, "0", 0)):
                    raise OmieError("omie_api_error", "A API Omie retornou uma falha de negócio.")
                return result
            time.sleep(0.25 * (attempt + 1))
        raise OmieError("network_error", "Não foi possível consultar a API Omie.")
