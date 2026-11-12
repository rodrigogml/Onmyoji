"""Catálogo de comandos e permissões para DMs de owners e outros usuários."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_OWNER_COMMANDS = (
    ("new", "Iniciar uma nova conversa"),
    ("config", "Configurar esta conversa"),
    ("channel", "Gerenciar canal de envio"),
    ("usage", "Consultar consumo e franquia do Codex"),
    ("login", "Autenticar ou trocar conta Codex"),
    ("fila", "Enfileirar uma mensagem: /fila <texto>"),
    ("direcionar", "Orientar o turno em andamento: /direcionar <texto>"),
    ("cancel", "Interromper o turno atual e preservar a fila"),
    ("resolver", "Consultar e reconciliar solicitações sem confirmação"),
)
_TOTP_COMMAND = ("totp", "Obter código de autenticação")
_OTHER_PRIVATE_COMMAND_NAMES = ("new", "resolver")
REGISTERED_COMMANDS = frozenset("/" + name for name, _ in (*_OWNER_COMMANDS, _TOTP_COMMAND)) | {"/pair"}
CONTROL_COMMANDS = REGISTERED_COMMANDS - {"/fila", "/direcionar"}
COMMAND_REFRESH_SECONDS = 300
COMMAND_RETRY_SECONDS = 60
COMMAND_SYNC_BUDGET_SECONDS = 30


def owner_commands(totp_enabled: bool) -> list[dict[str, str]]:
    entries = (*_OWNER_COMMANDS, _TOTP_COMMAND) if totp_enabled else _OWNER_COMMANDS
    return [{"command": name, "description": description} for name, description in entries]


def other_private_commands() -> list[dict[str, str]]:
    """Comandos seguros para qualquer usuário, somente em sua própria DM."""
    return [{"command": name, "description": description}
            for name, description in _OWNER_COMMANDS if name in _OTHER_PRIVATE_COMMAND_NAMES]


def command_catalog_revision(totp_enabled: bool) -> str:
    catalogs = {"owner": owner_commands(totp_enabled), "other_private": other_private_commands()}
    payload = json.dumps(catalogs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def command_catalog_matches(actual: object, expected: list[dict[str, str]]) -> bool:
    return (isinstance(actual, list) and len(actual) == len(expected)
            and all(isinstance(item, dict)
                    and item.get("command") == command["command"]
                    and item.get("description") == command["description"]
                    and item.get("is_ephemeral", False) is False
                    for item, command in zip(actual, expected)))


def command_sync_is_current(summary: object, totp_enabled: bool) -> bool:
    if not isinstance(summary, dict) or summary.get("totp_enabled") is not totp_enabled:
        return False
    owners, verified, failed = (summary.get(key) for key in ("owners", "verified", "failed"))
    return (all(type(value) is int for value in (owners, verified, failed))
            and owners > 0 and verified == owners and failed == 0
            and summary.get("pending", False) is False and summary.get("in_progress", False) is False
            and summary.get("expected") == [item["command"] for item in owner_commands(totp_enabled)]
            and summary.get("other_private_verified") is True
            and summary.get("expected_other_private") == [item["command"] for item in other_private_commands()]
            and summary.get("catalog_revision") == command_catalog_revision(totp_enabled))


def normalize_language_code(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z]{2}(?:[-_][a-zA-Z0-9]{2,8})*", value):
        return ""
    return value[:2].lower()


def invalid_command_message(totp_enabled: bool) -> str:
    names = ["/" + item["command"] for item in owner_commands(totp_enabled)]
    return "Comando inválido. Use " + ", ".join(names[:-1]) + " ou " + names[-1] + "."
