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


DEPARTMENTS_ENDPOINT = "https://app.omie.com.br/api/v1/geral/departamentos/"


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
        data = self._request(profile, defaults, "ListarDepartamentos", {"pagina": 1, "registros_por_pagina": 1})
        if not isinstance(data, Mapping):
            raise OmieError("invalid_response", "Omie não confirmou a resposta do perfil.")
        return {"profile": profile_name, "authenticated": True}

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

    def _request(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], method: str, parameters: Mapping[str, Any]) -> Any:
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
                request = Request(DEPARTMENTS_ENDPOINT, data=payload, headers={"Content-Type": "application/json"}, method="POST")
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
