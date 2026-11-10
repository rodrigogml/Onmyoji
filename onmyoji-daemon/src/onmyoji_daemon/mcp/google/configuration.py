"""Contrato persistente dos perfis Google MCP."""
from __future__ import annotations

import json
from typing import Any


DEFAULT_SCOPES = [
    "openid", "email", "profile", "https://mail.google.com/",
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar",
]
FIELDS = {"username", "password", "url", "notes"}


def empty_configuration() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"api_base": "https://www.googleapis.com", "oauth_base": "https://oauth2.googleapis.com", "scopes": list(DEFAULT_SCOPES), "timeout_seconds": 30, "max_retries": 2, "page_size": 100, "max_binary_bytes": 10485760}, "profiles": {}}


def validate_configuration(data: dict[str, Any]) -> None:
    if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict):
        raise ValueError("Estrutura de perfis Google inválida.")
    defaults = data["defaults"]
    if defaults.get("api_base") != "https://www.googleapis.com" or defaults.get("oauth_base") != "https://oauth2.googleapis.com":
        raise ValueError("Os endpoints Google são fixos e devem usar os hosts oficiais.")
    if not isinstance(defaults.get("scopes"), list) or not defaults["scopes"] or not all(isinstance(item, str) and item for item in defaults["scopes"]):
        raise ValueError("Escopos Google inválidos.")
    for key, minimum, maximum in (("timeout_seconds", 1, 300), ("max_retries", 0, 10), ("page_size", 1, 1000), ("max_binary_bytes", 1, 104857600)):
        value = defaults.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ValueError("Limites padrão Google inválidos.")
    for name, profile in data["profiles"].items():
        if not isinstance(name, str) or not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
            raise ValueError("Nome de perfil Google inválido.")
        if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
            raise ValueError("Estado do perfil Google inválido.")
        if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240:
            raise ValueError("Descrição do perfil Google é obrigatória e deve ter até 240 caracteres.")
        required = ("oauth_profile", "credential_profile", "credential_entry_path", "client_id_field", "client_secret_field", "user_id")
        if not all(isinstance(profile.get(key), str) and profile[key].strip() for key in required):
            raise ValueError("Configuração Google ou referência de vault incompleta.")
        if profile["client_id_field"] not in FIELDS or profile["client_secret_field"] not in FIELDS:
            raise ValueError("Campo KeePass Google inválido.")
        for key in ("readable_roots", "writable_roots"):
            roots = profile.get(key)
            if not isinstance(roots, list) or not all(isinstance(item, str) and item.strip() for item in roots):
                raise ValueError("Raízes de arquivos Google inválidas.")


def render_configuration(data: dict[str, Any]) -> str:
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    array = lambda values: json.dumps(values, ensure_ascii=False)
    defaults = data["defaults"]
    lines = ["schema_version = 1", "", "[defaults]", f"api_base = {quote(defaults['api_base'])}", f"oauth_base = {quote(defaults['oauth_base'])}", f"scopes = {array(defaults['scopes'])}", f"timeout_seconds = {defaults['timeout_seconds']}", f"max_retries = {defaults['max_retries']}", f"page_size = {defaults['page_size']}", f"max_binary_bytes = {defaults['max_binary_bytes']}"]
    for name, profile in sorted(data["profiles"].items()):
        lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {quote(profile['description'])}", f"oauth_profile = {quote(profile['oauth_profile'])}", f"credential_profile = {quote(profile['credential_profile'])}", f"credential_entry_path = {quote(profile['credential_entry_path'])}", f"client_id_field = {quote(profile['client_id_field'])}", f"client_secret_field = {quote(profile['client_secret_field'])}", f"user_id = {quote(profile['user_id'])}", f"readable_roots = {array(profile['readable_roots'])}", f"writable_roots = {array(profile['writable_roots'])}"]
    return "\n".join(lines) + "\n"
