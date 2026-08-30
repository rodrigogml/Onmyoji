from __future__ import annotations

import argparse
import fnmatch
from dataclasses import dataclass, field
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
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .rpc import RpcServer
from .instructions import InstructionComposer

GATEWAY_INSTRUCTIONS = """You are operating through a private Telegram owner DM. Reply normally to the active conversation. Do not expose local paths, bot tokens, chat IDs, owners or gateway state. Native /new, pairing and TOTP commands are handled by the gateway before your turn. Treat gateway failures as failures."""


def json_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try: value = json.loads(path.read_text(encoding="utf-8")); return value if isinstance(value, dict) else default
    except (OSError, ValueError): return default


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"); temporary.replace(path)


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    keepass_profile: str
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

    @classmethod
    def load(cls, root: Path, data_dir: Path) -> "Settings":
        path = data_dir / "telegram.toml"
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1: raise ValueError("unsupported telegram schema")
        telegram, agent, app_server, limits, voice, instructions = data.get("telegram", {}), data.get("agent", {}), data.get("app_server", {}), data.get("limits", {}), data.get("voice_reply", {}), data.get("instructions", {})
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
        project = Path(str(system.get("project_directory") or "")).resolve()
        if not project.is_dir(): raise ValueError("configured Shikigami workspace does not exist")
        profile, entry = str(telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or "")
        if not profile or not entry: raise ValueError("KeePass profile and token entry are required")
        idle = int(app_server.get("idle_timeout_seconds") or 1800)
        if idle < 60: raise ValueError("App Server idle timeout must be at least 60 seconds")
        per_file, batch, pending, retained = int(limits.get("max_attachment_bytes") or 20 * 1024 * 1024), int(limits.get("max_batch_attachment_bytes") or 50 * 1024 * 1024), int(limits.get("max_pending_items") or 50), int(limits.get("max_retained_attachment_bytes") or 250 * 1024 * 1024)
        if not 1 <= per_file <= batch <= retained: raise ValueError("Telegram attachment limits are invalid")
        output_bytes, output_count = int(limits.get("max_outbound_media_bytes") or 20 * 1024 * 1024), max(1, int(limits.get("max_outbound_media_per_turn") or 3))
        permitted_models = tuple(dict.fromkeys(str(value).strip() for value in agent.get("owner_allowed_models", []) if isinstance(value, str) and value.strip())) or (str(system.get("model") or ""),)
        permitted_efforts = tuple(dict.fromkeys(str(value).strip() for value in agent.get("owner_allowed_reasoning_efforts", []) if isinstance(value, str) and value.strip())) or (str(system.get("model_reasoning_effort") or "medium"),)
        enabled, voice_profile = bool(voice.get("enabled", False)), str(voice.get("eccovox_profile") or "")
        requested_format, voice_text, auto_off = str(voice.get("response_format") or "mp3").casefold(), int(voice.get("max_text_characters") or 3500), int(voice.get("auto_off_minutes") or 15)
        # A primeira configuração-modelo usava opus, mas o motor EccoVox atual
        # ainda o rejeita apesar de anunciá-lo no health. MP3 é suportado pelo
        # runtime e pelo Telegram; preservamos configurações já criadas.
        voice_format = "mp3" if requested_format == "opus" else requested_format
        if enabled and (not voice_profile or voice_format not in {"mp3", "wav", "flac"} or not 1 <= voice_text <= 4000 or not 1 <= auto_off <= 1440): raise ValueError("Telegram voice reply configuration is invalid")
        return cls(root, data_dir, profile, entry, project, str(system.get("executable") or "codex"), str(system.get("model") or ""), str(system.get("model_reasoning_effort") or "medium"), str(system.get("sandbox_mode") or "workspace-write"), str(system.get("approval_policy") or "never"), int(telegram.get("poll_timeout_seconds") or 30), int(agent.get("turn_timeout_seconds") or 900), max(1, int(agent.get("max_parallel_conversations") or 1)), str(instructions.get("shikigami_file") or "shikigami.md"), bool(instructions.get("enabled", True)), bool(agent.get("owner_execution_preferences", True)), permitted_models, permitted_efforts, bool(app_server.get("enabled", False)), idle, per_file, batch, max(1, pending), retained, output_bytes, output_count, enabled, voice_profile, str(voice.get("language") or "pt-BR"), str(voice.get("voice") or ""), float(voice.get("speed") or 1.0), voice_format, voice_text, auto_off, bool(voice.get("fallback_to_text", True)), bool(voice.get("agent_outbound_media", False)))


class CodexProtocolError(RuntimeError): pass


class CodexAppServer:
    """Processo Codex persistente, sempre privado à instância Onmyōji atual."""
    def __init__(self, settings: Settings):
        self.settings, self.process, self.reader, self.stderr_reader = settings, None, None, None
        self.write_lock, self.pending_lock, self.pending, self.next_id = threading.Lock(), threading.Lock(), {}, 1
        self.notification_handler: Any = None; self.request_handler: Any = None; self.last_error: str | None = None
    def running(self) -> bool: return bool(self.process and self.process.poll() is None)
    def start(self) -> None:
        if self.running(): return
        environment = dict(os.environ); environment["CODEX_HOME"] = str(self.settings.root)
        self.process = subprocess.Popen([self.settings.executable, "app-server", "--stdio"], cwd=self.settings.project, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.reader = threading.Thread(target=self._read_stdout, daemon=True); self.stderr_reader = threading.Thread(target=self._read_stderr, daemon=True); self.reader.start(); self.stderr_reader.start()
        self.request("initialize", {"clientInfo": {"name": "onmyoji_telegram_gateway", "title": "Onmyoji Telegram Gateway", "version": "1.0"}, "capabilities": {"experimentalApi": True}}); self.notify("initialized")
    def stop(self) -> None:
        process, self.process = self.process, None
        if not process: return
        try:
            if process.stdin: process.stdin.close()
            process.terminate(); process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try: process.kill()
            except OSError: pass
    def _send(self, payload: dict[str, Any]) -> None:
        self.start(); assert self.process and self.process.stdin
        with self.write_lock: self.process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"); self.process.stdin.flush()
    def request(self, method: str, params: dict[str, Any] | None = None, timeout: int = 45) -> dict[str, Any]:
        with self.pending_lock:
            request_id, response = self.next_id, queue.Queue(maxsize=1); self.next_id += 1; self.pending[request_id] = response
        try: self._send({"method": method, "id": request_id, "params": params or {}}); message = response.get(timeout=timeout)
        except queue.Empty as error: raise CodexProtocolError(f"Codex App Server excedeu o tempo de espera em {method}") from error
        finally:
            with self.pending_lock: self.pending.pop(request_id, None)
        if message.get("error"):
            detail = message["error"].get("message") if isinstance(message["error"], dict) else str(message["error"]); raise CodexProtocolError(f"Codex App Server recusou {method}: {detail}")
        return message.get("result") if isinstance(message.get("result"), dict) else {}
    def notify(self, method: str, params: dict[str, Any] | None = None) -> None: self._send({"method": method, "params": params or {}})
    def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        for raw in self.process.stdout:
            try: message = json.loads(raw)
            except ValueError: continue
            request_id = message.get("id")
            if isinstance(request_id, (int, str)) and ("result" in message or "error" in message):
                with self.pending_lock: target = self.pending.get(request_id)
                if target: target.put(message)
            elif isinstance(request_id, (int, str)) and isinstance(message.get("method"), str):
                try:
                    if self.request_handler: self._send({"id": request_id, "result": self.request_handler(message["method"], message.get("params") if isinstance(message.get("params"), dict) else {})})
                    else: self._send({"id": request_id, "error": {"code": -32000, "message": "Interactive gateway requests are disabled"}})
                except Exception as error:
                    try: self._send({"id": request_id, "error": {"code": -32000, "message": str(error)[:300]}})
                    except Exception: pass
            elif isinstance(message.get("method"), str) and isinstance(message.get("params"), dict) and self.notification_handler: self.notification_handler(message["method"], message["params"])
        if self.process and self.process.poll() not in {None, 0}: self.last_error = f"Codex App Server encerrou com código {self.process.poll()}"
    def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        for raw in self.process.stderr:
            line = raw.strip()
            if line: self.last_error = re.sub(r"(?i)(token|password|secret)=[^\s]+", r"\1=[redacted]", line)[-800:]


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


@dataclass
class MenuSession:
    token: str
    chat_id: int
    message_id: int
    options: list[dict[str, str]]
    completed: threading.Event = field(default_factory=threading.Event)
    selected: dict[str, str] | None = None


class Vault:
    def __init__(self, settings: Settings): self.settings = settings
    def read(self, entry: str, field: str = "password") -> str:
        result = self.request({"operation": "read", "entry": {"path": entry}, "field": field})
        return str(result["value"])
    def request(self, request: dict[str, Any]) -> dict[str, Any]:
        wrapper = self.settings.root / "available-skills" / "keepass-vault" / "scripts" / "keepass_vault.py"
        process = subprocess.run([sys.executable, str(wrapper), "--config", str(self.settings.root / "configs" / "keepass.toml"), "--profile", self.settings.keepass_profile], input=json.dumps(request), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=45)
        try: result = json.loads(process.stdout)
        except ValueError as error: raise RuntimeError("KeePass provider returned invalid response") from error
        if not result.get("ok"):
            error = result.get("error", {}); detail = error.get("message") if isinstance(error, dict) else ""
            raise RuntimeError(f"KeePass provider rejected the request: {detail or 'erro não detalhado'}")
        value = result.get("result")
        if not isinstance(value, dict): raise RuntimeError("KeePass provider returned invalid result")
        return value


class TelegramApi:
    def __init__(self, token: str): self.base, self.files = f"https://api.telegram.org/bot{token}/", f"https://api.telegram.org/file/bot{token}/"
    def call(self, method: str, values: dict[str, Any] | None = None) -> Any:
        body = urlencode({key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in (values or {}).items()}).encode()
        with urlopen(Request(self.base + method, data=body), timeout=45) as response: payload = json.loads(response.read())
        if not payload.get("ok"): raise RuntimeError("Telegram API rejected request")
        return payload.get("result")
    def send(self, chat_id: int, text: str, **values: Any) -> dict[str, Any]: return self.call("sendMessage", {"chat_id": chat_id, "text": text, **values})
    def typing(self, chat_id: int) -> None: self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
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
    def send_file(self, method: str, chat_id: int, path: Path, field: str, caption: str = "", reply_to: int | None = None) -> dict[str, Any]:
        boundary = "----Onmyoji" + secrets.token_hex(16); chunks: list[bytes] = []
        values = {"chat_id": str(chat_id), "protect_content": "true"}
        if caption: values["caption"] = caption[:1024]
        if reply_to: values["reply_parameters"] = json.dumps({"message_id": reply_to})
        for key, value in values.items(): chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(), value.encode("utf-8"), b"\r\n"))
        chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'.encode(), b"Content-Type: application/octet-stream\r\n\r\n", path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()))
        request = Request(self.base + method, data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urlopen(request, timeout=90) as response: payload = json.loads(response.read())
        if not payload.get("ok"): raise RuntimeError("Telegram API rejected media upload")
        return payload.get("result") if isinstance(payload.get("result"), dict) else {}
    def set_owner_commands(self, owner_id: int, totp_enabled: bool) -> bool:
        commands = [{"command": "new", "description": "Iniciar uma nova conversa"}, {"command": "config", "description": "Configurar esta conversa"}]
        if totp_enabled: commands.append({"command": "totp", "description": "Obter código de autenticação"})
        scope = {"type": "chat", "chat_id": owner_id}
        self.call("setMyCommands", {"commands": commands, "scope": scope})
        actual = self.call("getMyCommands", {"scope": scope})
        return isinstance(actual, list) and {str(item.get("command")) for item in actual if isinstance(item, dict)} == {item["command"] for item in commands}


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


def safe_name(value: str, fallback: str) -> str:
    name = re.sub(r"[^\w.() -]+", "_", Path(value).name, flags=re.UNICODE).strip(" .")
    return (name or fallback)[:180]


def under(path: Path, root: Path) -> bool:
    try: path.resolve().relative_to(root.resolve()); return True
    except ValueError: return False


class Gateway:
    def __init__(self, settings: Settings):
        self.settings, self.stop_event = settings, threading.Event(); self.contacts = Contacts(settings.data_dir / "contacts.json")
        (settings.data_dir / "state").mkdir(parents=True, exist_ok=True); self.database = sqlite3.connect(settings.data_dir / "state" / "gateway.sqlite3", check_same_thread=False); self.database_lock = threading.RLock()
        self.database.execute("CREATE TABLE IF NOT EXISTS conversations(chat_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL, share_thoughts INTEGER NOT NULL DEFAULT 1, delete_thoughts INTEGER NOT NULL DEFAULT 1, codex_thread_id TEXT, instruction_baseline_hash TEXT)")
        for column, definition in (("share_thoughts", "INTEGER NOT NULL DEFAULT 1"), ("delete_thoughts", "INTEGER NOT NULL DEFAULT 1"), ("codex_thread_id", "TEXT"), ("instruction_baseline_hash", "TEXT"), ("reply_mode", "TEXT NOT NULL DEFAULT 'text'"), ("voice_expires_at", "REAL"), ("last_interaction_at", "REAL NOT NULL DEFAULT 0"), ("settings_revision", "INTEGER NOT NULL DEFAULT 0"), ("owner_model", "TEXT"), ("owner_effort", "TEXT")):
            try: self.database.execute(f"ALTER TABLE conversations ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError: pass
        self.database.execute("CREATE TABLE IF NOT EXISTS conversation_attachments(id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, generation INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, mime_type TEXT, size INTEGER NOT NULL, archive_path TEXT NOT NULL, created_at REAL NOT NULL)")
        self.database.execute("CREATE INDEX IF NOT EXISTS attachment_scope ON conversation_attachments(chat_id, generation, created_at)")
        self.database.execute("CREATE TABLE IF NOT EXISTS inbox_items(id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, generation INTEGER NOT NULL, text TEXT NOT NULL, attachment_ids TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL)")
        self.database.execute("CREATE INDEX IF NOT EXISTS inbox_scope ON inbox_items(chat_id, generation, state, created_at)")
        self.database.execute("CREATE TABLE IF NOT EXISTS outbound_intents(id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, generation INTEGER NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, telegram_message_id INTEGER, created_at REAL NOT NULL, completed_at REAL)")
        self.database.execute("UPDATE inbox_items SET state='pending' WHERE state='running'")
        self.database.commit()
        self.identity = settings.root.name.removeprefix("Onmyoji-").strip() or "Shikigami"; self.instructions = InstructionComposer(settings.root, settings.data_dir, settings.instructions_file, settings.instructions_enabled)
        self.activity_root = settings.project / ".onmyoji" / "telegram"; self.archive_root = self.activity_root / "attachments"; self.staging_root = self.activity_root / "staging"
        self.archive_root.mkdir(parents=True, exist_ok=True); self.staging_root.mkdir(parents=True, exist_ok=True)
        self.api: TelegramApi | None = None; self.pair: tuple[str, float] | None = None; self.offset = 0; self.work = threading.BoundedSemaphore(settings.parallel); self.totp_sessions: dict[int, dict[str, Any]] = {}; self.config_sessions: dict[str, dict[str, Any]] = {}; self.menu_sessions: dict[str, MenuSession] = {}; self.menu_lock = threading.RLock(); self.last_error: str | None = None; self.cleanup_path = settings.data_dir / "state" / "totp-cleanup.json"; self.pending_deletions = json_file(self.cleanup_path, {}); self.app_server: CodexAppServer | None = None; self.app_server_lock = threading.RLock(); self.app_turns: dict[str, AppServerTurn] = {}; self.last_app_activity = time.monotonic(); self.turn_locks: dict[int, threading.Lock] = {}; self.turn_locks_lock = threading.Lock(); self.workers: set[int] = set(); self.workers_lock = threading.Lock()
    def _totp_enabled(self) -> bool:
        try: return bool(tomllib.loads((self.settings.data_dir / "telegram.toml").read_text(encoding="utf-8")).get("totp", {}).get("enabled", False))
        except (OSError, tomllib.TOMLDecodeError): return False
    def _record_error(self, error: Exception | str) -> None:
        message = str(error).replace("\n", " ")[-800:]
        self.last_error = __import__("re").sub(r"(?i)(token|password|secret)\s*[=:]\s*\S+", r"\1=[redacted]", message)
        state = json_file(self.settings.data_dir / "state" / "gateway-status.json", {})
        state.update({"last_error": self.last_error, "updated_at": time.time()}); write_json(self.settings.data_dir / "state" / "gateway-status.json", state)
    def _configure_owner_commands(self) -> dict[str, int]:
        if not self.api: return
        owners, verified, failed = self.contacts.owners(), 0, 0
        for owner in owners:
            try:
                if self.api.set_owner_commands(owner, self._totp_enabled()): verified += 1
                else: failed += 1; self._record_error("Telegram não confirmou os comandos privados do owner.")
            except Exception as error: failed += 1; self._record_error(error)
        summary = {"updated_at": time.time(), "owners": len(owners), "verified": verified, "failed": failed, "expected": ["new", "config", *( ["totp"] if self._totp_enabled() else [])]}
        state = json_file(self.settings.data_dir / "state" / "gateway-status.json", {}); state["commands"] = summary; write_json(self.settings.data_dir / "state" / "gateway-status.json", state)
        return summary
    def start(self) -> None:
        self.api = TelegramApi(Vault(self.settings).read(self.settings.token_entry)); self._restore_totp_cleanup(); self._configure_owner_commands(); threading.Thread(target=self._poll, daemon=True).start()
        with self.database_lock: queued = [int(row[0]) for row in self.database.execute("SELECT DISTINCT chat_id FROM inbox_items WHERE state='pending'").fetchall()]
        for chat_id in queued:
            with self.workers_lock:
                if chat_id not in self.workers: self.workers.add(chat_id); threading.Thread(target=self._worker, args=(chat_id,), daemon=True).start()
        if self.settings.app_server_enabled: threading.Thread(target=self._app_server_idle_watch, daemon=True).start()

    def _app_server_idle_watch(self) -> None:
        while not self.stop_event.wait(5):
            with self.app_server_lock:
                active = bool(self.app_turns)
                if self.app_server and self.app_server.running() and not active and time.monotonic() - self.last_app_activity >= self.settings.app_server_idle_seconds:
                    self.app_server.stop(); self.last_app_activity = time.monotonic()

    def _app_client(self) -> CodexAppServer:
        with self.app_server_lock:
            if not self.app_server:
                self.app_server = CodexAppServer(self.settings); self.app_server.notification_handler = self._app_notification; self.app_server.request_handler = self._app_server_request
            self.app_server.start(); self.last_app_activity = time.monotonic(); return self.app_server

    def _channel_state(self, chat_id: int, renew: bool = False) -> dict[str, Any]:
        now = time.time()
        with self.database_lock:
            self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at, last_interaction_at) VALUES (?, ?, ?)", (str(chat_id), now, now))
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
        with self.database_lock: self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at, last_interaction_at) VALUES (?, ?, ?)", (str(chat_id), now, now)); self.database.execute("UPDATE conversations SET reply_mode=?, voice_expires_at=?, settings_revision=settings_revision+1, updated_at=? WHERE chat_id=?", (mode, expiry, now, str(chat_id))); self.database.commit()
        return self._channel_state(chat_id)

    def _outbox(self, chat_id: int, turn_id: str) -> Path:
        value = self.staging_root / "outbox" / str(chat_id) / turn_id
        value.mkdir(parents=True, exist_ok=True); return value

    def _tts(self, text: str, output: Path) -> None:
        if not self.settings.voice_enabled: raise RuntimeError("Resposta em áudio não está disponível.")
        wrapper, config = self.settings.root / "available-skills" / "eccovox" / "scripts" / "eccovox.py", self.settings.root / "configs" / "eccovox.toml"
        # ``voice_profile`` seleciona o perfil local da skill (endpoint e ACLs).
        # Ele não é um perfil interno do EccoVox; reenviá-lo faria o runtime
        # rejeitar nomes locais como "eccovox" com HTTP 400.
        request = {"version": 1, "operation": "tts.synthesize", "text": text[:self.settings.voice_max_text], "output_path": str(output), "response_format": self.settings.voice_format, "confirm": True, "language": self.settings.voice_language, "speed": self.settings.voice_speed}
        process = subprocess.run([sys.executable, str(wrapper), "--config", str(config), "--profile", self.settings.voice_profile], input=json.dumps(request, ensure_ascii=False), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=180)
        try: result = json.loads(process.stdout)
        except ValueError as error: raise RuntimeError("EccoVox retornou resposta inválida.") from error
        if not result.get("ok") or not output.is_file() or output.is_symlink():
            error = result.get("error", {}) if isinstance(result, dict) else {}
            detail = str(error.get("message") or "sem detalhe seguro") if isinstance(error, dict) else "sem detalhe seguro"
            raise RuntimeError(f"EccoVox não conseguiu sintetizar a resposta: {detail}")

    def _generation(self, chat_id: int) -> int:
        with self.database_lock:
            self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at) VALUES (?, ?)", (str(chat_id), time.time()))
            row = self.database.execute("SELECT generation FROM conversations WHERE chat_id=?", (str(chat_id),)).fetchone(); self.database.commit()
        return int(row[0]) if row else 0

    def _attachment_rows(self, chat_id: int, limit: int = 50) -> list[dict[str, Any]]:
        generation = self._generation(chat_id)
        with self.database_lock:
            rows = self.database.execute("SELECT id, kind, name, mime_type, size, archive_path, created_at FROM conversation_attachments WHERE chat_id=? AND generation=? ORDER BY created_at DESC LIMIT ?", (str(chat_id), generation, max(1, min(limit, 50)))).fetchall()
        return [{"id": str(row[0]), "kind": str(row[1]), "name": str(row[2]), "mime_type": row[3], "size": int(row[4]), "archive_path": str(row[5]), "created_at": float(row[6])} for row in rows]

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
        declared = sum(int(item.get("file_size") or 0) for _, item, _ in candidates)
        if declared > self.settings.max_batch_attachment_bytes: raise RuntimeError("O conjunto de anexos excede o limite configurado.")
        result: list[dict[str, Any]] = []; generation = self._generation(chat_id)
        for kind, item, filename in candidates:
            file_id = item.get("file_id")
            if not isinstance(file_id, str) or not file_id: continue
            attachment_id, incoming = secrets.token_urlsafe(12), self.staging_root / "incoming" / secrets.token_urlsafe(10) / safe_name(filename, "attachment")
            self.api.download(file_id, incoming, self.settings.max_attachment_bytes)
            size = incoming.stat().st_size
            if sum(entry["size"] for entry in result) + size > self.settings.max_batch_attachment_bytes: incoming.unlink(missing_ok=True); raise RuntimeError("O conjunto de anexos excede o limite configurado.")
            with self.database_lock:
                total = self.database.execute("SELECT COALESCE(SUM(size), 0) FROM conversation_attachments WHERE chat_id=? AND generation=?", (str(chat_id), generation)).fetchone()[0]
            if int(total) + size > self.settings.max_retained_attachment_bytes: incoming.unlink(missing_ok=True); raise RuntimeError("A retenção de anexos desta conversa atingiu o limite configurado.")
            archive = self.archive_root / str(chat_id) / str(generation) / attachment_id / safe_name(filename, "attachment"); archive.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(incoming), str(archive)); shutil.rmtree(incoming.parent, ignore_errors=True)
            record = {"id": attachment_id, "kind": kind, "name": archive.name, "mime_type": str(item.get("mime_type") or ""), "size": size, "archive_path": str(archive), "created_at": time.time()}
            with self.database_lock:
                self.database.execute("INSERT INTO conversation_attachments(id, chat_id, generation, kind, name, mime_type, size, archive_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (attachment_id, str(chat_id), generation, kind, record["name"], record["mime_type"], size, str(archive), record["created_at"])); self.database.commit()
            result.append(record)
        return result

    def _stage_attachment(self, chat_id: int, attachment: dict[str, Any], turn: AppServerTurn) -> Path:
        source = Path(str(attachment["archive_path"])).resolve()
        if not under(source, self.archive_root) or not source.is_file() or source.is_symlink(): raise RuntimeError("O anexo retido não está disponível com segurança.")
        destination = self.staging_root / str(chat_id) / turn.thread_id / secrets.token_urlsafe(8) / safe_name(str(attachment["name"]), "attachment")
        destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination); turn.staging.append(destination)
        return destination

    def _enqueue_turn(self, chat_id: int, text: str, attachments: list[dict[str, Any]]) -> None:
        generation = self._generation(chat_id)
        with self.database_lock:
            pending = self.database.execute("SELECT COUNT(*) FROM inbox_items WHERE chat_id=? AND generation=? AND state IN ('pending', 'running')", (str(chat_id), generation)).fetchone()[0]
            if int(pending) >= self.settings.max_pending_items: raise RuntimeError("A fila desta conversa atingiu o limite configurado.")
            self.database.execute("INSERT INTO inbox_items(id, chat_id, generation, text, attachment_ids, state, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)", (secrets.token_urlsafe(12), str(chat_id), generation, text, json.dumps([item["id"] for item in attachments]), time.time())); self.database.commit()
        with self.workers_lock:
            if chat_id in self.workers: return
            self.workers.add(chat_id); threading.Thread(target=self._worker, args=(chat_id,), daemon=True).start()

    def _worker(self, chat_id: int) -> None:
        try:
            while not self.stop_event.is_set():
                generation = self._generation(chat_id)
                with self.database_lock:
                    row = self.database.execute("SELECT id, text, attachment_ids FROM inbox_items WHERE chat_id=? AND generation=? AND state='pending' ORDER BY created_at LIMIT 1", (str(chat_id), generation)).fetchone()
                    if row: self.database.execute("UPDATE inbox_items SET state='running' WHERE id=?", (str(row[0]),)); self.database.commit()
                if not row: return
                try:
                    wanted = json.loads(str(row[2])); available = {item["id"]: item for item in self._attachment_rows(chat_id, 50)}; attachments = [available[item] for item in wanted if item in available]
                    self._turn(chat_id, str(row[1]), attachments)
                    with self.database_lock: self.database.execute("DELETE FROM inbox_items WHERE id=?", (str(row[0]),)); self.database.commit()
                except Exception as error:
                    self._record_error(error)
                    with self.database_lock: self.database.execute("DELETE FROM inbox_items WHERE id=?", (str(row[0]),)); self.database.commit()
        finally:
            with self.workers_lock: self.workers.discard(chat_id)

    def _app_server_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "item/tool/call" or params.get("namespace") != "telegram_gateway": raise RuntimeError("Ferramenta do gateway não autorizada.")
        thread_id, tool = params.get("threadId"), params.get("tool")
        if not isinstance(thread_id, str) or not isinstance(tool, str): raise RuntimeError("Chamada de ferramenta inválida.")
        with self.app_server_lock: active = self.app_turns.get(thread_id)
        if not active: raise RuntimeError("Turno do Telegram não está ativo.")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if tool == "get_channel_state": return {"contentItems": [{"type": "inputText", "text": json.dumps(self._channel_state(active.chat_id), ensure_ascii=False)}], "success": True}
        if tool == "set_reply_mode":
            state = self._set_reply_mode(active.chat_id, str(arguments.get("mode") or "")); active.reply_mode = state["reply_mode"]
            return {"contentItems": [{"type": "inputText", "text": json.dumps(state, ensure_ascii=False)}], "success": True}
        if tool == "send_message":
            text, ttl = arguments.get("text"), arguments.get("ttl_seconds", 0)
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000: raise RuntimeError("text deve ter entre 1 e 4000 caracteres.")
            if not isinstance(ttl, int) or isinstance(ttl, bool) or not 0 <= ttl <= 86400: raise RuntimeError("ttl_seconds deve estar entre 0 e 86400.")
            if not self.api: raise RuntimeError("Gateway Telegram indisponível.")
            sent = self.api.send(active.chat_id, text, protect_content=bool(arguments.get("protect_content", False))); message_id = sent.get("message_id")
            if ttl and isinstance(message_id, int): self._schedule_delete(active.chat_id, message_id, ttl)
            return {"contentItems": [{"type": "inputText", "text": json.dumps({"sent": True, "ephemeral": bool(ttl)}, ensure_ascii=False)}], "success": True}
        if tool == "ask_menu":
            question, options, timeout = arguments.get("question"), arguments.get("options"), arguments.get("timeout_seconds", 120)
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000: raise RuntimeError("question deve ter entre 1 e 4000 caracteres.")
            if not isinstance(options, list) or not 2 <= len(options) <= 20 or not isinstance(timeout, int) or not 10 <= timeout <= 300: raise RuntimeError("Menu inválido.")
            normalized, ids = [], set()
            for option in options:
                if not isinstance(option, dict) or not isinstance(option.get("id"), str) or not isinstance(option.get("label"), str) or not 1 <= len(option["id"]) <= 64 or not 1 <= len(option["label"]) <= 64 or option["id"] in ids: raise RuntimeError("Cada opção exige id e label únicos de até 64 caracteres.")
                ids.add(option["id"]); normalized.append({"id": option["id"], "label": option["label"]})
            if not self.api: raise RuntimeError("Gateway Telegram indisponível.")
            token = secrets.token_urlsafe(12); keyboard = {"inline_keyboard": [[{"text": option["label"], "callback_data": f"ag:{token}:{index}"}] for index, option in enumerate(normalized)]}
            sent = self.api.send(active.chat_id, question, reply_markup=keyboard, protect_content=bool(arguments.get("protect_content", False))); message_id = sent.get("message_id")
            if not isinstance(message_id, int): raise RuntimeError("Telegram não retornou o identificador do menu.")
            session = MenuSession(token, active.chat_id, message_id, normalized)
            with self.menu_lock: self.menu_sessions[token] = session
            try:
                if not session.completed.wait(timeout): raise RuntimeError("O menu expirou sem seleção.")
                return {"contentItems": [{"type": "inputText", "text": json.dumps({"selected": session.selected}, ensure_ascii=False)}], "success": True}
            finally:
                with self.menu_lock: self.menu_sessions.pop(token, None)
                try: self.api.delete(active.chat_id, message_id)
                except Exception: pass
        if tool == "get_outbox":
            if not self.settings.agent_outbound_media: raise RuntimeError("Envio de mídia pelo agente está desabilitado.")
            active.outbox = active.outbox or self._outbox(active.chat_id, active.thread_id)
            return {"contentItems": [{"type": "inputText", "text": json.dumps({"outbox": str(active.outbox)}, ensure_ascii=False)}], "success": True}
        if tool == "send_file":
            if not self.settings.agent_outbound_media: raise RuntimeError("Envio de mídia pelo agente está desabilitado.")
            active.outbox = active.outbox or self._outbox(active.chat_id, active.thread_id); name = safe_name(str(arguments.get("file_name") or ""), "")
            path = (active.outbox / name).resolve()
            if not name or not under(path, active.outbox) or not path.is_file() or path.is_symlink() or path.stat().st_size > self.settings.max_outbound_media_bytes: raise RuntimeError("Arquivo do outbox inválido ou excede o limite.")
            kind = str(arguments.get("kind") or "document"); method, field = {"photo": ("sendPhoto", "photo"), "document": ("sendDocument", "document"), "audio": ("sendAudio", "audio")}[kind]
            result = self.api.send_file(method, active.chat_id, path, field, str(arguments.get("caption") or "")) if self.api else {}
            return {"contentItems": [{"type": "inputText", "text": json.dumps({"sent": True, "message_id": result.get("message_id")}, ensure_ascii=False)}], "success": True}
        if tool == "list_attachments":
            entries = [{key: entry[key] for key in ("id", "kind", "name", "mime_type", "size", "created_at")} for entry in self._attachment_rows(active.chat_id, int(arguments.get("limit", 20)))]
            return {"contentItems": [{"type": "inputText", "text": json.dumps(entries, ensure_ascii=False)}], "success": True}
        if tool == "materialize_attachment":
            wanted = str(arguments.get("attachment_id") or "")
            entry = next((item for item in self._attachment_rows(active.chat_id, 50) if item["id"] == wanted), None)
            if not entry: raise RuntimeError("Anexo não encontrado na conversa atual.")
            path = self._stage_attachment(active.chat_id, entry, active)
            data = {key: entry[key] for key in ("id", "kind", "name", "mime_type", "size")}; data["local_path"] = str(path)
            return {"contentItems": [{"type": "inputText", "text": json.dumps(data, ensure_ascii=False)}], "success": True}
        raise RuntimeError("Ferramenta do gateway desconhecida.")

    def _app_notification(self, method: str, params: dict[str, Any]) -> None:
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str): return
        with self.app_server_lock: active = self.app_turns.get(thread_id)
        if not active: return
        if method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else params
            active.status = str(turn.get("status") or "completed")
            if active.status != "completed": active.failure_detail = self._turn_failure_detail(turn)
            active.completed.set(); return
        if method != "item/completed": return
        item = params.get("item")
        if not isinstance(item, dict): return
        item_type = item.get("type")
        if item_type == "reasoning":
            fragments = item.get("summary") or item.get("content") or []
            if isinstance(fragments, str): text = fragments
            elif isinstance(fragments, list):
                text = "\n".join(fragment if isinstance(fragment, str) else str(fragment.get("text") or "") for fragment in fragments if isinstance(fragment, (str, dict)))
            else: text = ""
        else: text = item.get("text")
        if not isinstance(text, str) or not text.strip(): return
        if item_type == "agentMessage" and item.get("phase") in {None, "final_answer"}:
            active.final_text = text; return
        if item_type not in {"reasoning", "agentMessage"} or (item_type == "agentMessage" and item.get("phase") != "commentary"): return
        normalized = " ".join(text.split())
        if normalized in active.seen_thoughts: return
        active.seen_thoughts.add(normalized)
        if not self._conversation_settings(active.chat_id)["share_thoughts"] or not self.api: return
        try:
            sent = self.api.send(active.chat_id, f"💭 {text}", protect_content=True)
            if isinstance(sent.get("message_id"), int): active.thought_ids.append(sent["message_id"])
        except Exception as error: self._record_error(error)

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

    def _cleanup_thoughts(self, active: AppServerTurn) -> None:
        if not self._conversation_settings(active.chat_id)["delete_thoughts"] or not self.api: return
        for message_id in active.thought_ids:
            try: self.api.delete(active.chat_id, message_id)
            except Exception as error: self._record_error(error)

    def _app_thread(self, chat_id: int, client: CodexAppServer) -> str:
        state = self._channel_state(chat_id); bundle = self.instructions.compose(identity=self.identity, telegram=True, reply_mode=str(state["reply_mode"]), outbound_media=self.settings.agent_outbound_media)
        row = self.database.execute("SELECT codex_thread_id, instruction_baseline_hash FROM conversations WHERE chat_id=?", (str(chat_id),)).fetchone(); thread_id = str(row[0]) if row and row[0] else ""
        if thread_id and str(row[1] or "") != bundle.baseline_hash:
            self.database.execute("UPDATE conversations SET codex_thread_id=NULL, instruction_baseline_hash=NULL, updated_at=? WHERE chat_id=?", (time.time(), str(chat_id))); self.database.commit(); thread_id = ""
        params = {"cwd": str(self.settings.project), "approvalPolicy": self.settings.approval, "sandbox": self.settings.sandbox, "developerInstructions": bundle.text}
        model, _effort = self._execution_preferences(chat_id)
        if model: params["model"] = model
        if thread_id:
            try: client.request("thread/resume", {"threadId": thread_id, **params}); return thread_id
            except CodexProtocolError: self.database.execute("UPDATE conversations SET codex_thread_id=NULL WHERE chat_id=?", (str(chat_id),)); self.database.commit()
        tools = [{"type": "namespace", "name": "telegram_gateway", "description": "Interface restrita à DM Telegram ativa.", "tools": [{"type": "function", "name": "get_channel_state", "description": "Consulta o modo de resposta da conversa ativa.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}, {"type": "function", "name": "set_reply_mode", "description": "Define a resposta final como texto ou áudio nesta conversa.", "inputSchema": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["text", "audio"]}}, "required": ["mode"], "additionalProperties": False}}, {"type": "function", "name": "get_outbox", "description": "Retorna o diretório temporário seguro para arquivos que serão enviados neste turno.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}, {"type": "function", "name": "send_file", "description": "Envia um arquivo do outbox do turno à conversa ativa.", "inputSchema": {"type": "object", "properties": {"file_name": {"type": "string"}, "kind": {"type": "string", "enum": ["photo", "document", "audio"]}, "caption": {"type": "string"}}, "required": ["file_name", "kind"], "additionalProperties": False}}, {"type": "function", "name": "list_attachments", "description": "Lista os metadados dos anexos retidos na conversa atual.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}}, {"type": "function", "name": "materialize_attachment", "description": "Copia um anexo retido para o staging seguro do turno atual.", "inputSchema": {"type": "object", "properties": {"attachment_id": {"type": "string", "minLength": 1, "maxLength": 64}}, "required": ["attachment_id"], "additionalProperties": False}}]}]
        tools[0]["tools"].extend([{"type": "function", "name": "send_message", "description": "Envia mensagem adicional somente à DM Telegram ativa; pode expirar automaticamente.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 4000}, "ttl_seconds": {"type": "integer", "minimum": 0, "maximum": 86400}, "protect_content": {"type": "boolean"}}, "required": ["text"], "additionalProperties": False}}, {"type": "function", "name": "ask_menu", "description": "Exibe opções ao owner da DM ativa e aguarda a seleção com prazo limitado.", "inputSchema": {"type": "object", "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 4000}, "options": {"type": "array", "minItems": 2, "maxItems": 20, "items": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 64}, "label": {"type": "string", "minLength": 1, "maxLength": 64}}, "required": ["id", "label"], "additionalProperties": False}}, "timeout_seconds": {"type": "integer", "minimum": 10, "maximum": 300}, "protect_content": {"type": "boolean"}}, "required": ["question", "options"], "additionalProperties": False}}])
        started = client.request("thread/start", {**params, "serviceName": "onmyoji_telegram", "dynamicTools": tools}); thread = started.get("thread") if isinstance(started.get("thread"), dict) else started; thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
        if not thread_id: raise CodexProtocolError("Codex App Server não retornou uma thread")
        self.database.execute("UPDATE conversations SET codex_thread_id=?, instruction_baseline_hash=?, updated_at=? WHERE chat_id=?", (thread_id, bundle.baseline_hash, time.time(), str(chat_id))); self.database.commit(); return thread_id

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
        config = self.settings.root / "configs" / "eccovox.toml"; wrapper = self.settings.root / "available-skills" / "eccovox" / "scripts" / "eccovox.py"
        if not config.is_file() or not wrapper.is_file(): return None
        try:
            data = tomllib.loads(config.read_text(encoding="utf-8")); profiles = data.get("profiles", {})
            profile = next((name for name, value in profiles.items() if isinstance(name, str) and isinstance(value, dict) and name != "example"), "")
            if not profile: return None
            process = subprocess.run([sys.executable, str(wrapper), "--config", str(config), "--profile", profile], input=json.dumps({"version": 1, "operation": "stt.transcribe", "audio_path": str(path)}, ensure_ascii=False), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=150)
            result = json.loads(process.stdout)
            value = result.get("result", {}) if isinstance(result, dict) else {}
            return str(value.get("text") or "").strip() or None
        except Exception as error:
            self._record_error(f"Transcrição de voz indisponível: {error}"); return None

    def _app_turn(self, chat_id: int, text: str, attachments: list[dict[str, Any]] | None = None) -> tuple[str, str]:
        client = self._app_client(); thread_id = self._app_thread(chat_id, client); state = self._channel_state(chat_id); active = AppServerTurn(chat_id, thread_id, threading.Event(), reply_mode=state["reply_mode"])
        with self.app_server_lock: self.app_turns[thread_id] = active
        try:
            inputs: list[dict[str, Any]] = [{"type": "text", "text": text or "O owner enviou anexos sem mensagem textual."}]
            for attachment in attachments or []:
                staged = self._stage_attachment(chat_id, attachment, active)
                if attachment["kind"] == "photo": inputs.append({"type": "localImage", "path": str(staged)})
                elif attachment["kind"] == "voice":
                    transcript = self._transcribe_voice(staged); detail = f"\nTranscrição local: {transcript}" if transcript else "\nTranscrição local indisponível; trate o arquivo de voz diretamente."
                    inputs.append({"type": "text", "text": f"Mensagem de voz recebida: {attachment['name']}\nArquivo local temporário: {staged}{detail}"})
                else: inputs.append({"type": "text", "text": f"Anexo recebido ({attachment['kind']}): {attachment['name']}\nArquivo local temporário: {staged}"})
            model, effort = self._execution_preferences(chat_id)
            parameters: dict[str, Any] = {"threadId": thread_id, "input": inputs, "cwd": str(self.settings.project), "approvalPolicy": self.settings.approval, "sandboxPolicy": self._app_sandbox_policy(), "effort": effort}
            if model: parameters["model"] = model
            result = client.request("turn/start", parameters)
            turn = result.get("turn") if isinstance(result.get("turn"), dict) else result
            if isinstance(turn, dict): active.turn_id = str(turn.get("id") or "") or None
            if not active.completed.wait(self.settings.turn_timeout):
                if active.turn_id: client.request("turn/interrupt", {"threadId": thread_id, "turnId": active.turn_id})
                raise CodexProtocolError("O turno do Codex excedeu o tempo configurado")
            if active.final_text: return active.final_text, active.reply_mode
            if active.status == "completed": return "O agente concluiu o turno sem uma resposta textual.", active.reply_mode
            detail = active.failure_detail or client.last_error
            suffix = f": {detail}" if detail else ""
            raise CodexProtocolError(f"O turno do Codex terminou com estado {active.status}{suffix}")
        finally:
            with self.app_server_lock: self.app_turns.pop(thread_id, None); self.last_app_activity = time.monotonic()
            self._cleanup_thoughts(active)
            for staged in active.staging: shutil.rmtree(staged.parent, ignore_errors=True)
            if active.outbox: shutil.rmtree(active.outbox, ignore_errors=True)
    def _poll(self) -> None:
        assert self.api
        while not self.stop_event.is_set():
            try:
                for update in self.api.call("getUpdates", {"offset": self.offset, "timeout": self.settings.poll_timeout, "allowed_updates": ["message", "callback_query"]}) or []:
                    self.offset = int(update.get("update_id", self.offset)) + 1; self._update(update)
            except Exception as error: self._record_error(error); self.stop_event.wait(3)
    def _update(self, update: dict[str, Any]) -> None:
        if update.get("callback_query"): self._callback(update["callback_query"]); return
        message = update.get("message") or {}; chat, sender = message.get("chat") or {}, message.get("from") or {}
        if chat.get("type") != "private" or not isinstance(sender.get("id"), int): return
        text = str(message.get("text") or message.get("caption") or "").strip(); assert self.api
        if text.startswith("/"): self._delete_received_command(int(chat.get("id") or sender["id"]), message)
        if text.startswith("/pair ") and self.pair and time.time() < self.pair[1] and secrets.compare_digest(text[6:].strip(), self.pair[0]):
            self.contacts.add_owner(sender); self.pair = None; synced = self._configure_owner_commands()
            self.api.send(sender["id"], "Pairing concluído e comandos privados configurados." if synced.get("failed", 1) == 0 else "Pairing concluído, mas a configuração dos comandos falhou. Consulte o diagnóstico."); return
        if sender["id"] not in self.contacts.owners(): return
        channel = self._channel_state(sender["id"], renew=True)
        if channel["expired"]: self._ephemeral(sender["id"], "O modo de respostas em áudio foi desligado por inatividade.")
        if sender["id"] in self.totp_sessions:
            if text.startswith("/"):
                self._clear_totp_session(sender["id"]); self._ephemeral(sender["id"], "Fluxo TOTP cancelado por um novo comando.")
            else: self._totp_password(sender["id"], message)
            return
        command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold() if text else ""
        if command == "/new": self._new_conversation(sender["id"]); self.api.send(sender["id"], "Conversa reiniciada."); return
        if command == "/totp":
            if self._totp_enabled(): self._start_totp(sender["id"], message, text[len(text.split(maxsplit=1)[0]):].strip())
            else: self._ephemeral(sender["id"], "TOTP não está habilitado para este Shikigami. Configure-o no setup do Gateway Telegram.")
            return
        if command == "/config": self._open_config(sender["id"]); return
        if text.startswith("/"): self._ephemeral(sender["id"], "Comando inválido. Use /new, /config ou /totp quando habilitado."); return
        contains_attachment = any(isinstance(message.get(name), (dict, list)) and message.get(name) for name in ("photo", "document", "voice"))
        if not text and not contains_attachment: self._ephemeral(sender["id"], "Mensagem sem texto ou anexo reconhecido."); return
        if contains_attachment and not self.settings.app_server_enabled:
            self._ephemeral(sender["id"], "Anexos exigem que o App Server esteja habilitado na configuração do Gateway Telegram."); return
        try: attachments = self._collect_attachments(sender["id"], message) if contains_attachment else []
        except Exception as error:
            self._record_error(error); self._ephemeral(sender["id"], f"Não foi possível receber o anexo: {error}"); return
        try: self._enqueue_turn(sender["id"], text, attachments)
        except Exception as error: self._record_error(error); self._ephemeral(sender["id"], f"Não foi possível enfileirar a mensagem: {error}")

    def _delete_received_command(self, chat_id: int, message: dict[str, Any]) -> None:
        """Comandos são controles efêmeros e não devem permanecer no histórico da DM."""
        message_id = message.get("message_id")
        if not isinstance(message_id, int): return
        try:
            assert self.api; self.api.delete(chat_id, message_id)
        except Exception as error:
            self._record_error(f"Não foi possível excluir a mensagem de comando: {error}")

    def _new_conversation(self, chat_id: int) -> None:
        row = self.database.execute("SELECT codex_thread_id, generation FROM conversations WHERE chat_id=?", (str(chat_id),)).fetchone(); old = str(row[0]) if row and row[0] else ""; generation = int(row[1]) if row else 0
        self._remove_attachments(chat_id, generation)
        with self.database_lock:
            self.database.execute("DELETE FROM inbox_items WHERE chat_id=?", (str(chat_id),)); self.database.commit()
        self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at) VALUES (?, ?)", (str(chat_id), time.time()))
        self.database.execute("UPDATE conversations SET generation=generation+1, codex_thread_id=NULL, owner_model=NULL, owner_effort=NULL, updated_at=? WHERE chat_id=?", (time.time(), str(chat_id))); self.database.commit()
        if old and self.app_server and self.app_server.running():
            try: self.app_server.request("thread/delete", {"threadId": old})
            except Exception: pass

    def _conversation_settings(self, chat_id: int) -> dict[str, Any]:
        self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at) VALUES (?, ?)", (str(chat_id), time.time())); self.database.commit()
        row = self.database.execute("SELECT share_thoughts, delete_thoughts, reply_mode, voice_expires_at, owner_model, owner_effort FROM conversations WHERE chat_id=?", (str(chat_id),)).fetchone()
        return {"share_thoughts": bool(row[0]), "delete_thoughts": bool(row[1]), "reply_mode": str(row[2] or "text"), "voice_expires_at": row[3], "owner_model": str(row[4] or ""), "owner_effort": str(row[5] or "")} if row else {"share_thoughts": True, "delete_thoughts": True, "reply_mode": "text", "voice_expires_at": None, "owner_model": "", "owner_effort": ""}

    def _execution_preferences(self, chat_id: int) -> tuple[str, str]:
        settings = self._conversation_settings(chat_id)
        model = settings["owner_model"] if self.settings.owner_execution_preferences and settings["owner_model"] in self.settings.owner_allowed_models else self.settings.model
        effort = settings["owner_effort"] if self.settings.owner_execution_preferences and settings["owner_effort"] in self.settings.owner_allowed_efforts else self.settings.effort
        return model, effort

    @staticmethod
    def _config_keyboard(token: str, settings: dict[str, Any], thoughts: bool = False, voice_available: bool = False, execution_available: bool = False, allowed_models: tuple[str, ...] = (), allowed_efforts: tuple[str, ...] = ()) -> tuple[str, dict[str, Any]]:
        if not thoughts:
            rows = [[{"text": "💭 Pensamentos", "callback_data": f"cfg:{token}:thoughts"}]]
            if voice_available: rows.append([{"text": ("🔊 Respostas em áudio" if settings["reply_mode"] == "audio" else "💬 Respostas em texto"), "callback_data": f"cfg:{token}:audio"}])
            if execution_available: rows.append([{"text": "⚙️ Execução", "callback_data": f"cfg:{token}:execution"}])
            rows.append([{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]); return "Configurações do bot", {"inline_keyboard": rows}
        if thoughts == "execution":
            model = settings["owner_model"] or allowed_models[0]; effort = settings["owner_effort"] or allowed_efforts[0]
            return "Configurações › Execução", {"inline_keyboard": [[{"text": f"Modelo: {model}", "callback_data": f"cfg:{token}:model"}], [{"text": f"Raciocínio: {effort}", "callback_data": f"cfg:{token}:effort"}], [{"text": "↺ Padrão do setup", "callback_data": f"cfg:{token}:execution-reset"}], [{"text": "‹ Voltar", "callback_data": f"cfg:{token}:back"}], [{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]]}
        shared, deleted = ("☑" if settings["share_thoughts"] else "☐"), ("☑" if settings["delete_thoughts"] else "☐")
        return "Configurações › Pensamentos", {"inline_keyboard": [[{"text": f"{shared} Compartilha Pensamentos", "callback_data": f"cfg:{token}:share"}], [{"text": f"{deleted} Excluir Pensamentos", "callback_data": f"cfg:{token}:delete"}], [{"text": "‹ Voltar", "callback_data": f"cfg:{token}:back"}], [{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]]}

    def _open_config(self, chat_id: int) -> None:
        assert self.api
        token, settings = secrets.token_urlsafe(8), self._conversation_settings(chat_id); text, keyboard = self._config_keyboard(token, settings, voice_available=self.settings.voice_enabled, execution_available=self.settings.owner_execution_preferences, allowed_models=self.settings.owner_allowed_models, allowed_efforts=self.settings.owner_allowed_efforts)
        sent = self._ephemeral(chat_id, text, 180, reply_markup=keyboard)
        if isinstance(sent.get("message_id"), int): self.config_sessions[token] = {"chat_id": chat_id, "message_id": sent["message_id"], "expires_at": time.time() + 180}

    def _config_callback(self, chat_id: int, callback: dict[str, Any], parts: list[str]) -> bool:
        if len(parts) != 3 or parts[0] != "cfg": return False
        session = self.config_sessions.get(parts[1]); action = parts[2]
        if not session or session["chat_id"] != chat_id or time.time() > float(session["expires_at"]): self._ephemeral(chat_id, "Esta configuração expirou."); return True
        if action == "close": self.config_sessions.pop(parts[1], None); self._delete_later(chat_id, int(session["message_id"])); return True
        if action == "audio":
            try: self._set_reply_mode(chat_id, "text" if self._conversation_settings(chat_id)["reply_mode"] == "audio" else "audio")
            except Exception as error: self._ephemeral(chat_id, str(error)); return True
        settings = self._conversation_settings(chat_id)
        if action in {"share", "delete"}:
            column = "share_thoughts" if action == "share" else "delete_thoughts"; self.database.execute(f"UPDATE conversations SET {column} = NOT {column}, updated_at=? WHERE chat_id=?", (time.time(), str(chat_id))); self.database.commit(); settings = self._conversation_settings(chat_id); thoughts = True
        elif action in {"model", "effort", "execution-reset"}:
            if not self.settings.owner_execution_preferences: self._ephemeral(chat_id, "Preferências de execução não estão liberadas pelo setup."); return True
            if action == "execution-reset": self.database.execute("UPDATE conversations SET owner_model=NULL, owner_effort=NULL, updated_at=? WHERE chat_id=?", (time.time(), str(chat_id)))
            else:
                column, permitted, current = ("owner_model", self.settings.owner_allowed_models, str(settings["owner_model"] or "")) if action == "model" else ("owner_effort", self.settings.owner_allowed_efforts, str(settings["owner_effort"] or ""))
                value = permitted[(permitted.index(current) + 1) % len(permitted)] if current in permitted else permitted[0]
                self.database.execute(f"UPDATE conversations SET {column}=?, updated_at=? WHERE chat_id=?", (value, time.time(), str(chat_id)))
            self.database.commit(); settings = self._conversation_settings(chat_id); thoughts = "execution"
        else: thoughts = "execution" if action == "execution" else action == "thoughts"
        if action not in {"thoughts", "share", "delete", "back", "audio", "execution", "model", "effort", "execution-reset"}: self._ephemeral(chat_id, "Opção de configuração inválida."); return True
        text, keyboard = self._config_keyboard(parts[1], settings, thoughts, self.settings.voice_enabled, self.settings.owner_execution_preferences, self.settings.owner_allowed_models, self.settings.owner_allowed_efforts)
        try: assert self.api; self.api.edit(chat_id, int(session["message_id"]), text, keyboard)
        except Exception as error: self._record_error(error); self._ephemeral(chat_id, "Não foi possível atualizar esta configuração.")
        return True
    def _turn(self, chat_id: int, text: str, attachments: list[dict[str, Any]] | None = None) -> None:
        with self.turn_locks_lock: lock = self.turn_locks.setdefault(chat_id, threading.Lock())
        with lock: self._turn_serial(chat_id, text, attachments or [])

    def _turn_serial(self, chat_id: int, text: str, attachments: list[dict[str, Any]]) -> None:
        self.work.acquire()
        typing_stop = threading.Event()
        def renew_typing() -> None:
            while not typing_stop.is_set():
                try:
                    if self.api: self.api.typing(chat_id)
                except Exception as error: self._record_error(error)
                typing_stop.wait(4)
        typing_thread = threading.Thread(target=renew_typing, daemon=True); typing_thread.start()
        try:
            mode = self._channel_state(chat_id)["reply_mode"]
            if self.settings.app_server_enabled:
                answer, mode = self._app_turn(chat_id, text, attachments)
            else: raise RuntimeError("O Gateway Telegram exige que o App Server esteja habilitado.")
            if answer and self.api:
                if mode == "audio":
                    output = self._outbox(chat_id, secrets.token_urlsafe(10)) / f"resposta.{self.settings.voice_format}"
                    try:
                        self._tts(answer, output); self.api.send_file("sendVoice", chat_id, output, "voice")
                    except Exception as voice_error:
                        self._record_error(voice_error)
                        if self.settings.voice_fallback_to_text: self.api.send(chat_id, "Não consegui gerar a resposta em áudio.\n\n" + answer[-3500:])
                        else: self._ephemeral(chat_id, "Não consegui gerar a resposta em áudio. Tente novamente ou altere o modo para texto.")
                    finally: shutil.rmtree(output.parent, ignore_errors=True)
                else: self.api.send(chat_id, answer[-4000:])
        except Exception as error:
            self._record_error(error)
            if self.api: self.api.send(chat_id, "O agente não conseguiu concluir esta solicitação.")
        finally:
            typing_stop.set(); typing_thread.join(timeout=1); self.work.release()

    def _ephemeral(self, chat_id: int, text: str, seconds: float = 8, **values: Any) -> dict[str, Any]:
        assert self.api
        protect_content = bool(values.pop("protect_content", True))
        message = self.api.send(chat_id, text, protect_content=protect_content, **values)
        message_id = message.get("message_id")
        if isinstance(message_id, int): self._schedule_delete(chat_id, message_id, seconds)
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
        assert self.api
        try:
            profile = tomllib.loads((self.settings.data_dir / "telegram.toml").read_text(encoding="utf-8")).get("totp", {})
            if not str(profile.get("real_password_entry") or "") or not str(profile.get("fake_password_entry") or ""):
                self._ephemeral(chat_id, "TOTP está habilitado, mas as entradas de senha real e falsa não foram configuradas."); return
            self._clear_totp_session(chat_id)
            self.totp_sessions[chat_id] = {"phase": "password", "query": query, "expires_at": time.time() + 180, "message_ids": []}
            prompt = self._ephemeral(chat_id, "Informe a senha TOTP na próxima mensagem. Ela será apagada.", 180)
            if isinstance(prompt.get("message_id"), int): self.totp_sessions[chat_id]["message_ids"].append(prompt["message_id"])
        except Exception as error:
            self._record_error(error)
            try: self.api.send(chat_id, "Não foi possível iniciar o TOTP. Consulte o diagnóstico do gateway.")
            except Exception: pass

    def _totp_password(self, chat_id: int, message: dict[str, Any]) -> None:
        assert self.api
        session = self.totp_sessions.pop(chat_id, None)
        try:
            message_id = message.get("message_id")
            if isinstance(message_id, int): self.api.delete(chat_id, message_id)
        except Exception: pass
        self._delete_totp_messages(chat_id, session)
        if not session or session.get("phase") != "password" or time.time() > float(session["expires_at"]): self._ephemeral(chat_id, "Sessão TOTP expirada."); return
        typing_stop = threading.Event()
        def renew_typing() -> None:
            while not typing_stop.is_set():
                try: self.api.typing(chat_id)
                except Exception as error: self._record_error(error)
                typing_stop.wait(4)
        typing_thread = threading.Thread(target=renew_typing, daemon=True); typing_thread.start()
        try:
            data = tomllib.loads((self.settings.data_dir / "telegram.toml").read_text(encoding="utf-8")); profile = data.get("totp", {})
            real, fake = Vault(self.settings).read(str(profile.get("real_password_entry") or "")), Vault(self.settings).read(str(profile.get("fake_password_entry") or ""))
            supplied = str(message.get("text") or "")
            if secrets.compare_digest(supplied, fake): self._ephemeral(chat_id, "Não existe TOTP cadastrado."); return
            if not secrets.compare_digest(supplied, real): self._ephemeral(chat_id, "Senha TOTP inválida."); return
            raw = Vault(self.settings).request({"operation": "list.totp"}).get("entries", [])
            entries = self._filter_totp([str(item["path"]) for item in raw if isinstance(item, dict) and isinstance(item.get("path"), str)], str(session["query"]))
            if not entries: self._ephemeral(chat_id, "Nenhuma entrada TOTP encontrada."); return
            token = secrets.token_urlsafe(8); self.totp_sessions[chat_id] = {"phase": "selection", "entries": entries, "token": token, "expires_at": time.time() + 180, "message_ids": []}
            self._show_totp_page(chat_id, 0)
        except Exception as error:
            self._record_error(error); self._ephemeral(chat_id, "Não foi possível consultar os TOTPs. Consulte o diagnóstico do gateway.")
        finally:
            typing_stop.set(); typing_thread.join(timeout=1)

    def _show_totp_page(self, chat_id: int, page: int) -> None:
        assert self.api
        session = self.totp_sessions.get(chat_id)
        if not session or session.get("phase") != "selection" or time.time() > float(session["expires_at"]): self._clear_totp_session(chat_id); self._ephemeral(chat_id, "Sessão TOTP expirada."); return
        self._delete_totp_messages(chat_id, session)
        entries, token = session["entries"], session["token"]; size = 10; start = page * size; chunk = entries[start:start + size]
        keyboard = [[{"text": entry[-48:], "callback_data": f"totp:{token}:{start + index}"}] for index, entry in enumerate(chunk)]
        navigation = []
        if page: navigation.append({"text": "‹", "callback_data": f"totp:{token}:p:{page - 1}"})
        if start + size < len(entries): navigation.append({"text": "›", "callback_data": f"totp:{token}:p:{page + 1}"})
        if navigation: keyboard.append(navigation)
        keyboard.append([{"text": "Cancelar", "callback_data": f"totp:{token}:x"}])
        sent = self._ephemeral(chat_id, "Escolha a entrada TOTP:", 180, reply_markup={"inline_keyboard": keyboard})
        if isinstance(sent.get("message_id"), int): session.setdefault("message_ids", []).append(sent["message_id"])

    def _delete_totp_messages(self, chat_id: int, session: dict[str, Any] | None) -> None:
        if not session: return
        for message_id in list(session.get("message_ids", [])):
            if isinstance(message_id, int): self._delete_later(chat_id, message_id)
        session["message_ids"] = []

    def _clear_totp_session(self, chat_id: int) -> dict[str, Any] | None:
        session = self.totp_sessions.pop(chat_id, None); self._delete_totp_messages(chat_id, session); return session

    def _callback(self, callback: dict[str, Any]) -> None:
        assert self.api
        sender = callback.get("from") or {}; message = callback.get("message") or {}; chat = message.get("chat") or {}; chat_id = sender.get("id")
        if chat.get("type") != "private" or not isinstance(chat_id, int) or chat_id not in self.contacts.owners(): return
        data = str(callback.get("data") or ""); parts = data.split(":")
        try: self.api.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
        except Exception: pass
        if len(parts) == 3 and parts[0] == "ag":
            with self.menu_lock: session = self.menu_sessions.get(parts[1])
            try: index = int(parts[2])
            except ValueError: index = -1
            if not session or session.chat_id != chat_id or session.message_id != message.get("message_id") or not 0 <= index < len(session.options): self._ephemeral(chat_id, "Seleção inválida ou expirada."); return
            session.selected = session.options[index]; session.completed.set(); return
        if self._config_callback(chat_id, callback, parts): return
        if len(parts) < 3 or parts[0] != "totp": self._ephemeral(chat_id, "Ação inválida ou expirada."); return
        session = self.totp_sessions.get(chat_id)
        if not session or session.get("phase") != "selection" or not secrets.compare_digest(str(session.get("token", "")), parts[1]) or time.time() > float(session.get("expires_at", 0)): self._ephemeral(chat_id, "Seleção TOTP expirada. Inicie /totp novamente."); return
        if len(parts) == 4 and parts[2] == "p": self._show_totp_page(chat_id, int(parts[3])); return
        if parts[2] == "x": self._clear_totp_session(chat_id); self._ephemeral(chat_id, "TOTP cancelado."); return
        try:
            entry = session["entries"][int(parts[2])]; code = Vault(self.settings).read(entry, "totp")
            self._clear_totp_session(chat_id); remaining = 30 - (int(time.time()) % 30)
            self._ephemeral(chat_id, code, remaining + 5, protect_content=False); self._ephemeral(chat_id, f"Expira em {remaining} segundos.", remaining + 5)
        except Exception as error:
            self._record_error(error); self._ephemeral(chat_id, "Não foi possível obter este TOTP.")
    def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "ping": return {"service": "telegram", "state": "running"}
        if method == "telegram.status": return {"owners": len(self.contacts.owners()), "listener": "running", "totp_enabled": self._totp_enabled(), "last_error": self.last_error, "commands": json_file(self.settings.data_dir / "state" / "gateway-status.json", {}).get("commands"), "execution_mode": "app-server" if self.settings.app_server_enabled else "codex-exec", "app_server_running": bool(self.app_server and self.app_server.running()), "app_server_idle_timeout_seconds": self.settings.app_server_idle_seconds}
        if method == "telegram.sync-commands": return self._configure_owner_commands()
        if method == "telegram.pair-request":
            self.pair = (f"{secrets.randbelow(1_000_000):06d}", time.time() + min(600, max(60, int(params.get("ttl_seconds", 300)))))
            return {"pin": self.pair[0], "expires_at": self.pair[1]}
        if method == "telegram.owners": return sorted(self.contacts.owners())
        if method == "telegram.send":
            if not self.api: raise RuntimeError("listener unavailable")
            self.api.send(int(params["chat_id"]), str(params["text"])); return {"sent": True}
        if method == "shutdown":
            self.stop_event.set()
            if self.app_server: self.app_server.stop()
            return {"state": "stopping"}
        raise ValueError("unknown Telegram method")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--data-dir", type=Path, required=True); parser.add_argument("--onmyoji-root", type=Path, required=True); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, required=True); parser.add_argument("--token", required=True); args = parser.parse_args(argv)
    gateway = Gateway(Settings.load(args.onmyoji_root.resolve(), args.data_dir)); gateway.start(); RpcServer(args.host, args.port, args.token, gateway.handle).serve_forever(gateway.stop_event); return 0


if __name__ == "__main__": raise SystemExit(main())
