"""Cliente BTG fechado; credenciais e tokens permanecem no KeePass."""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import secrets
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..credentials import CredentialError, KeePassCredentialResolver
from .configuration import DEFAULT_SCOPES, validate_configuration
from .contracts import BtgError, CONTRACTS, bank_company_id, digest, json_exact, money, require, text


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BtgError("redirect_denied", "Redirecionamento HTTP recusado.", code)


def instance_root(config: Path) -> Path:
    return config.resolve().parents[2]


def instance_workspace(config: Path) -> Path:
    root = instance_root(config)
    try:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        value = system["codex"]["project_directory"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BtgError("invalid_workspace", "Configure a pasta do projeto no menu Codex-CLI.") from error
    require(isinstance(value, str) and bool(value.strip()) and Path(value).is_absolute(), "invalid_workspace", "Workspace BTG inválido.")
    workspace = Path(value).resolve()
    require(workspace.is_dir() and not workspace.is_relative_to(root), "invalid_workspace", "O workspace deve existir e ficar fora do CODEX_HOME.")
    return workspace


def load_profile(config: Path, name: str) -> dict[str, Any]:
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
        validate_configuration(data)
        raw = dict(data["defaults"], **data["profiles"][name])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BtgError("invalid_config", "Configuração ou perfil BTG MCP inválido.") from error
    require(raw["enabled"] is True, "profile_disabled", "Perfil BTG MCP está desabilitado.")
    raw["vault_profile"], raw["vault_entry_path"] = raw["credential_profile"], raw["credential_entry_path"]
    raw["client_id"] = ""
    for key in ("account_id", "account_number", "branch_code", "redirect_uri"):
        raw.setdefault(key, "")
    raw.setdefault("scopes", DEFAULT_SCOPES)
    raw.setdefault("oauth_mode", "remote")
    raw["workspace"] = str(instance_workspace(config))
    for key, default in (("timeout_seconds", 30), ("pending_interval", 300), ("scheduled_interval", 3600), ("confirmed_interval", 86400), ("confirmation_days", 7), ("max_pages", 20), ("max_pending_days", 30)):
        raw.setdefault(key, default)
    raw.update(_config=str(config.resolve()), _profile=name)
    return raw


def context(profile: dict[str, Any]) -> str:
    return digest({**{k: profile[k] for k in ("environment", "company_id", "account_id", "account_number", "branch_code")}, "bank_company_id": bank_company_id(profile)})


def normalized_account(value: Any) -> dict[str, Any] | None:
    """Normalize a identidade redundante da conta sem aceitar divergências."""
    if not isinstance(value, dict):
        return None
    result = dict(value)
    for target, alias in (("number", "accountNumber"), ("branchCode", "branch"), ("bankCode", "bank")):
        if result.get(target) in (None, "") and result.get(alias) not in (None, ""):
            result[target] = result[alias]
    account_id = result.get("accountId")
    match = re.fullmatch(r"(\d{14})-(\d{3})-(\d+)-(\d+)", account_id) if isinstance(account_id, str) else None
    if not match:
        return result
    derived = dict(zip(("taxId", "bankCode", "branchCode", "number"), match.groups()))
    for field, expected in derived.items():
        supplied = result.get(field)
        if supplied not in (None, "") and str(supplied) != expected:
            return result
    return dict(result, **derived)


def account_company_matches(profile: dict[str, Any], row: dict[str, Any]) -> bool:
    """Valide o CNPJ real em produção e aceite a identidade fictícia do mock."""
    if profile["environment"] == "sandbox":
        return True
    return row.get("taxId") in (None, "") or str(row["taxId"]) == bank_company_id(profile)


@contextlib.contextmanager
def credential_lock(profile: dict[str, Any], *, scope: str | None = None):
    """Serialize rotação de token por entrada do vault entre processos."""
    root = Path(profile["workspace"]) / ".onmyoji" / "btg-empresas" / "locks"
    require(root.resolve().is_relative_to(Path(profile["workspace"]).resolve()), "invalid_workspace", "Locks devem permanecer dentro do workspace.")
    root.mkdir(parents=True, exist_ok=True)
    name = digest(scope) if scope is not None else digest({k: profile[k] for k in ("vault_profile", "vault_entry_path")})
    with (root / (name + ".lock")).open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0"); stream.flush()
        deadline = time.monotonic() + 60
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                require(time.monotonic() < deadline, "credential_busy", "Outra operação está renovando o token.")
                time.sleep(0.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)


class Client:
    def __init__(self, profile: dict[str, Any], credentials: KeePassCredentialResolver | None = None):
        self.profile = profile
        self.base = "https://api." + ("sandbox." if profile["environment"] == "sandbox" else "") + "empresas.btgpactual.com"
        self.id_base = "https://id." + ("sandbox." if profile["environment"] == "sandbox" else "") + "btgpactual.com"
        self.opener = urllib.request.build_opener(NoRedirect())
        self.deadline: float | None = None
        self.credentials = credentials or KeePassCredentialResolver(instance_root(Path(profile['_config'])))
        self._client_id: str | None = None

    def vault(self, operation: str, field: str | None = None, value: str | None = None) -> Any:
        try:
            if operation == "read":
                return self.credentials.resolve(self.profile["credential_profile"], self.profile["credential_entry_path"], field or "password")
            require(operation == "edit" and isinstance(value, str), "unsupported_operation", "Operação de cofre BTG inválida.")
            self.credentials.save_oauth_state(self.profile["credential_profile"], self.profile["credential_entry_path"], json.loads(value))
            return None
        except CredentialError as error:
            if operation == "read" and field == "notes" and error.code == "secret_not_found":
                return None
            raise BtgError("credential_" + error.code, "Não foi possível acessar o cofre MCP do BTG.") from error

    def auth_data(self) -> dict[str, Any]:
        raw = self.vault("read", "notes")
        if raw in (None, ""):
            return {}
        try:
            result = json.loads(raw)
        except (ValueError, TypeError) as error:
            raise BtgError("invalid_auth_state", "Use uma entrada KeePass dedicada; notes deve conter JSON OAuth.") from error
        require(isinstance(result, dict), "invalid_auth_state", "Estado OAuth inválido.")
        return result

    def save_auth(self, value: dict[str, Any]) -> None:
        self.vault("edit", value=json.dumps(value))

    def client_id(self) -> str:
        """Leia o Client ID de username no KeePass; aceite perfis legados explícitos.

        A configuração pode ser salva sem acessar o vault. A credencial só é
        exigida na conexão, com mensagem específica se username estiver vazio.
        """
        if self._client_id is None:
            value = self.profile.get("client_id") or self.vault("read", "username")
            require(isinstance(value, str) and bool(value.strip()), "client_id_missing", "Preencha o campo Nome de usuário (username) da entrada KeePass BTG com o Client ID do aplicativo. O Client Secret deve ficar em Senha (password).")
            self._client_id = text(value, "Client ID no KeePass", 2000)
        return self._client_id

    def binding(self) -> str:
        return digest({"environment": self.profile["environment"], "clientId": self.client_id(), "redirect": self.profile["redirect_uri"]})

    def auth_start(self, *, state: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        with credential_lock(self.profile):
            data = self.auth_data()
            require(data.get("pending", {}).get("expiresAt", 0) <= time.time(), "authorization_pending", "Existe consentimento pendente; conclua, cancele ou aguarde expiração.")
            state = state or secrets.token_urlsafe(32)
            data["pending"] = {"state": state, "binding": self.binding(), "context": context(self.profile), "sessionId": session_id, "expiresAt": time.time() + 600}
            self.save_auth(data)
        query = {"response_type": "code", "client_id": self.client_id(), "redirect_uri": self.profile["redirect_uri"], "scope": self.profile["scopes"], "state": state, "prompt": "login"}
        return {"authorizationUrl": self.id_base + "/oauth2/authorize?" + urllib.parse.urlencode(query), "expiresIn": 600}

    def token_request(self, fields: dict[str, str], old: dict[str, Any] | None = None) -> dict[str, Any]:
        secret = self.vault("read", "password")
        require(isinstance(secret, str) and bool(secret), "credential_missing", "Client secret ausente no campo password do KeePass.")
        basic = base64.b64encode((self.client_id() + ":" + secret).encode()).decode()
        _, raw = self.http(self.id_base + "/oauth2/token", "POST", urllib.parse.urlencode(fields).encode(), {"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
        require(isinstance(raw, dict) and isinstance(raw.get("access_token"), str) and isinstance(raw.get("expires_in"), (int, Decimal)) and int(raw["expires_in"]) > 0, "invalid_token_response", "Resposta OAuth incompleta.")
        refresh = raw.get("refresh_token") or (old or {}).get("refresh_token")
        require(isinstance(refresh, str) and bool(refresh), "invalid_token_response", "Refresh token ausente.")
        operator = (old or {}).get("operatorId")
        if raw.get("id_token"):
            try:
                part = raw["id_token"].split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
                operator = text(claims["sub"], "Identidade OAuth", 500)
            except (ValueError, KeyError, IndexError, TypeError) as error:
                raise BtgError("operator_unavailable", "Identidade OAuth não disponível.") from error
        require(isinstance(operator, str) and bool(operator), "operator_unavailable", "OAuth não informou identidade do operador.")
        require(not old or old.get("operatorId") == operator, "operator_changed", "Operador mudou durante renovação; obtenha novo consentimento.")
        return {"access_token": raw["access_token"], "refresh_token": refresh, "expiresAt": time.time() + int(raw["expires_in"]), "operatorId": operator, "binding": self.binding()}

    def auth_complete(self, code: str, state: str, *, session_id: str | None = None, verify_account: bool = False) -> dict[str, Any]:
        with credential_lock(self.profile):
            data = self.auth_data()
            pending = data.get("pending", {})
            require(isinstance(state, str) and secrets.compare_digest(str(pending.get("state", "")), state) and bool(state), "invalid_oauth_state", "State OAuth divergente.")
            require(pending.get("binding") == self.binding() and pending.get("expiresAt", 0) > time.time(), "expired_oauth_state", "Consentimento expirado ou ambiente divergente.")
            require(pending.get("sessionId") == session_id and pending.get("context", context(self.profile)) == context(self.profile), "invalid_oauth_session", "Sessão OAuth não corresponde ao contexto.")
            # O callback já foi consumido pelo Gateway; não preserve uma
            # pendência que possa bloquear uma nova tentativa se a troca do
            # código, a descoberta da conta ou o KeePass falhar depois.
            data.pop("pending", None)
            data["lastAuthorization"] = {"sessionId": session_id, "status": "processing", "startedAt": time.time()}
            self.save_auth(data)
            candidate = self.token_request({"grant_type": "authorization_code", "code": text(code, "Código OAuth", 4000), "redirect_uri": self.profile["redirect_uri"]})
            data["provisionalTokens"] = candidate
            self.save_auth(data)
            if not self.profile["account_id"]:
                row = self.discover_account(token_override=candidate)
                self.persist_account(row)
            elif verify_account:
                self.verify_account(token_override=candidate)
            data["tokens"] = candidate
            data.pop("provisionalTokens", None)
            data["lastAuthorization"] = {"sessionId": session_id, "status": "completed", "completedAt": time.time()}
            self.save_auth(data)
        return {"authenticated": True, "environment": self.profile["environment"]}

    def auth_cancel(self, session_id: str) -> dict[str, Any]:
        with credential_lock(self.profile):
            data = self.auth_data()
            pending = data.get("pending", {})
            require(pending.get("sessionId") == session_id, "invalid_oauth_session", "Consentimento pertence a outra sessão.")
            data.pop("pending", None)
            data["lastAuthorization"] = {"sessionId": session_id, "status": "cancelled", "completedAt": time.time()}
            self.save_auth(data)
        return {"cancelled": True}

    def auth_status(self) -> dict[str, Any]:
        with credential_lock(self.profile):
            data = self.auth_data()
        tokens = data.get("tokens", {})
        last = data.get("lastAuthorization")
        last = {key: last.get(key) for key in ("sessionId", "status", "completedAt")} if isinstance(last, dict) else None
        return {"authenticated": tokens.get("binding") == self.binding() and tokens.get("expiresAt", 0) > time.time(), "pending": data.get("pending", {}).get("expiresAt", 0) > time.time(), "lastAuthorization": last, "source": "vault-metadata"}

    def remote_oauth(self, action: str, *, session_id: str | None = None, confirm: bool = False) -> dict[str, Any]:
        root = instance_root(Path(self.profile["_config"]))
        source = root / "onmyoji-daemon" / "src"
        if source.is_dir() and str(source) not in sys.path:
            sys.path.insert(0, str(source))
        try:
            from onmyoji_daemon.supervisor import endpoint
            from onmyoji_daemon.rpc import RpcError, call
            params = {"skill": "mcp-btg", "profile": self.profile["_profile"], "confirm": confirm}
            if session_id:
                params["session_id"] = session_id
            host, port, token = endpoint(root)
            return call(host, port, token, "mini-apps.oauth." + action, params, timeout=200)
        except RpcError as error:
            raise BtgError("remote_oauth_rejected", "OAuth remoto recusado pelo Gateway: " + str(error)) from error
        except (OSError, ValueError, ImportError, RuntimeError) as error:
            raise BtgError("remote_oauth_unavailable", "OAuth remoto indisponível; confira Gateway, Tunnel, callback e perfil.") from error

    def token(self) -> dict[str, Any]:
        with credential_lock(self.profile):
            data = self.auth_data()
            token = data.get("tokens", {})
            require(token.get("binding") == self.binding(), "consent_required", "Obtenha consentimento OAuth para este aplicativo/ambiente.")
            if token.get("expiresAt", 0) <= time.time() + 120:
                token = self.token_request({"grant_type": "refresh_token", "refresh_token": token.get("refresh_token", "")}, token)
                data["tokens"] = token
                self.save_auth(data)
            return token

    def http(self, url: str, method: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, Any]:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            timeout = self.profile["timeout_seconds"]
            if self.deadline is not None:
                remaining = self.deadline - time.monotonic()
                require(remaining > 0, "check_budget_exhausted", "Prazo do ciclo de consulta esgotado.")
                timeout = min(timeout, remaining)
            with self.opener.open(request, timeout=timeout) as response:
                status = response.status
                raw = response.read(8 * 1024 * 1024 + 1)
                require(len(raw) <= 8 * 1024 * 1024, "response_too_large", "Resposta BTG excedeu o limite.",)
                if headers.get("Accept") == "application/pdf":
                    return status, raw
                if not raw:
                    return status, None
                try:
                    return status, json.loads(raw, parse_float=Decimal)
                except ValueError as error:
                    raise BtgError("invalid_bank_response", "BTG não retornou JSON válido.", status) from error
        except urllib.error.HTTPError as error:
            provider, provider_code = "", ""
            try:
                payload = json.loads(error.read(65537).decode("utf-8", "replace"))
                name = payload.get("name") if isinstance(payload, dict) else None
                if isinstance(name, str):
                    provider = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")[:80]
                message = payload.get("message") if isinstance(payload, dict) else None
                match = re.search(r"\[([a-z0-9_-]{1,32})\]", message, re.IGNORECASE) if isinstance(message, str) else None
                if match:
                    provider_code = match.group(1).casefold()
            except (OSError, ValueError, TypeError):
                pass
            details = "_".join(value for value in (provider, provider_code) if value)
            code = "bank_http_error_" + str(error.code) + (("_" + details) if details else "")
            raise BtgError(code, f"BTG retornou HTTP {error.code}.", error.code) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise BtgError("bank_unavailable", "Falha de comunicação com BTG; resultado de escrita pode ser desconhecido.") from error

    def call(self, operation: str, *, payment_id: str | None = None, params: dict[str, Any] | None = None, body: Any = None, key: str | None = None, expected_operator: str | None = None, token_override: dict[str, Any] | None = None) -> Any:
        require(operation in CONTRACTS, "unsupported_operation", "Operação BTG não registrada.")
        require(operation == "accounts.get" or bool(self.profile["account_id"]), "account_setup_required", "Conclua a conexão da conta pelo setup BTG antes de operar.")
        contract, profile = CONTRACTS[operation], self.profile
        params = params or {}
        allowed = {p["name"] for p in contract["parameters"] if p["in"] == "query"}
        require(set(params) <= allowed and all(p["name"] in params for p in contract["parameters"] if p["in"] == "query" and p.get("required")), "invalid_query", "Parâmetros de consulta inválidos/incompletos.")
        for name, value in params.items():
            schema = next(p["schema"] for p in contract["parameters"] if p["name"] == name)
            require(isinstance(value, (str, int, bool, Decimal)) and not isinstance(value, float), "invalid_query", "Parâmetro de consulta inválido.")
            if "enum" in schema:
                require(value in schema["enum"], "invalid_query", "Valor de consulta fora do contrato.")
            if name in {"pageNumber", "pageSize"}:
                require(not isinstance(value, bool) and str(value).isdigit() and 1 <= int(value) <= 1000, "invalid_query", "Paginação remota inválida.")
        path = contract["path"].replace("{companyId}", urllib.parse.quote(bank_company_id(profile), safe="")).replace("{accountId}", urllib.parse.quote(profile["account_id"], safe=""))
        if "{paymentId}" in path:
            path = path.replace("{paymentId}", urllib.parse.quote(text(payment_id, "ID bancário", 200), safe=""))
        token = token_override or self.token()
        require(expected_operator is None or expected_operator == token["operatorId"], "operator_changed", "Operador divergente da tentativa persistida.")
        headers = {"Authorization": "Bearer " + token["access_token"], "Accept": "application/pdf" if operation == "payments.receipt" else "application/json"}
        if profile["environment"] == "sandbox":
            headers["x-response"] = "success"
        if key:
            headers["x-idempotency-key"] = key
        raw = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            raw = json_exact(body).encode("utf-8")
        query = urllib.parse.urlencode({k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in params.items()})
        status, result = self.http(self.base + path + ("?" + query if query else ""), contract["method"].upper(), raw, headers)
        require(str(status) in contract["responses"], "invalid_bank_response", "Status HTTP não previsto no contrato.",)
        return result

    def verify_account(self, token_override: dict[str, Any] | None = None) -> dict[str, Any]:
        require(bool(self.profile["account_id"]), "account_setup_required", "Conclua a conexão da conta pelo setup BTG.")
        result = self.call("accounts.get", token_override=token_override)
        require(isinstance(result, dict) and isinstance(result.get("data"), list), "invalid_bank_response", "Lista de contas incompleta.")
        rows = [row for value in result["data"] if (row := normalized_account(value)) is not None and row["accountId"] == self.profile["account_id"]]
        require(len(rows) == 1, "context_mismatch", "Conta não encontrada ou ambígua no consentimento.")
        row = rows[0]
        require(account_company_matches(self.profile, row), "context_mismatch", "Empresa diverge do consentimento BTG.")
        require(row.get("bankCode") in (None, "") or str(row["bankCode"]) == "208", "context_mismatch", "Banco diverge do perfil BTG.")
        require(str(row.get("number")) == self.profile["account_number"] and str(row.get("branchCode")) == self.profile["branch_code"], "context_mismatch", "Conta diverge do perfil BTG.")
        return row

    def discover_account(self, token_override: dict[str, Any] | None = None) -> dict[str, Any]:
        """Obtenha uma conta BTG única da empresa, respeitando filtros já informados.

        Não escolhe a primeira conta nem substitui uma conta já configurada.
        Ausência, ambiguidade ou resposta incompleta impedem a persistência.
        """
        result = self.call("accounts.get", token_override=token_override)
        require(isinstance(result, dict) and isinstance(result.get("data"), list), "invalid_bank_response", "Lista de contas incompleta.")
        rows = [row for value in result["data"] if (row := normalized_account(value)) is not None
                and account_company_matches(self.profile, row)
                and (row.get("bankCode") in (None, "") or str(row["bankCode"]) == "208")
                and all(not self.profile[key] or str(row.get(field)) == self.profile[key]
                        for key, field in (("account_id", "accountId"), ("account_number", "number"), ("branch_code", "branchCode")))]
        require(bool(rows), "account_not_found", "Nenhuma conta BTG corresponde à empresa e aos filtros do perfil.")
        require(len(rows) == 1, "account_ambiguous", "Mais de uma conta disponível; informe número/agência no setup para selecionar e conecte novamente.")
        row = rows[0]
        text(row.get("accountId"), "ID da conta retornado pelo BTG", 2000)
        require(isinstance(row.get("number"), str) and row["number"].isdigit() and str(row.get("branchCode", "")).isdigit(), "invalid_bank_response", "Número/agência ausentes na resposta BTG.")
        return row

    def persist_account(self, row: dict[str, Any]) -> None:
        """Salve os identificadores descobertos sem trocar empresa ou conta existente.

        Grava o TOML por substituição atômica e recusa perfil alterado durante o
        consentimento. Não grava tokens ou outros dados da resposta bancária.
        """
        from .account_setup import persist_account
        persist_account(self.profile, row)
