"""OAuth, consultas e pagamentos BTG com credenciais mantidas no cofre MCP."""
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
from .exception import BtgError


class BtgService:
    """Orquestra consentimento, consultas e pagamentos com memória transacional."""

    def __init__(self, root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = root.resolve()
        self.path = self.root / "configs" / "mcp" / "btg-empresas.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def client(self, name: str):
        from .client import Client, load_profile
        return Client(load_profile(self.path, name), self.credentials)

    def get_oauth_callback(self, name: str) -> dict[str, Any]:
        from ...rpc import RpcError, call
        from ...supervisor import endpoint
        client = self.client(name)
        try:
            host, port, token = endpoint(self.root)
            result = call(host, port, token, "mini-apps.oauth.callback-url", {"skill": "mcp-btg"}, timeout=10)
        except (OSError, ValueError, KeyError, RuntimeError, RpcError) as error:
            raise BtgError("remote_oauth_unavailable", "Inicie o Gateway e o Tunnel para calcular o callback BTG MCP.") from error
        return {"callbackUrl": result["callbackUrl"], "configuredRedirectUri": client.profile["redirect_uri"], "redirectMatches": result["callbackUrl"] == client.profile["redirect_uri"]}

    def invoke(self, name: str, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Executa somente operações registradas e sanitiza falhas de persistência."""
        from .contracts import date, now, public, require, text
        from .payment_tools import PAYMENT_DESCRIPTIONS
        reads = {"get_account": "accounts.get", "get_balance": "balances.get", "list_statements": "statements.list", "list_dda": "dda.list", "get_barcode": "barcodes.get", "list_bank_payments": "payments.remote-list"}
        require(isinstance(arguments, dict), "invalid_request", "Argumentos BTG devem ser objeto.")
        require(operation in reads or operation in PAYMENT_DESCRIPTIONS or operation in {"get_oauth_callback", "start_authorization", "get_authorization_status", "cancel_authorization"}, "unsupported_operation", "Operação BTG MCP não registrada.")
        if operation in {"start_authorization", "cancel_authorization", "prepare_payment", "submit_payment", "cancel_payment"}:
            require(arguments.get("confirm") is True, "confirmation_required", "Obtenha autorização explícita para a operação e informe confirm=true.")
        if operation == "get_oauth_callback":
            return self.get_oauth_callback(name)
        client = self.client(name)
        if operation in PAYMENT_DESCRIPTIONS:
            import sqlite3
            from .payments import Engine
            try:
                return public(Engine(client.profile, client).invoke(operation, arguments))
            except BtgError:
                raise
            except (sqlite3.Error, OSError) as error:
                raise BtgError("persistence_unavailable", "Memória indisponível; não repita escritas bancárias sem recuperação.") from error
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                raise BtgError("invalid_data", "Dados ou resposta inválidos; consulte a memória antes de repetir um envio.") from error
        if operation == "start_authorization":
            require(client.profile["oauth_mode"] == "remote", "remote_mode_required", "Use OAuth remoto para o BTG MCP.")
            require(bool(client.profile["redirect_uri"]), "redirect_required", "Configure a URI de retorno BTG no menu MCP antes de conectar.")
            return client.remote_oauth("start", confirm=True)
        if operation in {"get_authorization_status", "cancel_authorization"}:
            session = arguments.get("session_id")
            if not session:
                active = client.remote_oauth("active")
                if isinstance(active, dict):
                    session = active.get("id")
                if not session and operation == "get_authorization_status":
                    last = client.auth_data().get("lastAuthorization", {})
                    if isinstance(last, dict):
                        session = last.get("sessionId")
            if not session:
                require(operation != "cancel_authorization", "authorization_not_found", "Não há autorização ativa para cancelar.")
                return self.get_auth_status(name)
            session = text(session, "ID da sessão OAuth", 100)
            return client.remote_oauth("cancel" if operation == "cancel_authorization" else "status", session_id=session, confirm=operation == "cancel_authorization")
        require(bool(client.profile["account_id"]), "account_setup_required", "Conecte a conta BTG no menu MCP antes de consultar.")
        params = arguments.get("params", {})
        require(isinstance(params, dict), "invalid_query", "params deve ser um objeto.")
        if operation == "get_account":
            require(not params, "invalid_query", "Consulta da conta configurada não aceita params.")
            result = client.verify_account()
        else:
            if operation == "list_statements":
                date(params.get("startDate")); date(params.get("endDate"))
                require(params["startDate"] <= params["endDate"], "invalid_query", "Intervalo de extrato invertido.")
            result = client.call(reads[operation], params=params)
        return {"source": "bank", "observedAt": now(), "response": public(result), "pagination": "explicit; never infer completion without _links"}

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
