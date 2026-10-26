"""Diagnóstico local do consentimento BTG usando exclusivamente o cofre MCP."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
import tomllib
from typing import Any

from ..credentials import CredentialError, KeePassCredentialResolver
from .configuration import BtgConfigurationError, validate_configuration


class BtgError(ValueError):
    """Erro sanitizado do módulo BTG MCP."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class BtgService:
    """Consulta metadados OAuth sem expor ou alterar tokens e sem chamadas ao banco."""

    def __init__(self, root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = root.resolve()
        self.path = self.root / "configs" / "mcp" / "btg-empresas.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def get_auth_status(self, name: str) -> dict[str, Any]:
        try:
            data = tomllib.loads(self.path.read_text(encoding="utf-8"))
            validate_configuration(data)
        except FileNotFoundError as error:
            raise BtgError("config_not_found", "Configuração BTG MCP não encontrada.") from error
        except (OSError, ValueError, BtgConfigurationError) as error:
            raise BtgError("invalid_config", "Configuração BTG MCP inválida.") from error
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            raise BtgError("profile_not_found", "Perfil BTG MCP não encontrado.")
        if profile["enabled"] is not True:
            raise BtgError("profile_disabled", "Perfil BTG MCP está desabilitado.")
        try:
            vault, entry = profile["credential_profile"], profile["credential_entry_path"]
            client_id = self.credentials.resolve(vault, entry, "username")
            self.credentials.resolve(vault, entry, "password")
            try:
                raw = self.credentials.resolve(vault, entry, "notes")
            except CredentialError as error:
                if error.code != "secret_not_found":
                    raise
                raw = "{}"
        except CredentialError as error:
            raise BtgError("credential_" + error.code, "Não foi possível consultar as credenciais BTG no cofre MCP.") from error
        try:
            auth = json.loads(raw)
        except (ValueError, TypeError) as error:
            raise BtgError("invalid_auth_state", "Use entrada KeePass dedicada ao BTG; notes deve conter JSON OAuth.") from error
        if not isinstance(auth, dict):
            raise BtgError("invalid_auth_state", "Metadados de consentimento BTG inválidos.")
        token, pending = auth.get("tokens", {}), auth.get("pending", {})
        if not isinstance(token, dict) or not isinstance(pending, dict):
            raise BtgError("invalid_auth_state", "Metadados de consentimento BTG inválidos.")
        binding_data = {"environment": profile["environment"], "clientId": client_id, "redirect": profile.get("redirect_uri", "")}
        binding = hashlib.sha256(json.dumps(binding_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        current = time.time()
        authenticated = token.get("binding") == binding and self._unexpired(token.get("expiresAt"), current) and isinstance(token.get("access_token"), str) and bool(token["access_token"])
        return {"profile": name, "environment": profile["environment"], "credentials_configured": True, "account_configured": bool(profile.get("account_id")), "authenticated": authenticated, "pending": self._unexpired(pending.get("expiresAt"), current), "source": "vault-metadata", "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    @staticmethod
    def _unexpired(value: Any, current: float) -> bool:
        return type(value) in (int, float) and math.isfinite(value) and value > current
