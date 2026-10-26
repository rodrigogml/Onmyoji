"""Cliente seguro da API Forward Email para domínios e aliases."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import time
import tomllib
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from ..credentials import CredentialError, KeePassCredentialResolver


OPERATIONS: dict[str, tuple[str, str, bool]] = {
    "list_domains": ("/domains", "GET", False),
    "get_domain": ("/domains/{domain}", "GET", False),
    "create_domain": ("/domains", "POST", True),
    "update_domain": ("/domains/{domain}", "PUT", True),
    "verify_domain_records": ("/domains/{domain}/verify-records", "GET", False),
    "verify_domain_smtp": ("/domains/{domain}/verify-smtp", "GET", False),
    "list_aliases": ("/domains/{domain}/aliases", "GET", False),
    "get_alias": ("/domains/{domain}/aliases/{alias}", "GET", False),
    "create_alias": ("/domains/{domain}/aliases", "POST", True),
    "update_alias": ("/domains/{domain}/aliases/{alias}", "PUT", True),
    "delete_alias": ("/domains/{domain}/aliases/{alias}", "DELETE", True),
    "generate_alias_password": ("/domains/{domain}/aliases/{alias}/generate-password", "POST", True),
}


class ForwardEmailError(ValueError):
    """Erro de domínio sanitizado da integração Forward Email."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ForwardEmailService:
    """Executa o contrato Forward Email sem expor o token do vault."""

    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve()
        self.path = self.root / "configs" / "mcp" / "forward-email.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        """Confirma o token por uma listagem mínima de domínios."""
        data = self.invoke(profile_name, "list_domains", {"query": {"limit": 1}}).get("data")
        if not isinstance(data, list):
            raise ForwardEmailError("invalid_response", "Forward Email não confirmou uma lista de domínios.")
        return {"profile": profile_name, "authenticated": True}

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Valida e executa uma operação publicada pelo MCP."""
        if not isinstance(arguments, Mapping):
            raise ForwardEmailError("invalid_request", "Os argumentos devem ser um objeto JSON.")
        specification = OPERATIONS.get(operation)
        if specification is None:
            raise ForwardEmailError("unsupported_operation", "Operação Forward Email não suportada.")
        route, method, writes = specification
        if writes and arguments.get("confirm") is not True:
            raise ForwardEmailError("confirmation_required", "Operações de escrita exigem confirm=true.")
        profile, defaults = self._profile(profile_name)
        domain = arguments.get("domain") or profile.get("domain")
        if "{domain}" in route and (not isinstance(domain, str) or not domain.strip()):
            raise ForwardEmailError("domain_required", "domain é obrigatório no perfil ou na chamada.")
        alias = arguments.get("alias_id") or arguments.get("alias")
        if "{alias}" in route and (not isinstance(alias, str) or not alias.strip()):
            raise ForwardEmailError("alias_required", "alias_id ou alias é obrigatório para esta operação.")
        query = arguments.get("query", {})
        if not isinstance(query, Mapping):
            raise ForwardEmailError("invalid_request", "query deve ser um objeto JSON.")
        body = arguments.get("body")
        if operation in {"create_domain", "update_domain", "create_alias", "update_alias"} and not isinstance(body, Mapping):
            raise ForwardEmailError("body_required", "body deve ser um objeto JSON para esta operação.")
        path = route.format(domain=quote(str(domain).strip(), safe=""), alias=quote(str(alias).strip(), safe=""))
        return {"data": self._request(profile, defaults, method, path, query, body)}

    def _profile(self, profile_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ForwardEmailError("config_not_found", "Configuração Forward Email MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ForwardEmailError("invalid_config", "Configuração Forward Email MCP inválida.") from error
        if document.get("schema_version") != 1:
            raise ForwardEmailError("unsupported_config", "Versão da configuração Forward Email MCP não suportada.")
        profile = document.get("profiles", {}).get(profile_name)
        defaults = document.get("defaults", {})
        if not isinstance(profile, dict) or not isinstance(defaults, dict):
            raise ForwardEmailError("profile_not_found", "Perfil Forward Email não encontrado.")
        if profile.get("enabled") is not True:
            raise ForwardEmailError("profile_disabled", "Perfil Forward Email está desabilitado.")
        return profile, defaults

    def _request(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], method: str, path: str, query: Mapping[str, Any], body: Any) -> Any:
        credential_profile, entry, field = profile.get("credential_profile"), profile.get("credential_entry_path"), profile.get("credential_field")
        if not all(isinstance(value, str) and value for value in (credential_profile, entry, field)):
            raise ForwardEmailError("invalid_config", "Referência de credencial Forward Email incompleta.")
        try:
            api_base = str(defaults.get("api_base", "")).rstrip("/")
            timeout = float(defaults.get("timeout_seconds", 30))
            retries = int(defaults.get("max_retries", 2))
        except (TypeError, ValueError) as error:
            raise ForwardEmailError("invalid_config", "Timeout ou tentativas Forward Email inválidos.") from error
        parsed = urlparse(api_base)
        if api_base != "https://api.forwardemail.net/v1" or parsed.scheme != "https" or parsed.netloc != "api.forwardemail.net" or timeout <= 0 or retries < 0:
            raise ForwardEmailError("invalid_config", "Configuração de acesso Forward Email inválida.")
        try:
            token = self.credentials.resolve(credential_profile, entry, field)
        except CredentialError as error:
            raise ForwardEmailError("credential_" + error.code, "Não foi possível obter o token Forward Email.") from error
        suffix = urlencode(query, doseq=True)
        url = api_base + path + (("?" + suffix) if suffix else "")
        authorization = base64.b64encode((token + ":").encode("utf-8")).decode("ascii")
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = Request(url, data=data, method=method, headers={"Authorization": "Basic " + authorization, "Accept": "application/json", "Content-Type": "application/json"})
        del token, authorization
        for attempt in range(retries + 1):
            try:
                with urlopen(request, timeout=timeout) as response:
                    result = json.loads(response.read())
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == retries:
                    raise ForwardEmailError("forward_email_http_error", f"Forward Email retornou HTTP {error.code}.") from error
            except (URLError, TimeoutError) as error:
                if attempt == retries:
                    raise ForwardEmailError("network_error", "Não foi possível consultar a API Forward Email.") from error
            except (json.JSONDecodeError, UnicodeError) as error:
                raise ForwardEmailError("invalid_response", "Forward Email retornou uma resposta inválida.") from error
            else:
                if not isinstance(result, (Mapping, list)):
                    raise ForwardEmailError("invalid_response", "Forward Email retornou uma resposta inválida.")
                return result
            time.sleep(0.25 * (attempt + 1))
        raise ForwardEmailError("network_error", "Não foi possível consultar a API Forward Email.")
