"""Cliente allowlisted para Gmail, People, Drive e Calendar."""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
import time
import tomllib
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

from ..credentials import CredentialError, KeePassCredentialResolver


class GoogleError(ValueError):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message); self.code = code; self.status = status


def _operations() -> dict[str, tuple[str, str]]:
    operations: dict[str, tuple[str, str]] = {}
    def add(prefix: str, method: str, names: dict[str, str]) -> None:
        operations.update({prefix + name: (method, path) for name, path in names.items()})
    add("gmail.", "GET", {
        "messages.list": "/gmail/v1/users/{user_id}/messages", "messages.get": "/gmail/v1/users/{user_id}/messages/{id}", "threads.list": "/gmail/v1/users/{user_id}/threads", "threads.get": "/gmail/v1/users/{user_id}/threads/{id}", "labels.list": "/gmail/v1/users/{user_id}/labels", "labels.get": "/gmail/v1/users/{user_id}/labels/{id}", "drafts.list": "/gmail/v1/users/{user_id}/drafts", "drafts.get": "/gmail/v1/users/{user_id}/drafts/{id}", "attachments.get": "/gmail/v1/users/{user_id}/messages/{message_id}/attachments/{id}", "history.list": "/gmail/v1/users/{user_id}/history", "profile.get": "/gmail/v1/users/{user_id}/profile", "settings.filters.list": "/gmail/v1/users/{user_id}/settings/filters", "settings.forwarding.list": "/gmail/v1/users/{user_id}/settings/forwardingAddresses", "settings.sendAs.list": "/gmail/v1/users/{user_id}/settings/sendAs", "settings.delegates.list": "/gmail/v1/users/{user_id}/settings/delegates",
    })
    for name, method, path in [
        ("messages.modify","POST","/gmail/v1/users/{user_id}/messages/{id}/modify"),("messages.delete","DELETE","/gmail/v1/users/{user_id}/messages/{id}"),("messages.send","POST","/gmail/v1/users/{user_id}/messages/send"),("messages.insert","POST","/gmail/v1/users/{user_id}/messages/insert"),("messages.import","POST","/gmail/v1/users/{user_id}/messages/import"),("messages.batchModify","POST","/gmail/v1/users/{user_id}/messages/batchModify"),("threads.modify","POST","/gmail/v1/users/{user_id}/threads/{id}/modify"),("threads.delete","DELETE","/gmail/v1/users/{user_id}/threads/{id}"),("labels.create","POST","/gmail/v1/users/{user_id}/labels"),("labels.update","PUT","/gmail/v1/users/{user_id}/labels/{id}"),("labels.patch","PATCH","/gmail/v1/users/{user_id}/labels/{id}"),("labels.delete","DELETE","/gmail/v1/users/{user_id}/labels/{id}"),("drafts.create","POST","/gmail/v1/users/{user_id}/drafts"),("drafts.update","PUT","/gmail/v1/users/{user_id}/drafts/{id}"),("drafts.send","POST","/gmail/v1/users/{user_id}/drafts/send"),("drafts.delete","DELETE","/gmail/v1/users/{user_id}/drafts/{id}"),("settings.filters.create","POST","/gmail/v1/users/{user_id}/settings/filters"),("settings.filters.delete","DELETE","/gmail/v1/users/{user_id}/settings/filters/{id}")]: operations["gmail."+name]=(method,path)
    for name, method, path in [
        ("connections.list","GET","/people/v1/people/me/connections"),("get","GET","/people/v1/{resource_name}"),("searchContacts","GET","/people/v1/people:searchContacts"),("createContact","POST","/people/v1/people:createContact"),("updateContact","PATCH","/people/v1/{resource_name}:updateContact"),("deleteContact","DELETE","/people/v1/{resource_name}:deleteContact"),("contactGroups.list","GET","/people/v1/contactGroups"),("contactGroups.get","GET","/people/v1/{resource_name}"),("otherContacts.list","GET","/people/v1/otherContacts")]: operations["people."+name]=(method,path)
    for name, method, path in [
        ("files.list","GET","/drive/v3/files"),("files.get","GET","/drive/v3/files/{fileId}"),("files.create","POST","/drive/v3/files"),("files.update","PATCH","/drive/v3/files/{fileId}"),("files.copy","POST","/drive/v3/files/{fileId}/copy"),("files.delete","DELETE","/drive/v3/files/{fileId}"),("files.emptyTrash","DELETE","/drive/v3/files/trash"),("files.export","GET","/drive/v3/files/{fileId}/export"),("files.download","GET","/drive/v3/files/{fileId}"),("permissions.list","GET","/drive/v3/files/{fileId}/permissions"),("permissions.get","GET","/drive/v3/files/{fileId}/permissions/{permissionId}"),("permissions.create","POST","/drive/v3/files/{fileId}/permissions"),("permissions.update","PATCH","/drive/v3/files/{fileId}/permissions/{permissionId}"),("permissions.delete","DELETE","/drive/v3/files/{fileId}/permissions/{permissionId}"),("comments.list","GET","/drive/v3/files/{fileId}/comments"),("revisions.list","GET","/drive/v3/files/{fileId}/revisions"),("changes.list","GET","/drive/v3/changes"),("about.get","GET","/drive/v3/about"),("drives.list","GET","/drive/v3/drives"),("uploads.create","POST","/upload/drive/v3/files")]: operations["drive."+name]=(method,path)
    for name, method, path in [
        ("calendars.list","GET","/calendar/v3/users/me/calendarList"),("calendars.get","GET","/calendar/v3/calendars/{calendarId}"),("calendars.clear","POST","/calendar/v3/calendars/{calendarId}/clear"),("calendars.delete","DELETE","/calendar/v3/calendars/{calendarId}"),("calendars.insert","POST","/calendar/v3/calendars"),("calendars.update","PUT","/calendar/v3/calendars/{calendarId}"),("calendars.patch","PATCH","/calendar/v3/calendars/{calendarId}"),("events.list","GET","/calendar/v3/calendars/{calendarId}/events"),("events.get","GET","/calendar/v3/calendars/{calendarId}/events/{eventId}"),("events.insert","POST","/calendar/v3/calendars/{calendarId}/events"),("events.update","PUT","/calendar/v3/calendars/{calendarId}/events/{eventId}"),("events.patch","PATCH","/calendar/v3/calendars/{calendarId}/events/{eventId}"),("events.delete","DELETE","/calendar/v3/calendars/{calendarId}/events/{eventId}"),("events.move","POST","/calendar/v3/calendars/{calendarId}/events/{eventId}/move"),("events.quickAdd","POST","/calendar/v3/calendars/{calendarId}/events/quickAdd"),("events.instances","GET","/calendar/v3/calendars/{calendarId}/events/{eventId}/instances"),("events.watch","POST","/calendar/v3/calendars/{calendarId}/events/watch"),("acl.list","GET","/calendar/v3/calendars/{calendarId}/acl"),("acl.insert","POST","/calendar/v3/calendars/{calendarId}/acl"),("acl.update","PUT","/calendar/v3/calendars/{calendarId}/acl/{ruleId}"),("acl.delete","DELETE","/calendar/v3/calendars/{calendarId}/acl/{ruleId}"),("colors.get","GET","/calendar/v3/colors"),("freebusy.query","POST","/calendar/v3/freeBusy"),("settings.list","GET","/calendar/v3/users/me/settings"),("channels.stop","POST","/calendar/v3/channels/stop")]: operations["calendar."+name]=(method,path)
    return operations


OPERATIONS = _operations()
WRITE_OPERATIONS = {name for name, (method, _) in OPERATIONS.items() if method != "GET"}
SENSITIVE_KEYS = {"access_token", "refresh_token", "client_secret", "authorization", "password", "token"}


def sanitize(value: Any) -> Any:
    if isinstance(value, Mapping): return {key: "[REDACTED]" if str(key).casefold() in SENSITIVE_KEYS else sanitize(item) for key, item in value.items()}
    if isinstance(value, list): return [sanitize(item) for item in value]
    if isinstance(value, str) and any(marker in value.casefold() for marker in ("access_token=", "refresh_token=", "token=")): return "[REDACTED_URL]"
    return value


class GoogleService:
    def __init__(self, root: Path, credentials: KeePassCredentialResolver | None = None, opener=None):
        self.root = root.resolve(); self.credentials = credentials or KeePassCredentialResolver(self.root); self.opener = opener or urllib.request.urlopen

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if operation not in OPERATIONS: raise GoogleError("unsupported_operation", "Operação Google não permitida.")
        if operation in WRITE_OPERATIONS and arguments.get("confirm") is not True: raise GoogleError("confirmation_required", "Operações Google de escrita exigem confirm=true.")
        profile, defaults = self._profile(profile_name)
        method, template = OPERATIONS[operation]
        path = self._substitute(template, {"user_id": profile["user_id"], **self._mapping(arguments.get("params"), "params")})
        query = self._mapping(arguments.get("query"), "query")
        body = arguments.get("body")
        headers: dict[str, str] = {}
        if operation == "drive.uploads.create":
            if not isinstance(body, Mapping) or not isinstance(body.get("file_path"), str): raise GoogleError("file_not_found", "drive.uploads.create exige body.file_path.")
            source = self._confined_file(body["file_path"], profile["readable_roots"]); data = source.read_bytes(); headers["Content-Type"] = mimetypes.guess_type(source.name)[0] or "application/octet-stream"; query = {**query, "uploadType": "media"}
        else:
            data = json.dumps(body).encode("utf-8") if body is not None else None
            if body is not None: headers["Content-Type"] = "application/json"
        client_id, client_secret, refresh = self._secrets(profile)
        access = self._refresh(defaults, client_id, client_secret, refresh)
        response = self._paginated(defaults, access, method, path, query, data, headers, arguments.get("paginate") is True)
        output_path = arguments.get("output_path")
        if isinstance(response, bytes):
            if output_path is not None:
                target = self._confined_output(output_path, profile["writable_roots"]); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(response); result: Any = {"path": str(target), "bytes": len(response)}
            else:
                if len(response) > defaults["max_binary_bytes"]: raise GoogleError("binary_too_large", "A resposta binária excede o limite; informe output_path em uma raiz autorizada.")
                result = {"raw_base64": base64.b64encode(response).decode("ascii"), "bytes": len(response)}
        else: result = response
        return {"profile": profile_name, "operation": operation, "data": result}

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        result = self.invoke(profile_name, "gmail.profile.get", {})
        return {"profile": profile_name, "authenticated": isinstance(result.get("data"), Mapping)}

    def store_refresh_token(self, profile_name: str, refresh_token: str) -> None:
        profile, _ = self._profile(profile_name, require_enabled=False)
        try: raw = self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], "notes")
        except CredentialError: raw = ""
        if raw:
            try: store = json.loads(raw)
            except json.JSONDecodeError as error: raise GoogleError("invalid_vault_notes", "As notas da entrada Google não contêm JSON válido.") from error
        else: store = {"version": 1, "profiles": {}}
        if not isinstance(store, dict) or store.get("version") != 1 or not isinstance(store.get("profiles"), dict): raise GoogleError("invalid_vault_notes", "As notas Google devem usar version=1 e profiles.")
        store["profiles"][profile["oauth_profile"]] = {"refresh_token": refresh_token}
        try: self.credentials.save_oauth_state(profile["credential_profile"], profile["credential_entry_path"], store)
        except CredentialError as error: raise GoogleError("credential_" + error.code, "Não foi possível salvar o refresh token Google.") from error

    def _profile(self, name: str, require_enabled: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
        path = self.root / "configs" / "mcp" / "google.toml"
        try: data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error: raise GoogleError("invalid_config", "Configuração Google MCP ausente ou inválida.") from error
        profile, defaults = data.get("profiles", {}).get(name), data.get("defaults", {})
        if not isinstance(profile, dict) or not isinstance(defaults, dict): raise GoogleError("profile_not_found", "Perfil Google não encontrado.")
        if require_enabled and profile.get("enabled") is not True: raise GoogleError("profile_disabled", "Perfil Google desabilitado.")
        return profile, defaults

    def _secrets(self, profile: Mapping[str, Any]) -> tuple[str, str, str]:
        try:
            client_id = self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["client_id_field"]); client_secret = self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["client_secret_field"]); raw = self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], "notes")
            store = json.loads(raw); refresh = store["profiles"][profile["oauth_profile"]]["refresh_token"]
            if not all(isinstance(item, str) and item for item in (client_id, client_secret, refresh)): raise ValueError()
            return client_id, client_secret, refresh
        except (CredentialError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error: raise GoogleError("oauth_required", "Credenciais ou autorização OAuth Google não estão completas no vault.") from error

    def _refresh(self, defaults: Mapping[str, Any], client_id: str, client_secret: str, refresh: str) -> str:
        body = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh, "grant_type": "refresh_token"}).encode()
        request = urllib.request.Request(defaults["oauth_base"] + "/token", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with self.opener(request, timeout=defaults["timeout_seconds"]) as response: payload = json.loads(response.read())
            token = payload.get("access_token")
            if not isinstance(token, str) or not token: raise ValueError()
            return token
        except Exception as error: raise GoogleError("oauth_refresh_failed", "Não foi possível renovar o acesso Google.") from error

    @staticmethod
    def _mapping(value: Any, label: str) -> dict[str, Any]:
        if value is None: return {}
        if not isinstance(value, Mapping): raise GoogleError("invalid_request", f"{label} deve ser um objeto.")
        return dict(value)

    @staticmethod
    def _substitute(template: str, params: Mapping[str, Any]) -> str:
        for part in template.split("/"):
            if part.startswith("{") and part.endswith("}"):
                key = part[1:-1]; value = params.get(key)
                if value is None or isinstance(value, (dict, list)): raise GoogleError("missing_parameter", f"Parâmetro obrigatório ausente: {key}.")
                template = template.replace("{" + key + "}", urllib.parse.quote(str(value), safe=""))
        return template

    def _paginated(self, defaults: Mapping[str, Any], access: str, method: str, path: str, query: Mapping[str, Any], data: bytes | None, headers: Mapping[str, str], paginate: bool) -> Any:
        collected: list[Any] = []; page_token = None
        while True:
            current = dict(query)
            if page_token: current["pageToken"] = page_token
            url = defaults["api_base"] + path + (("?" + urllib.parse.urlencode({k: v for k, v in current.items() if v is not None}, doseq=True)) if current else "")
            request = urllib.request.Request(url, data=data, headers={"Authorization": "Bearer " + access, **headers}, method=method)
            response = self._send(request, defaults)
            if not paginate or not isinstance(response, Mapping): return response
            collection_key = next((key for key in ("items", "messages", "threads", "events", "connections") if isinstance(response.get(key), list)), None)
            if collection_key: collected.extend(response[collection_key])
            page_token = response.get("nextPageToken")
            if not page_token:
                merged = dict(response)
                if collection_key: merged[collection_key] = collected
                merged["nextPageToken"] = None
                return merged

    def _send(self, request: urllib.request.Request, defaults: Mapping[str, Any]) -> Any:
        for attempt in range(defaults["max_retries"] + 1):
            try:
                with self.opener(request, timeout=defaults["timeout_seconds"]) as response:
                    raw = response.read()
                    if not raw: return None
                    try: return sanitize(json.loads(raw))
                    except (json.JSONDecodeError, UnicodeDecodeError): return raw
            except urllib.error.HTTPError as error:
                if error.code in {429, 500, 502, 503, 504} and attempt < defaults["max_retries"]: time.sleep(0.01); continue
                raise GoogleError("google_http_error", f"A API Google retornou HTTP {error.code}.", error.code) from error
            except urllib.error.URLError as error:
                if attempt < defaults["max_retries"]: time.sleep(0.01); continue
                raise GoogleError("network_error", "Não foi possível conectar às APIs Google.") from error
        raise GoogleError("request_failed", "A requisição Google não foi concluída.")

    @staticmethod
    def _confined_file(value: str, roots: list[str]) -> Path:
        path = Path(value).resolve()
        if not path.is_file() or not any(path == Path(root).resolve() or Path(root).resolve() in path.parents for root in roots): raise GoogleError("file_not_allowed", "O arquivo não pertence a uma raiz de leitura autorizada.")
        return path

    @staticmethod
    def _confined_output(value: Any, roots: list[str]) -> Path:
        if not isinstance(value, str) or not value: raise GoogleError("invalid_output_path", "output_path deve ser um caminho local.")
        path = Path(value).resolve()
        if not any(path == Path(root).resolve() or Path(root).resolve() in path.parents for root in roots): raise GoogleError("file_not_allowed", "O destino não pertence a uma raiz de escrita autorizada.")
        return path
