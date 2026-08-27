#!/usr/bin/env python3
"""Secure JSON wrapper for Gmail, People, Drive and Calendar APIs."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
import tomllib


class GoogleError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Settings:
    api_base: str
    oauth_base: str
    credentials_file: str
    scopes: tuple[str, ...]
    timeout: float
    retries: int
    page_size: int
    user_id: str
    download_dir: str
    vault_profile: str
    token_entry: str
    profile: str
    client_id_field: str
    client_secret_field: str
    profiles_field: str


def fail(code: str, message: str, status: int | None = None) -> None:
    raise GoogleError(code, message, status)


def load_settings(path: str, profile_name: str) -> Settings:
    cfg = Path(path)
    if not cfg.is_file():
        fail("config_not_found", "Arquivo de configuração não encontrado.")
    try:
        document = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        fail("invalid_config", "Não foi possível ler o arquivo de configuração.")
    defaults, profile_data = document.get("defaults", {}), document.get("profiles", {}).get(profile_name)
    if not isinstance(defaults, Mapping) or not isinstance(profile_data, Mapping): fail("missing_config", "O perfil solicitado não foi encontrado.")
    google = {**defaults, **profile_data}
    try:
        timeout = float(google.get("timeout_seconds", 30)); retries = int(google.get("max_retries", 2)); page_size = int(google.get("page_size", 100))
    except (TypeError, ValueError):
        fail("invalid_config", "timeout_seconds, max_retries e page_size devem ser numéricos.")
    api_base = google.get("api_base", "https://www.googleapis.com").rstrip("/")
    oauth_base = google.get("oauth_base", "https://oauth2.googleapis.com").rstrip("/")
    if api_base != "https://www.googleapis.com" or oauth_base != "https://oauth2.googleapis.com":
        fail("invalid_config", "Os hosts Google configurados não são permitidos.")
    credentials_file = str(google.get("credentials_file", "")).strip(); profile = str(google.get("oauth_profile", "")).strip()
    scopes = tuple(item for item in google.get("scopes", []) if isinstance(item, str) and item)
    if not scopes or timeout <= 0 or retries < 0 or not 1 <= page_size <= 1000:
        fail("invalid_config", "Configuração Google incompleta ou inválida.")
    vault_profile = str(google.get("vault_profile", "")).strip(); token_entry = str(google.get("vault_entry_path", "")).strip()
    client_id_field = str(google.get("client_id_field", "username")).strip(); client_secret_field = str(google.get("client_secret_field", "password")).strip(); profiles_field = str(google.get("profiles_field", "notes")).strip()
    valid_fields = {"username", "password", "url", "notes"}
    if not profile or not vault_profile or not token_entry or client_id_field not in valid_fields or client_secret_field not in valid_fields or profiles_field != "notes":
        fail("invalid_config", "Configuração KeePassVault incompleta.")
    return Settings(api_base, oauth_base, credentials_file, scopes, timeout, retries, page_size, str(google.get("user_id", "me")), str(google.get("download_dir", "downloads")), vault_profile, token_entry, profile, client_id_field, client_secret_field, profiles_field)


def vault_request(settings: Settings, request: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        vault_script = Path(__file__).resolve().parents[2] / "keepass-vault" / "scripts" / "keepass_vault.py"; vault_config = Path(__file__).resolve().parents[3] / "configs" / "keepass.toml"
        result = subprocess.run([sys.executable, str(vault_script), "--config", str(vault_config), "--profile", settings.vault_profile], input=json.dumps(request), text=True, capture_output=True, timeout=settings.timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        fail("vault_unavailable", "Não foi possível consultar o provedor KeePassVault.")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("vault_protocol_error", "O provedor KeePassVault não retornou JSON válido.")
    if result.returncode != 0 or payload.get("ok") is not True:
        fail("vault_operation_failed", "A operação na KeePassVault falhou.")
    return payload


def read_vault_field(settings: Settings, field: str) -> str | None:
    request = {"operation": "read", "path": settings.token_entry, "field": field, "auth": {"mode":"configured"}}
    try:
        payload = vault_request(settings, request)
    except GoogleError as error:
        if error.code in {"vault_operation_failed"}:
            return None
        raise
    value = payload.get("result", {}).get("value")
    return value if isinstance(value, str) and value else None


def read_profile_store(settings: Settings) -> dict[str, Any]:
    raw = read_vault_field(settings, settings.profiles_field)
    if not raw:
        return {"version": 1, "profiles": {}}
    try:
        store = json.loads(raw)
    except json.JSONDecodeError:
        fail("invalid_vault_notes", "As notas da entrada Google não contêm JSON válido.")
    if not isinstance(store, dict) or store.get("version") != 1 or not isinstance(store.get("profiles"), dict):
        fail("invalid_vault_notes", "As notas da entrada Google devem usar version=1 e profiles.")
    return store


def read_refresh_token(settings: Settings) -> str | None:
    profile_data = read_profile_store(settings).get("profiles", {}).get(settings.profile)
    if not isinstance(profile_data, dict):
        return None
    token = profile_data.get("refresh_token")
    return token if isinstance(token, str) and token else None


def write_refresh_token(settings: Settings, token: str) -> None:
    store = read_profile_store(settings)
    store["profiles"][settings.profile] = {"refresh_token": token}
    request = {"operation": "edit", "path": settings.token_entry, "fields": {settings.profiles_field: json.dumps(store, ensure_ascii=True, separators=(",", ":"))}, "auth": {"mode":"configured"}}
    vault_request(settings, request)


def load_client_credentials(path: str) -> tuple[str, str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        block = data.get("installed") or data.get("desktop")
        client_id, client_secret = block["client_id"], block.get("client_secret", "")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError):
        fail("invalid_credentials", "JSON de credenciais Desktop inválido.")
    if not isinstance(client_id, str) or not client_id:
        fail("invalid_credentials", "client_id ausente nas credenciais.")
    return client_id, client_secret if isinstance(client_secret, str) else ""


def load_vault_client_credentials(settings: Settings) -> tuple[str, str]:
    client_id = read_vault_field(settings, settings.client_id_field)
    client_secret = read_vault_field(settings, settings.client_secret_field)
    if not client_id or not client_secret:
        fail("invalid_vault_credentials", "client_id ou client_secret ausente na KeePassVault.")
    return client_id, client_secret


def load_configured_client_credentials(settings: Settings) -> tuple[str, str]:
    if settings.credentials_file:
        return load_client_credentials(settings.credentials_file)
    return load_vault_client_credentials(settings)


def token_exchange(settings: Settings, client_id: str, client_secret: str, code: str, redirect_uri: str) -> Mapping[str, Any]:
    body = urllib.parse.urlencode({"code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    request = urllib.request.Request(settings.oauth_base + "/token", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout) as response:
            return json.loads(response.read())
    except (HTTPError, URLError, json.JSONDecodeError):
        fail("oauth_exchange_failed", "Não foi possível trocar o código OAuth por tokens.")


def refresh_access_token(settings: Settings, refresh_token: str, client_id: str, client_secret: str) -> str:
    body = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token, "grant_type": "refresh_token"}).encode()
    request = urllib.request.Request(settings.oauth_base + "/token", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout) as response:
            data = json.loads(response.read())
    except (HTTPError, URLError, json.JSONDecodeError):
        fail("oauth_refresh_failed", "Não foi possível renovar o access token.")
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        fail("oauth_refresh_failed", "A resposta OAuth não contém access_token.")
    return token


SENSITIVE_KEYS = {"access_token", "refresh_token", "client_secret", "authorization", "password", "token"}


def sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and any(marker in value.lower() for marker in ("access_token=", "refresh_token=", "token=")):
        return "[REDACTED_URL]"
    return value


# Explicitly allowlisted common and administrative operations across the four APIs.
OPERATIONS: dict[str, tuple[str, str]] = {
    "gmail.messages.list": ("GET", "/gmail/v1/users/{user_id}/messages"), "gmail.messages.get": ("GET", "/gmail/v1/users/{user_id}/messages/{id}"), "gmail.messages.modify": ("POST", "/gmail/v1/users/{user_id}/messages/{id}/modify"), "gmail.messages.delete": ("DELETE", "/gmail/v1/users/{user_id}/messages/{id}"), "gmail.messages.send": ("POST", "/gmail/v1/users/{user_id}/messages/send"), "gmail.messages.insert": ("POST", "/gmail/v1/users/{user_id}/messages/insert"), "gmail.messages.import": ("POST", "/gmail/v1/users/{user_id}/messages/import"), "gmail.messages.batchModify": ("POST", "/gmail/v1/users/{user_id}/messages/batchModify"),
    "gmail.threads.list": ("GET", "/gmail/v1/users/{user_id}/threads"), "gmail.threads.get": ("GET", "/gmail/v1/users/{user_id}/threads/{id}"), "gmail.threads.modify": ("POST", "/gmail/v1/users/{user_id}/threads/{id}/modify"), "gmail.threads.delete": ("DELETE", "/gmail/v1/users/{user_id}/threads/{id}"),
    "gmail.labels.list": ("GET", "/gmail/v1/users/{user_id}/labels"), "gmail.labels.get": ("GET", "/gmail/v1/users/{user_id}/labels/{id}"), "gmail.labels.create": ("POST", "/gmail/v1/users/{user_id}/labels"), "gmail.labels.update": ("PUT", "/gmail/v1/users/{user_id}/labels/{id}"), "gmail.labels.patch": ("PATCH", "/gmail/v1/users/{user_id}/labels/{id}"), "gmail.labels.delete": ("DELETE", "/gmail/v1/users/{user_id}/labels/{id}"),
    "gmail.drafts.list": ("GET", "/gmail/v1/users/{user_id}/drafts"), "gmail.drafts.get": ("GET", "/gmail/v1/users/{user_id}/drafts/{id}"), "gmail.drafts.create": ("POST", "/gmail/v1/users/{user_id}/drafts"), "gmail.drafts.update": ("PUT", "/gmail/v1/users/{user_id}/drafts/{id}"), "gmail.drafts.send": ("POST", "/gmail/v1/users/{user_id}/drafts/send"), "gmail.drafts.delete": ("DELETE", "/gmail/v1/users/{user_id}/drafts/{id}"),
    "gmail.attachments.get": ("GET", "/gmail/v1/users/{user_id}/messages/{message_id}/attachments/{id}"), "gmail.history.list": ("GET", "/gmail/v1/users/{user_id}/history"), "gmail.profile.get": ("GET", "/gmail/v1/users/{user_id}/profile"), "gmail.settings.filters.list": ("GET", "/gmail/v1/users/{user_id}/settings/filters"), "gmail.settings.filters.create": ("POST", "/gmail/v1/users/{user_id}/settings/filters"), "gmail.settings.filters.delete": ("DELETE", "/gmail/v1/users/{user_id}/settings/filters/{id}"), "gmail.settings.forwarding.list": ("GET", "/gmail/v1/users/{user_id}/settings/forwardingAddresses"), "gmail.settings.sendAs.list": ("GET", "/gmail/v1/users/{user_id}/settings/sendAs"), "gmail.settings.delegates.list": ("GET", "/gmail/v1/users/{user_id}/settings/delegates"),
    "people.connections.list": ("GET", "/people/v1/people/me/connections"), "people.get": ("GET", "/people/v1/{resource_name}"), "people.searchContacts": ("GET", "/people/v1/people:searchContacts"), "people.createContact": ("POST", "/people/v1/people:createContact"), "people.updateContact": ("PATCH", "/people/v1/{resource_name}:updateContact"), "people.deleteContact": ("DELETE", "/people/v1/{resource_name}:deleteContact"), "people.contactGroups.list": ("GET", "/people/v1/contactGroups"), "people.contactGroups.get": ("GET", "/people/v1/{resource_name}"), "people.otherContacts.list": ("GET", "/people/v1/otherContacts"),
    "drive.files.list": ("GET", "/drive/v3/files"), "drive.files.get": ("GET", "/drive/v3/files/{fileId}"), "drive.files.create": ("POST", "/drive/v3/files"), "drive.files.update": ("PATCH", "/drive/v3/files/{fileId}"), "drive.files.copy": ("POST", "/drive/v3/files/{fileId}/copy"), "drive.files.delete": ("DELETE", "/drive/v3/files/{fileId}"), "drive.files.emptyTrash": ("DELETE", "/drive/v3/files/trash"), "drive.files.export": ("GET", "/drive/v3/files/{fileId}/export"), "drive.files.download": ("GET", "/drive/v3/files/{fileId}"),
    "drive.permissions.list": ("GET", "/drive/v3/files/{fileId}/permissions"), "drive.permissions.get": ("GET", "/drive/v3/files/{fileId}/permissions/{permissionId}"), "drive.permissions.create": ("POST", "/drive/v3/files/{fileId}/permissions"), "drive.permissions.update": ("PATCH", "/drive/v3/files/{fileId}/permissions/{permissionId}"), "drive.permissions.delete": ("DELETE", "/drive/v3/files/{fileId}/permissions/{permissionId}"), "drive.comments.list": ("GET", "/drive/v3/files/{fileId}/comments"), "drive.revisions.list": ("GET", "/drive/v3/files/{fileId}/revisions"), "drive.changes.list": ("GET", "/drive/v3/changes"), "drive.about.get": ("GET", "/drive/v3/about"), "drive.drives.list": ("GET", "/drive/v3/drives"), "drive.uploads.create": ("POST", "/upload/drive/v3/files"),
    "calendar.calendars.list": ("GET", "/calendar/v3/users/me/calendarList"), "calendar.calendars.get": ("GET", "/calendar/v3/calendars/{calendarId}"), "calendar.calendars.clear": ("POST", "/calendar/v3/calendars/{calendarId}/clear"), "calendar.calendars.delete": ("DELETE", "/calendar/v3/calendars/{calendarId}"), "calendar.calendars.insert": ("POST", "/calendar/v3/calendars"), "calendar.calendars.update": ("PUT", "/calendar/v3/calendars/{calendarId}"), "calendar.calendars.patch": ("PATCH", "/calendar/v3/calendars/{calendarId}"),
    "calendar.events.list": ("GET", "/calendar/v3/calendars/{calendarId}/events"), "calendar.events.get": ("GET", "/calendar/v3/calendars/{calendarId}/events/{eventId}"), "calendar.events.insert": ("POST", "/calendar/v3/calendars/{calendarId}/events"), "calendar.events.update": ("PUT", "/calendar/v3/calendars/{calendarId}/events/{eventId}"), "calendar.events.patch": ("PATCH", "/calendar/v3/calendars/{calendarId}/events/{eventId}"), "calendar.events.delete": ("DELETE", "/calendar/v3/calendars/{calendarId}/events/{eventId}"), "calendar.events.move": ("POST", "/calendar/v3/calendars/{calendarId}/events/{eventId}/move"), "calendar.events.quickAdd": ("POST", "/calendar/v3/calendars/{calendarId}/events/quickAdd"), "calendar.events.instances": ("GET", "/calendar/v3/calendars/{calendarId}/events/{eventId}/instances"), "calendar.events.watch": ("POST", "/calendar/v3/calendars/{calendarId}/events/watch"),
    "calendar.acl.list": ("GET", "/calendar/v3/calendars/{calendarId}/acl"), "calendar.acl.insert": ("POST", "/calendar/v3/calendars/{calendarId}/acl"), "calendar.acl.update": ("PUT", "/calendar/v3/calendars/{calendarId}/acl/{ruleId}"), "calendar.acl.delete": ("DELETE", "/calendar/v3/calendars/{calendarId}/acl/{ruleId}"), "calendar.colors.get": ("GET", "/calendar/v3/colors"), "calendar.freebusy.query": ("POST", "/calendar/v3/freeBusy"), "calendar.settings.list": ("GET", "/calendar/v3/users/me/settings"), "calendar.channels.stop": ("POST", "/calendar/v3/channels/stop"),
}


def substitute(template: str, params: Mapping[str, Any], settings: Settings) -> str:
    params = {"user_id": settings.user_id, **dict(params or {})}
    for part in template.split("/"):
        if part.startswith("{") and part.endswith("}"):
            key = part[1:-1]
            value = params.get(key)
            if value is None or isinstance(value, (dict, list)):
                fail("missing_parameter", f"Parâmetro obrigatório ausente: {key}.")
            template = template.replace("{" + key + "}", urllib.parse.quote(str(value), safe=""))
    return template


class Client:
    def __init__(self, settings: Settings, access_token: str):
        self.settings, self.access_token = settings, access_token

    def request(self, service: str, operation: str, params: Mapping[str, Any], query: Mapping[str, Any], body: Any = None, paginate: bool = False, confirm: bool = False) -> Any:
        name = f"{service}.{operation}"
        if name not in OPERATIONS:
            fail("unsupported_operation", "Operação Google não permitida.")
        if any(word in operation.lower() for word in ("delete", "clear", "emptytrash", "revoke")) and confirm is not True:
            fail("confirmation_required", "Operações destrutivas exigem confirm=true.")
        method, template = OPERATIONS[name]
        path = substitute(template, params, self.settings)
        if name == "drive.uploads.create":
            file_path = Path(str((body or {}).get("file_path", ""))) if isinstance(body, Mapping) else Path("")
            if not file_path.is_file():
                fail("file_not_found", "drive.uploads.create exige body.file_path.")
            data = file_path.read_bytes()
            query = {**dict(query or {}), "uploadType": "media"}
            headers = {"Content-Type": mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"}
        else:
            data = json.dumps(body).encode("utf-8") if body is not None else None
            headers = {"Content-Type": "application/json"} if body is not None else {}
        return self._paginated(method, path, query or {}, data, headers, paginate)

    def _paginated(self, method: str, path: str, query: Mapping[str, Any], data: bytes | None, extra_headers: Mapping[str, str], paginate: bool) -> Any:
        collected: list[Any] = []
        page_token: str | None = None
        while True:
            params = dict(query)
            if page_token:
                params["pageToken"] = page_token
            url = self.settings.api_base + path
            if params:
                url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
            headers = {"Authorization": f"Bearer {self.access_token}", **dict(extra_headers)}
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            response = self._send(request)
            if not paginate or not isinstance(response, Mapping):
                return response
            if isinstance(response.get("items"), list):
                collected.extend(response["items"])
            elif isinstance(response.get("messages"), list):
                collected.extend(response["messages"])
            elif isinstance(response.get("threads"), list):
                collected.extend(response["threads"])
            elif isinstance(response.get("events"), list):
                collected.extend(response["events"])
            elif isinstance(response.get("connections"), list):
                collected.extend(response["connections"])
            page_token = response.get("nextPageToken")
            if not page_token:
                merged = dict(response)
                for key in ("items", "messages", "threads", "events", "connections"):
                    if key in merged:
                        merged[key] = collected
                merged["nextPageToken"] = None
                return merged

    def _send(self, request: urllib.request.Request) -> Any:
        for attempt in range(self.settings.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                    raw = response.read()
                    if not raw:
                        return None
                    try:
                        return sanitize(json.loads(raw))
                    except json.JSONDecodeError:
                        return {"raw_base64": base64.b64encode(raw).decode("ascii")}
            except HTTPError as error:
                if error.code == 401:
                    fail("token_expired", "O access token expirou; renove-o antes de repetir.", error.code)
                if error.code in {429, 500, 502, 503, 504} and attempt < self.settings.retries:
                    try:
                        delay = min(float(error.headers.get("Retry-After", "1")), 10)
                    except ValueError:
                        delay = 1
                    time.sleep(delay)
                    continue
                fail("google_http_error", f"A API Google retornou HTTP {error.code}.", error.code)
            except URLError:
                if attempt < self.settings.retries:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                fail("network_error", "Não foi possível conectar às APIs Google.")
        fail("request_failed", "A requisição Google não foi concluída.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, Mapping) or request.get("version") != 1:
            fail("unsupported_version", "Somente requisições com version=1 são aceitas.")
        settings = load_settings(args.config, args.profile)
        client_id, client_secret = load_configured_client_credentials(settings)
        refresh = read_refresh_token(settings)
        if not refresh:
            fail("oauth_required", "Nenhum refresh token foi encontrado na KeePassVault; execute oauth_bootstrap.py.")
        client = Client(settings, refresh_access_token(settings, refresh, client_id, client_secret))
        data = client.request(str(request.get("service", "")), str(request.get("operation", "")), request.get("params", {}), request.get("query", {}), request.get("body"), bool(request.get("paginate", False)), bool(request.get("confirm", False)))
        print(json.dumps({"version": 1, "ok": True, "service": request.get("service"), "operation": request.get("operation"), "data": data}, ensure_ascii=True))
        return 0
    except json.JSONDecodeError:
        error = {"code": "invalid_json", "message": "A entrada não contém JSON válido."}
    except GoogleError as exc:
        error = {"code": exc.code, "message": exc.message}
    except Exception:
        error = {"code": "internal_error", "message": "Falha interna ao processar a solicitação."}
    print(json.dumps({"version": 1, "ok": False, "error": error}, ensure_ascii=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
