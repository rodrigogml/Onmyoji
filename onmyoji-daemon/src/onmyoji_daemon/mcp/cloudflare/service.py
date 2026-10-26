"""Cliente seguro da API Cloudflare para zonas e registros DNS."""
from __future__ import annotations

import json
from pathlib import Path
import time
import tomllib
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from ..credentials import CredentialError, KeePassCredentialResolver


OPERATIONS: dict[str, tuple[str, str, bool]] = {
    "list_zones": ("/zones", "GET", False),
    "get_zone": ("/zones/{zone_id}", "GET", False),
    "list_dns_records": ("/zones/{zone_id}/dns_records", "GET", False),
    "get_dns_record": ("/zones/{zone_id}/dns_records/{record_id}", "GET", False),
    "create_dns_record": ("/zones/{zone_id}/dns_records", "POST", True),
    "update_dns_record": ("/zones/{zone_id}/dns_records/{record_id}", "PATCH", True),
    "replace_dns_record": ("/zones/{zone_id}/dns_records/{record_id}", "PUT", True),
    "delete_dns_record": ("/zones/{zone_id}/dns_records/{record_id}", "DELETE", True),
    "export_dns_records": ("/zones/{zone_id}/dns_records/export", "GET", False),
    "import_dns_records": ("/zones/{zone_id}/dns_records/import", "POST", True),
    "get_dns_usage": ("/zones/{zone_id}/dns_records/usage", "GET", False),
}


class CloudflareError(ValueError):
    """Erro de domínio sanitizado da integração Cloudflare."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CloudflareService:
    """Executa o contrato Cloudflare sem expor o API Token ao agente."""

    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve()
        self.path = self.root / "configs" / "mcp" / "cloudflare.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        """Confirma o token por uma consulta mínima de zonas."""
        result = self.invoke(profile_name, "list_zones", {"query": {"per_page": 1}})
        if not isinstance(result.get("data"), list):
            raise CloudflareError("invalid_response", "Cloudflare não confirmou uma lista de zonas.")
        return {"profile": profile_name, "authenticated": True}

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Valida e executa uma operação publicada pelo MCP."""
        if not isinstance(arguments, Mapping):
            raise CloudflareError("invalid_request", "Os argumentos devem ser um objeto JSON.")
        specification = OPERATIONS.get(operation)
        if specification is None:
            raise CloudflareError("unsupported_operation", "Operação Cloudflare não suportada.")
        route, method, writes = specification
        if writes and arguments.get("confirm") is not True:
            raise CloudflareError("confirmation_required", "Operações de escrita exigem confirm=true.")
        profile, defaults = self._profile(profile_name)
        zone_id = arguments.get("zone_id") or profile.get("zone_id")
        if "{zone_id}" in route and (not isinstance(zone_id, str) or not zone_id.strip()):
            raise CloudflareError("zone_required", "zone_id é obrigatório no perfil ou na chamada.")
        record_id = arguments.get("record_id")
        if "{record_id}" in route and (not isinstance(record_id, str) or not record_id.strip()):
            raise CloudflareError("record_required", "record_id é obrigatório para esta operação.")
        query = arguments.get("query", {})
        if not isinstance(query, Mapping):
            raise CloudflareError("invalid_request", "query deve ser um objeto JSON.")
        body = arguments.get("body")
        if method in {"POST", "PATCH", "PUT"} and body is None:
            raise CloudflareError("body_required", "body é obrigatório para esta operação.")
        path = route.format(zone_id=str(zone_id).strip(), record_id=str(record_id).strip())
        return {"data": self._request(profile, defaults, method, path, query, body)}

    def _profile(self, profile_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CloudflareError("config_not_found", "Configuração Cloudflare MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise CloudflareError("invalid_config", "Configuração Cloudflare MCP inválida.") from error
        if document.get("schema_version") != 1:
            raise CloudflareError("unsupported_config", "Versão da configuração Cloudflare MCP não suportada.")
        profile = document.get("profiles", {}).get(profile_name)
        defaults = document.get("defaults", {})
        if not isinstance(profile, dict) or not isinstance(defaults, dict):
            raise CloudflareError("profile_not_found", "Perfil Cloudflare não encontrado.")
        if profile.get("enabled") is not True:
            raise CloudflareError("profile_disabled", "Perfil Cloudflare está desabilitado.")
        return profile, defaults

    def _request(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], method: str, path: str, query: Mapping[str, Any], body: Any) -> Any:
        credential_profile, entry, field = profile.get("credential_profile"), profile.get("credential_entry_path"), profile.get("credential_field")
        if not all(isinstance(value, str) and value for value in (credential_profile, entry, field)):
            raise CloudflareError("invalid_config", "Referência de credencial Cloudflare incompleta.")
        try:
            token = self.credentials.resolve(credential_profile, entry, field)
            api_base = str(defaults.get("api_base", "")).rstrip("/")
            timeout = float(defaults.get("timeout_seconds", 30))
            retries = int(defaults.get("max_retries", 2))
        except CredentialError as error:
            raise CloudflareError("credential_" + error.code, "Não foi possível obter o API Token Cloudflare.") from error
        except (TypeError, ValueError) as error:
            raise CloudflareError("invalid_config", "Timeout ou tentativas Cloudflare inválidos.") from error
        parsed = urlparse(api_base)
        if parsed.scheme != "https" or not parsed.netloc or timeout <= 0 or retries < 0:
            raise CloudflareError("invalid_config", "Configuração de acesso Cloudflare inválida.")
        suffix = urlencode(query, doseq=True)
        url = api_base + path + (("?" + suffix) if suffix else "")
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = Request(url, data=data, method=method, headers={"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json"})
        del token
        for attempt in range(retries + 1):
            try:
                with urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read())
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == retries:
                    raise CloudflareError("cloudflare_http_error", f"Cloudflare retornou HTTP {error.code}.") from error
            except (URLError, TimeoutError) as error:
                if attempt == retries:
                    raise CloudflareError("network_error", "Não foi possível consultar a API Cloudflare.") from error
            except (json.JSONDecodeError, UnicodeError) as error:
                raise CloudflareError("invalid_response", "Cloudflare retornou uma resposta inválida.") from error
            else:
                if not isinstance(payload, Mapping) or payload.get("success") is not True or "result" not in payload:
                    raise CloudflareError("cloudflare_api_error", "Cloudflare recusou a operação.")
                return payload["result"]
            time.sleep(0.25 * (attempt + 1))
        raise CloudflareError("network_error", "Não foi possível consultar a API Cloudflare.")
