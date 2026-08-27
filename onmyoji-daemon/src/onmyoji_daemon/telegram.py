from __future__ import annotations

import argparse
import fnmatch
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import re
import secrets
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
    developer_file: str
    app_server_enabled: bool
    app_server_idle_seconds: int

    @classmethod
    def load(cls, root: Path, data_dir: Path) -> "Settings":
        path = data_dir / "telegram.toml"
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1: raise ValueError("unsupported telegram schema")
        telegram, agent, app_server = data.get("telegram", {}), data.get("agent", {}), data.get("app_server", {})
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
        project = Path(str(system.get("project_directory") or "")).resolve()
        if not project.is_dir(): raise ValueError("configured Shikigami workspace does not exist")
        profile, entry = str(telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or "")
        if not profile or not entry: raise ValueError("KeePass profile and token entry are required")
        idle = int(app_server.get("idle_timeout_seconds") or 1800)
        if idle < 60: raise ValueError("App Server idle timeout must be at least 60 seconds")
        return cls(root, data_dir, profile, entry, project, str(system.get("executable") or "codex"), str(system.get("model") or ""), str(system.get("model_reasoning_effort") or "medium"), str(system.get("sandbox_mode") or "workspace-write"), str(system.get("approval_policy") or "never"), int(telegram.get("poll_timeout_seconds") or 30), int(agent.get("turn_timeout_seconds") or 900), max(1, int(agent.get("max_parallel_conversations") or 1)), str(agent.get("developer_file") or ""), bool(app_server.get("enabled", False)), idle)


class CodexProtocolError(RuntimeError): pass


class CodexAppServer:
    """Processo Codex persistente, sempre privado à instância Onmyōji atual."""
    def __init__(self, settings: Settings):
        self.settings, self.process, self.reader, self.stderr_reader = settings, None, None, None
        self.write_lock, self.pending_lock, self.pending, self.next_id = threading.Lock(), threading.Lock(), {}, 1
        self.notification_handler: Any = None; self.last_error: str | None = None
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
                try: self._send({"id": request_id, "error": {"code": -32000, "message": "Interactive gateway requests are disabled"}})
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
    final_text: str = ""


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
    def __init__(self, token: str): self.base = f"https://api.telegram.org/bot{token}/"
    def call(self, method: str, values: dict[str, Any] | None = None) -> Any:
        body = urlencode({key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in (values or {}).items()}).encode()
        with urlopen(Request(self.base + method, data=body), timeout=45) as response: payload = json.loads(response.read())
        if not payload.get("ok"): raise RuntimeError("Telegram API rejected request")
        return payload.get("result")
    def send(self, chat_id: int, text: str, **values: Any) -> dict[str, Any]: return self.call("sendMessage", {"chat_id": chat_id, "text": text, **values})
    def typing(self, chat_id: int) -> None: self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    def delete(self, chat_id: int, message_id: int) -> None: self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    def edit(self, chat_id: int, message_id: int, text: str, keyboard: dict[str, Any]) -> None: self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": keyboard})
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


class Gateway:
    def __init__(self, settings: Settings):
        self.settings, self.stop_event = settings, threading.Event(); self.contacts = Contacts(settings.data_dir / "contacts.json")
        (settings.data_dir / "state").mkdir(parents=True, exist_ok=True); self.database = sqlite3.connect(settings.data_dir / "state" / "gateway.sqlite3", check_same_thread=False)
        self.database.execute("CREATE TABLE IF NOT EXISTS conversations(chat_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL, share_thoughts INTEGER NOT NULL DEFAULT 1, delete_thoughts INTEGER NOT NULL DEFAULT 1, codex_thread_id TEXT)")
        for column, definition in (("share_thoughts", "INTEGER NOT NULL DEFAULT 1"), ("delete_thoughts", "INTEGER NOT NULL DEFAULT 1"), ("codex_thread_id", "TEXT")):
            try: self.database.execute(f"ALTER TABLE conversations ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError: pass
        self.database.commit()
        self.api: TelegramApi | None = None; self.pair: tuple[str, float] | None = None; self.offset = 0; self.work = threading.BoundedSemaphore(settings.parallel); self.totp_sessions: dict[int, dict[str, Any]] = {}; self.config_sessions: dict[str, dict[str, Any]] = {}; self.last_error: str | None = None; self.cleanup_path = settings.data_dir / "state" / "totp-cleanup.json"; self.pending_deletions = json_file(self.cleanup_path, {}); self.app_server: CodexAppServer | None = None; self.app_server_lock = threading.RLock(); self.app_turns: dict[str, AppServerTurn] = {}; self.last_app_activity = time.monotonic()
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
        if self.settings.app_server_enabled: threading.Thread(target=self._app_server_idle_watch, daemon=True).start()

    def _app_server_idle_watch(self) -> None:
        while not self.stop_event.wait(5):
            with self.app_server_lock:
                active = bool(self.app_turns)
                if self.app_server and self.app_server.running() and not active and time.monotonic() - self.last_app_activity >= self.settings.app_server_idle_seconds:
                    self.app_server.stop(); self.last_app_activity = time.monotonic()

    def _app_client(self) -> CodexAppServer:
        with self.app_server_lock:
            if not self.app_server: self.app_server = CodexAppServer(self.settings); self.app_server.notification_handler = self._app_notification
            self.app_server.start(); self.last_app_activity = time.monotonic(); return self.app_server

    def _app_notification(self, method: str, params: dict[str, Any]) -> None:
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str): return
        with self.app_server_lock: active = self.app_turns.get(thread_id)
        if not active: return
        if method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else params; active.status = str(turn.get("status") or "completed"); active.completed.set(); return
        if method != "item/completed": return
        item = params.get("item")
        if not isinstance(item, dict): return
        if item.get("type") == "agentMessage" and item.get("phase") in {None, "final_answer"}:
            text = item.get("text")
            if isinstance(text, str): active.final_text = text

    def _app_thread(self, chat_id: int, client: CodexAppServer) -> str:
        row = self.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id=?", (str(chat_id),)).fetchone(); thread_id = str(row[0]) if row and row[0] else ""
        params = {"cwd": str(self.settings.project), "approvalPolicy": self.settings.approval, "sandbox": self.settings.sandbox, "developerInstructions": GATEWAY_INSTRUCTIONS}
        if self.settings.model: params["model"] = self.settings.model
        if thread_id:
            try: client.request("thread/resume", {"threadId": thread_id, **params}); return thread_id
            except CodexProtocolError: self.database.execute("UPDATE conversations SET codex_thread_id=NULL WHERE chat_id=?", (str(chat_id),)); self.database.commit()
        started = client.request("thread/start", {**params, "serviceName": "onmyoji_telegram"}); thread = started.get("thread") if isinstance(started.get("thread"), dict) else started; thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
        if not thread_id: raise CodexProtocolError("Codex App Server não retornou uma thread")
        self.database.execute("UPDATE conversations SET codex_thread_id=?, updated_at=? WHERE chat_id=?", (thread_id, time.time(), str(chat_id))); self.database.commit(); return thread_id

    def _app_sandbox_policy(self) -> dict[str, Any]:
        if self.settings.sandbox == "danger-full-access": return {"type": "dangerFullAccess"}
        if self.settings.sandbox == "read-only": return {"type": "readOnly", "networkAccess": False}
        roots = [str(self.settings.project)]
        try:
            system = tomllib.loads((self.settings.root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")); extra = system.get("sandbox_workspace_write", {}).get("writable_roots", [])
            roots.extend(str(Path(value).resolve()) for value in extra if isinstance(value, str))
        except (OSError, tomllib.TOMLDecodeError, AttributeError): pass
        return {"type": "workspaceWrite", "writableRoots": list(dict.fromkeys(roots)), "networkAccess": False}

    def _app_turn(self, chat_id: int, text: str) -> str:
        client = self._app_client(); thread_id = self._app_thread(chat_id, client); active = AppServerTurn(chat_id, thread_id, threading.Event())
        with self.app_server_lock: self.app_turns[thread_id] = active
        try:
            parameters: dict[str, Any] = {"threadId": thread_id, "input": [{"type": "text", "text": text}], "cwd": str(self.settings.project), "approvalPolicy": self.settings.approval, "sandboxPolicy": self._app_sandbox_policy(), "effort": self.settings.effort}
            if self.settings.model: parameters["model"] = self.settings.model
            result = client.request("turn/start", parameters)
            turn = result.get("turn") if isinstance(result.get("turn"), dict) else result
            if isinstance(turn, dict): active.turn_id = str(turn.get("id") or "") or None
            if not active.completed.wait(self.settings.turn_timeout):
                if active.turn_id: client.request("turn/interrupt", {"threadId": thread_id, "turnId": active.turn_id})
                raise CodexProtocolError("O turno do Codex excedeu o tempo configurado")
            if active.final_text: return active.final_text
            if active.status == "completed": return "O agente concluiu o turno sem uma resposta textual."
            raise CodexProtocolError(f"O turno do Codex terminou com estado {active.status}")
        finally:
            with self.app_server_lock: self.app_turns.pop(thread_id, None); self.last_app_activity = time.monotonic()
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
        text = str(message.get("text") or "").strip(); assert self.api
        if text.startswith("/pair ") and self.pair and time.time() < self.pair[1] and secrets.compare_digest(text[6:].strip(), self.pair[0]):
            self.contacts.add_owner(sender); self.pair = None; synced = self._configure_owner_commands()
            self.api.send(sender["id"], "Pairing concluído e comandos privados configurados." if synced.get("failed", 1) == 0 else "Pairing concluído, mas a configuração dos comandos falhou. Consulte o diagnóstico."); return
        if sender["id"] not in self.contacts.owners(): return
        if sender["id"] in self.totp_sessions:
            if text.startswith("/"):
                self._clear_totp_session(sender["id"]); self._ephemeral(sender["id"], "Fluxo TOTP cancelado por um novo comando.")
            else: self._totp_password(sender["id"], message)
            return
        command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold()
        if command == "/new": self._new_conversation(sender["id"]); self.api.send(sender["id"], "Conversa reiniciada."); return
        if command == "/totp":
            if self._totp_enabled(): self._start_totp(sender["id"], message, text[len(text.split(maxsplit=1)[0]):].strip())
            else: self._ephemeral(sender["id"], "TOTP não está habilitado para este Shikigami. Configure-o no setup do Gateway Telegram.")
            return
        if command == "/config": self._open_config(sender["id"]); return
        if text.startswith("/"): self._ephemeral(sender["id"], "Comando inválido. Use /new, /config ou /totp quando habilitado."); return
        threading.Thread(target=self._turn, args=(sender["id"], text), daemon=True).start()

    def _new_conversation(self, chat_id: int) -> None:
        row = self.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id=?", (str(chat_id),)).fetchone(); old = str(row[0]) if row and row[0] else ""
        self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at) VALUES (?, ?)", (str(chat_id), time.time()))
        self.database.execute("UPDATE conversations SET generation=generation+1, codex_thread_id=NULL, updated_at=? WHERE chat_id=?", (time.time(), str(chat_id))); self.database.commit()
        if old and self.app_server and self.app_server.running():
            try: self.app_server.request("thread/delete", {"threadId": old})
            except Exception: pass

    def _conversation_settings(self, chat_id: int) -> dict[str, bool]:
        self.database.execute("INSERT OR IGNORE INTO conversations(chat_id, updated_at) VALUES (?, ?)", (str(chat_id), time.time())); self.database.commit()
        row = self.database.execute("SELECT share_thoughts, delete_thoughts FROM conversations WHERE chat_id=?", (str(chat_id),)).fetchone()
        return {"share_thoughts": bool(row[0]), "delete_thoughts": bool(row[1])} if row else {"share_thoughts": True, "delete_thoughts": True}

    @staticmethod
    def _config_keyboard(token: str, settings: dict[str, bool], thoughts: bool = False) -> tuple[str, dict[str, Any]]:
        if not thoughts: return "Configurações do bot", {"inline_keyboard": [[{"text": "Pensamentos", "callback_data": f"cfg:{token}:thoughts"}], [{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]]}
        shared, deleted = ("☑" if settings["share_thoughts"] else "☐"), ("☑" if settings["delete_thoughts"] else "☐")
        return "Configurações › Pensamentos", {"inline_keyboard": [[{"text": f"{shared} Compartilha Pensamentos", "callback_data": f"cfg:{token}:share"}], [{"text": f"{deleted} Excluir Pensamentos", "callback_data": f"cfg:{token}:delete"}], [{"text": "‹ Voltar", "callback_data": f"cfg:{token}:back"}], [{"text": "Fechar", "callback_data": f"cfg:{token}:close"}]]}

    def _open_config(self, chat_id: int) -> None:
        assert self.api
        token, settings = secrets.token_urlsafe(8), self._conversation_settings(chat_id); text, keyboard = self._config_keyboard(token, settings)
        sent = self._ephemeral(chat_id, text, 180, reply_markup=keyboard)
        if isinstance(sent.get("message_id"), int): self.config_sessions[token] = {"chat_id": chat_id, "message_id": sent["message_id"], "expires_at": time.time() + 180}

    def _config_callback(self, chat_id: int, callback: dict[str, Any], parts: list[str]) -> bool:
        if len(parts) != 3 or parts[0] != "cfg": return False
        session = self.config_sessions.get(parts[1]); action = parts[2]
        if not session or session["chat_id"] != chat_id or time.time() > float(session["expires_at"]): self._ephemeral(chat_id, "Esta configuração expirou."); return True
        if action == "close": self.config_sessions.pop(parts[1], None); self._delete_later(chat_id, int(session["message_id"])); return True
        settings = self._conversation_settings(chat_id)
        if action in {"share", "delete"}:
            column = "share_thoughts" if action == "share" else "delete_thoughts"; self.database.execute(f"UPDATE conversations SET {column} = NOT {column}, updated_at=? WHERE chat_id=?", (time.time(), str(chat_id))); self.database.commit(); settings = self._conversation_settings(chat_id); thoughts = True
        else: thoughts = action == "thoughts"
        if action not in {"thoughts", "share", "delete", "back"}: self._ephemeral(chat_id, "Opção de configuração inválida."); return True
        text, keyboard = self._config_keyboard(parts[1], settings, thoughts)
        try: assert self.api; self.api.edit(chat_id, int(session["message_id"]), text, keyboard)
        except Exception as error: self._record_error(error); self._ephemeral(chat_id, "Não foi possível atualizar esta configuração.")
        return True
    def _turn(self, chat_id: int, text: str) -> None:
        if not self.work.acquire(timeout=1): return
        typing_stop = threading.Event()
        def renew_typing() -> None:
            while not typing_stop.is_set():
                try:
                    if self.api: self.api.typing(chat_id)
                except Exception as error: self._record_error(error)
                typing_stop.wait(4)
        typing_thread = threading.Thread(target=renew_typing, daemon=True); typing_thread.start()
        try:
            if self.settings.app_server_enabled:
                answer = self._app_turn(chat_id, text)
            else:
                # O processo recebe somente o CODEX_HOME desta instância e o workspace configurado.
                environment = dict(os.environ); environment["CODEX_HOME"] = str(self.settings.root)
                prompt = GATEWAY_INSTRUCTIONS + "\n\nMensagem do owner:\n" + text
                command = [self.settings.executable, "exec", "-C", str(self.settings.project), "--skip-git-repo-check", "-m", self.settings.model, "-c", f"model_reasoning_effort={json.dumps(self.settings.effort)}", "-s", self.settings.sandbox, prompt]
                result = subprocess.run(command, cwd=self.settings.project, env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=self.settings.turn_timeout)
                if result.returncode != 0:
                    self._record_error(f"Codex exec exit {result.returncode}: {result.stderr or result.stdout}"); answer = "O agente não conseguiu concluir esta solicitação. Consulte o diagnóstico do Gateway Telegram no setup."
                else: answer = result.stdout.strip()
            if answer and self.api: self.api.send(chat_id, answer[-4000:])
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
            message_id = message.get("message_id")
            if isinstance(message_id, int): self.api.delete(chat_id, message_id)
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
