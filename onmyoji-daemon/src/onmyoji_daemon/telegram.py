from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
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

    @classmethod
    def load(cls, root: Path, data_dir: Path) -> "Settings":
        path = data_dir / "telegram.toml"
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1: raise ValueError("unsupported telegram schema")
        telegram, agent = data.get("telegram", {}), data.get("agent", {})
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
        project = Path(str(system.get("project_directory") or "")).resolve()
        if not project.is_dir(): raise ValueError("configured Shikigami workspace does not exist")
        profile, entry = str(telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or "")
        if not profile or not entry: raise ValueError("KeePass profile and token entry are required")
        return cls(root, data_dir, profile, entry, project, str(system.get("executable") or "codex"), str(system.get("model") or ""), str(system.get("model_reasoning_effort") or "medium"), str(system.get("sandbox_mode") or "workspace-write"), str(system.get("approval_policy") or "never"), int(telegram.get("poll_timeout_seconds") or 30), int(agent.get("turn_timeout_seconds") or 900), max(1, int(agent.get("max_parallel_conversations") or 1)), str(agent.get("developer_file") or ""))


class Vault:
    def __init__(self, settings: Settings): self.settings = settings
    def read(self, entry: str, field: str = "password") -> str:
        wrapper = self.settings.root / "available-skills" / "keepass-vault" / "scripts" / "keepass_vault.py"
        request = {"operation": "read", "entry": {"path": entry}, "field": field}
        process = subprocess.run([sys.executable, str(wrapper), "--config", str(self.settings.root / "configs" / "keepass.toml"), "--profile", self.settings.keepass_profile], input=json.dumps(request), text=True, capture_output=True, timeout=45)
        try: result = json.loads(process.stdout)
        except ValueError as error: raise RuntimeError("KeePass provider returned invalid response") from error
        if not result.get("ok"): raise RuntimeError("KeePass provider rejected the request")
        return str(result["result"]["value"])


class TelegramApi:
    def __init__(self, token: str): self.base = f"https://api.telegram.org/bot{token}/"
    def call(self, method: str, values: dict[str, Any] | None = None) -> Any:
        body = urlencode({key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in (values or {}).items()}).encode()
        with urlopen(Request(self.base + method, data=body), timeout=45) as response: payload = json.loads(response.read())
        if not payload.get("ok"): raise RuntimeError("Telegram API rejected request")
        return payload.get("result")
    def send(self, chat_id: int, text: str) -> None: self.call("sendMessage", {"chat_id": chat_id, "text": text})


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
        self.database.execute("CREATE TABLE IF NOT EXISTS conversations(chat_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)"); self.database.commit()
        self.api: TelegramApi | None = None; self.pair: tuple[str, float] | None = None; self.offset = 0; self.work = threading.BoundedSemaphore(settings.parallel)
    def start(self) -> None: self.api = TelegramApi(Vault(self.settings).read(self.settings.token_entry)); threading.Thread(target=self._poll, daemon=True).start()
    def _poll(self) -> None:
        assert self.api
        while not self.stop_event.is_set():
            try:
                for update in self.api.call("getUpdates", {"offset": self.offset, "timeout": self.settings.poll_timeout, "allowed_updates": ["message"]}) or []:
                    self.offset = int(update.get("update_id", self.offset)) + 1; self._update(update)
            except Exception: self.stop_event.wait(3)
    def _update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}; chat, sender = message.get("chat") or {}, message.get("from") or {}
        if chat.get("type") != "private" or not isinstance(sender.get("id"), int): return
        text = str(message.get("text") or "").strip(); assert self.api
        if text.startswith("/pair ") and self.pair and time.time() < self.pair[1] and secrets.compare_digest(text[6:].strip(), self.pair[0]): self.contacts.add_owner(sender); self.pair = None; self.api.send(sender["id"], "Pairing concluído."); return
        if sender["id"] not in self.contacts.owners(): return
        if text == "/new": self.database.execute("UPDATE conversations SET generation=generation+1, updated_at=? WHERE chat_id=?", (time.time(), str(sender["id"]))); self.database.commit(); self.api.send(sender["id"], "Conversa reiniciada."); return
        if text.startswith("/"): return
        threading.Thread(target=self._turn, args=(sender["id"], text), daemon=True).start()
    def _turn(self, chat_id: int, text: str) -> None:
        if not self.work.acquire(timeout=1): return
        try:
            # O processo recebe somente o CODEX_HOME desta instância e o workspace configurado.
            environment = dict(__import__("os").environ); environment["CODEX_HOME"] = str(self.settings.root)
            prompt = GATEWAY_INSTRUCTIONS + "\n\nMensagem do owner:\n" + text
            command = [self.settings.executable, "exec", "-C", str(self.settings.project), "-m", self.settings.model, "-c", f"model_reasoning_effort={json.dumps(self.settings.effort)}", "-s", self.settings.sandbox, "-a", self.settings.approval, prompt]
            result = subprocess.run(command, cwd=self.settings.project, env=environment, text=True, capture_output=True, timeout=self.settings.turn_timeout)
            answer = result.stdout.strip() if result.returncode == 0 else "O agente não conseguiu concluir esta solicitação."
            if answer and self.api: self.api.send(chat_id, answer[-4000:])
        except Exception:
            if self.api: self.api.send(chat_id, "O agente não conseguiu concluir esta solicitação.")
        finally: self.work.release()
    def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "ping": return {"service": "telegram", "state": "running"}
        if method == "telegram.status": return {"owners": len(self.contacts.owners()), "listener": "running"}
        if method == "telegram.pair-request":
            self.pair = (f"{secrets.randbelow(1_000_000):06d}", time.time() + min(600, max(60, int(params.get("ttl_seconds", 300)))))
            return {"pin": self.pair[0], "expires_at": self.pair[1]}
        if method == "telegram.owners": return sorted(self.contacts.owners())
        if method == "telegram.send":
            if not self.api: raise RuntimeError("listener unavailable")
            self.api.send(int(params["chat_id"]), str(params["text"])); return {"sent": True}
        if method == "shutdown": self.stop_event.set(); return {"state": "stopping"}
        raise ValueError("unknown Telegram method")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--data-dir", type=Path, required=True); parser.add_argument("--onmyoji-root", type=Path, required=True); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, required=True); parser.add_argument("--token", required=True); args = parser.parse_args(argv)
    gateway = Gateway(Settings.load(args.onmyoji_root.resolve(), args.data_dir)); gateway.start(); RpcServer(args.host, args.port, args.token, gateway.handle).serve_forever(gateway.stop_event); return 0


if __name__ == "__main__": raise SystemExit(main())
