"""Contrato persistente dos perfis Spotify MCP."""
from __future__ import annotations

import json
from typing import Any


DEFAULT_SCOPES = [
    "user-read-private", "user-read-email", "playlist-read-private",
    "playlist-read-collaborative", "playlist-modify-public",
    "playlist-modify-private", "user-library-read", "user-library-modify",
    "user-follow-read", "user-follow-modify", "user-top-read",
    "user-read-recently-played", "user-read-playback-position", "user-read-playback-state",
    "user-modify-playback-state", "user-read-currently-playing",
]
FIELDS = {"username", "password", "url", "notes"}


def empty_configuration() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"api_base": "https://api.spotify.com/v1", "accounts_base": "https://accounts.spotify.com", "scopes": list(DEFAULT_SCOPES), "timeout_seconds": 30, "max_retries": 2}, "profiles": {}}


def validate_configuration(data: dict[str, Any]) -> None:
    if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict):
        raise ValueError("Estrutura de perfis Spotify inválida.")
    defaults = data["defaults"]
    if defaults.get("api_base") != "https://api.spotify.com/v1" or defaults.get("accounts_base") != "https://accounts.spotify.com":
        raise ValueError("Os endpoints Spotify são fixos e devem usar os hosts oficiais.")
    if not isinstance(defaults.get("scopes"), list) or not defaults["scopes"] or not all(isinstance(item, str) and item for item in defaults["scopes"]):
        raise ValueError("Escopos Spotify inválidos.")
    for key, minimum, maximum in (("timeout_seconds", 1, 300), ("max_retries", 0, 10)):
        value = defaults.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ValueError("Limites padrão Spotify inválidos.")
    for name, profile in data["profiles"].items():
        if not isinstance(name, str) or not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
            raise ValueError("Nome de perfil Spotify inválido.")
        if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
            raise ValueError("Estado do perfil Spotify inválido.")
        if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240:
            raise ValueError("Descrição do perfil Spotify é obrigatória e deve ter até 240 caracteres.")
        required = ("credential_profile", "credential_entry_path", "client_id_field", "refresh_token_field")
        if not all(isinstance(profile.get(key), str) and profile[key].strip() for key in required):
            raise ValueError("Referência de vault do perfil Spotify incompleta.")
        if profile["client_id_field"] not in FIELDS or profile["refresh_token_field"] not in FIELDS or profile["client_id_field"] == profile["refresh_token_field"]:
            raise ValueError("Campos KeePass Spotify inválidos.")
        port = profile.get("callback_port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            raise ValueError("Porta de callback Spotify inválida.")


def render_configuration(data: dict[str, Any]) -> str:
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    array = lambda values: json.dumps(values, ensure_ascii=False)
    defaults = data["defaults"]
    lines = ["schema_version = 1", "", "[defaults]", f"api_base = {quote(defaults['api_base'])}", f"accounts_base = {quote(defaults['accounts_base'])}", f"scopes = {array(defaults['scopes'])}", f"timeout_seconds = {defaults['timeout_seconds']}", f"max_retries = {defaults['max_retries']}"]
    for name, profile in sorted(data["profiles"].items()):
        lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {quote(profile['description'])}", f"credential_profile = {quote(profile['credential_profile'])}", f"credential_entry_path = {quote(profile['credential_entry_path'])}", f"client_id_field = {quote(profile['client_id_field'])}", f"refresh_token_field = {quote(profile['refresh_token_field'])}", f"callback_port = {profile['callback_port']}"]
    return "\n".join(lines) + "\n"
