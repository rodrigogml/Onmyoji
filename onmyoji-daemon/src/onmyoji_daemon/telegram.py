from __future__ import annotations

import argparse
import fnmatch
import hashlib
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config_publication import write_instance_text
from .rpc import RpcServer
from .telegram_channels import TelegramChannels
from .telegram_location import OMITTED, LocationValidationError, UnsupportedLocationError, normalize_location, normalize_stored_location, parse_message_location
from .telegram_execution import TelegramExecution
from .agent_notification.ingress import NotificationIngress
from .telegram_work import RelatedWorkMonitor
from .telegram_persistence import GatewayStore, GatewayPersistenceError, protect_legacy_gateway
from .telegram_process import ProcessRecoveryError
from .telegram_delivery import GatewayDelivery, DeliveryOwner, DeliveryUncertain, DeliverySuppressed
from .codex_capabilities import CapabilitySet
from .codex_usage import format_rate_limits
from .telegram_login import TelegramLogin
from .instructions import InstructionComposer, TelegramTurnContext
from .instance import identity, telegram_configuration_path
from .codex_execution import CodexExecutionError, CodexLaunch
from .mcp.credentials import CredentialError, KeePassCredentialResolver
from .mcp.eccovox import EccoVoxError, EccoVoxService
from .mcp.reconciliation import mcp_reconciliation_pending
from .telegram_stickers import load as load_sticker_catalog
from .mcp.artifacts import ArtifactPathError, authorized_file, transfer_roots
from .telegram_formatting import MAX_MESSAGE_LENGTH, plain_text, render_html, split_html
from .telegram_commands import (COMMAND_REFRESH_SECONDS, COMMAND_RETRY_SECONDS, COMMAND_SYNC_BUDGET_SECONDS,
                                command_catalog_matches, command_catalog_revision, command_sync_is_current, invalid_command_message,
                                normalize_language_code, owner_commands, other_private_commands)

SERVICE_MESSAGE_FIELDS = frozenset((
    "forum_topic_closed", "forum_topic_created", "forum_topic_edited", "forum_topic_reopened",
    "general_forum_topic_hidden", "general_forum_topic_unhidden", "group_chat_created",
    "left_chat_member", "migrate_from_chat_id", "migrate_to_chat_id", "new_chat_members",
    "new_chat_photo", "new_chat_title", "pinned_message", "video_chat_ended",
    "video_chat_participants_invited", "video_chat_scheduled", "video_chat_started",
))


_JSON_IO_LOCK = threading.RLock()


def json_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    with _JSON_IO_LOCK:
        try: value = json.loads(path.read_text(encoding="utf-8")); return value if isinstance(value, dict) else default
        except (OSError, ValueError): return default


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Publica metadados sem staging em configs nem disputa entre escritores."""
    with _JSON_IO_LOCK:
        instance = next((parent.parent for parent in path.parents if parent.name == "configs"), Path(__file__).resolve().parents[3])
        write_instance_text(instance, path, json.dumps(value, ensure_ascii=False, indent=2))


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    vault_profile: str
    token_entry: str
    project: Path
    executable: str
    model: str
    effort: str
    sandbox: str
    approval: str
    poll_timeout: int
    turn_timeout: int
    parallel: int
    instructions_file: str
    instructions_enabled: bool
    owner_execution_preferences: bool
    owner_allowed_models: tuple[str, ...]
    owner_allowed_efforts: tuple[str, ...]
    app_server_enabled: bool
    app_server_idle_seconds: int
    max_attachment_bytes: int
    max_batch_attachment_bytes: int
    max_pending_items: int
    max_retained_attachment_bytes: int
    max_outbound_media_bytes: int
    max_outbound_media_per_turn: int
    context_max_messages: int
    context_max_age_seconds: int
    context_max_bytes: int
    context_compaction_message_interval: int
    voice_enabled: bool
    voice_profile: str
    voice_language: str
    voice_name: str
    voice_speed: float
    voice_format: str
    voice_max_text: int
    voice_auto_off_minutes: int
    voice_fallback_to_text: bool
    agent_outbound_media: bool
    totp_enabled: bool
    default_share_thoughts: bool
    default_delete_thoughts: bool
    busy_input_policy: str = "queue"
    preparation_workers: int = 4
    interrupt_grace_seconds: float = 15.0
    post_turn_results: bool = False

    @property
    def state_dir(self) -> Path:
        """Retorna a área durável, separada da configuração do Gateway."""
        return self.root / "state" / "daemon" / "services" / "telegram"

    @classmethod
    def load(cls, root: Path, data_dir: Path) -> "Settings":
        path = telegram_configuration_path(root)
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if type(data.get("schema_version")) is not int or data.get("schema_version") not in {1, 2}: raise ValueError("unsupported telegram schema")
        telegram, agent, limits, voice, totp = data.get("telegram", {}), data.get("agent", {}), data.get("limits", {}), data.get("voice_reply", {}), data.get("totp", {})
        try:
            platform = tomllib.loads((root / "configs" / "daemon" / "daemon.toml").read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError("Instale e configure o daemon antes de iniciar o Gateway Telegram.") from error
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"A configuração da plataforma do daemon é inválida: {error}") from error
        app_server, instructions = platform.get("app_server", {}), platform.get("instructions", {})
        if not isinstance(app_server, dict) or not isinstance(instructions, dict):
            raise ValueError("A configuração da plataforma do daemon é inválida.")
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
        project = Path(str(system.get("project_directory") or "")).resolve()
        if not project.is_dir(): raise ValueError("configured Shikigami workspace does not exist")
        profile = str(telegram.get("vault_profile") or "")
        entry = str(telegram.get("token_entry") or "")
        from .agent_notification.configuration import NotificationConfiguration
        notification = NotificationConfiguration(root).load()
        if (not profile or not entry) and notification["telegram_receiver_enabled"]: raise ValueError("Perfil de vault MCP e entrada do token são obrigatórios")
        idle = int(app_server.get("idle_timeout_seconds") or 1800)
        if idle < 60: raise ValueError("App Server idle timeout must be at least 60 seconds")
        per_file, batch, pending, retained = int(limits.get("max_attachment_bytes") or 20 * 1024 * 1024), int(limits.get("max_batch_attachment_bytes") or 50 * 1024 * 1024), int(limits.get("max_pending_items") or 50), int(limits.get("max_retained_attachment_bytes") or 250 * 1024 * 1024)
        if not 1 <= per_file <= batch <= retained: raise ValueError("Telegram attachment limits are invalid")
        output_bytes, output_count = int(limits.get("max_outbound_media_bytes") or 20 * 1024 * 1024), max(1, int(limits.get("max_outbound_media_per_turn") or 3))
        context_messages, context_age = int(limits.get("context_max_messages", 100)), int(limits.get("context_max_age_seconds", 7 * 24 * 3600))
        context_bytes, compact_interval = int(limits.get("context_max_bytes", 512 * 1024)), int(limits.get("context_compaction_message_interval", 16))
        if context_messages < 0 or context_age < 0 or not 64 * 1024 <= context_bytes < 4 * 1024 * 1024 or compact_interval < 0: raise ValueError("Telegram context window limits are invalid")
        permitted_models = tuple(dict.fromkeys(str(value).strip() for value in agent.get("owner_allowed_models", []) if isinstance(value, str) and value.strip())) or (str(system.get("model") or ""),)
        permitted_efforts = tuple(dict.fromkeys(str(value).strip() for value in agent.get("owner_allowed_reasoning_efforts", []) if isinstance(value, str) and value.strip())) or (str(system.get("model_reasoning_effort") or "medium"),)
        enabled, voice_profile = bool(voice.get("enabled", False)), str(voice.get("eccovox_profile") or "")
        outbound_media = bool(telegram.get("agent_outbound_media", voice.get("agent_outbound_media", False)))
        requested_format, voice_text, auto_off = str(voice.get("response_format") or "mp3").casefold(), int(voice.get("max_text_characters") or 3500), int(voice.get("auto_off_minutes") or 15)
        # A primeira configuração-modelo usava opus, mas o motor EccoVox atual
        # ainda o rejeita apesar de anunciá-lo no health. MP3 é suportado pelo
        # runtime e pelo Telegram; preservamos configurações já criadas.
        voice_format = "mp3" if requested_format == "opus" else requested_format
        if enabled and (not voice_profile or voice_format not in {"mp3", "wav", "flac"} or not 1 <= voice_text <= 4000 or not 1 <= auto_off <= 1440): raise ValueError("Telegram voice reply configuration is invalid")
        if bool(totp.get("enabled", False)) and notification["telegram_receiver_enabled"]:
            real, fake = str(totp.get("real_password_entry") or ""), str(totp.get("fake_password_entry") or "")
            if not real or not fake:
                raise ValueError("TOTP habilitado exige as entradas de senha real e falsa no vault MCP.")
            try:
                resolver = KeePassCredentialResolver(root)
                for entry_path in (real, fake):
                    if not resolver.entry_exists(profile, entry_path) or not resolver.password_exists(profile, entry_path):
                        raise ValueError("TOTP habilitado exige senhas real e falsa existentes no vault MCP.")
            except CredentialError as error:
                raise ValueError("Não foi possível validar as senhas TOTP no vault MCP.") from error
        value = cls(root, data_dir, profile, entry, project, str(system.get("executable") or "codex"), str(system.get("model") or ""), str(system.get("model_reasoning_effort") or "medium"), str(system.get("sandbox_mode") or "workspace-write"), str(system.get("approval_policy") or "never"), int(telegram.get("poll_timeout_seconds") or 30), int(agent.get("turn_timeout_seconds") or 900), max(1, int(agent.get("max_parallel_conversations") or 1)), str(instructions.get("shikigami_file") or "instructions.md"), bool(instructions.get("enabled", True)), bool(agent.get("owner_execution_preferences", True)), permitted_models, permitted_efforts, bool(app_server.get("enabled", False)), idle, per_file, batch, max(1, pending), retained, output_bytes, output_count, context_messages, context_age, context_bytes, compact_interval, enabled, voice_profile, str(voice.get("language") or ""), str(voice.get("voice") or ""), float(voice.get("speed") or 1.0), voice_format, voice_text, auto_off, bool(voice.get("fallback_to_text", True)), outbound_media, bool(totp.get("enabled", False)), bool(telegram.get("default_share_thoughts", True)), bool(telegram.get("default_delete_thoughts", True)))
        policy = str(agent.get("busy_input_policy") or "queue")
        return replace(value, busy_input_policy=policy if policy in {"queue", "steer"} else "queue", preparation_workers=max(1, min(16, int(limits.get("preparation_workers") or 4))), interrupt_grace_seconds=max(0.1, min(60.0, float(agent.get("interrupt_grace_seconds", 15)))), post_turn_results=bool(agent.get("post_turn_results", False)))


from .codex_app_server import CodexAppServer, CodexProtocolError


class TelegramMediaUploadError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "uncertain"):
        super().__init__(message)
        self.kind = kind


class TelegramApiError(RuntimeError):
    """Falha sanitizada; kind só afirma recusa quando houve resposta conclusiva."""
    def __init__(self, message: str, *, kind: str = "uncertain", retry_after: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after if type(retry_after) is int and 0 < retry_after <= 2_147_483_647 else None


def _telegram_retry_after(payload: object) -> int | None:
    parameters = payload.get("parameters") if isinstance(payload, dict) else None
    value = parameters.get("retry_after") if isinstance(parameters, dict) else None
    return value if type(value) is int and 0 < value <= 2_147_483_647 else None


def _telegram_error_detail(error: Exception | str) -> str:
    """Expõe um diagnóstico operacional curto sem incluir credenciais ou URLs."""
    detail = str(error).replace("\n", " ").strip()
    detail = re.sub(r"https?://[^\s]+", "API do Telegram", detail)
    detail = re.sub(r"(?i)(token|password|secret)\s*[=:]\s*\S+", r"\1=[redacted]", detail)
    return detail[-300:] or "Falha sem detalhe retornado pela API do Telegram."


@dataclass
class AppServerTurn:
    chat_id: int
    thread_id: str
    completed: threading.Event
    turn_id: str | None = None
    status: str = "inProgress"
    failure_detail: str = ""
    final_text: str = ""
    staging: list[Path] = field(default_factory=list)
    reply_mode: str = "text"
    outbox: Path | None = None
    thought_ids: list[int] = field(default_factory=list)
    seen_thoughts: set[str] = field(default_factory=set)
    telegram_topic_values: dict[str, int] = field(default_factory=dict)
    conversation_id: str = ""
    reply_to_message_id: int | None = None
    source_trust: str = "UNTRUSTED_USER"
    sticker_only_sent: bool = False
    silent_completion: bool = False
    location_reply_context: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    generation: int = 0
    process_epoch: str = "legacy"
    execution_id: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    inbox_id: str | None = None
    operation_id: str | None = None
    final_item_id: str = "final"
    final_items: dict[str, str] = field(default_factory=dict)
    suppressed: bool = False
    delete_on_finish: bool | None = None
    terminal_announced: bool = False
    cancel_requested: bool = False
    user_cancelled: bool = False
    delivery_failed: bool = False
    tool_count: int = 0
    cleanup_lock: Any = field(default_factory=threading.RLock)
    output_futures: list[Any] = field(default_factory=list)
    progress_failed: bool = False

    def __post_init__(self) -> None:
        if not self.conversation_id:
            self.conversation_id = str(self.chat_id)


@dataclass
class ConversationExecution:
    identifier: str
    conversation_id: str
    generation: int
    inbox: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    active: AppServerTurn | None = None
    cancel_requested: bool = False


@dataclass
class MenuSession:
    token: str
    chat_id: int
    message_id: int
    options: list[dict[str, str]]
    completed: threading.Event = field(default_factory=threading.Event)
    selected: dict[str, str] | None = None
    conversation_id: str = ""
    generation: int = 0
    execution_id: str = ""
    turn_id: str | None = None
    invalidated: bool = False


class McpVault:
    """Adaptador do gateway para o cofre interno compartilhado pelo MCP."""

    def __init__(self, settings: Settings): self.settings, self.resolver = settings, KeePassCredentialResolver(settings.root)
    def read(self, entry: str, field: str = "password") -> str:
        try: return self.resolver.resolve(self.settings.vault_profile, entry, field)
        except CredentialError as error: raise RuntimeError("O cofre MCP não disponibilizou a credencial solicitada.") from error
    def list_totp(self) -> list[str]:
        try: return self.resolver.list_totp(self.settings.vault_profile)
        except CredentialError as error: raise RuntimeError("O cofre MCP não disponibilizou as entradas TOTP.") from error


class TelegramApi:
    def __init__(self, token: str): self.base, self.files = f"https://api.telegram.org/bot{token}/", f"https://api.telegram.org/file/bot{token}/"
    def call(self, method: str, values: dict[str, Any] | None = None, timeout: float = 45) -> Any:
        body = urlencode({key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in (values or {}).items()}).encode()
        try:
            with urlopen(Request(self.base + method, data=body), timeout=timeout) as response: payload = json.loads(response.read())
        except HTTPError as error:
            response = {}
            try:
                response = json.loads(error.read().decode("utf-8", errors="replace"))
                detail = str(response.get("description") or f"HTTP {error.code}") if isinstance(response, dict) else f"HTTP {error.code}"
            except Exception:
                detail = f"HTTP {error.code}"
            raise TelegramApiError(f"Telegram recusou {method}: {detail[:240]}", kind="not_sent" if 400 <= error.code < 500 and error.code != 408 else "uncertain", retry_after=_telegram_retry_after(response)) from error
        except (OSError, ValueError) as error:
            raise TelegramApiError(f"Não foi possível concluir {method}: {_telegram_error_detail(error)}") from error
        if not isinstance(payload, dict): raise TelegramApiError(f"Telegram retornou uma resposta inválida ao executar {method}.")
        if not payload.get("ok"):
            detail = str(payload.get("description") or "resposta sem descrição")
            raise TelegramApiError(f"Telegram recusou {method}: {detail[:240]}", kind="not_sent", retry_after=_telegram_retry_after(payload))
        return payload.get("result")
    def answer_callback(self, callback_id: str) -> None:
        """Confirma o clique rapidamente para não deixar o botão em estado pendente."""
        self.call("answerCallbackQuery", {"callback_query_id": callback_id}, timeout=8)
    def send(self, chat_id: int, text: str, **values: Any) -> dict[str, Any]: return self.call("sendMessage", {"chat_id": chat_id, "text": text, **values})
    def typing(self, chat_id: int, **values: Any) -> None: self.call("sendChatAction", {"chat_id": chat_id, "action": "typing", **values})
    def delete(self, chat_id: int, message_id: int) -> None: self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    def edit(self, chat_id: int, message_id: int, text: str, keyboard: dict[str, Any]) -> None: self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": keyboard})
    def download(self, file_id: str, destination: Path, maximum: int) -> Path:
        metadata = self.call("getFile", {"file_id": file_id}) or {}; declared = metadata.get("file_size")
        if isinstance(declared, int) and declared > maximum: raise RuntimeError("O anexo excede o limite configurado.")
        relative = metadata.get("file_path")
        if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")) or ".." in Path(relative).parts: raise RuntimeError("Telegram não retornou um caminho de arquivo válido.")
        destination.parent.mkdir(parents=True, exist_ok=True); written = 0
        try:
            with urlopen(Request(self.files + relative), timeout=45) as response, destination.open("xb") as output:
                while chunk := response.read(64 * 1024):
                    written += len(chunk)
                    if written > maximum: raise RuntimeError("O anexo excede o limite configurado.")
                    output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True); raise
        return destination
    def send_file(self, method: str, chat_id: int, path: Path, field: str, caption: str = "", reply_to: int | None = None, **extra: Any) -> dict[str, Any]:
        boundary = "----Onmyoji" + secrets.token_hex(16); chunks: list[bytes] = []
        values = {"chat_id": str(chat_id), "protect_content": "true"}
        if caption: values["caption"] = caption[:1024]
        if reply_to: values["reply_parameters"] = json.dumps({"message_id": reply_to})
        for key, value in extra.items():
            if value is not None: values[key] = str(value)
        for key, value in values.items(): chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(), value.encode("utf-8"), b"\r\n"))
        chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'.encode(), b"Content-Type: application/octet-stream\r\n\r\n", path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()))
        request = Request(self.base + method, data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urlopen(request, timeout=90) as response: payload = json.loads(response.read())
        except HTTPError as error:
            try:
                body = json.loads(error.read().decode("utf-8", errors="replace")); detail = str(body.get("description") or "resposta sem descrição") if isinstance(body, dict) else "resposta inválida"
            except Exception:
                detail = "resposta sem descrição legível"
            raise TelegramMediaUploadError(f"Telegram API HTTP {error.code} ao executar {method}: {detail[:300]}", kind="not_sent" if 400 <= error.code < 500 and error.code != 408 else "uncertain") from error
        except URLError as error:
            raise TelegramMediaUploadError(f"Não foi possível alcançar a API do Telegram ao executar {method}: {str(error.reason)[:300]}") from error
        except (OSError, ValueError) as error:
            raise TelegramMediaUploadError(f"Não foi possível enviar a mídia pelo Telegram ao executar {method}: {str(error)[:300]}") from error
        if not payload.get("ok"):
            detail = str(payload.get("description") or "resposta sem descrição") if isinstance(payload, dict) else "resposta inválida"
            raise TelegramMediaUploadError(f"Telegram API recusou {method}: {detail[:300]}", kind="not_sent")
        return payload.get("result") if isinstance(payload.get("result"), dict) else {}
    def set_other_private_commands(self, *, deadline: float | None = None) -> bool:
        """Publica somente /new e /resolver no escopo de todas as DMs."""
        deadline = time.monotonic() + COMMAND_SYNC_BUDGET_SECONDS if deadline is None else deadline
        scope = {"type": "all_private_chats"}
        target = {"scope": scope}
        commands = other_private_commands()

        def request(method: str, values: dict[str, Any]) -> Any:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TelegramApiError("A atualização dos comandos privados excedeu o tempo disponível.")
            return self.call(method, values, timeout=min(8, remaining))

        if not command_catalog_matches(request("getMyCommands", target), commands):
            if request("setMyCommands", {**target, "commands": commands}) is not True:
                return False
            if not command_catalog_matches(request("getMyCommands", target), commands):
                return False
        return True

    def set_owner_commands(self, owner_id: int, totp_enabled: bool, language_codes: tuple[str, ...] = (), *, deadline: float | None = None) -> bool:
        """Reconcilia o catálogo e o botão somente no chat privado deste owner."""
        commands = owner_commands(totp_enabled)
        scope = {"type": "chat", "chat_id": owner_id}
        deadline = time.monotonic() + COMMAND_SYNC_BUDGET_SECONDS if deadline is None else deadline

        def request(method: str, values: dict[str, Any]) -> Any:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TelegramApiError("A atualização dos comandos excedeu o tempo disponível.")
            return self.call(method, values, timeout=min(8, remaining))

        languages = ("", *sorted({code for value in language_codes if (code := normalize_language_code(value))}))
        for language in languages:
            target = {"scope": scope, "language_code": language}
            if not command_catalog_matches(request("getMyCommands", target), commands):
                if request("setMyCommands", {**target, "commands": commands}) is not True:
                    return False
                if not command_catalog_matches(request("getMyCommands", target), commands):
                    return False
        menu = {"type": "commands"}
        if request("getChatMenuButton", {"chat_id": owner_id}) != menu:
            if request("setChatMenuButton", {"chat_id": owner_id, "menu_button": menu}) is not True:
                return False
            if request("getChatMenuButton", {"chat_id": owner_id}) != menu:
                return False
        return True


class Contacts:
    def __init__(self, path: Path): self.path = path; self.lock = threading.RLock(); self.path.parent.mkdir(parents=True, exist_ok=True)
    def owners(self) -> set[int]:
        data = json_file(self.path, {"contacts": []}); return {int(item["telegram_user_id"]) for item in data.get("contacts", []) if isinstance(item, dict) and "owner" in item.get("roles", []) and str(item.get("telegram_user_id", "")).isdigit()}
    def add_owner(self, user: dict[str, Any]) -> None:
        with self.lock:
            data = json_file(self.path, {"version": 1, "contacts": []}); contacts = data.setdefault("contacts", [])
            if not any(item.get("telegram_user_id") == user.get("id") for item in contacts if isinstance(item, dict)):
                contacts.append({"telegram_user_id": user.get("id"), "username": user.get("username"), "display_name": user.get("first_name"), "roles": ["owner"], "source": "pair"})
                write_json(self.path, data)
        self.observe_owner_language(user)

    def owner_languages(self, owner_id: int) -> tuple[str, ...]:
        data = json_file(self.path, {"contacts": []})
        for item in data.get("contacts", []):
            if isinstance(item, dict) and item.get("telegram_user_id") == owner_id and "owner" in item.get("roles", []):
                previous = item.get("command_languages")
                values = previous if isinstance(previous, list) else []
                return tuple(sorted({code for value in [item.get("language_code"), *values] if (code := normalize_language_code(value))}))
        return ()

    def observe_owner_language(self, user: dict[str, Any]) -> bool:
        language = normalize_language_code(user.get("language_code"))
        if not language:
            return False
        with self.lock:
            data = json_file(self.path, {"version": 1, "contacts": []})
            for item in data.get("contacts", []):
                if not isinstance(item, dict) or item.get("telegram_user_id") != user.get("id") or "owner" not in item.get("roles", []):
                    continue
                known = self.owner_languages(user["id"])
                if item.get("language_code") == language and language in known:
                    return False
                item["language_code"] = language
                item["command_languages"] = sorted({*known, language})
                write_json(self.path, data)
                return True
        return False


def safe_name(value: str, fallback: str) -> str:
    name = re.sub(r"[^\w.() -]+", "_", Path(value).name, flags=re.UNICODE).strip(" .")
    return (name or fallback)[:180]


def under(path: Path, root: Path) -> bool:
    try: path.resolve().relative_to(root.resolve()); return True
    except ValueError: return False


class Gateway(TelegramLogin, NotificationIngress, TelegramExecution, TelegramChannels):
    def __init__(self, settings: Settings):
        lease = GatewayStore.open_lease(settings.state_dir / "gateway-writer.lock")
        try:
            self._initialize_persistence(settings)
        except Exception:
            database = getattr(self, "database", None)
            if database is not None:
                database.close()
            raise
        finally:
            lease.close()
        self.deliveries = GatewayDelivery(self.store, self._generation, can_send=self._delivery_authorized)
        self.capabilities = CapabilitySet()
        self._recovery_delivery_lock = threading.Lock()
        self._status_lock = threading.RLock()
        self.control_state_lock = threading.RLock()
        self._client_start_lock = threading.RLock()
        self._commands_sync_lock = threading.Lock()
        self._commands_refresh_needed = threading.Event()
        self._commands_next_sync = 0.0
        self._commands_retry_not_before = 0.0
        self._commands_verified_owners: dict[int, tuple[str, tuple[str, ...]]] = {}
        self._commands_other_private_verified = False
        self._commands_owner_attempts: dict[int, int] = {}
        self._commands_attempt_sequence = 0
        self._commands_cycle_complete = False
        self._commands_cycle_started: float | None = None
        self._execution_local = threading.local()
        self._input_local = threading.local()
        self._tool_local = threading.local()
        self._slots: dict[str, ConversationExecution] = {}
        self._uncertain_permits: dict[str, str] = {}
        self._recorded_process_epoch: str | None = None
        self._generation_cleanup: set[tuple[str, int]] = set()
        self._control_limit = threading.BoundedSemaphore(16)
        self._preparation_limit = threading.BoundedSemaphore(settings.preparation_workers)
        self._ingress_workers: set[str] = set()
        self._private_updates: dict[str, dict[str, Any]] = {}
        self._totp_intents: dict[str, str] = {}
        self.identity = identity(settings.root); self.instructions = InstructionComposer(settings.root, settings.data_dir, settings.instructions_file, settings.instructions_enabled)
        self.activity_root = settings.project / ".onmyoji" / "telegram"; self.archive_root = self.activity_root / "attachments"; self.staging_root = self.activity_root / "staging"
        self.archive_root.mkdir(parents=True, exist_ok=True); self.staging_root.mkdir(parents=True, exist_ok=True)
        self.api: TelegramApi | None = None; self.bot_id: int | None = None; self.pair: tuple[str, float] | None = None; self.offset = 0; self.work = threading.BoundedSemaphore(settings.parallel); self.totp_sessions: dict[int, dict[str, Any]] = {}; self.config_sessions: dict[str, dict[str, Any]] = {}; self.config_lock = threading.RLock(); self.menu_sessions: dict[str, MenuSession] = {}; self.channel_identify_sessions: dict[str, dict[str, Any]] = {}; self.menu_lock = threading.RLock(); self.last_error: str | None = None; self.cleanup_path = settings.state_dir / "totp-cleanup.json"; self.pending_deletions = json_file(self.cleanup_path, {}); self.app_server: CodexAppServer | None = None; self.app_server_lock = threading.RLock(); self.app_turns: dict[str, AppServerTurn] = {}; self.last_app_activity = time.monotonic(); self.turn_locks: dict[str, threading.Lock] = {}; self.turn_locks_lock = threading.Lock(); self.workers: set[str] = set(); self.workers_lock = threading.Lock()
        self.workers_changed = threading.Condition(self.workers_lock)
        self._initialize_login()
        self.related_work = RelatedWorkMonitor(self.store, lambda: self.capabilities, lambda: self.app_server, self._generation, self._enqueue_related_result, enabled=settings.post_turn_results, timeout_seconds=settings.turn_timeout, diagnostic=self._diagnostic)
        self._init_notifications()

    def _initialize_persistence(self, settings: Settings) -> None:
        """Migra sob posse exclusiva; nenhum cliente externo foi aberto ainda."""
        self.settings, self.stop_event = settings, threading.Event(); self.contacts = Contacts(settings.data_dir / "contacts.json")
        settings.state_dir.mkdir(parents=True, exist_ok=True); self.database = sqlite3.connect(settings.state_dir / "gateway.sqlite3", check_same_thread=False); self.database_lock = threading.RLock()
        GatewayStore.validate_schema(self.database)
        protect_legacy_gateway(settings.root, telegram_configuration_path(settings.root))
        self.database.execute("CREATE TABLE IF NOT EXISTS conversations(chat_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL, share_thoughts INTEGER NOT NULL DEFAULT 1, delete_thoughts INTEGER NOT NULL DEFAULT 1, codex_thread_id TEXT, instruction_baseline_hash TEXT)")
        for column, definition in (("share_thoughts", "INTEGER NOT NULL DEFAULT 1"), ("delete_thoughts", "INTEGER NOT NULL DEFAULT 1"), ("codex_thread_id", "TEXT"), ("instruction_baseline_hash", "TEXT"), ("reply_mode", "TEXT NOT NULL DEFAULT 'text'"), ("voice_expires_at", "REAL"), ("last_interaction_at", "REAL NOT NULL DEFAULT 0"), ("settings_revision", "INTEGER NOT NULL DEFAULT 0"), ("owner_model", "TEXT"), ("owner_effort", "TEXT"), ("conversation_uuid", "TEXT"), ("context_max_messages", "INTEGER"), ("context_max_age_seconds", "INTEGER"), ("context_compaction_message_count", "INTEGER NOT NULL DEFAULT 0")):
            try: self.database.execute(f"ALTER TABLE conversations ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError: pass
        self.database.execute("CREATE TABLE IF NOT EXISTS conversation_attachments(id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, generation INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, mime_type TEXT, size INTEGER NOT NULL, archive_path TEXT NOT NULL, created_at REAL NOT NULL)")
        self.database.execute("CREATE INDEX IF NOT EXISTS attachment_scope ON conversation_attachments(chat_id, generation, created_at)")
        self.database.execute("CREATE TABLE IF NOT EXISTS inbox_items(id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, generation INTEGER NOT NULL, text TEXT NOT NULL, attachment_ids TEXT NOT NULL, turn_context TEXT NOT NULL DEFAULT '{}', structured_content TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL, created_at REAL NOT NULL)")
        try: self.database.execute("ALTER TABLE inbox_items ADD COLUMN turn_context TEXT NOT NULL DEFAULT '{}'")
        except sqlite3.OperationalError: pass
        self.database.execute("CREATE INDEX IF NOT EXISTS inbox_scope ON inbox_items(chat_id, generation, state, created_at)")
        self.database.execute("CREATE TABLE IF NOT EXISTS outbound_intents(id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, generation INTEGER NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, telegram_message_id INTEGER, created_at REAL NOT NULL, completed_at REAL)")
        self.database.execute("CREATE TABLE IF NOT EXISTS conversation_messages(id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, generation INTEGER NOT NULL, telegram_message_id INTEGER, sender_id INTEGER, sender_name TEXT, trust TEXT NOT NULL, text TEXT NOT NULL, attachment_ids TEXT NOT NULL, structured_content TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL)")
        for table in ("conversation_messages",):
            if "structured_content" not in {row[1] for row in self.database.execute(f"PRAGMA table_info({table})")}:
                self.database.execute(f"ALTER TABLE {table} ADD COLUMN structured_content TEXT NOT NULL DEFAULT '{{}}'")
        self.database.execute("CREATE INDEX IF NOT EXISTS message_scope ON conversation_messages(chat_id, generation, created_at)")
        self.database.execute("CREATE TABLE IF NOT EXISTS bot_messages(chat_id TEXT NOT NULL, generation INTEGER NOT NULL, telegram_message_id INTEGER NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(chat_id, generation, telegram_message_id))")
        self.database.commit()
        self._init_channel_tables(); self._init_external_table()
        self.store = GatewayStore(self.database, self.database_lock)

    @staticmethod
    def _conversation_id(chat: dict[str, Any], message: dict[str, Any]) -> str:
        chat_id = int(chat["id"])
        thread = message.get("message_thread_id")
        if chat.get("type") == "private":
            direct_topic = message.get("direct_messages_topic")
            direct_id = direct_topic.get("topic_id") if isinstance(direct_topic, dict) else None
            if isinstance(direct_id, int): return f"direct:{chat_id}:{direct_id}"
            return f"private:{chat_id}:{int(thread)}" if isinstance(thread, int) else str(chat_id)
        return f"group:{chat_id}:{int(thread) if isinstance(thread, int) else 0}"

    @staticmethod
    def _telegram_address(conversation_id: str) -> tuple[int, dict[str, int]]:
        if not conversation_id.startswith(("group:", "private:", "direct:")): return int(conversation_id), {}
        _, chat_id, thread = conversation_id.split(":", 2)
        if conversation_id.startswith("direct:"): return int(chat_id), {"direct_messages_topic_id": int(thread)}
        return int(chat_id), ({"message_thread_id": int(thread)} if int(thread) else {})

    def _ensure_conversation(self, conversation_id: str) -> str:
        now = time.time()
        with self.database_lock:
            self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at, share_thoughts, delete_thoughts, conversation_uuid) VALUES (?, ?, ?, ?, ?)", (conversation_id, now, self.settings.default_share_thoughts, self.settings.default_delete_thoughts, secrets.token_urlsafe(18)))
            row = self.database.execute("SELECT conversation_uuid FROM conversations WHERE chat_id=?", (conversation_id,)).fetchone()
            if not row or not row[0]:
                value = secrets.token_urlsafe(18); self.database.execute("UPDATE conversations SET conversation_uuid=? WHERE chat_id=?", (value, conversation_id)); self.database.commit(); return value
            self.database.commit(); return str(row[0])

    def _trust(self, sender: dict[str, Any]) -> str:
        return "OWNER" if isinstance(sender.get("id"), int) and sender["id"] in self.contacts.owners() else "UNTRUSTED_USER"
    def _totp_enabled(self) -> bool:
        return self.settings.totp_enabled
    def _record_error(self, error: Exception | str) -> None:
        kind = error.reason if isinstance(error, ProcessRecoveryError) else getattr(error, "kind", "operational_failure")
        code = kind if isinstance(kind, str) and re.fullmatch(r"[a-z_]{1,64}", kind) else "operational_failure"
        self.last_error = "Falha operacional registrada; confira a etapa e o código de diagnóstico."
        with self._status_lock:
            state = json_file(self.settings.state_dir / "gateway-status.json", {})
            now = time.time()
            state.update({"last_error": self.last_error, "last_error_code": code, "last_error_at": now, "updated_at": now})
            write_json(self.settings.state_dir / "gateway-status.json", state)

    def _stickers(self):
        """Lê o catálogo atual sem manter caminhos ou IDs remotos em cache."""
        return load_sticker_catalog(self.settings.root, self.settings.max_outbound_media_bytes)
    def _diagnostic(self, event: str, **values: Any) -> None:
        allowed = {"generation", "size", "count", "duration_ms", "elapsed_ms", "stage", "state", "route", "method", "kind", "source", "delivery", "action", "reason", "code", "execution_id", "input_id", "work_id"}
        safe = {key: value for key, value in values.items() if key in allowed and (isinstance(value, (int, float, bool)) or isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", value))}
        if "conversation" in values:
            safe["conversation_hash"] = hashlib.sha256(str(values["conversation"]).encode()).hexdigest()[:20]
        event = event if re.fullmatch(r"[a-z_.]{1,96}", event) else "gateway.event"
        with self._status_lock:
            state = json_file(self.settings.state_dir / "gateway-status.json", {})
            entries = state.get("diagnostics")
            entries = entries if isinstance(entries, list) else []
            entries.append({"at": time.time(), "event": event, **safe})
            state["diagnostics"] = entries[-100:]
            counters = state.setdefault("counters", {})
            counters[event] = int(counters.get(event, 0)) + 1
            write_json(self.settings.state_dir / "gateway-status.json", state)

    def _request_command_sync(self) -> None:
        """Sinaliza reconciliação sem fazer I/O de rede no recebimento de updates."""
        with self._status_lock:
            self._commands_refresh_needed.set()
            state = json_file(self.settings.state_dir / "gateway-status.json", {})
            commands = state.get("commands")
            if isinstance(commands, dict):
                commands["pending"] = True
                write_json(self.settings.state_dir / "gateway-status.json", state)

    def _observe_command_language(self, user: dict[str, Any]) -> None:
        if self.contacts.observe_owner_language(user):
            self._request_command_sync()

    def _configure_owner_commands(self) -> dict[str, Any]:
        """Confirma catálogo e botão; serializa tentativas e respeita flood control."""
        if not self._commands_sync_lock.acquire(blocking=False):
            with self._status_lock:
                state = json_file(self.settings.state_dir / "gateway-status.json", {}).get("commands", {})
                return {**(state if isinstance(state, dict) else {}), "pending": True, "in_progress": True}
        try:
            with self._status_lock:
                if time.monotonic() < self._commands_retry_not_before:
                    state = json_file(self.settings.state_dir / "gateway-status.json", {}).get("commands", {})
                    return {**(state if isinstance(state, dict) else {}), "pending": True, "in_progress": False}
                self._commands_refresh_needed.clear()
                owners = sorted(self.contacts.owners())
                totp = self._totp_enabled()
                revision = command_catalog_revision(totp)
                languages = {owner: self.contacts.owner_languages(owner) for owner in owners}
                targets = {owner: (revision, languages[owner]) for owner in owners}
                # Retoma ciclos parciais; um ciclo concluído exige verificação nova.
                now = time.monotonic()
                if (self._commands_cycle_complete or not self.api or self._commands_cycle_started is None
                        or now - self._commands_cycle_started >= COMMAND_REFRESH_SECONDS):
                    self._commands_verified_owners.clear()
                    self._commands_other_private_verified = False
                    self._commands_cycle_started = now
                self._commands_cycle_complete = False
                self._commands_verified_owners = {
                    owner: target for owner, target in self._commands_verified_owners.items()
                    if targets.get(owner) == target
                }
                self._commands_owner_attempts = {
                    owner: attempt for owner, attempt in self._commands_owner_attempts.items() if owner in targets
                }
                pending_owners = sorted(
                    (owner for owner in owners if owner not in self._commands_verified_owners),
                    key=lambda owner: (self._commands_owner_attempts.get(owner, 0), owner),
                )
                summary = {"updated_at": time.time(), "owners": len(owners), "verified": len(self._commands_verified_owners), "failed": 0,
                           "expected": [item["command"] for item in owner_commands(totp)],
                           "expected_other_private": [item["command"] for item in other_private_commands()],
                           "other_private_verified": self._commands_other_private_verified,
                           "catalog_revision": revision, "totp_enabled": totp,
                           "pending": True, "in_progress": True, "next_retry_at": None}
                state = json_file(self.settings.state_dir / "gateway-status.json", {})
                state["commands"] = summary
                write_json(self.settings.state_dir / "gateway-status.json", state)
            deadline = time.monotonic() + COMMAND_SYNC_BUDGET_SECONDS
            for owner in pending_owners:
                if not self.api or time.monotonic() >= deadline:
                    break
                self._commands_attempt_sequence += 1
                self._commands_owner_attempts[owner] = self._commands_attempt_sequence
                try:
                    if self.api.set_owner_commands(owner, totp, languages[owner], deadline=deadline):
                        self._commands_verified_owners[owner] = targets[owner]
                    else:
                        self._record_error("Telegram não confirmou os comandos privados do owner.")
                except Exception as error:
                    self._record_error(error)
                    retry_after = getattr(error, "retry_after", None)
                    if type(retry_after) is int and retry_after > 0:
                        self._commands_retry_not_before = time.monotonic() + retry_after
                        break
            if (self.api and not self._commands_other_private_verified
                    and time.monotonic() < deadline
                    and time.monotonic() >= self._commands_retry_not_before):
                try:
                    if self.api.set_other_private_commands(deadline=deadline):
                        self._commands_other_private_verified = True
                    else:
                        self._record_error("Telegram não confirmou o menu privado dos demais usuários.")
                except Exception as error:
                    self._record_error(error)
                    retry_after = getattr(error, "retry_after", None)
                    if type(retry_after) is int and retry_after > 0:
                        self._commands_retry_not_before = time.monotonic() + retry_after
            summary["verified"] = len(self._commands_verified_owners)
            summary["failed"] = len(owners) - summary["verified"]
            summary["other_private_verified"] = self._commands_other_private_verified
            now = time.monotonic()
            pending = bool(summary["failed"] or not owners or not self._commands_other_private_verified
                           or self._commands_refresh_needed.is_set())
            delay = COMMAND_RETRY_SECONDS if pending else COMMAND_REFRESH_SECONDS
            delay = max(delay, self._commands_retry_not_before - now)
            self._commands_next_sync = now + delay
            with self._status_lock:
                summary.update(updated_at=time.time(), in_progress=False,
                               pending=pending,
                               next_retry_at=time.time() + delay if pending else None)
                self._commands_cycle_complete = not summary["pending"]
                state = json_file(self.settings.state_dir / "gateway-status.json", {})
                state["commands"] = summary
                write_json(self.settings.state_dir / "gateway-status.json", state)
            return summary
        finally:
            self._commands_sync_lock.release()

    def _owner_commands_watch(self) -> None:
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now >= self._commands_retry_not_before and (self._commands_refresh_needed.is_set() or now >= self._commands_next_sync):
                try:
                    self._configure_owner_commands()
                except Exception as error:
                    self._record_error(error)
                    self._commands_refresh_needed.clear()
                    self._commands_next_sync = time.monotonic() + COMMAND_RETRY_SECONDS
            self.stop_event.wait(1)
    def start(self) -> None:
        self.store.acquire_lease(self.settings.state_dir / "gateway-writer.lock")
        try:
            receiver = self.notification_configuration.load()["telegram_receiver_enabled"]
            if self.settings.vault_profile and self.settings.token_entry:
                try:
                    self.api = TelegramApi(McpVault(self.settings).read(self.settings.token_entry))
                    identity_info = self.api.call("getMe")
                except Exception:
                    self.api = None
                    if receiver:
                        raise
                    identity_info = {}
            else:
                identity_info = {}
            self.bot_id = identity_info.get("id") if isinstance(identity_info, dict) and type(identity_info.get("id")) is int else None
            self.bot_username = str(identity_info.get("username") or "") if isinstance(identity_info, dict) else ""
            if self.api:
                self._restore_totp_cleanup()
                self._request_command_sync()
            self._start_execution_state()
            if self.api:
                threading.Thread(target=self._owner_commands_watch, daemon=True).start()
            threading.Thread(target=self._notification_watch, daemon=True).start()
            self._start_notification_http()
            if receiver:
                threading.Thread(target=self._poll, daemon=True).start()
            if self.settings.app_server_enabled:
                threading.Thread(target=self._app_server_idle_watch, daemon=True).start()
        except Exception:
            self.store.release_lease()
            raise

    def _channel_state(self, chat_id: int, renew: bool = False) -> dict[str, Any]:
        now = time.time()
        with self.database_lock:
            self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at, share_thoughts, delete_thoughts, last_interaction_at) VALUES (?, ?, ?, ?, ?)", (str(chat_id), now, self.settings.default_share_thoughts, self.settings.default_delete_thoughts, now))
            row = self.database.execute("SELECT reply_mode, voice_expires_at, settings_revision FROM conversations WHERE chat_id=?", (str(chat_id),)).fetchone()
            mode, expiry, revision = str(row[0] or "text"), row[1], int(row[2] or 0)
            expired = mode == "audio" and (not expiry or float(expiry) <= now)
            if expired: mode, expiry = "text", None; self.database.execute("UPDATE conversations SET reply_mode='text', voice_expires_at=NULL, settings_revision=settings_revision+1 WHERE chat_id=?", (str(chat_id),)); revision += 1
            if renew: self.database.execute("UPDATE conversations SET last_interaction_at=?, voice_expires_at=? WHERE chat_id=?", (now, now + self.settings.voice_auto_off_minutes * 60 if mode == "audio" else expiry, str(chat_id)))
            self.database.commit()
        return {"reply_mode": mode, "voice_expires_at": expiry, "settings_revision": revision, "expired": expired, "voice_available": self.settings.voice_enabled}

    def _set_reply_mode(self, chat_id: int, mode: str) -> dict[str, Any]:
        if mode not in {"text", "audio"}: raise RuntimeError("Modo de resposta inválido.")
        if mode == "audio" and not self.settings.voice_enabled: raise RuntimeError("Respostas em áudio não estão configuradas para esta instância.")
        now = time.time(); expiry = now + self.settings.voice_auto_off_minutes * 60 if mode == "audio" else None
        with self.database_lock: self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at, share_thoughts, delete_thoughts, last_interaction_at) VALUES (?, ?, ?, ?, ?)", (str(chat_id), now, self.settings.default_share_thoughts, self.settings.default_delete_thoughts, now)); self.database.execute("UPDATE conversations SET reply_mode=?, voice_expires_at=?, settings_revision=settings_revision+1, updated_at=? WHERE chat_id=?", (mode, expiry, now, str(chat_id))); self.database.commit()
        return self._channel_state(chat_id)

    def _outbox(self, chat_id: int, turn_id: str) -> Path:
        value = self.staging_root / "outbox" / self._ensure_conversation(str(chat_id)) / turn_id
        value.mkdir(parents=True, exist_ok=True); return value

    def _send_agent_text(self, chat_id: int, text: str, **values: Any) -> list[dict[str, Any]]:
        """Envia a resposta do agente em HTML seguro, preservando todo o texto."""
        if not self.api:
            raise RuntimeError("Gateway Telegram indisponível.")
        rendered = render_html(text)
        return [self.api.send(chat_id, chunk, parse_mode="HTML", **values) for chunk in split_html(rendered, MAX_MESSAGE_LENGTH)]

    def _tts(self, text: str, output: Path) -> None:
        if not self.settings.voice_enabled: raise RuntimeError("Resposta em áudio não está disponível.")
        try:
            EccoVoxService(self.settings.root).synthesize(self.settings.voice_profile, text[:self.settings.voice_max_text], str(output), self.settings.voice_format, voice=self.settings.voice_name or None, language=self.settings.voice_language or None, speed=self.settings.voice_speed)
        except EccoVoxError as error:
            raise RuntimeError(str(error)) from error
        if not output.is_file() or output.is_symlink(): raise RuntimeError("EccoVox não gerou o áudio.")

    def _generation(self, chat_id: int) -> int:
        with self.database_lock:
            self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at, share_thoughts, delete_thoughts) VALUES (?, ?, ?, ?)", (str(chat_id), time.time(), self.settings.default_share_thoughts, self.settings.default_delete_thoughts))
            row = self.database.execute("SELECT generation FROM conversations WHERE chat_id=?", (str(chat_id),)).fetchone(); self.database.commit()
        return int(row[0]) if row else 0

    def _attachment_rows(self, chat_id: int, limit: int = 50) -> list[dict[str, Any]]:
        generation = self._generation(chat_id)
        with self.database_lock:
            rows = self.database.execute("SELECT id, kind, name, mime_type, size, archive_path, created_at, transcription FROM conversation_attachments WHERE chat_id=? AND generation=? ORDER BY created_at DESC LIMIT ?", (str(chat_id), generation, max(1, min(limit, 50)))).fetchall()
        return [{"id": str(row[0]), "kind": str(row[1]), "name": str(row[2]), "mime_type": row[3], "size": int(row[4]), "archive_path": str(row[5]), "created_at": float(row[6]), "transcription": json.loads(row[7]) if row[7] else None} for row in rows]

    def _remove_attachments(self, chat_id: int, generation: int) -> None:
        with self.database_lock:
            rows = self.database.execute("SELECT archive_path FROM conversation_attachments WHERE chat_id=? AND generation=?", (str(chat_id), generation)).fetchall()
            self.database.execute("DELETE FROM conversation_attachments WHERE chat_id=? AND generation=?", (str(chat_id), generation)); self.database.commit()
        for row in rows:
            path = Path(str(row[0]))
            if under(path, self.archive_root): shutil.rmtree(path.parent, ignore_errors=True)

    def _collect_attachments(self, chat_id: int, message: dict[str, Any]) -> list[dict[str, Any]]:
        assert self.api
        candidates: list[tuple[str, dict[str, Any], str]] = []
        if isinstance(message.get("photo"), list) and message["photo"]:
            photo = message["photo"][-1]
            if isinstance(photo, dict): candidates.append(("photo", photo, f"photo-{message.get('message_id', 'telegram')}.jpg"))
        if isinstance(message.get("document"), dict):
            document = message["document"]; candidates.append(("document", document, safe_name(str(document.get("file_name") or "document"), "document")))
        if isinstance(message.get("voice"), dict):
            voice = message["voice"]; candidates.append(("voice", voice, f"voice-{message.get('message_id', 'telegram')}.ogg"))
        if isinstance(message.get("audio"), dict):
            audio = message["audio"]; candidates.append(("audio", audio, safe_name(str(audio.get("file_name") or "audio.m4a"), "audio.m4a")))
        declared = sum(int(item.get("file_size") or 0) for _, item, _ in candidates)
        if declared > self.settings.max_batch_attachment_bytes: raise RuntimeError("O conjunto de anexos excede o limite configurado.")
        result: list[dict[str, Any]] = []; generation = getattr(self._input_local, "generation", None); generation = self._generation(chat_id) if generation is None else generation; conversation_uuid = self._ensure_conversation(str(chat_id))
        for kind, item, filename in candidates:
            file_id = item.get("file_id")
            if not isinstance(file_id, str) or not file_id: raise RuntimeError("Metadados do anexo não contêm um identificador válido.")
            attachment_id, incoming = secrets.token_urlsafe(12), self.staging_root / "incoming" / secrets.token_urlsafe(10) / safe_name(filename, "attachment")
            self.api.download(file_id, incoming, self.settings.max_attachment_bytes)
            if generation != self._generation(chat_id):
                shutil.rmtree(incoming.parent, ignore_errors=True); raise RuntimeError("Conversa reiniciada durante o download.")
            size = incoming.stat().st_size
            if sum(entry["size"] for entry in result) + size > self.settings.max_batch_attachment_bytes: incoming.unlink(missing_ok=True); raise RuntimeError("O conjunto de anexos excede o limite configurado.")
            with self.database_lock:
                total = self.database.execute("SELECT COALESCE(SUM(size), 0) FROM conversation_attachments WHERE chat_id=? AND generation=?", (str(chat_id), generation)).fetchone()[0]
            if int(total) + size > self.settings.max_retained_attachment_bytes: incoming.unlink(missing_ok=True); raise RuntimeError("A retenção de anexos desta conversa atingiu o limite configurado.")
            transcription = None
            if kind == "voice":
                transcript = self._transcribe_voice(incoming)
                transcription = {"status": "available", "text": transcript, "automatic": True} if transcript else {"status": "unavailable", "automatic": True}
            if generation != self._generation(chat_id):
                shutil.rmtree(incoming.parent, ignore_errors=True); raise RuntimeError("Conversa reiniciada durante a preparação da voz.")
            archive = self.archive_root / conversation_uuid / str(generation) / attachment_id / safe_name(filename, "attachment"); archive.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(incoming), str(archive)); shutil.rmtree(incoming.parent, ignore_errors=True)
            record = {"id": attachment_id, "kind": kind, "name": archive.name, "mime_type": str(item.get("mime_type") or ""), "size": size, "archive_path": str(archive), "created_at": time.time(), "transcription": transcription}
            with self.database_lock:
                current = generation == self._generation(chat_id)
                if current:
                    self.database.execute("INSERT INTO conversation_attachments(id, chat_id, generation, kind, name, mime_type, size, archive_path, created_at, transcription) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (attachment_id, str(chat_id), generation, kind, record["name"], record["mime_type"], size, str(archive), record["created_at"], json.dumps(transcription, ensure_ascii=False) if transcription else None)); self.database.commit()
            if not current:
                shutil.rmtree(archive.parent, ignore_errors=True)
                raise RuntimeError("Conversa reiniciada durante a retenção do anexo.")
            result.append(record)
        return result

    def _stage_attachment(self, chat_id: int, attachment: dict[str, Any], turn: AppServerTurn) -> Path:
        candidate = Path(str(attachment["archive_path"]))
        for component in (candidate, *candidate.parents):
            if component.is_symlink() or component.exists() and getattr(component.lstat(), "st_file_attributes", 0) & 0x400:
                raise RuntimeError("Links e reparse points não são aceitos para anexos.")
            if component == self.archive_root:
                break
        source = candidate.resolve()
        if not under(source, self.archive_root) or not source.is_file(): raise RuntimeError("O anexo retido não está disponível com segurança.")
        if turn.conversation_id != str(chat_id): raise RuntimeError("Tentativa de acessar anexo de outra conversa.")
        destination = self.staging_root / self._ensure_conversation(str(chat_id)) / turn.execution_id / secrets.token_urlsafe(8) / safe_name(str(attachment["name"]), "attachment")
        destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination); turn.staging.append(destination)
        return destination

    def _context_limits(self, conversation_id: str) -> tuple[int, int]:
        self._ensure_conversation(conversation_id)
        with self.database_lock:
            row = self.database.execute("SELECT context_max_messages, context_max_age_seconds FROM conversations WHERE chat_id=?", (conversation_id,)).fetchone()
        count = self.settings.context_max_messages if not row or row[0] is None else int(row[0])
        age = self.settings.context_max_age_seconds if not row or row[1] is None else int(row[1])
        return count, age

    @staticmethod
    def _conversation_kind(conversation_id: str) -> str:
        prefix = str(conversation_id).split(":", 1)[0]
        return {"private": "private", "group": "group", "forum": "forum_topic", "direct": "direct_topic"}.get(prefix, "private")

    def _turn_context(self, conversation_id: str, sender: dict[str, Any], text: str, attachments: list[dict[str, Any]], *, contains_untrusted_context: bool = False, is_context_snapshot: bool = False, has_location: bool = False) -> dict[str, Any]:
        context = {
            "conversation_kind": self._conversation_kind(conversation_id),
            "source_trust": self._trust(sender),
            "has_text": bool(text.strip()),
            "attachment_kinds": list(dict.fromkeys(str(item.get("kind") or "") for item in attachments if str(item.get("kind") or "") in {"photo", "document", "voice", "audio"})),
            "contains_untrusted_context": contains_untrusted_context,
            "is_context_snapshot": is_context_snapshot,
        }
        if has_location: context["has_location"] = True
        return context

    def _structured_location_content(self, value: object) -> dict[str, Any]:
        """Recupera somente conteúdo geográfico estático válido, sem expor dados no diagnóstico."""
        try:
            data = json.loads(value) if isinstance(value, str) else value
            if not isinstance(data, dict): raise ValueError("Invalid structured content")
            if not data: return {}
            if "location" not in data: return {}
            result: dict[str, Any] = {"location": normalize_stored_location(data["location"])}
            message_id = data.get("message_id")
            if isinstance(message_id, int) and not isinstance(message_id, bool) and message_id > 0:
                result["message_id"] = message_id
            timestamp = data.get("timestamp")
            if isinstance(timestamp, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
                try: time.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
                except ValueError: pass
                else: result["timestamp"] = timestamp
            return result
        except (TypeError, ValueError, OverflowError):
            try: self._diagnostic("location.content_discarded", reason="invalid_structured_content")
            except Exception: pass
            return {}

    def _cache_message(self, conversation_id: str, message: dict[str, Any], sender: dict[str, Any], text: str, attachments: list[dict[str, Any]], structured_content: dict[str, Any] | None = None) -> None:
        generation = getattr(self._input_local, "generation", None)
        generation = self._generation(conversation_id) if generation is None else generation
        self._ensure_conversation(conversation_id)
        message_id = message.get("message_id")
        if not isinstance(message_id, int): return
        created_at = float(message.get("date") or time.time())
        with self.database_lock:
            if generation != self._generation(conversation_id): return
            self.database.execute("INSERT OR IGNORE INTO conversation_messages(id, chat_id, generation, telegram_message_id, sender_id, sender_name, trust, text, attachment_ids, structured_content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (secrets.token_urlsafe(12), conversation_id, generation, message_id, sender.get("id") if isinstance(sender.get("id"), int) else None, str(sender.get("username") or sender.get("first_name") or "unknown"), self._trust(sender), text, json.dumps([item["id"] for item in attachments]), json.dumps(self._structured_location_content(structured_content or {}), ensure_ascii=False, allow_nan=False), created_at))
            self.database.commit()

    def _context_snapshot(self, conversation_id: str) -> tuple[str, list[dict[str, Any]], dict[str, bool]]:
        generation = self._generation(conversation_id); maximum, age = self._context_limits(conversation_id); now = time.time()
        with self.database_lock:
            all_rows = self.database.execute("SELECT telegram_message_id, sender_name, trust, text, attachment_ids, created_at, structured_content FROM conversation_messages WHERE chat_id=? AND generation=? ORDER BY created_at, rowid", (conversation_id, generation)).fetchall()
        filtered = [row for row in all_rows if not age or float(row[5]) >= now - age]
        selected = filtered[-maximum:] if maximum else filtered
        def item_for(row: tuple[Any, ...]) -> dict[str, Any]:
            item = {"message_id": row[0], "sender": row[1], "trust": row[2], "text": row[3], "attachment_ids": json.loads(str(row[4])), "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(row[5])))}
            structured = self._structured_location_content(row[6])
            if "location" in structured: item["location"] = structured["location"]
            return item
        bounded_rows: list[tuple[Any, ...]] = []
        bounded_items: list[dict[str, Any]] = []
        for row in reversed(selected):
            candidate = item_for(row)
            projected = {"conversation_context": {"included_messages": [candidate, *bounded_items]}}
            if len(json.dumps(projected, ensure_ascii=False, allow_nan=False).encode("utf-8")) > self.settings.context_max_bytes:
                continue
            bounded_rows.insert(0, row); bounded_items.insert(0, candidate)
        selected = bounded_rows
        dropped = len(all_rows) - len(selected)
        reasons = [name for name, enabled in (("max_age", bool(age and any(float(row[5]) < now - age for row in all_rows))), ("max_messages", bool(maximum and len(filtered) > maximum)), ("max_bytes", len(selected) < len(filtered[-maximum:] if maximum else filtered))) if enabled]
        payload = {"conversation_context": {"included_messages": bounded_items, "context_window": {"included_messages": len(selected), "dropped_messages": dropped, "cutoff_reasons": reasons}}}
        attachments = {item["id"]: item for item in self._attachment_rows(conversation_id, 50)}
        wanted = [attachment_id for row in selected for attachment_id in json.loads(str(row[4]))]
        summary = {"contains_untrusted_context": any(str(row[2]) != "OWNER" for row in selected)}
        if any("location" in item for item in payload["conversation_context"]["included_messages"]): summary["has_location"] = True
        return json.dumps(payload, ensure_ascii=False, allow_nan=False), [attachments[item] for item in dict.fromkeys(wanted) if item in attachments], summary



    def _send_active_location(self, active: AppServerTurn, arguments: dict[str, Any]) -> dict[str, Any]:
        """Envia um ponto fixo somente ao turno autorizado, sem registrar geodados em diagnósticos.

        Falhas retornam ausência de entrega confirmada. Depois do recibo Telegram,
        falha no registro local mantém sucesso com aviso para evitar reenvio.
        """
        def failed(stage: str, error: str) -> dict[str, Any]:
            try: self._diagnostic("location.send_failed", conversation=active.conversation_id, stage=stage)
            except Exception: pass
            return {"sent": False, "stage": stage, "error": error}

        if not self.settings.agent_outbound_media:
            return failed("capability", "Envio de mídia pelo agente está desabilitado.")
        if active.source_trust not in {"OWNER", "AUTHORIZED_USER"} or active.completed.is_set() or active.status != "inProgress" or active.suppressed or active.cancel_requested:
            return failed("authorization", "Somente um turno ativo de owner ou usuário autorizado pode enviar localização.")
        generation = self._generation(active.conversation_id)
        if active.generation != generation:
            return failed("authorization", "O contexto de resposta expirou após o reinício da conversa.")
        if set(arguments) - {"latitude", "longitude", "horizontal_accuracy"}:
            return failed("argument_validation", "Os dados de localização contêm campos não permitidos.")
        try:
            location = normalize_location(arguments.get("latitude"), arguments.get("longitude"), arguments.get("horizontal_accuracy", OMITTED))
        except LocationValidationError as error:
            return failed("argument_validation", str(error))
        if not self.api:
            return failed("gateway", "Gateway Telegram indisponível.")
        values = {key: value for key, value in location.items() if key != "kind"}
        invalid_receipt = False
        def send() -> dict[str, Any]:
            nonlocal invalid_receipt
            result = self.api.call("sendLocation", {"chat_id": active.chat_id, **active.telegram_topic_values, **values})
            message = result.get("message_id") if isinstance(result, dict) else None
            if isinstance(message, bool) or not isinstance(message, int) or message <= 0:
                invalid_receipt = True
                raise DeliveryUncertain("Telegram não confirmou o identificador da localização.")
            return result
        try:
            sent = self._tool_effect(active, "media", {"location": location}, send)
        except DeliverySuppressed:
            return failed("authorization", "O contexto de resposta expirou antes do envio.")
        except Exception:
            response = failed("receipt_validation" if invalid_receipt else "telegram_api", "Não foi possível confirmar o envio da localização. Ele pode ter ocorrido; não repita automaticamente.")
            return {**response, "uncertain": True, "retry_safe": False}
        message_id = sent["message_id"]
        response: dict[str, Any] = {"sent": True, "message_id": message_id}
        try:
            with self.database_lock:
                self.database.execute("INSERT OR IGNORE INTO bot_messages(chat_id, generation, telegram_message_id, created_at) VALUES (?, ?, ?, ?)", (active.conversation_id, generation, message_id, time.time()))
                self.database.commit()
        except Exception:
            response["warning"] = "Telegram confirmou o envio, mas não foi possível preservar integralmente o contexto local."
        try: self._diagnostic("location.sent", conversation=active.conversation_id, message_id=message_id)
        except Exception: pass
        return response

    def _reply_send_location(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resolve a capacidade efêmera de uma thread antiga sem aceitar um destino arbitrário.

        O token é emitido somente pela ferramenta da conversa ativa e deixa de
        funcionar ao terminar o turno. Nunca autoriza envio para outro contexto.
        """
        token = params.get("reply_context")
        if not isinstance(token, str) or not token.isascii() or not 1 <= len(token) <= 128:
            return {"sent": False, "stage": "authorization", "error": "Contexto de resposta inválido ou expirado."}
        with self.app_server_lock:
            active = next((item for item in self.app_turns.values() if secrets.compare_digest(item.location_reply_context, token)), None)
        if active is None:
            return {"sent": False, "stage": "authorization", "error": "Contexto de resposta inválido ou expirado."}
        return self._send_active_location(active, {key: value for key, value in params.items() if key != "reply_context"})

    def _app_server_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "item/tool/call": raise RuntimeError("Ferramenta do gateway não autorizada.")
        if params.get("namespace") == "onmyoji_gateway":
            active = self._gateway_tool_active(params)
            if params.get("tool") != "complete_silently" or params.get("arguments") not in ({}, None):
                raise RuntimeError("Chamada de conclusão silenciosa inválida.")
            active.silent_completion = True
            with self.database_lock, self.database:
                self.database.execute("UPDATE execution_turns SET silent_completion=1 WHERE execution_id=?", (active.execution_id,))
            self._diagnostic("event.silent_completion_requested", execution_id=active.execution_id)
            return {"contentItems": [{"type": "inputText", "text": json.dumps({"completed_silently": True})}], "success": True}
        if params.get("namespace") != "telegram_gateway": raise RuntimeError("Ferramenta do gateway não autorizada.")
        thread_id, tool = params.get("threadId"), params.get("tool")
        if not isinstance(thread_id, str) or not isinstance(tool, str): raise RuntimeError("Chamada de ferramenta inválida.")
        active = self._tool_active(params)
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if tool == "get_channel_state":
            state = self._channel_state(active.conversation_id)
            generation = self._generation(active.conversation_id)
            if self.settings.agent_outbound_media and active.source_trust in {"OWNER", "AUTHORIZED_USER"} and not active.completed.is_set() and active.status == "inProgress" and not active.suppressed and not active.cancel_requested and active.generation == generation:
                state["location_reply_context"] = active.location_reply_context
            return {"contentItems": [{"type": "inputText", "text": json.dumps(state, ensure_ascii=False)}], "success": True}
        if tool == "send_location":
            return {"contentItems": [{"type": "inputText", "text": json.dumps(self._send_active_location(active, arguments), ensure_ascii=False, allow_nan=False)}], "success": True}
        if tool == "set_reply_mode":
            state = self._set_reply_mode(active.conversation_id, str(arguments.get("mode") or "")); active.reply_mode = state["reply_mode"]
            return {"contentItems": [{"type": "inputText", "text": json.dumps(state, ensure_ascii=False)}], "success": True}
        if tool == "send_message":
            text, ttl = arguments.get("text"), arguments.get("ttl_seconds", 0)
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000: raise RuntimeError("text deve ter entre 1 e 4000 caracteres.")
            if not isinstance(ttl, int) or isinstance(ttl, bool) or not 0 <= ttl <= 86400: raise RuntimeError("ttl_seconds deve estar entre 0 e 86400.")
            event = getattr(self._tool_local, "event", None) or {}
            request = event.get("rpc_id", event.get("request_id", secrets.token_urlsafe(18)))
            if active.cancel_requested or active.suppressed: raise RuntimeError("Turno cancelado antes deste envio.")
            try:
                sent = self.deliveries.text(self._owner(active), f"rpc:{active.process_epoch}:{request}:message", "additional", str(request), self.api, active.chat_id, text, protect_content=bool(arguments.get("protect_content", False)), **active.telegram_topic_values)
            except DeliveryUncertain:
                active.delivery_failed = True
                return {"contentItems": [{"type": "inputText", "text": json.dumps({"sent": None, "uncertain": True, "retry_safe": False, "reason": "confirmation_missing"})}], "success": True}
            if ttl:
                for item in sent:
                    message_id = item.get("message_id")
                    if isinstance(message_id, int): self._schedule_delete(active.chat_id, message_id, ttl)
            return {"contentItems": [{"type": "inputText", "text": json.dumps({"sent": True, "messages": len(sent), "ephemeral": bool(ttl)}, ensure_ascii=False)}], "success": True}
        if tool == "ask_menu":
            if self._conversation_kind(active.conversation_id) != "private": raise RuntimeError("Menus interativos estão disponíveis somente em conversas privadas ativas.")
            question, options, timeout = arguments.get("question"), arguments.get("options"), arguments.get("timeout_seconds", 120)
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000: raise RuntimeError("question deve ter entre 1 e 4000 caracteres.")
            if not isinstance(options, list) or not 2 <= len(options) <= 20 or not isinstance(timeout, int) or not 10 <= timeout <= 300: raise RuntimeError("Menu inválido.")
            normalized, ids = [], set()
            for option in options:
                if not isinstance(option, dict) or not isinstance(option.get("id"), str) or not isinstance(option.get("label"), str) or not 1 <= len(option["id"]) <= 64 or not 1 <= len(option["label"]) <= 64 or option["id"] in ids: raise RuntimeError("Cada opção exige id e label únicos de até 64 caracteres.")
                ids.add(option["id"]); normalized.append({"id": option["id"], "label": option["label"]})
            token = secrets.token_urlsafe(12); keyboard = {"inline_keyboard": [[{"text": option["label"], "callback_data": f"ag:{token}:{index}"}] for index, option in enumerate(normalized)]}
            rendered = render_html(question)
            if len(rendered) > MAX_MESSAGE_LENGTH: raise RuntimeError("A pergunta do menu excede o limite de uma mensagem Telegram após a formatação.")
            if not self.api: raise RuntimeError("Gateway Telegram indisponível.")
            try:
                sent = self._tool_effect(active, "menu", {"question": rendered}, lambda: self.api.send(active.chat_id, rendered, parse_mode="HTML", reply_markup=keyboard, protect_content=bool(arguments.get("protect_content", False)), **active.telegram_topic_values))
            except DeliveryUncertain:
                return {"contentItems": [{"type": "inputText", "text": json.dumps({"sent": None, "selected": None, "uncertain": True, "retry_safe": False, "reason": "menu_confirmation_missing"})}], "success": True}
            message_id = sent.get("message_id")
            if not isinstance(message_id, int): raise RuntimeError("Telegram não retornou o identificador do menu.")
            session = MenuSession(token, active.chat_id, message_id, normalized, conversation_id=active.conversation_id, generation=active.generation, execution_id=active.execution_id, turn_id=active.turn_id)
            with self.menu_lock: self.menu_sessions[token] = session
            try:
                if not session.completed.wait(timeout): raise RuntimeError("O menu expirou sem seleção.")
                if session.invalidated or active.suppressed or active.generation != self._generation(active.conversation_id): raise RuntimeError("O menu foi cancelado com o turno ou a conversa.")
                return {"contentItems": [{"type": "inputText", "text": json.dumps({"selected": session.selected}, ensure_ascii=False)}], "success": True}
            finally:
                with self.menu_lock: self.menu_sessions.pop(token, None)
                try: self.api.delete(active.chat_id, message_id)
                except Exception: pass
        if tool == "get_outbox":
            if not self.settings.agent_outbound_media: raise RuntimeError("Envio de mídia pelo agente está desabilitado.")
            active.outbox = active.outbox or self._outbox(active.conversation_id, active.execution_id)
            return {"contentItems": [{"type": "inputText", "text": json.dumps({"outbox": str(active.outbox)}, ensure_ascii=False)}], "success": True}
        if tool == "list_stickers":
            if not self.settings.agent_outbound_media: raise RuntimeError("Envio de mídia pelo agente está desabilitado.")
            if active.source_trust not in {"OWNER", "AUTHORIZED_USER"}: raise RuntimeError("Somente owner ou usuário autorizado pode consultar figurinhas.")
            catalog = self._stickers()
            payload = [{"id": sticker.identifier, "context": sticker.context} for sticker in catalog.stickers]
            return {"contentItems": [{"type": "inputText", "text": json.dumps(payload, ensure_ascii=False)}], "success": True}
        if tool == "send_sticker":
            def failed(stage: str, error: Exception | str) -> dict[str, Any]:
                self._record_error(error)
                self._diagnostic("sticker.send_failed", conversation=active.conversation_id, sticker_id=str(arguments.get("sticker_id") or "")[:64], stage=stage, error=self.last_error)
                return {"contentItems": [{"type": "inputText", "text": json.dumps({"sent": None if isinstance(error, DeliveryUncertain) else False, "uncertain": isinstance(error, DeliveryUncertain), "retry_safe": False, "stage": stage, "error": self.last_error}, ensure_ascii=False)}], "success": True}
            if not self.settings.agent_outbound_media: return failed("capability", "Envio de mídia pelo agente está desabilitado.")
            if active.source_trust not in {"OWNER", "AUTHORIZED_USER"}: return failed("authorization", "Somente owner ou usuário autorizado pode enviar figurinha.")
            identifier = str(arguments.get("sticker_id") or "")
            delivery = str(arguments.get("delivery") or "supplement")
            if delivery not in {"supplement", "only"}: return failed("argument_validation", "delivery deve ser supplement ou only.")
            sticker = next((item for item in self._stickers().stickers if item.identifier == identifier), None)
            if sticker is None: return failed("catalog", "Figurinha desconhecida ou indisponível no catálogo.")
            if not self.api: return failed("gateway", "Gateway Telegram indisponível.")
            self._diagnostic("sticker.send_started", conversation=active.conversation_id, sticker_id=sticker.identifier, file_name=sticker.path.name, size=sticker.path.stat().st_size, method="sendSticker")
            try:
                sent = self._tool_effect(active, "sticker", {"sticker_id": identifier}, lambda: self.api.send_file("sendSticker", active.chat_id, sticker.path, "sticker", **active.telegram_topic_values))
            except Exception as error:
                return failed("telegram_api", error)
            active.sticker_only_sent = delivery == "only"
            self._diagnostic("sticker.sent", conversation=active.conversation_id, sticker_id=sticker.identifier, file_name=sticker.path.name, size=sticker.path.stat().st_size, method="sendSticker", delivery=delivery, message_id=sent.get("message_id"))
            return {"contentItems": [{"type": "inputText", "text": json.dumps({"sent": True, "message_id": sent.get("message_id"), "delivery": delivery}, ensure_ascii=False)}], "success": True}
        if tool == "send_file":
            def failed(stage: str, error: Exception | str) -> dict[str, Any]:
                self._record_error(error)
                self._diagnostic("media.send_failed", conversation=active.conversation_id, kind=str(arguments.get("kind") or ""), file_name=str(arguments.get("file_name") or "")[:128], stage=stage, error=self.last_error)
                payload = {"sent": None if isinstance(error, DeliveryUncertain) else False, "uncertain": isinstance(error, DeliveryUncertain), "retry_safe": False, "stage": stage, "error": self.last_error}
                return {"contentItems": [{"type": "inputText", "text": json.dumps(payload, ensure_ascii=False)}], "success": True}
            if not self.settings.agent_outbound_media: return failed("capability", "Envio de mídia pelo agente está desabilitado.")
            direct = str(arguments.get("file_path") or "").strip()
            if direct:
                if active.source_trust not in {"OWNER", "AUTHORIZED_USER"}: return failed("authorization", "Somente owner ou usuário autorizado pode enviar arquivo por caminho direto.")
                try: path = authorized_file(direct, transfer_roots(self.settings.root), self.settings.max_outbound_media_bytes)
                except ArtifactPathError as error: return failed("input_validation", error)
                name = path.name
            else:
                active.outbox = active.outbox or self._outbox(active.conversation_id, active.execution_id); name = safe_name(str(arguments.get("file_name") or ""), "")
                path = (active.outbox / name).resolve()
                if not name or not under(path, active.outbox) or not path.is_file() or path.is_symlink() or path.stat().st_size > self.settings.max_outbound_media_bytes: return failed("outbox_validation", "Arquivo do outbox inválido ou excede o limite.")
            kind = str(arguments.get("kind") or "document")
            target = {"photo": ("sendPhoto", "photo"), "document": ("sendDocument", "document"), "audio": ("sendAudio", "audio")}.get(kind)
            if not target: return failed("argument_validation", "Tipo de mídia inválido.")
            method, field = target
            caption = render_html(str(arguments.get("caption") or ""))
            if len(caption) > 1024: return failed("caption_validation", "A legenda excede o limite do Telegram após a formatação.")
            if not self.api: return failed("gateway", "Gateway Telegram indisponível.")
            self._diagnostic("media.send_started", conversation=active.conversation_id, kind=kind, file_name=name, size=path.stat().st_size, method=method, source="direct" if direct else "outbox")
            try:
                result = self._tool_effect(active, "media", {"path": str(path), "kind": kind}, lambda: self.api.send_file(method, active.chat_id, path, field, caption, parse_mode="HTML", **active.telegram_topic_values))
            except Exception as error:
                return failed("telegram_api", error)
            self._diagnostic("media.sent", conversation=active.conversation_id, kind=kind, file_name=name, size=path.stat().st_size, method=method, message_id=result.get("message_id"), source="direct" if direct else "outbox")
            return {"contentItems": [{"type": "inputText", "text": json.dumps({"sent": True, "message_id": result.get("message_id")}, ensure_ascii=False)}], "success": True}
        if tool == "list_attachments":
            entries = [{key: entry[key] for key in ("id", "kind", "name", "mime_type", "size", "created_at")} for entry in self._attachment_rows(active.conversation_id, int(arguments.get("limit", 20)))]
            return {"contentItems": [{"type": "inputText", "text": json.dumps(entries, ensure_ascii=False)}], "success": True}
        if tool == "materialize_attachment":
            wanted = str(arguments.get("attachment_id") or "")
            entry = next((item for item in self._attachment_rows(active.conversation_id, 50) if item["id"] == wanted), None)
            if not entry: raise RuntimeError("Anexo não encontrado na conversa atual.")
            path = self._stage_attachment(active.conversation_id, entry, active)
            data = {key: entry[key] for key in ("id", "kind", "name", "mime_type", "size")}; data["local_path"] = str(path)
            return {"contentItems": [{"type": "inputText", "text": json.dumps(data, ensure_ascii=False)}], "success": True}
        raise RuntimeError("Ferramenta do gateway desconhecida.")


    @staticmethod
    def _turn_failure_detail(turn: dict[str, Any]) -> str:
        """Retém apenas o diagnóstico protocolar, sem input, instruções ou segredos do turno."""
        error = turn.get("error")
        parts: list[str] = []
        if isinstance(error, dict):
            for key in ("code", "type", "message"):
                value = error.get(key)
                if value not in {None, ""}: parts.append(f"{key}: {value}")
        elif isinstance(error, str) and error.strip(): parts.append(error)
        for key in ("errorMessage", "message"):
            value = turn.get(key)
            if isinstance(value, str) and value.strip(): parts.append(value)
        text = " · ".join(dict.fromkeys(parts)).replace("\n", " ")[:800]
        return re.sub(r"(?i)(token|password|secret)\s*[=:]\s*\S+", r"\1=[redacted]", text)


    @staticmethod
    def _thread_is_missing(error: CodexProtocolError) -> bool:
        """Distingue uma thread removida de falhas transitórias ao retomá-la."""
        detail = str(error).casefold()
        return any(fragment in detail for fragment in ("thread not found", "unknown thread", "no such thread", "thread does not exist", "invalid thread id"))

    def _app_thread(self, chat_id: int, client: CodexAppServer, turn_context: dict[str, Any] | None = None) -> str:
        notification = (turn_context or {}).get("notification")
        if notification and notification["response_channel"]["type"] == "email":
            return self._notification_thread(str(chat_id), client, notification)
        state = self._channel_state(chat_id); bundle = self.instructions.compose(identity=self.identity, telegram=True, reply_mode=str(state["reply_mode"]), outbound_media=self.settings.agent_outbound_media, telegram_context=TelegramTurnContext.from_value(turn_context))
        tool_contract = f"{bundle.baseline_hash}:telegram-tools-v4:{self.settings.busy_input_policy}:{int(self.capabilities.supports('steering'))}:{int(self.capabilities.supports('subagents'))}"
        self._execution_local.thread_contract = tool_contract
        generation = self._generation(chat_id)
        with self.database_lock:
            row = self.database.execute("SELECT codex_thread_id, instruction_baseline_hash FROM conversations WHERE chat_id=? AND generation=?", (str(chat_id), generation)).fetchone()
        thread_id = str(row[0]) if row and row[0] else ""
        if notification and notification["thread_policy"] == "new":
            thread_id = ""
        revised = bool(thread_id and str(row[1] or "") != tool_contract)
        params = {"cwd": str(self.settings.project), "approvalPolicy": self.settings.approval, "sandbox": self.settings.sandbox, "developerInstructions": bundle.text + self._channel_capability_instructions()}
        if notification:
            params["developerInstructions"] += "\n\nEm eventos, use onmyoji_gateway.complete_silently para concluir com sucesso sem enviar ao canal. Não produza resposta final vazia sem chamar essa ferramenta."
        model, _effort = self._execution_preferences(chat_id)
        if model: params["model"] = model
        if thread_id:
            try:
                resume = {"threadId": thread_id, **params}
                if notification:
                    resume["dynamicTools"] = self._notification_tools()
                client.request("thread/resume", resume)
                with self.database_lock:
                    if generation != self._generation(chat_id): raise DeliverySuppressed("Conversa reiniciada durante a retomada da thread.")
                    if revised:
                        self.database.execute("UPDATE conversations SET instruction_baseline_hash=?, updated_at=? WHERE chat_id=? AND generation=?", (tool_contract, time.time(), str(chat_id), generation)); self.database.commit()
                if revised:
                    self._diagnostic("thread.revision_updated", conversation=str(chat_id))
                return thread_id
            except CodexProtocolError as error:
                if not self._thread_is_missing(error):
                    self._diagnostic("thread.resume_failed", conversation=str(chat_id), reason="transient")
                    raise
                with self.database_lock:
                    if generation != self._generation(chat_id): raise DeliverySuppressed("Conversa reiniciada durante a verificação da thread.")
                    if not notification:
                        self.database.execute("UPDATE conversations SET codex_thread_id=NULL, instruction_baseline_hash=NULL, updated_at=? WHERE chat_id=? AND generation=?", (time.time(), str(chat_id), generation)); self.database.commit()
                self._diagnostic("thread.reset", conversation=str(chat_id), reason="resume_missing")
        tools = [{"type": "namespace", "name": "telegram_gateway", "description": "Interface restrita à DM Telegram ativa.", "tools": [{"type": "function", "name": "get_channel_state", "description": "Consulta o modo de resposta da conversa ativa.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}, {"type": "function", "name": "set_reply_mode", "description": "Define a resposta final como texto ou áudio nesta conversa.", "inputSchema": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["text", "audio"]}}, "required": ["mode"], "additionalProperties": False}}, {"type": "function", "name": "get_outbox", "description": "Reserva o diretório exclusivo deste turno para mídia recém-criada.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}, {"type": "function", "name": "send_file", "description": "Envia arquivo do outbox por file_name ou arquivo existente em raiz autorizada por file_path. Caminho direto exige owner autorizado.", "inputSchema": {"type": "object", "properties": {"file_name": {"type": "string"}, "file_path": {"type": "string"}, "kind": {"type": "string", "enum": ["photo", "document", "audio"]}, "caption": {"type": "string"}}, "required": ["kind"], "additionalProperties": False}}, {"type": "function", "name": "list_stickers", "description": "Lista IDs e contextos das figurinhas pré-cadastradas para a conversa ativa.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}, {"type": "function", "name": "send_sticker", "description": "Envia uma figurinha pré-cadastrada pelo seu ID, sem legenda.", "inputSchema": {"type": "object", "properties": {"sticker_id": {"type": "string", "minLength": 1, "maxLength": 64}}, "required": ["sticker_id"], "additionalProperties": False}}, {"type": "function", "name": "list_attachments", "description": "Lista os metadados dos anexos retidos na conversa atual.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}}, {"type": "function", "name": "materialize_attachment", "description": "Copia um anexo retido para o staging seguro do turno atual.", "inputSchema": {"type": "object", "properties": {"attachment_id": {"type": "string", "minLength": 1, "maxLength": 64}}, "required": ["attachment_id"], "additionalProperties": False}}]}]
        tools.extend(self._notification_tools())
        tools[0]["tools"].extend([{"type": "function", "name": "send_message", "description": "Envia mensagem adicional somente à DM Telegram ativa; pode expirar automaticamente.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 4000}, "ttl_seconds": {"type": "integer", "minimum": 0, "maximum": 86400}, "protect_content": {"type": "boolean"}}, "required": ["text"], "additionalProperties": False}}, {"type": "function", "name": "ask_menu", "description": "Exibe opções ao owner da DM ativa e aguarda a seleção com prazo limitado.", "inputSchema": {"type": "object", "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 4000}, "options": {"type": "array", "minItems": 2, "maxItems": 20, "items": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 64}, "label": {"type": "string", "minLength": 1, "maxLength": 64}}, "required": ["id", "label"], "additionalProperties": False}}, "timeout_seconds": {"type": "integer", "minimum": 10, "maximum": 300}, "protect_content": {"type": "boolean"}}, "required": ["question", "options"], "additionalProperties": False}}])
        tools[0]["description"] = "Interface restrita à conversa Telegram ativa. Cada ferramenta declara o escopo em que funciona."
        tools[0]["tools"].append({"type": "function", "name": "send_location", "description": "Envia um ponto geográfico fixo nativo somente à conversa ativa. Exige origem autorizada e mídia de saída habilitada; não acompanha localização ao vivo.", "inputSchema": {"type": "object", "properties": {"latitude": {"type": "number", "minimum": -90, "maximum": 90}, "longitude": {"type": "number", "minimum": -180, "maximum": 180}, "horizontal_accuracy": {"type": "number", "minimum": 0, "maximum": 1500, "description": "Raio de incerteza opcional em metros; omita quando desconhecido."}}, "required": ["latitude", "longitude"], "additionalProperties": False}})
        for tool in tools[0]["tools"]:
            if tool["name"] == "send_message":
                tool["description"] = "Envia mensagem adicional à conversa Telegram ativa; pode expirar automaticamente."
            elif tool["name"] == "send_sticker":
                tool["description"] = "Envia uma figurinha pré-cadastrada pelo ID, sem legenda. Use delivery=only somente quando ela for a resposta completa; o gateway não enviará texto final."
                tool["inputSchema"]["properties"]["delivery"] = {"type": "string", "enum": ["supplement", "only"], "default": "supplement"}
        if self._conversation_kind(str(chat_id)) != "private":
            tools[0]["tools"] = [tool for tool in tools[0]["tools"] if tool["name"] != "ask_menu"]
        started = client.request("thread/start", {**params, "serviceName": "onmyoji_telegram", "dynamicTools": tools}); thread = started.get("thread") if isinstance(started.get("thread"), dict) else started; thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
        if not thread_id: raise CodexProtocolError("Codex App Server não retornou uma thread")
        with self.database_lock:
            if generation != self._generation(chat_id): raise DeliverySuppressed("Conversa reiniciada durante a criação da thread.")
            if not notification:
                self.database.execute("UPDATE conversations SET codex_thread_id=?, instruction_baseline_hash=?, updated_at=? WHERE chat_id=? AND generation=?", (thread_id, tool_contract, time.time(), str(chat_id), generation)); self.database.commit()
        return thread_id

    def _app_sandbox_policy(self) -> dict[str, Any]:
        if self.settings.sandbox == "danger-full-access": return {"type": "dangerFullAccess"}
        if self.settings.sandbox == "read-only": return {"type": "readOnly", "networkAccess": False}
        roots = [str(self.settings.project)]
        try:
            system = tomllib.loads((self.settings.root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")); extra = system.get("codex", {}).get("additional_writable_directories", [])
            roots.extend(str(Path(value).resolve()) for value in extra if isinstance(value, str))
        except (OSError, tomllib.TOMLDecodeError, AttributeError): pass
        return {"type": "workspaceWrite", "writableRoots": list(dict.fromkeys(roots)), "networkAccess": False}

    def _transcribe_voice(self, path: Path) -> str | None:
        try:
            value = EccoVoxService(self.settings.root).transcribe(self.settings.voice_profile, str(path))
            return str(value.get("text") or "").strip() or None
        except Exception as error:
            self._record_error(f"Transcrição de voz indisponível: {error}"); return None

    def _usage(self, conversation_id: str) -> None:
        """Consulta a franquia da conta sem iniciar um turno nem expor dados de login."""
        generation = getattr(self._input_local, "generation", None)
        if generation is None:
            generation = self._generation(conversation_id)

        def reply(text: str, *, ephemeral: bool = False) -> None:
            if generation != self._generation(conversation_id):
                return
            telegram_chat_id, topic_values = self._telegram_address(conversation_id)
            if ephemeral:
                receipts = [self._ephemeral(conversation_id, text)]
            else:
                receipts = self._send_agent_text(telegram_chat_id, text, protect_content=True, **topic_values)
            if generation != self._generation(conversation_id):
                for receipt in receipts:
                    message_id = receipt.get("message_id") if isinstance(receipt, dict) else None
                    if isinstance(message_id, int) and self.api:
                        try: self.api.delete(telegram_chat_id, message_id)
                        except Exception: self._record_error("Não foi possível remover uma consulta de uso encerrada.")

        if generation != self._generation(conversation_id):
            return
        if not self.settings.app_server_enabled:
            reply("A consulta de consumo e franquia exige o App Server habilitado no setup do daemon.", ephemeral=True)
            return
        try:
            client = self._app_client()
            try:
                account_state = client.request("account/read", {"refreshToken": False}, timeout=25)
            except CodexProtocolError as error:
                if not error.definitive_rejection or error.rpc_code != -32601:
                    raise
                account_state = {}
            account = account_state.get("account")
            account_type = account.get("type") if isinstance(account, dict) else None
            if isinstance(account_type, str) and account_type in {"apiKey", "amazonBedrock"}:
                mode = "chave de API" if account_type == "apiKey" else "Amazon Bedrock"
                reply(f"O Codex desta instância usa {mode}. A franquia do plano ChatGPT não está disponível nesse modo.")
                return
            if "account" in account_state and account is None:
                if account_state.get("requiresOpenaiAuth") is True:
                    reply("Não há uma conta ChatGPT conectada ao Codex desta instância. Faça login no Codex pelo setup e consulte /usage novamente.")
                    return
                if account_state.get("requiresOpenaiAuth") is False:
                    reply("O provedor configurado no Codex não disponibiliza uma franquia ChatGPT por esta consulta.")
                    return
            response = client.request("account/rateLimits/read", {}, timeout=25)
            output = format_rate_limits(response)
        except CodexProtocolError as error:
            self._record_error(error)
            if error.definitive_rejection and error.rpc_code in {-32600, -32601}:
                reply("O Codex instalado não reconheceu a consulta de franquia. Atualize o Codex pelo setup e tente /usage novamente.", ephemeral=True)
            else:
                reply("Não foi possível consultar o consumo e a franquia do Codex agora. Tente novamente.", ephemeral=True)
            return
        except (CodexExecutionError, ProcessRecoveryError, OSError) as error:
            self._record_error(error)
            reply("Não foi possível consultar o consumo e a franquia do Codex agora. Tente novamente.", ephemeral=True)
            return
        reply(output)

    def _update(self, update: dict[str, Any]) -> None:
        self._input_local.route = "auto"
        if update.get("callback_query"): self._callback(update["callback_query"]); return
        message = update.get("message") or update.get("edited_message") or {}; chat, sender = message.get("chat") or {}, message.get("from") or {}
        if chat.get("type") not in {"private", "group", "supergroup"} or not isinstance(sender.get("id"), int): return
        self._observe_command_language(sender)
        private = chat.get("type") == "private"
        if not isinstance(chat.get("id"), int):
            if not private: return
            chat = {**chat, "id": sender["id"]}; message = {**message, "chat": chat}
        conversation_id = self._conversation_id(chat, message)
        topic_event = message.get("forum_topic_created") or message.get("forum_topic_edited")
        topic_name = topic_event.get("name") if isinstance(topic_event, dict) else ""
        chat_name = str(chat.get("title") or sender.get("first_name") or sender.get("username") or "").strip()
        if chat_name or topic_name:
            self._remember_channel_label(conversation_id, chat_name, str(topic_name or ""))
        text = str(message.get("text") or message.get("caption") or "").strip(); assert self.api
        identified_generation = None
        with self.database_lock:
            generation = self._generation(conversation_id)
            with self.control_state_lock:
                identify = self.channel_identify_sessions.get(conversation_id)
                if identify and (identify.get("generation") != generation or time.time() > float(identify["expires_at"])):
                    self.channel_identify_sessions.pop(conversation_id, None)
                elif identify and sender.get("id") == identify["owner_id"] and text and not text.startswith("/"):
                    incoming_generation = getattr(self._input_local, "generation", None)
                    if incoming_generation is not None and incoming_generation != generation: return
                    self._channel_set_alias(conversation_id, text)
                    self.channel_identify_sessions.pop(conversation_id, None)
                    identified_generation = generation
        if identified_generation is not None:
            target_chat, target_topic = self._telegram_address(conversation_id)
            if identified_generation == self._generation(conversation_id):
                sent = self.api.send(target_chat, "Nome visual atualizado.", **target_topic)
                identifier = sent.get("message_id") if isinstance(sent, dict) else None
                if identified_generation != self._generation(conversation_id) and isinstance(identifier, int):
                    try: self.api.delete(target_chat, identifier)
                    except Exception: self._record_error("Não foi possível remover um aviso de identificação encerrado.")
            return
        if not private:
            self._group_message(conversation_id, chat, sender, message, text)
            return
        command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold() if text else ""
        if command == "/channel":
            self._delete_received_command(int(chat["id"]), message)
            if sender["id"] in self.contacts.owners():
                self._channel_menu(conversation_id, chat, sender)
            return
        if text.startswith("/"): self._delete_received_command(int(chat.get("id") or sender["id"]), message)
        if int(chat["id"]) != sender["id"]:
            return
        if command == "/pair":
            arguments = text.split(maxsplit=1)
            if len(arguments) == 2:
                with self.control_state_lock:
                    pair = self.pair
                    paired = bool(pair and time.time() < pair[1] and secrets.compare_digest(arguments[1].strip(), pair[0]))
                    if paired:
                        self.pair = None  # Consumo atômico: um PIN não pode parear dois usuários.
                if paired:
                    self.contacts.add_owner(sender)
                    self._request_command_sync()
                    synced = self._configure_owner_commands()
                    telegram_chat_id, topic_values = self._telegram_address(conversation_id)
                    self.api.send(telegram_chat_id, "Pairing concluído e comandos privados configurados." if command_sync_is_current(synced, self._totp_enabled()) else "Pairing concluído; a atualização dos comandos privados está pendente. Consulte o diagnóstico.", **topic_values)
            return
        is_owner = sender["id"] in self.contacts.owners()
        if not is_owner and command not in {"/new", "/resolver"}:
            return
        channel = self._channel_state(conversation_id, renew=True)
        if channel["expired"]: self._ephemeral(conversation_id, "O modo de respostas em áudio foi desligado por inatividade.")
        telegram_chat_id, topic_values = self._telegram_address(conversation_id)
        if command == "/new": self._new_conversation(conversation_id); self.api.send(telegram_chat_id, "Conversa reiniciada.", **topic_values); return
        if command == "/cancel":
            cancelled = self._cancel_conversation(conversation_id)
            self._ephemeral(conversation_id, "Cancelamento solicitado; a fila foi preservada." if cancelled else "Não há turno ativo para cancelar."); return
        if command == "/resolver":
            self._recovery_control(conversation_id, text[len(text.split(maxsplit=1)[0]):].strip().casefold()); return
        if command == "/usage":
            self._usage(conversation_id); return
        if command == "/login":
            self._login(conversation_id, text[len(text.split(maxsplit=1)[0]):].strip()); return
        if conversation_id in self.totp_sessions:
            if text.startswith("/"):
                self._clear_totp_session(conversation_id); self._ephemeral(conversation_id, "Fluxo TOTP cancelado por um novo comando.")
            else: self._totp_password(conversation_id, message)
            return
        command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold() if text else ""
        if command == "/totp":
            if self._totp_enabled(): self._start_totp(conversation_id, message, text[len(text.split(maxsplit=1)[0]):].strip())
            else: self._ephemeral(conversation_id, "TOTP não está habilitado para este Shikigami. Configure-o no setup do Gateway Telegram.")
            return
        if command == "/config": self._open_config(conversation_id); return
        if command in {"/fila", "/direcionar"}:
            self._input_local.route = "queue" if command == "/fila" else "steer"
            text = text[len(text.split(maxsplit=1)[0]):].strip()
            if not text:
                self._ephemeral(conversation_id, "Use /fila <texto> ou /direcionar <texto>, com anexos na mesma mensagem quando necessário."); return
            if command == "/direcionar" and not self.capabilities.supports("steering"):
                self._ephemeral(conversation_id, "Direcionamento não certificado neste runtime; a solicitação entrará na fila.")
        if text.startswith("/") and command not in {"/fila", "/direcionar"}: self._ephemeral(conversation_id, invalid_command_message(self._totp_enabled())); return
        structured: dict[str, Any] = {}
        location_error = ""
        try:
            location = parse_message_location(message)
            if location is not None:
                structured["location"] = location
                message_id, timestamp = message.get("message_id"), message.get("date")
                if isinstance(message_id, int) and not isinstance(message_id, bool) and message_id > 0: structured["message_id"] = message_id
                if isinstance(timestamp, int) and not isinstance(timestamp, bool) and timestamp >= 0:
                    try: structured["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))
                    except (ValueError, OverflowError, OSError): pass
        except UnsupportedLocationError:
            location_error = "Localização ao vivo não é suportada. Envie um ponto fixo."
        except LocationValidationError:
            location_error = "A localização recebida é inválida; envie novamente."
        if location_error: self._ephemeral(conversation_id, location_error)
        contains_attachment = any(isinstance(message.get(name), (dict, list)) and message.get(name) for name in ("photo", "document", "voice", "audio"))
        if not text and not contains_attachment and not structured and (location_error or SERVICE_MESSAGE_FIELDS.intersection(message)): return
        if not text and not contains_attachment and not structured: self._ephemeral(conversation_id, "Mensagem sem texto ou anexo reconhecido."); return
        if contains_attachment and not self.settings.app_server_enabled:
            self._ephemeral(conversation_id, "Anexos exigem que o App Server esteja habilitado na configuração do Gateway Telegram."); return
        try: attachments = self._collect_attachments(conversation_id, message) if contains_attachment else []
        except Exception as error:
            self._record_error(error); self._ephemeral(conversation_id, f"Não foi possível receber o anexo: {error}"); return
        try:
            context = self._turn_context(conversation_id, sender, text, attachments, has_location=bool(structured))
            if structured: self._enqueue_turn(conversation_id, text, attachments, context, structured)
            else: self._enqueue_turn(conversation_id, text, attachments, context)
        except Exception as error: self._record_error(error); self._ephemeral(conversation_id, f"Não foi possível enfileirar a mensagem: {error}")

    def _group_message(self, conversation_id: str, chat: dict[str, Any], sender: dict[str, Any], message: dict[str, Any], text: str) -> None:
        """Caches every delivered group item but only routes explicit authorized invocations."""
        assert self.api
        if text.startswith("/"):
            self._delete_received_command(int(chat["id"]), message)
            if text.split(maxsplit=1)[0].split("@", 1)[0].casefold() == "/channel":
                if sender.get("id") in self.contacts.owners():
                    self._channel_menu(conversation_id, chat, sender)
                return
            if sender.get("id") in self.contacts.owners() and text.split(maxsplit=1)[0].split("@", 1)[0].casefold() == "/new":
                self._new_conversation(conversation_id)
                values = {"message_thread_id": message["message_thread_id"]} if isinstance(message.get("message_thread_id"), int) else {}
                self.api.send(int(chat["id"]), "Conversa reiniciada.", **values)
            if sender.get("id") in self.contacts.owners() and text.split(maxsplit=1)[0].split("@", 1)[0].casefold() == "/config":
                values = text.split()[1:]
                if len(values) > 2 or any(not value.isdigit() for value in values): return
                maximum, age = self._context_limits(conversation_id)
                if values: maximum = int(values[0])
                if len(values) == 2: age = int(values[1])
                with self.database_lock:
                    self.database.execute("UPDATE conversations SET context_max_messages=?, context_max_age_seconds=?, updated_at=? WHERE chat_id=?", (maximum, age, time.time(), conversation_id)); self.database.commit()
                thread_values = {"message_thread_id": message["message_thread_id"]} if isinstance(message.get("message_thread_id"), int) else {}
                self.api.send(int(chat["id"]), f"Janela deste canal: {maximum} mensagens; {age} segundos.", **thread_values)
            if sender.get("id") in self.contacts.owners() and text.split(maxsplit=1)[0].split("@", 1)[0].casefold() == "/resolver":
                argument = text[len(text.split(maxsplit=1)[0]):].strip().casefold()
                self._recovery_control(conversation_id, argument)
            return
        attachments: list[dict[str, Any]] = []
        structured: dict[str, Any] = {}
        location_error = ""
        try:
            location = parse_message_location(message)
            if location is not None: structured["location"] = location
        except UnsupportedLocationError:
            location_error = "Localização ao vivo não é suportada. Envie um ponto fixo."
        except LocationValidationError:
            location_error = "A localização recebida é inválida; envie novamente."
        has_attachment = any(isinstance(message.get(name), (dict, list)) and message.get(name) for name in ("photo", "document", "voice", "audio"))
        if has_attachment and self.settings.app_server_enabled:
            try: attachments = self._collect_attachments(conversation_id, message)
            except Exception as error: self._record_error(error); return
        if structured: self._cache_message(conversation_id, message, sender, text, attachments, structured)
        elif text or attachments: self._cache_message(conversation_id, message, sender, text, attachments)
        if sender.get("id") not in self.contacts.owners(): return
        entities = message.get("entities") or message.get("caption_entities") or []
        mentioned = any(isinstance(entity, dict) and entity.get("type") == "mention" and isinstance(entity.get("offset"), int) and isinstance(entity.get("length"), int) and bool(getattr(self, "bot_username", "")) and text[entity["offset"]:entity["offset"] + entity["length"]].casefold() == f"@{self.bot_username}".casefold() for entity in entities)
        reply = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else {}
        reply_id, reply_from = reply.get("message_id"), reply.get("from") if isinstance(reply.get("from"), dict) else {}
        with self.database_lock:
            known_reply = isinstance(reply_id, int) and bool(self.database.execute("SELECT 1 FROM bot_messages WHERE chat_id=? AND generation=? AND telegram_message_id=?", (conversation_id, self._generation(conversation_id), reply_id)).fetchone())
        direct_reply = known_reply or (self.bot_id is not None and reply_from.get("id") == self.bot_id)
        if not (mentioned or direct_reply): return
        if location_error:
            self._ephemeral(conversation_id, location_error)
            if not text and not attachments: return
        snapshot, cached_attachments, summary = self._context_snapshot(conversation_id)
        try: self._enqueue_turn(conversation_id, snapshot, cached_attachments, self._turn_context(conversation_id, sender, text, cached_attachments, contains_untrusted_context=summary["contains_untrusted_context"], is_context_snapshot=True, has_location=summary.get("has_location", False)))
        except Exception as error: self._record_error(error)

    def _delete_received_command(self, chat_id: int, message: dict[str, Any]) -> None:
        """Comandos são controles efêmeros e não devem permanecer no histórico da DM."""
        message_id = message.get("message_id")
        if not isinstance(message_id, int): return
        try:
            assert self.api; self.api.delete(chat_id, message_id)
        except Exception as error:
            self._record_error(f"Não foi possível excluir a mensagem de comando: {error}")


    def _conversation_settings(self, chat_id: int) -> dict[str, Any]:
        with self.database_lock:
            self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at, share_thoughts, delete_thoughts) VALUES (?, ?, ?, ?)", (str(chat_id), time.time(), self.settings.default_share_thoughts, self.settings.default_delete_thoughts)); self.database.commit()
            row = self.database.execute("SELECT share_thoughts, delete_thoughts, reply_mode, voice_expires_at, owner_model, owner_effort FROM conversations WHERE chat_id=?", (str(chat_id),)).fetchone()
        return {"share_thoughts": bool(row[0]), "delete_thoughts": bool(row[1]), "reply_mode": str(row[2] or "text"), "voice_expires_at": row[3], "owner_model": str(row[4] or ""), "owner_effort": str(row[5] or "")} if row else {"share_thoughts": True, "delete_thoughts": True, "reply_mode": "text", "voice_expires_at": None, "owner_model": "", "owner_effort": ""}

    def _execution_preferences(self, chat_id: int) -> tuple[str, str]:
        settings = self._conversation_settings(chat_id)
        model = settings["owner_model"] if self.settings.owner_execution_preferences and settings["owner_model"] in self.settings.owner_allowed_models else self.settings.model
        effort = settings["owner_effort"] if self.settings.owner_execution_preferences and settings["owner_effort"] in self.settings.owner_allowed_efforts else self.settings.effort
        return model, effort

    def _config_execution_choices(self, session: dict[str, Any], settings: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Retorna escolhas estáticas ou o catálogo já confirmado para esta sessão."""
        models = tuple(session.get("models") or self.settings.owner_allowed_models)
        configured = str(settings.get("owner_model") or "")
        selected = configured if configured in models else (models[0] if models else "")
        efforts_by_model = session.get("efforts_by_model")
        efforts = tuple(efforts_by_model.get(selected, ()) if isinstance(efforts_by_model, dict) else self.settings.owner_allowed_efforts)
        return models, efforts

    def _refresh_config_model_catalog(self, session: dict[str, Any]) -> None:
        """Consulta o App Server uma vez e só publica modelos visíveis e autorizados."""
        response = self._app_client().request("model/list", {"limit": 100, "includeHidden": False}, timeout=25)
        data = response.get("data")
        if not isinstance(data, list):
            raise CodexProtocolError("O Codex não retornou uma lista de modelos válida.")
        allowed_models = set(self.settings.owner_allowed_models)
        allowed_efforts = set(self.settings.owner_allowed_efforts)
        models: list[str] = []
        efforts_by_model: dict[str, tuple[str, ...]] = {}
        for entry in data:
            if not isinstance(entry, dict) or entry.get("hidden") is True:
                continue
            model = entry.get("model", entry.get("id", ""))
            if not isinstance(model, str) or not model.strip() or model.strip() not in allowed_models:
                continue
            raw_efforts = entry.get("supportedReasoningEfforts", [])
            efforts = tuple(dict.fromkeys(str(item.get("reasoningEffort")).strip() for item in raw_efforts if isinstance(item, dict) and isinstance(item.get("reasoningEffort"), str) and str(item["reasoningEffort"]).strip() in allowed_efforts)) if isinstance(raw_efforts, list) else ()
            if efforts:
                models.append(model.strip()); efforts_by_model[model.strip()] = efforts
        if not models:
            raise CodexProtocolError("Nenhum modelo permitido pelo setup está disponível para a conta atual do Codex.")
        session["models"] = tuple(dict.fromkeys(models)); session["efforts_by_model"] = efforts_by_model

    def _render_config(self, chat_id: int, token: str, session: dict[str, Any], settings: dict[str, Any], page: str | bool) -> None:
        """Renderiza somente o controle da geração vigente, sem I/O sob mutex DB."""
        generation = session.get("generation")
        telegram_chat_id, topic_values = self._telegram_address(str(chat_id))
        def current() -> bool:
            if type(generation) is not int or session.get("chat_id") != str(chat_id) or generation != self._generation(chat_id): return False
            with self.config_lock: return self.config_sessions.get(token) is session
        def invalidate(*message_ids: int) -> None:
            with self.config_lock:
                if self.config_sessions.get(token) is session: self.config_sessions.pop(token, None)
            for message_id in set(message_ids):
                try:
                    if self.api: self.api.delete(telegram_chat_id, message_id)
                except Exception: self._record_error("Não foi possível remover um controle de configuração encerrado.")
        previous = int(session["message_id"])
        if not current():
            invalidate(previous)
            raise DeliverySuppressed("O controle pertence a uma geração encerrada.")
        models, efforts = self._config_execution_choices(session, settings)
        effective = dict(settings)
        if effective.get("owner_model") not in models: effective["owner_model"] = ""
        if effective.get("owner_effort") not in efforts: effective["owner_effort"] = ""
        text, keyboard = self._config_keyboard(token, effective, page, self.settings.voice_enabled, self.settings.owner_execution_preferences, models, efforts)
        assert self.api
        if not current():
            invalidate(previous)
            raise DeliverySuppressed("A conversa foi reiniciada antes de atualizar o controle.")
        if "direct_messages_topic_id" in topic_values:
            sent = self.api.send(telegram_chat_id, text, protect_content=True, reply_markup=keyboard, **topic_values)
            message_id = sent.get("message_id") if isinstance(sent, dict) else None
            if not isinstance(message_id, int) or isinstance(message_id, bool): raise TelegramApiError("Telegram não retornou o identificador da nova mensagem de configuração.")
            with self.database_lock:
                valid = generation == self._generation(chat_id)
                with self.config_lock:
                    valid = valid and self.config_sessions.get(token) is session
                    if valid: session["message_id"] = message_id
            if not valid:
                invalidate(previous, message_id)
                raise DeliverySuppressed("A conversa foi reiniciada durante a publicação do controle.")
            self._delete_later(telegram_chat_id, previous)
        else:
            self.api.edit(telegram_chat_id, previous, text, keyboard)
            if not current():
                invalidate(previous)
                raise DeliverySuppressed("A conversa foi reiniciada durante a atualização do controle.")

    def _close_config_with_error(self, chat_id: int, token: str, session: dict[str, Any], action: str, error: Exception | str) -> None:
        """Finaliza uma sessão quebrada e devolve ao owner o motivo útil da falha."""
        detail = _telegram_error_detail(error)
        self._record_error(detail); self._diagnostic("config.render_failed", conversation=str(chat_id), action=action, error=detail)
        with self.config_lock: self.config_sessions.pop(token, None)
        try:
            assert self.api
            telegram_chat_id, _topic_values = self._telegram_address(str(chat_id))
            self.api.delete(telegram_chat_id, int(session["message_id"]))
        except Exception as close_error:
            self._record_error(close_error); self._diagnostic("config.close_failed", conversation=str(chat_id), action=action, error=_telegram_error_detail(close_error))
        try:
            self._ephemeral(chat_id, f"Não foi possível concluir '{action}': {detail} O menu foi fechado; envie /config para tentar novamente.", 15)
        except Exception as notify_error:
            self._record_error(notify_error); self._diagnostic("config.error_notification_failed", conversation=str(chat_id), action=action, error=_telegram_error_detail(notify_error))

    @staticmethod
    def _config_keyboard(token: str, settings: dict[str, Any], thoughts: bool = False, voice_available: bool = False, execution_available: bool = False, allowed_models: tuple[str, ...] = (), allowed_efforts: tuple[str, ...] = ()) -> tuple[str, dict[str, Any]]:
        if not thoughts:
            rows = [[{"text": "💭 Pensamentos", "callback_data": f"cfg:{token}:thoughts"}]]
            if voice_available: rows.append([{"text": ("🔊 Respostas em áudio" if settings["reply_mode"] == "audio" else "💬 Respostas em texto"), "callback_data": f"cfg:{token}:audio"}])
            if execution_available: rows.append([{"text": "⚙️ Execução", "callback_data": f"cfg:{token}:execution"}])
            rows.append([{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]); return "Configurações do bot", {"inline_keyboard": rows}
        if thoughts == "execution":
            model = settings["owner_model"] or allowed_models[0]; effort = settings["owner_effort"] or allowed_efforts[0]
            return "Configurações › Execução", {"inline_keyboard": [[{"text": f"Modelo: {model}", "callback_data": f"cfg:{token}:models"}], [{"text": f"Raciocínio: {effort}", "callback_data": f"cfg:{token}:efforts"}], [{"text": "↺ Padrão do setup", "callback_data": f"cfg:{token}:execution-reset"}], [{"text": "‹ Voltar", "callback_data": f"cfg:{token}:back"}], [{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]]}
        if thoughts == "models_loading":
            return "Configurações › Execução", {"inline_keyboard": [[{"text": "⌛ Consultando modelos disponíveis no Codex…", "callback_data": f"cfg:{token}:models-loading"}], [{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]]}
        if thoughts == "models":
            selected = settings["owner_model"] or allowed_models[0]
            rows = [[{"text": f"{'✓ ' if model == selected else ''}{model}", "callback_data": f"cfg:{token}:model-{index}"}] for index, model in enumerate(allowed_models)]
            return "Configurações › Execução › Modelo", {"inline_keyboard": rows + [[{"text": "‹ Voltar", "callback_data": f"cfg:{token}:execution"}], [{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]]}
        if thoughts == "efforts":
            selected = settings["owner_effort"] or allowed_efforts[0]
            rows = [[{"text": f"{'✓ ' if effort == selected else ''}{effort}", "callback_data": f"cfg:{token}:effort-{index}"}] for index, effort in enumerate(allowed_efforts)]
            return "Configurações › Execução › Raciocínio", {"inline_keyboard": rows + [[{"text": "‹ Voltar", "callback_data": f"cfg:{token}:execution"}], [{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]]}
        shared, deleted = ("☑" if settings["share_thoughts"] else "☐"), ("☑" if settings["delete_thoughts"] else "☐")
        return "Configurações › Pensamentos", {"inline_keyboard": [[{"text": f"{shared} Compartilha Pensamentos", "callback_data": f"cfg:{token}:share"}], [{"text": f"{deleted} Excluir Pensamentos", "callback_data": f"cfg:{token}:delete"}], [{"text": "‹ Voltar", "callback_data": f"cfg:{token}:back"}], [{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]]}

    def _open_config(self, chat_id: int) -> None:
        """Publica um controle vinculado à geração capturada antes do HTTP."""
        assert self.api
        with self.database_lock:
            generation = self._generation(chat_id)
            incoming_generation = getattr(self._input_local, "generation", None)
            if incoming_generation is not None and incoming_generation != generation: return
            settings = self._conversation_settings(chat_id)
        token = secrets.token_urlsafe(8)
        text, keyboard = self._config_keyboard(token, settings, voice_available=self.settings.voice_enabled, execution_available=self.settings.owner_execution_preferences, allowed_models=self.settings.owner_allowed_models, allowed_efforts=self.settings.owner_allowed_efforts)
        sent = self._ephemeral(chat_id, text, 180, reply_markup=keyboard)
        message_id = sent.get("message_id") if isinstance(sent, dict) else None
        if not isinstance(message_id, int) or isinstance(message_id, bool): return
        with self.database_lock:
            valid = generation == self._generation(chat_id)
            if valid:
                with self.config_lock:
                    self.config_sessions[token] = {"chat_id": str(chat_id), "generation": generation, "message_id": message_id, "expires_at": time.time() + 180}
        if not valid:
            telegram_chat_id, _topic_values = self._telegram_address(str(chat_id))
            try: self.api.delete(telegram_chat_id, message_id)
            except Exception: self._record_error("Não foi possível remover um controle publicado após reinício da conversa.")
            self._diagnostic("config.suppressed", reason="generation_changed")
            return
        self._diagnostic("config.open", conversation=str(chat_id), generation=generation, message_id=message_id)

    def _config_callback(self, chat_id: int, callback: dict[str, Any], parts: list[str]) -> bool:
        if len(parts) != 3 or parts[0] != "cfg": return False
        token, action = parts[1], parts[2]
        with self.config_lock: session = self.config_sessions.get(token)
        self._diagnostic("config.callback", action=action, conversation=str(chat_id), session_found=bool(session), callback_message_id=callback.get("message", {}).get("message_id"))
        if not session or session["chat_id"] != str(chat_id) or time.time() > float(session["expires_at"]):
            self._ephemeral(chat_id, "Esta configuração expirou."); return True
        generation = session.get("generation")
        def current() -> bool:
            if type(generation) is not int or generation != self._generation(chat_id): return False
            with self.config_lock: return self.config_sessions.get(token) is session
        def invalidate() -> None:
            with self.config_lock:
                if self.config_sessions.get(token) is session: self.config_sessions.pop(token, None)
            telegram_chat_id, _topic_values = self._telegram_address(str(chat_id))
            try:
                if self.api: self.api.delete(telegram_chat_id, int(session["message_id"]))
            except Exception: self._record_error("Não foi possível remover um controle de configuração encerrado.")
        def mutate(statement: str, values: tuple[Any, ...]) -> None:
            with self.database_lock:
                cursor = self.database.execute(statement + " AND generation=?", (*values, generation))
                self.database.commit()
            if not cursor.rowcount: raise DeliverySuppressed("A conversa foi reiniciada antes de alterar a preferência.")
        if not current():
            invalidate(); return True
        if action == "close":
            invalidate()
            self._diagnostic("config.close", conversation=str(chat_id), message_id=session["message_id"])
            return True
        if action not in {"thoughts", "share", "delete", "back", "audio", "execution", "models", "efforts", "model", "effort", "execution-reset"} and not action.startswith(("model-", "effort-")):
            self._close_config_with_error(chat_id, token, session, action, "Opção de configuração inválida."); return True
        try:
            settings = self._conversation_settings(chat_id)
            if action == "audio":
                mode = "text" if settings["reply_mode"] == "audio" else "audio"
                if mode == "audio" and not self.settings.voice_enabled: raise RuntimeError("Respostas em áudio não estão configuradas para esta instância.")
                now = time.time(); expiry = now + self.settings.voice_auto_off_minutes * 60 if mode == "audio" else None
                mutate("UPDATE conversations SET reply_mode=?,voice_expires_at=?,settings_revision=settings_revision+1,updated_at=? WHERE chat_id=?", (mode, expiry, now, str(chat_id)))
                settings, page = self._conversation_settings(chat_id), False
            elif action in {"share", "delete"}:
                column = "share_thoughts" if action == "share" else "delete_thoughts"
                mutate(f"UPDATE conversations SET {column} = NOT {column}, updated_at=? WHERE chat_id=?", (time.time(), str(chat_id)))
                settings, page = self._conversation_settings(chat_id), True
            elif action.startswith(("model-", "effort-")):
                if not self.settings.owner_execution_preferences: raise ValueError("Preferências de execução não estão liberadas pelo setup.")
                prefix, _, raw_index = action.partition("-"); models, efforts = self._config_execution_choices(session, settings); permitted = models if prefix == "model" else efforts
                try: index = int(raw_index)
                except ValueError: index = -1
                if not 0 <= index < len(permitted): raise ValueError("A opção escolhida não pertence mais ao catálogo atual do Codex.")
                column = "owner_model" if prefix == "model" else "owner_effort"
                mutate(f"UPDATE conversations SET {column}=?, updated_at=? WHERE chat_id=?", (permitted[index], time.time(), str(chat_id)))
                settings, page = self._conversation_settings(chat_id), "execution"
            elif action == "model":
                if not self.settings.owner_execution_preferences: raise ValueError("Preferências de execução não estão liberadas pelo setup.")
                permitted, _efforts = self._config_execution_choices(session, settings)
                if not permitted: raise ValueError("Não há modelos permitidos pelo setup.")
                selected = str(settings["owner_model"] or ""); value = permitted[(permitted.index(selected) + 1) % len(permitted)] if selected in permitted else permitted[0]
                mutate("UPDATE conversations SET owner_model=?, updated_at=? WHERE chat_id=?", (value, time.time(), str(chat_id)))
                settings, page = self._conversation_settings(chat_id), "execution"
            elif action == "models":
                if not self.settings.owner_execution_preferences: raise ValueError("Preferências de execução não estão liberadas pelo setup.")
                if isinstance(self.api, TelegramApi):
                    self._render_config(chat_id, token, session, settings, "models_loading")
                    self._refresh_config_model_catalog(session)
                    if not current(): raise DeliverySuppressed("A conversa foi reiniciada durante a consulta de modelos.")
                    settings = self._conversation_settings(chat_id)
                page = "models"
            elif action in {"effort", "execution-reset"}:
                if not self.settings.owner_execution_preferences: raise ValueError("Preferências de execução não estão liberadas pelo setup.")
                if action == "execution-reset":
                    mutate("UPDATE conversations SET owner_model=NULL, owner_effort=NULL, updated_at=? WHERE chat_id=?", (time.time(), str(chat_id)))
                else:
                    _models, permitted = self._config_execution_choices(session, settings)
                    if not permitted: raise ValueError("O modelo atual não possui esforços de raciocínio permitidos pelo setup.")
                    selected = str(settings["owner_effort"] or ""); value = permitted[(permitted.index(selected) + 1) % len(permitted)] if selected in permitted else permitted[0]
                    mutate("UPDATE conversations SET owner_effort=?, updated_at=? WHERE chat_id=?", (value, time.time(), str(chat_id)))
                settings, page = self._conversation_settings(chat_id), "execution"
            else:
                page = "execution" if action == "execution" else action if action in {"thoughts", "efforts"} else False
            if not current(): raise DeliverySuppressed("A geração do controle já foi encerrada.")
            models, efforts = self._config_execution_choices(session, settings)
            if page in {"execution", "models", "efforts"} and (not models or not efforts): raise ValueError("A configuração de execução não possui modelo e esforço permitidos. Corrija-a no setup do Telegram.")
            self._render_config(chat_id, token, session, settings, page)
            _telegram_chat_id, topic_values = self._telegram_address(str(chat_id))
            self._diagnostic("config.rendered", conversation=str(chat_id), generation=generation, action=action, message_id=session["message_id"], route="direct" if "direct_messages_topic_id" in topic_values else "thread" if "message_thread_id" in topic_values else "chat")
        except DeliverySuppressed:
            invalidate()
        except Exception as error:
            if not current(): invalidate()
            else: self._close_config_with_error(chat_id, token, session, action, error)
        return True

    def _turn(self, chat_id: int, text: str, attachments: list[dict[str, Any]] | None = None, turn_context: dict[str, Any] | None = None, structured_content: dict[str, Any] | None = None) -> None:
        with self.turn_locks_lock: lock = self.turn_locks.setdefault(chat_id, threading.Lock())
        with lock:
            if structured_content: self._turn_serial(chat_id, text, attachments or [], turn_context, structured_content)
            else: self._turn_serial(chat_id, text, attachments or [], turn_context)

    @staticmethod
    def _public_turn_error(error: Exception | str) -> str:
        """Traduz apenas condições operacionais seguras; nunca entrega o erro bruto ao chat."""
        detail = str(error).casefold()
        if "out of credits" in detail or "insufficient credits" in detail:
            return "O workspace do Codex está sem créditos. Adicione créditos ao workspace e tente novamente."
        return "O agente não conseguiu concluir esta solicitação."


    def _ephemeral(self, chat_id: int, text: str, seconds: float = 8, **values: Any) -> dict[str, Any]:
        assert self.api
        telegram_chat_id, topic_values = self._telegram_address(str(chat_id))
        protect_content = bool(values.pop("protect_content", True))
        message = self.api.send(telegram_chat_id, text, protect_content=protect_content, **topic_values, **values)
        message_id = message.get("message_id")
        if isinstance(message_id, int): self._schedule_delete(telegram_chat_id, message_id, seconds)
        return message

    def _schedule_delete(self, chat_id: int, message_id: int, seconds: float) -> None:
        key = f"{chat_id}:{message_id}"; self.pending_deletions[key] = {"chat_id": chat_id, "message_id": message_id, "expires_at": time.time() + max(0, seconds)}; write_json(self.cleanup_path, self.pending_deletions)
        timer = threading.Timer(max(0, seconds), lambda: self._delete_later(chat_id, message_id)); timer.daemon = True; timer.start()

    def _restore_totp_cleanup(self) -> None:
        for pending in list(self.pending_deletions.values()):
            try: self._schedule_delete(int(pending["chat_id"]), int(pending["message_id"]), float(pending["expires_at"]) - time.time())
            except (KeyError, TypeError, ValueError): continue

    def _delete_later(self, chat_id: int, message_id: int) -> None:
        try:
            if self.api: self.api.delete(chat_id, message_id)
        except Exception: pass
        finally:
            self.pending_deletions.pop(f"{chat_id}:{message_id}", None); write_json(self.cleanup_path, self.pending_deletions)

    @staticmethod
    def _filter_totp(entries: list[str], query: str) -> list[str]:
        pattern = f"*{query.casefold()}*" if query else "*"
        return sorted(entry for entry in entries if fnmatch.fnmatchcase(entry.casefold(), pattern))

    def _start_totp(self, chat_id: int, message: dict[str, Any], query: str) -> None:
        """Abre um desafio privado com geração e token próprios."""
        assert self.api
        with self.database_lock:
            generation = self._generation(chat_id)
            incoming_generation = getattr(self._input_local, "generation", None)
            if incoming_generation is not None and incoming_generation != generation: return
            intent = getattr(self._input_local, "totp_intent_token", None)
            token = intent if isinstance(intent, str) and intent else secrets.token_urlsafe(8)
            session = {"phase": "password", "generation": generation, "token": token, "query": query, "expires_at": time.time() + 180, "message_ids": []}
            with self.control_state_lock:
                if isinstance(intent, str) and intent:
                    prompt = self.store.private_prompt(str(chat_id))
                    if not prompt or prompt.get("generation") != generation or prompt.get("token_hash") != hashlib.sha256(intent.encode()).hexdigest(): return
                if not self.store.record_private_prompt(str(chat_id), generation, session["token"], session["expires_at"]): return
                previous = self.totp_sessions.get(chat_id)
                self.totp_sessions[chat_id] = session
        self._delete_totp_messages(chat_id, previous)
        def current() -> bool:
            with self.database_lock:
                valid = generation == self._generation(chat_id)
                with self.control_state_lock: return valid and self.totp_sessions.get(chat_id) is session
        def publish(text: str, *, remember: bool = False) -> None:
            if not current(): return
            sent = self._ephemeral(chat_id, text, 180 if remember else 8)
            identifier = sent.get("message_id") if isinstance(sent, dict) else None
            with self.database_lock:
                valid = generation == self._generation(chat_id)
                with self.control_state_lock:
                    valid = valid and self.totp_sessions.get(chat_id) is session
                    if valid and remember and isinstance(identifier, int):
                        valid = self.store.record_private_prompt(str(chat_id), generation, session["token"], session["expires_at"], identifier)
                        if valid: session["message_ids"].append(identifier)
            if not valid and isinstance(identifier, int):
                address, _topic = self._telegram_address(str(chat_id))
                try: self.api.delete(address, identifier)
                except Exception: self._record_error("Não foi possível remover um controle TOTP encerrado.")
        try:
            profile = tomllib.loads(telegram_configuration_path(self.settings.root).read_text(encoding="utf-8")).get("totp", {})
            if not current(): return
            if not str(profile.get("real_password_entry") or "") or not str(profile.get("fake_password_entry") or ""):
                publish("TOTP está habilitado, mas as entradas de senha real e falsa não foram configuradas.")
                self._clear_totp_session(chat_id, expected_session=session); return
            publish("Informe a senha TOTP na próxima mensagem. Ela será apagada.", remember=True)
        except Exception:
            self._record_error("Não foi possível iniciar o controle privado TOTP.")
            try: publish("Não foi possível iniciar o TOTP. Consulte o diagnóstico do gateway.")
            except Exception: pass
            self._clear_totp_session(chat_id, expected_session=session)

    def _totp_password(self, chat_id: int, message: dict[str, Any], *, expected_token: str | None = None) -> None:
        """Verifica senha somente na memória e abandona respostas de desafio antigo."""
        assert self.api
        address, topic_values = self._telegram_address(str(chat_id))
        with self.database_lock:
            generation = self._generation(chat_id)
            incoming_generation = getattr(self._input_local, "generation", None)
            with self.control_state_lock:
                session = self.totp_sessions.get(chat_id)
                claimed = bool(session and session.get("generation") == generation and (incoming_generation is None or incoming_generation == generation) and (expected_token is None or session.get("token") == expected_token) and session.get("phase") == "password" and not session.get("checking") and time.time() <= float(session.get("expires_at", 0)))
                if claimed: session["checking"] = True
        try:
            identifier = message.get("message_id")
            if isinstance(identifier, int): self.api.delete(address, identifier)
        except Exception: pass
        if not claimed: return
        self._delete_totp_messages(chat_id, session)
        def current() -> bool:
            with self.database_lock:
                valid = generation == self._generation(chat_id)
                with self.control_state_lock: return valid and self.totp_sessions.get(chat_id) is session
        def notify(text: str) -> None:
            if not current(): return
            sent = self._ephemeral(chat_id, text)
            identifier = sent.get("message_id") if isinstance(sent, dict) else None
            if not current() and isinstance(identifier, int):
                try: self.api.delete(address, identifier)
                except Exception: self._record_error("Não foi possível remover um aviso TOTP encerrado.")
        typing_stop = threading.Event()
        def renew_typing() -> None:
            while not typing_stop.is_set() and current():
                try: self.api.typing(address, **topic_values)
                except Exception: self._record_error("Não foi possível atualizar o estado do controle privado TOTP.")
                typing_stop.wait(4)
        typing_thread = threading.Thread(target=renew_typing, daemon=True); typing_thread.start()
        try:
            data = tomllib.loads(telegram_configuration_path(self.settings.root).read_text(encoding="utf-8")); profile = data.get("totp", {})
            real = McpVault(self.settings).read(str(profile.get("real_password_entry") or ""))
            if not current(): return
            fake = McpVault(self.settings).read(str(profile.get("fake_password_entry") or ""))
            if not current(): return
            supplied = str(message.get("text") or "")
            if secrets.compare_digest(supplied, fake):
                notify("Não existe TOTP cadastrado."); self._clear_totp_session(chat_id, expected_session=session); return
            if not secrets.compare_digest(supplied, real):
                notify("Senha TOTP inválida."); self._clear_totp_session(chat_id, expected_session=session); return
            entries = self._filter_totp(McpVault(self.settings).list_totp(), str(session["query"]))
            if not current(): return
            if not entries:
                notify("Nenhuma entrada TOTP encontrada."); self._clear_totp_session(chat_id, expected_session=session); return
            selection = {"phase": "selection", "generation": generation, "entries": entries, "token": secrets.token_urlsafe(8), "expires_at": time.time() + 180, "message_ids": []}
            with self.database_lock:
                valid = generation == self._generation(chat_id)
                with self.control_state_lock:
                    if not valid or self.totp_sessions.get(chat_id) is not session: return
                    self.store.clear_private_prompt(str(chat_id), generation, session["token"])
                    self.totp_sessions[chat_id] = selection
            self._show_totp_page(chat_id, 0, expected_session=selection)
        except Exception:
            self._record_error("Não foi possível consultar o controle privado TOTP.")
            try: notify("Não foi possível consultar os TOTPs. Consulte o diagnóstico do gateway.")
            except Exception: pass
            self._clear_totp_session(chat_id, expected_session=session)
        finally:
            typing_stop.set(); typing_thread.join(timeout=1)

    def _show_totp_page(self, chat_id: int, page: int, *, expected_session: dict[str, Any] | None = None) -> None:
        """Publica somente a seleção capturada, sem reler ou limpar desafio novo."""
        assert self.api
        with self.control_state_lock:
            session = self.totp_sessions.get(chat_id)
            if expected_session is not None and session is not expected_session: return
        if not session or session.get("phase") != "selection": return
        generation, token = session.get("generation"), session.get("token")
        def current() -> bool:
            with self.database_lock:
                valid = type(generation) is int and generation == self._generation(chat_id)
                with self.control_state_lock:
                    return valid and self.totp_sessions.get(chat_id) is session and session.get("token") == token
        if not current(): return
        if time.time() > float(session.get("expires_at", 0)):
            self._clear_totp_session(chat_id, expected_session=session); return
        self._delete_totp_messages(chat_id, session)
        if not current(): return
        entries = session["entries"]; size = 10; start = max(0, page) * size; chunk = entries[start:start + size]
        keyboard = [[{"text": entry[-48:], "callback_data": f"totp:{token}:{start + index}"}] for index, entry in enumerate(chunk)]
        navigation = []
        if page > 0: navigation.append({"text": "‹", "callback_data": f"totp:{token}:p:{page - 1}"})
        if start + size < len(entries): navigation.append({"text": "›", "callback_data": f"totp:{token}:p:{page + 1}"})
        if navigation: keyboard.append(navigation)
        keyboard.append([{"text": "Fechar", "callback_data": f"totp:{token}:x"}])
        sent = self._ephemeral(chat_id, "Escolha a entrada TOTP:", 180, reply_markup={"inline_keyboard": keyboard})
        identifier = sent.get("message_id") if isinstance(sent, dict) else None
        with self.database_lock:
            valid = type(generation) is int and generation == self._generation(chat_id)
            with self.control_state_lock:
                valid = valid and self.totp_sessions.get(chat_id) is session and session.get("token") == token
                if valid and isinstance(identifier, int): session.setdefault("message_ids", []).append(identifier)
        if not valid and isinstance(identifier, int):
            address, _topic = self._telegram_address(str(chat_id))
            try: self.api.delete(address, identifier)
            except Exception: self._record_error("Não foi possível remover um controle TOTP encerrado.")

    def _delete_totp_messages(self, chat_id: int, session: dict[str, Any] | None) -> None:
        if not session: return
        telegram_chat_id, _topic_values = self._telegram_address(str(chat_id))
        for message_id in list(session.get("message_ids", [])):
            if isinstance(message_id, int): self._delete_later(telegram_chat_id, message_id)
        session["message_ids"] = []

    def _clear_totp_session(self, chat_id: int, *, expected_session: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Remove por identidade quando houver espera em andamento; HTTP fora do lock."""
        with self.database_lock:
            with self.control_state_lock:
                session = self.totp_sessions.get(chat_id)
                if expected_session is not None and session is not expected_session: return None
                self.totp_sessions.pop(chat_id, None)
                if session and type(session.get("generation")) is int and isinstance(session.get("token"), str):
                    self.store.clear_private_prompt(str(chat_id), session["generation"], session["token"])
        self._delete_totp_messages(chat_id, session)
        return session

    def _callback(self, callback: dict[str, Any]) -> None:
        self._observe_command_language(callback.get("from") or {})
        assert self.api
        sender = callback.get("from") or {}; message = callback.get("message") or {}; chat = message.get("chat") or {}; chat_id = sender.get("id")
        if self._channel_callback(callback): return
        if chat.get("type") != "private" or not isinstance(chat_id, int) or chat_id not in self.contacts.owners(): return
        if not isinstance(chat.get("id"), int): chat = {**chat, "id": chat_id}
        conversation_id = self._conversation_id(chat, message)
        data = str(callback.get("data") or ""); parts = data.split(":")
        def acknowledge() -> None:
            try:
                acknowledgement = getattr(self.api, "answer_callback", None)
                if callable(acknowledgement): acknowledgement(str(callback["id"]))
                else: self.api.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
            except Exception as error:
                self._record_error(error); self._diagnostic("callback.ack_failed", conversation=str(conversation_id), error=_telegram_error_detail(error))
        threading.Thread(target=acknowledge, daemon=True).start()
        if len(parts) == 3 and parts[0] == "ag":
            try: index = int(parts[2])
            except ValueError: index = -1
            with self.menu_lock:
                session = self.menu_sessions.get(parts[1])
                valid = session and session.chat_id == chat_id and session.message_id == message.get("message_id") and not session.invalidated and not session.completed.is_set() and session.generation == self._generation(session.conversation_id or conversation_id) and (not session.conversation_id or session.conversation_id == conversation_id) and 0 <= index < len(session.options)
                if valid:
                    session.selected = session.options[index]; session.completed.set()
            if not valid: self._ephemeral(conversation_id, "Seleção inválida ou expirada.")
            return
        if len(parts) == 3 and parts[0] == "cfg":
            with self.config_lock: session = self.config_sessions.get(parts[1])
            if session and session.get("message_id") == message.get("message_id"):
                expected_chat_id, _topic_values = self._telegram_address(str(session["chat_id"]))
                if expected_chat_id == int(chat["id"]): conversation_id = str(session["chat_id"])
        if len(parts) == 3 and parts[0] == "cfg" and isinstance(self.api, TelegramApi):
            threading.Thread(target=self._config_callback, args=(conversation_id, callback, parts), daemon=True).start(); return
        if self._config_callback(conversation_id, callback, parts): return
        if len(parts) < 3 or parts[0] != "totp": self._ephemeral(conversation_id, "Ação inválida ou expirada."); return
        with self.control_state_lock: session = self.totp_sessions.get(conversation_id)
        generation = session.get("generation") if session else None
        token = session.get("token") if session else None
        def current(*, cleared: bool = False) -> bool:
            with self.database_lock:
                valid = type(generation) is int and generation == self._generation(conversation_id)
                with self.control_state_lock:
                    selected = self.totp_sessions.get(conversation_id)
                    return valid and (selected is None if cleared else selected is session and session.get("token") == token)
        def publish(text: str, seconds: float = 8, *, protect: bool = True, cleared: bool = False) -> bool:
            if not current(cleared=cleared): return False
            sent = self._ephemeral(conversation_id, text, seconds, protect_content=protect)
            identifier = sent.get("message_id") if isinstance(sent, dict) else None
            valid = current(cleared=cleared)
            if not valid and isinstance(identifier, int):
                address, _topic = self._telegram_address(str(conversation_id))
                try: self.api.delete(address, identifier)
                except Exception: self._record_error("Não foi possível remover uma resposta TOTP encerrada.")
            return valid
        if not session or session.get("phase") != "selection" or not secrets.compare_digest(str(token or ""), parts[1]) or time.time() > float(session.get("expires_at", 0)):
            self._ephemeral(conversation_id, "Seleção TOTP expirada. Inicie /totp novamente."); return
        if not current():
            self._clear_totp_session(conversation_id, expected_session=session); return
        if len(parts) == 4 and parts[2] == "p":
            try: page = int(parts[3])
            except ValueError: return
            self._show_totp_page(conversation_id, page, expected_session=session); return
        if parts[2] == "x":
            removed = self._clear_totp_session(conversation_id, expected_session=session)
            if removed: publish("TOTP cancelado.", cleared=True)
            return
        try:
            index = int(parts[2])
            if index < 0 or index >= len(session["entries"]): raise ValueError("Opção TOTP inválida.")
            with self.database_lock:
                if generation != self._generation(conversation_id): return
                with self.control_state_lock:
                    if self.totp_sessions.get(conversation_id) is not session or session.get("token") != token or session.get("retrieving"): return
                    session["retrieving"] = True
            code = McpVault(self.settings).read(session["entries"][index], "totp")
            if not current(): return
            if not isinstance(code, str) or not code.strip(): raise ValueError("Código TOTP indisponível.")
            with self.database_lock:
                if generation != self._generation(conversation_id): return
                with self.control_state_lock:
                    if self.totp_sessions.get(conversation_id) is not session or session.get("token") != token: return
                    session["phase"] = "delivering"
            self._delete_totp_messages(conversation_id, session)
            remaining = 30 - (int(time.time()) % 30)
            if publish(code, remaining + 5, protect=False): publish(f"Expira em {remaining} segundos.", remaining + 5)
            self._clear_totp_session(conversation_id, expected_session=session)
        except Exception:
            self._record_error("Não foi possível obter o código do controle privado TOTP.")
            if current():
                removed = self._clear_totp_session(conversation_id, expected_session=session)
                if removed: publish("Não foi possível obter este TOTP.", cleared=True)

    def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method.startswith("notification."):
            return self._notification_handle(method, params)
        if method == "ping": return {"service": "telegram", "state": "running"}
        if method == "telegram.status": return {"owners": len(self.contacts.owners()), "listener": "running", "totp_enabled": self._totp_enabled(), "last_error": self.last_error, "commands": json_file(self.settings.state_dir / "gateway-status.json", {}).get("commands"), "execution_mode": "app-server" if self.settings.app_server_enabled else "unavailable", "busy_input_policy": self.settings.busy_input_policy, "steering_available": self.capabilities.supports("steering"), "steering_reason": self.capabilities.reason("steering"), "post_turn_results": self.settings.post_turn_results and self.capabilities.supports("post_turn"), "execution": self.store.summary(), "app_server_running": bool(self.app_server and self.app_server.running()), "app_server_idle_timeout_seconds": self.settings.app_server_idle_seconds}
        if method == "telegram.sync-commands": return self._configure_owner_commands()
        if method == "telegram.pair-request":
            with self.control_state_lock:
                self.pair = (f"{secrets.randbelow(1_000_000):06d}", time.time() + min(600, max(60, int(params.get("ttl_seconds", 300)))))
                return {"pin": self.pair[0], "expires_at": self.pair[1]}
        if method == "telegram.owners": return sorted(self.contacts.owners())
        if method == "telegram.channels.list": return self._channels_list()
        if method == "telegram.channels.remove":
            channel = str(params.get("channel") or "")
            if not channel: raise ValueError("Informe o identificador do canal.")
            return self._channels_remove(channel)
        if method == "telegram.outbound.send": return self._outbound_send(params)
        if method == "telegram.outbound.send_location": return self._outbound_send_location(params)
        if method == "telegram.reply.send_location": return self._reply_send_location(params)
        if method == "telegram.send":
            if not self.api: raise RuntimeError("listener unavailable")
            self.api.send(int(params["chat_id"]), str(params["text"])); return {"sent": True}
        if method == "shutdown":
            self._shutdown_execution()
            return {"state": "stopping"}
        raise ValueError("unknown Telegram method")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--data-dir", type=Path, required=True); parser.add_argument("--onmyoji-root", type=Path, required=True); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, required=True); parser.add_argument("--token", required=True); args = parser.parse_args(argv)
    gateway = Gateway(Settings.load(args.onmyoji_root.resolve(), args.data_dir))
    gateway.start()
    configuration = gateway.notification_configuration.load()
    server = RpcServer(args.host, args.port, args.token, gateway.handle, long_methods=("notification.submit",), long_limit=configuration["long_limit"], event_max_bytes=configuration["max_request_bytes"], on_rejection=gateway._notification_transport_rejected)
    try:
        server.serve_forever(gateway.stop_event)
    finally:
        gateway._shutdown_execution()
        server.close()
    return 0


if __name__ == "__main__": raise SystemExit(main())
