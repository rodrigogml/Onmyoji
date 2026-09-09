"""Gateway local para publicacao de Mini Apps do Onmyoji.

O gateway nunca toma posse dos arquivos publicados: ele somente registra uma
raiz no workspace da instancia e entrega cada request ao contrato Python da
aplicacao. A API administrativa fica em loopback e usa o token efemero que o
supervisor entrega ao servico.
"""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256, scrypt
import hmac
import importlib.util
import inspect
import json
from pathlib import Path
import secrets
import site
import sqlite3
import sys
import threading
import time
from typing import Any
import uuid

from aiohttp import web

from .rpc import RpcServer
from .miniapps_pki import ensure_material, install_trust, server_context
from .miniapps_tunnel import TunnelController, TunnelError


STATES = {"CREATED", "PUBLISHED_UNCLAIMED", "ACTIVE", "UNPUBLISHED", "EXPIRED", "ERROR", "DELETED"}
MAX_BODY_BYTES = 64 * 1024 * 1024
CLEANUP_TASK_KEY: web.AppKey[asyncio.Task[None]] = web.AppKey("miniapps_cleanup_task", asyncio.Task)


@dataclass(frozen=True)
class AuthenticatedIdentity:
    authenticated: bool
    auth_type: str
    username: str | None = None
    session_id: str | None = None


@dataclass
class HttpRequest:
    method: str
    scheme: str
    host: str
    path: str
    raw_path: str
    query_string: str
    query: dict[str, list[str]]
    headers: list[tuple[str, str]]
    cookies: dict[str, str]
    body: bytes
    body_file: Path | None
    content_type: str | None
    content_length: int | None
    client_ip: str | None
    client_port: int | None
    server: tuple[str, int] | None
    http_version: str
    authenticated_identity: AuthenticatedIdentity

    def text(self, encoding: str = "utf-8") -> str: return self.body.decode(encoding)
    def json(self) -> Any: return json.loads(self.body)
    def open_body(self):
        if not self.body_file: raise ValueError("o corpo está em memória")
        return self.body_file.open("rb")


@dataclass
class HttpResponse:
    status: int = 200
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes | str | AsyncIterable[bytes] | None = b""

    @classmethod
    def json(cls, value: Any, status: int = 200) -> "HttpResponse":
        return cls(status, [("Content-Type", "application/json; charset=utf-8")], json.dumps(value, ensure_ascii=False).encode("utf-8"))


class MiniApp:
    async def handle(self, request: HttpRequest) -> HttpResponse: raise NotImplementedError


class WebSocket:
    """Adaptador minimo e independente de framework para WebSocket."""
    def __init__(self, socket: web.WebSocketResponse, identity: AuthenticatedIdentity): self._socket, self.identity = socket, identity
    async def receive(self) -> str | bytes | None:
        message = await self._socket.receive()
        if message.type == web.WSMsgType.TEXT: return str(message.data)
        if message.type == web.WSMsgType.BINARY: return bytes(message.data)
        return None
    async def send(self, value: str | bytes) -> None:
        if isinstance(value, bytes): await self._socket.send_bytes(value)
        else: await self._socket.send_str(value)
    async def close(self, code: int = 1000, message: bytes = b"") -> None: await self._socket.close(code=code, message=message)


def _now() -> float: return time.time()
def _digest(value: str) -> str: return sha256(value.encode("utf-8")).hexdigest()


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()
    return f"scrypt$16384$8$1${salt.hex()}${digest}"


def _password_matches(password: str, stored: str) -> bool:
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, expected = stored.split("$", 5)
        if algorithm != "scrypt": return False
        actual = scrypt(password.encode("utf-8"), salt=bytes.fromhex(raw_salt), n=int(raw_n), r=int(raw_r), p=int(raw_p)).hex()
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError): return False
def _json(value: Any) -> str: return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class MiniAppsGateway:
    def __init__(self, root: Path, data_dir: Path, token: str, http_port: int):
        self.root, self.data_dir, self.token, self.http_port = root.resolve(), data_dir.resolve(), token, http_port
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.data_dir / "registry.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row; self.lock = threading.RLock()
        self.instances: dict[str, tuple[Any, float, int, asyncio.Lock]] = {}
        self.callback_hits: dict[tuple[str, str], list[float]] = {}
        self.sockets: dict[str, set[web.WebSocketResponse]] = {}
        self.http_loop: asyncio.AbstractEventLoop | None = None; self.http_thread: threading.Thread | None = None
        self.http_runner: web.AppRunner | None = None; self.last_error: str | None = None
        self.tunnel = TunnelController(self.root, self.data_dir, f"https://localhost:{self.http_port}")
        self._migrate()
        self._restore_publications()

    def _migrate(self) -> None:
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS apps (
                  id TEXT PRIMARY KEY, alias TEXT, root_path TEXT NOT NULL, entrypoint TEXT NOT NULL,
                  python_executable TEXT NOT NULL, state TEXT NOT NULL, persistent INTEGER NOT NULL,
                  auth_json TEXT NOT NULL, policies_json TEXT NOT NULL, created_at REAL NOT NULL,
                  updated_at REAL NOT NULL, last_activity REAL NOT NULL, last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY, app_id TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                  FOREIGN KEY(app_id) REFERENCES apps(id)
                );
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, app_id TEXT,
                  timestamp REAL NOT NULL, error_code TEXT, details TEXT
                );
            """)
            columns = {str(row[1]) for row in self.db.execute("PRAGMA table_info(apps)")}
            if "last_activity" not in columns:
                self.db.execute("ALTER TABLE apps ADD COLUMN last_activity REAL NOT NULL DEFAULT 0")
                self.db.execute("UPDATE apps SET last_activity=updated_at WHERE last_activity=0")
            self.db.commit()

    def _restore_publications(self) -> None:
        """Marca referências persistentes quebradas sem recriar arquivos."""
        with self.lock:
            rows = self.db.execute("SELECT id,root_path,entrypoint,persistent,state FROM apps WHERE state != 'DELETED'").fetchall()
        for row in rows:
            entrypoint = Path(str(row["root_path"])) / str(row["entrypoint"])
            if bool(row["persistent"]) and not entrypoint.is_file():
                self.mark_error(str(row["id"]), "missing_root", "root_path ou entrypoint não existe após reinício")
            elif bool(row["persistent"]) and str(row["state"]) == "ERROR":
                with self.lock:
                    state = "PUBLISHED_UNCLAIMED" if json.loads(self.db.execute("SELECT auth_json FROM apps WHERE id=?", (row["id"],)).fetchone()[0]).get("type") == "claim" else "ACTIVE"
                    self.db.execute("UPDATE apps SET state=?,last_error=NULL,updated_at=? WHERE id=?", (state, _now(), row["id"])); self.db.commit()
                self._event("app.recovered", str(row["id"]))

    def _workspace(self) -> Path:
        import tomllib
        path = self.root / "configs" / "onmyoji-system.toml"
        try: value = tomllib.loads(path.read_text(encoding="utf-8")); raw = value["codex"]["project_directory"]
        except (OSError, ValueError, KeyError, TypeError) as error: raise ValueError("workspace do Shikigami não está configurado") from error
        workspace = Path(str(raw)).resolve()
        if not workspace.is_dir(): raise ValueError("workspace configurado não existe")
        return workspace

    def _event(self, event: str, app_id: str | None = None, error_code: str | None = None, details: str | None = None) -> None:
        with self.lock:
            self.db.execute("INSERT INTO events(event,app_id,timestamp,error_code,details) VALUES(?,?,?,?,?)", (event, app_id, _now(), error_code, details)); self.db.commit()

    def _app(self, app_id: str, include_deleted: bool = True) -> dict[str, Any]:
        with self.lock: row = self.db.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
        if not row or (not include_deleted and row["state"] == "DELETED"): raise KeyError("publication not found")
        item = dict(row); item["persistent"] = bool(item["persistent"]); item["auth"] = json.loads(item.pop("auth_json")); item["policies"] = json.loads(item.pop("policies_json")); return item

    def _public(self, item: dict[str, Any]) -> dict[str, Any]:
        result = {key: item[key] for key in ("id", "alias", "root_path", "entrypoint", "python_executable", "state", "persistent", "created_at", "updated_at", "last_error")}
        local_url = f"https://localhost:{self.http_port}/a/{item['id']}/"
        tunnel = self.tunnel.status(); hostname = tunnel.get("hostname") if tunnel.get("provisioned") else None
        public_url = f"https://{hostname}/a/{item['id']}/" if hostname else None
        entrypoint_ok = (Path(item["root_path"]) / item["entrypoint"]).is_file()
        health = "ok" if entrypoint_ok and item["state"] not in {"ERROR", "EXPIRED"} else "degraded"
        result.update({"auth_type": item["auth"].get("type"), "timeouts": item["policies"], "health": health, "url": public_url or local_url, "local_url": local_url, "public_url": public_url})
        return result

    def _validate(self, root_path: str, entrypoint: str) -> tuple[Path, Path]:
        workspace = self._workspace(); app_root = Path(root_path).resolve()
        try: app_root.relative_to(workspace)
        except ValueError as error: raise ValueError("root_path deve pertencer ao workspace configurado") from error
        if not app_root.is_dir(): raise ValueError("root_path não existe")
        target = (app_root / entrypoint).resolve()
        try: target.relative_to(app_root)
        except ValueError as error: raise ValueError("entrypoint deve pertencer ao root_path") from error
        if not target.is_file() or target.suffix != ".py": raise ValueError("entrypoint Python não existe")
        try: compile(target.read_text(encoding="utf-8"), str(target), "exec")
        except (OSError, UnicodeDecodeError, SyntaxError) as error: raise ValueError("entrypoint Python não pode ser carregado") from error
        return app_root, target

    def create(self, values: dict[str, Any]) -> dict[str, Any]:
        root_path, target = self._validate(str(values["root_path"]), str(values["entrypoint"]))
        python_executable = Path(str(values.get("python_executable") or sys.executable)).resolve()
        if not python_executable.is_file(): raise ValueError("python_executable não existe")
        auth = dict(values.get("auth") or {"type": "claim"}); kind = str(auth.get("type") or "claim")
        if kind not in {"claim", "basic"}: raise ValueError("auth.type deve ser claim ou basic")
        secret: str | None = None
        routes = auth.get("public_callbacks", [])
        if not isinstance(routes, list) or any(not isinstance(route, dict) or not isinstance(route.get("path"), str) or not isinstance(route.get("method"), str) for route in routes): raise ValueError("public_callbacks inválido")
        callback_secret: str | None = None
        normalized_routes = [{"path": str(route["path"]), "method": str(route["method"]).upper()} for route in routes]
        if normalized_routes: callback_secret = secrets.token_urlsafe(32)
        if kind == "claim": secret = secrets.token_urlsafe(32); auth = {"type": "claim", "claim_hash": _digest(secret), "public_callbacks": normalized_routes, "callback_secret_hash": _digest(callback_secret) if callback_secret else ""}
        else:
            username, password = str(auth.get("username") or ""), str(auth.get("password") or "")
            if not username or not password: raise ValueError("Basic Auth exige username e password")
            auth = {"type": "basic", "username": username, "password_hash": _password_hash(password), "public_callbacks": normalized_routes, "callback_secret_hash": _digest(callback_secret) if callback_secret else ""}
        policies = {"claim_timeout": int(values.get("claim_timeout", 300)), "lifetime": int(values.get("lifetime", 7200)), "idle_timeout": int(values.get("idle_timeout", 1800)), "runtime_idle_timeout": int(values.get("runtime_idle_timeout", 300)), "single_client": bool(values.get("single_client", True))}
        if any(value < 0 for value in policies.values() if isinstance(value, int)): raise ValueError("timeouts não podem ser negativos")
        app_id, now = uuid.uuid4().hex, _now(); state = "PUBLISHED_UNCLAIMED" if kind == "claim" else "ACTIVE"
        with self.lock:
            self.db.execute("INSERT INTO apps VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (app_id, values.get("alias"), str(root_path), str(target.relative_to(root_path)), str(python_executable), state, bool(values.get("persistent", False)), _json(auth), _json(policies), now, now, now, None)); self.db.commit()
        self._event("app.created", app_id); self._event("app.published", app_id)
        result = self._public(self._app(app_id));
        if secret: result["claim_url"] = result["url"] + "?claim=" + secret
        if callback_secret: result["callback_secret"] = callback_secret
        return result

    def list(self) -> list[dict[str, Any]]:
        with self.lock: ids = [str(row[0]) for row in self.db.execute("SELECT id FROM apps WHERE state != 'DELETED' ORDER BY created_at DESC")]
        return [self._public(self._app(app_id)) for app_id in ids]

    def inspect(self, app_id: str) -> dict[str, Any]: return self._public(self._app(app_id))

    def events(self, app_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.lock:
            if app_id:
                rows = self.db.execute("SELECT event,app_id,timestamp,error_code,details FROM events WHERE app_id=? ORDER BY id DESC LIMIT ?", (app_id, limit)).fetchall()
            else: rows = self.db.execute("SELECT event,app_id,timestamp,error_code,details FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        output = []
        for row in rows:
            detail = str(row["details"] or "")
            detail = __import__("re").sub(r"(?i)(token|password|authorization|cookie|secret)[=:][^\s,]+", r"\1=[redacted]", detail)[:800]
            output.append({"event": row["event"], "app_id": row["app_id"], "timestamp": row["timestamp"], "error_code": row["error_code"], "details": detail})
        return output

    def _store(self, item: dict[str, Any]) -> None:
        with self.lock:
            self.db.execute("UPDATE apps SET alias=?,root_path=?,entrypoint=?,python_executable=?,state=?,persistent=?,auth_json=?,policies_json=?,updated_at=?,last_error=? WHERE id=?", (item["alias"], item["root_path"], item["entrypoint"], item["python_executable"], item["state"], item["persistent"], _json(item["auth"]), _json(item["policies"]), _now(), item["last_error"], item["id"])); self.db.commit()

    def update(self, app_id: str, values: dict[str, Any]) -> dict[str, Any]:
        item = self._app(app_id)
        if item["state"] == "DELETED": raise ValueError("publication deleted")
        reload_required = False
        for key in ("alias", "persistent"):
            if key in values: item[key] = values[key]
        if "root_path" in values or "entrypoint" in values:
            root_path, target = self._validate(str(values.get("root_path", item["root_path"])), str(values.get("entrypoint", item["entrypoint"])))
            item["root_path"], item["entrypoint"] = str(root_path), str(target.relative_to(root_path)); reload_required = True
        if "python_executable" in values and str(values["python_executable"]) != item["python_executable"]:
            candidate = Path(str(values["python_executable"]));
            if not candidate.is_file(): raise ValueError("python_executable não existe")
            item["python_executable"] = str(candidate.resolve()); reload_required = True
        for key in ("claim_timeout", "lifetime", "idle_timeout", "runtime_idle_timeout", "single_client"):
            if key in values: item["policies"][key] = values[key]
        self._store(item)
        if reload_required: self._schedule_release(app_id, "app.reloaded")
        return self.inspect(app_id)

    def _schedule_release(self, app_id: str, event: str | None = None) -> None:
        if self.http_loop:
            async def release() -> None:
                for socket in list(self.sockets.get(app_id, set())):
                    await socket.close(code=1012, message=b"Service restart")
                deadline = _now() + 30
                while (current := self.instances.get(app_id)) and current[2] > 0 and _now() < deadline:
                    await asyncio.sleep(0.05)
                if (current := self.instances.get(app_id)) and current[2] > 0:
                    self.mark_error(app_id, "release_timeout", "requisições não encerraram antes de reload")
                    return
                await self._release(app_id)
                if event: self._event(event, app_id)
            asyncio.run_coroutine_threadsafe(release(), self.http_loop)

    def publish(self, app_id: str) -> dict[str, Any]:
        item = self._app(app_id)
        if item["state"] == "DELETED": raise ValueError("publication deleted")
        item["state"] = "PUBLISHED_UNCLAIMED" if item["auth"]["type"] == "claim" else "ACTIVE"; item["last_error"] = None; self._store(item); self._event("app.published", app_id); return self.inspect(app_id)

    def unpublish(self, app_id: str) -> dict[str, Any]:
        item = self._app(app_id); item["state"] = "UNPUBLISHED"; self._store(item)
        with self.lock: self.db.execute("DELETE FROM sessions WHERE app_id=?", (app_id,)); self.db.commit()
        self._schedule_release(app_id); self._event("session.invalidated", app_id); self._event("app.unpublished", app_id); return self.inspect(app_id)

    def delete(self, app_id: str) -> dict[str, Any]:
        item = self._app(app_id); item["state"] = "DELETED"; self._store(item)
        with self.lock: self.db.execute("DELETE FROM sessions WHERE app_id=?", (app_id,)); self.db.commit()
        self._schedule_release(app_id); self._event("app.deleted", app_id); return self.inspect(app_id)

    def reload(self, app_id: str) -> dict[str, Any]:
        self._app(app_id); self._schedule_release(app_id, "app.reloaded"); return self.inspect(app_id)

    async def _release(self, app_id: str) -> None:
        item = self.instances.pop(app_id, None)
        if not item: return
        app, _last, _active, _guard = item; closer = getattr(app, "close", None)
        if closer:
            result = closer()
            if inspect.isawaitable(result): await result
        self._event("instance.released", app_id)

    async def _load(self, item: dict[str, Any]) -> tuple[Any, asyncio.Lock]:
        current = self.instances.get(item["id"])
        if current: return current[0], current[3]
        executable = Path(item["python_executable"]).resolve()
        # A app continua no processo do Gateway; um venv preexistente fornece
        # somente suas dependências. Não há instalação automática de pacotes.
        venv_root = executable.parent.parent if executable.parent.name.casefold() in {"scripts", "bin"} else executable.parent
        candidates = [venv_root / "Lib" / "site-packages"]
        candidates.extend((venv_root / "lib").glob("python*/site-packages") if (venv_root / "lib").is_dir() else [])
        for candidate in candidates:
            if candidate.is_dir(): site.addsitedir(str(candidate))
        entrypoint = Path(item["root_path"]) / item["entrypoint"]
        module_name = f"_onmyoji_miniapp_{item['id']}_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, entrypoint)
        if not spec or not spec.loader: raise RuntimeError("não foi possível carregar entrypoint")
        module = importlib.util.module_from_spec(spec); sys.modules[module_name] = module
        root_path = str(Path(item["root_path"]).resolve())
        if root_path not in sys.path: sys.path.insert(0, root_path)
        spec.loader.exec_module(module)
        factory = getattr(module, "create_app", None)
        if not callable(factory): raise RuntimeError("entrypoint deve exportar create_app")
        app = factory(); app = await app if inspect.isawaitable(app) else app
        if not callable(getattr(app, "handle", None)): raise RuntimeError("Mini App deve implementar handle")
        guard = asyncio.Lock(); self.instances[item["id"]] = (app, _now(), 0, guard); self._event("instance.loaded", item["id"]); return app, guard

    async def _identity(self, item: dict[str, Any], request: web.Request) -> AuthenticatedIdentity | None:
        auth = item["auth"]
        if auth["type"] == "basic":
            header = request.headers.get("Authorization", "")
            if not header.startswith("Basic "): return None
            import base64
            try: username, password = base64.b64decode(header[6:]).decode("utf-8").split(":", 1)
            except Exception: return None
            if request.scheme != "https" or username != auth["username"] or not _password_matches(password, auth["password_hash"]): return None
            return AuthenticatedIdentity(True, "basic", username=username)
        session_id = request.cookies.get("onmyoji_miniapp_session")
        if not session_id: return None
        with self.lock: row = self.db.execute("SELECT id FROM sessions WHERE id=? AND app_id=? AND expires_at>?", (session_id, item["id"], _now())).fetchone()
        return AuthenticatedIdentity(True, "session", session_id=session_id) if row else None

    async def _dispatch(self, item: dict[str, Any], request: web.Request, identity: AuthenticatedIdentity) -> web.StreamResponse:
        if request.content_length and request.content_length > MAX_BODY_BYTES: raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY_BYTES, actual_size=request.content_length)
        temporary: Path | None = None
        if request.content_length and request.content_length > 1024 * 1024:
            uploads = self.data_dir / "uploads"; uploads.mkdir(parents=True, exist_ok=True)
            temporary = uploads / (uuid.uuid4().hex + ".body")
            with temporary.open("wb") as output:
                while chunk := await request.content.readany(): output.write(chunk)
            body = b""
        else: body = await request.read()
        app, guard = await self._load(item)
        with self.lock:
            first_active = self.db.execute("SELECT last_activity,created_at FROM apps WHERE id=?", (item["id"],)).fetchone()
            self.db.execute("UPDATE apps SET last_activity=?,updated_at=?,state='ACTIVE' WHERE id=?", (_now(), _now(), item["id"])); self.db.commit()
        if first_active and float(first_active["last_activity"]) <= float(first_active["created_at"]): self._event("app.active", item["id"])
        peer = request.transport.get_extra_info("peername") if request.transport else None
        tail = request.match_info.get("tail", "")
        internal_path = "/" + tail.lstrip("/")
        raw_internal_path = internal_path + (("?" + request.query_string) if request.query_string else "")
        values = HttpRequest(request.method, request.scheme, request.host, internal_path, raw_internal_path, request.query_string, {key: request.query.getall(key) for key in request.query.keys()}, list(request.headers.items()), dict(request.cookies), body, temporary, request.content_type, request.content_length, peer[0] if peer else None, peer[1] if peer else None, request.transport.get_extra_info("sockname") if request.transport else None, f"HTTP/{request.version.major}.{request.version.minor}", identity)
        current = self.instances[item["id"]]; self.instances[item["id"]] = (current[0], _now(), current[2] + 1, guard)
        try:
            async with guard:
                outcome = app.handle(values)
                response = await asyncio.wait_for(outcome, timeout=60) if inspect.isawaitable(outcome) else outcome
            if not isinstance(response, HttpResponse): raise RuntimeError("handle deve retornar HttpResponse")
        except TimeoutError:
            self.mark_error(item["id"], "request_timeout", "request excedeu 60 segundos"); raise web.HTTPGatewayTimeout(text="Mini App timeout")
        except web.HTTPException: raise
        except Exception as error:
            self.mark_error(item["id"], "app_exception", repr(error)); raise web.HTTPInternalServerError(text="Mini App error")
        finally:
            if temporary: temporary.unlink(missing_ok=True)
            current = self.instances.get(item["id"])
            if current: self.instances[item["id"]] = (current[0], _now(), max(0, current[2] - 1), current[3])
        headers = response.headers
        if isinstance(response.body, AsyncIterable):
            outgoing = web.StreamResponse(status=response.status, headers=headers); await outgoing.prepare(request)
            async for chunk in response.body: await outgoing.write(chunk)
            await outgoing.write_eof(); return outgoing
        return web.Response(status=response.status, headers=headers, body=response.body.encode("utf-8") if isinstance(response.body, str) else response.body)

    async def _dispatch_websocket(self, item: dict[str, Any], request: web.Request, identity: AuthenticatedIdentity) -> web.WebSocketResponse:
        app, guard = await self._load(item)
        handler = getattr(app, "websocket", None)
        if not callable(handler): raise web.HTTPNotImplemented(text="Mini App does not implement WebSocket")
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        self.sockets.setdefault(item["id"], set()).add(socket)
        current = self.instances[item["id"]]; self.instances[item["id"]] = (current[0], _now(), current[2] + 1, guard)
        try:
            async with guard:
                outcome = handler(WebSocket(socket, identity))
                if inspect.isawaitable(outcome): await outcome
        except Exception as error:
            self.mark_error(item["id"], "websocket_exception", repr(error))
            await socket.close(code=1011, message=b"Mini App error")
        finally:
            self.sockets.get(item["id"], set()).discard(socket)
            current = self.instances.get(item["id"])
            if current: self.instances[item["id"]] = (current[0], _now(), max(0, current[2] - 1), current[3])
        return socket

    def mark_error(self, app_id: str, code: str, details: str) -> None:
        with self.lock: self.db.execute("UPDATE apps SET state='ERROR',last_error=?,updated_at=? WHERE id=?", (details, _now(), app_id)); self.db.commit()
        self._event("app.error", app_id, code, details)

    async def public_request(self, request: web.Request) -> web.StreamResponse:
        app_id = request.match_info["app_id"]
        try: item = self._app(app_id, include_deleted=False)
        except KeyError: raise web.HTTPNotFound()
        self._expire(item)
        item = self._app(app_id, include_deleted=False)
        if item["state"] in {"ERROR", "EXPIRED", "UNPUBLISHED"}: raise web.HTTPServiceUnavailable(text="Mini App unavailable")
        if item["auth"]["type"] == "claim" and request.query.get("claim"):
            claim = str(request.query["claim"])
            if _now() > item["created_at"] + item["policies"]["claim_timeout"]:
                self._expire_item(item, "claim expired"); raise web.HTTPGone(text="Mini App claim expired")
            if not hmac.compare_digest(_digest(claim), item["auth"]["claim_hash"]): self._event("auth.failed", app_id); raise web.HTTPUnauthorized()
            session_id = secrets.token_urlsafe(32); expiry = _now() + item["policies"]["lifetime"]
            with self.lock:
                if item["policies"]["single_client"] and self.db.execute("SELECT 1 FROM sessions WHERE app_id=? AND expires_at>?", (app_id, _now())).fetchone(): raise web.HTTPForbidden(text="Mini App already claimed")
                self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (session_id, app_id, _now(), expiry)); self.db.execute("UPDATE apps SET state='ACTIVE',updated_at=? WHERE id=?", (_now(), app_id)); self.db.commit()
            self._event("app.claimed", app_id); self._event("session.created", app_id)
            location = request.path; response = web.HTTPFound(location); response.set_cookie("onmyoji_miniapp_session", session_id, secure=True, httponly=True, samesite="Strict", max_age=max(1, int(expiry - _now()))); raise response
        callback = self._callback_allowed(item, request)
        identity = AuthenticatedIdentity(False, "public_callback") if callback else await self._identity(item, request)
        if not identity:
            self._event("auth.failed", app_id)
            if item["auth"]["type"] == "basic": raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="Onmyoji Mini App"'})
            raise web.HTTPUnauthorized()
        if request.headers.get("Upgrade", "").casefold() == "websocket": return await self._dispatch_websocket(item, request, identity)
        return await self._dispatch(item, request, identity)

    def _callback_allowed(self, item: dict[str, Any], request: web.Request) -> bool:
        tail = "/" + request.match_info.get("tail", "").lstrip("/")
        routes = item["auth"].get("public_callbacks", [])
        if not any(route["path"] == tail and route["method"] == request.method for route in routes): return False
        secret = request.headers.get("X-Onmyoji-Callback-Secret", "")
        expected = str(item["auth"].get("callback_secret_hash", ""))
        if not expected or not hmac.compare_digest(_digest(secret), expected): return False
        peer = request.remote or "unknown"; key = (item["id"], peer); now = _now()
        hits = [value for value in self.callback_hits.get(key, []) if value > now - 60]
        if len(hits) >= 30: raise web.HTTPTooManyRequests(text="Callback rate limit")
        hits.append(now); self.callback_hits[key] = hits
        return True

    def _expire_item(self, item: dict[str, Any], reason: str) -> None:
        if item["persistent"]: return
        if item["state"] in {"EXPIRED", "DELETED", "UNPUBLISHED"}: return
        item["state"] = "EXPIRED"; item["last_error"] = reason; self._store(item)
        with self.lock: self.db.execute("DELETE FROM sessions WHERE app_id=?", (item["id"],)); self.db.commit()
        self._schedule_release(item["id"]); self._event("session.invalidated", item["id"]); self._event("app.expired", item["id"])

    def _expire(self, item: dict[str, Any]) -> None:
        policies = item["policies"]; now = _now()
        lifetime = int(policies["lifetime"]); idle = int(policies["idle_timeout"])
        if (lifetime and now > item["created_at"] + lifetime) or (idle and now > item["last_activity"] + idle): self._expire_item(item, "publication timeout")

    async def cleanup(self) -> None:
        for item in [self._app(app_id) for app_id in [row[0] for row in self.db.execute("SELECT id FROM apps")]]:
            self._expire(item)
        now = _now()
        for app_id, (_app, last, active, _guard) in list(self.instances.items()):
            try: item = self._app(app_id)
            except KeyError: continue
            if active == 0 and now > last + int(item["policies"]["runtime_idle_timeout"]): await self._release(app_id)

    def rpc(self, method: str, params: dict[str, Any]) -> Any:
        action = method.removeprefix("mini-apps.")
        if action == "status": return {"state": "running", "http_port": self.http_port, "apps": len(self.list()), "last_error": self.last_error}
        if action == "list": return self.list()
        if action == "create": return self.create(params)
        if action == "inspect": return self.inspect(str(params["id"]))
        if action == "events": return self.events(str(params["id"]) if params.get("id") else None, int(params.get("limit", 100)))
        if action == "update": return self.update(str(params["id"]), dict(params.get("values") or {}))
        if action == "publish": return self.publish(str(params["id"]))
        if action == "unpublish": return self.unpublish(str(params["id"]))
        if action == "reload": return self.reload(str(params["id"]))
        if action == "delete": return self.delete(str(params["id"]))
        if action == "tunnel.status": return self.tunnel.status()
        if action == "tunnel.configure": return self.tunnel.configure(params)
        if action == "tunnel.provision":
            result = self.tunnel.provision(bool(params.get("confirm", False))); self._event("tunnel.recovered" if result.get("provisioned") else "tunnel.failed"); return result
        if action == "tunnel.start":
            result = self.tunnel.start(); self._event("tunnel.started" if result.get("running") else "tunnel.failed"); return result
        if action == "tunnel.install-cloudflared": return self.tunnel.install_cloudflared(bool(params.get("confirm", False)))
        if action == "tunnel.stop":
            result = self.tunnel.stop(); self._event("tunnel.stopped"); return result
        if action == "tunnel.restart":
            self.tunnel.stop(); result = self.tunnel.start(); self._event("tunnel.recovered" if result.get("running") else "tunnel.failed"); return result
        if action == "tls.status":
            ca, cert, _key = ensure_material(self.data_dir / "tls")
            return {"ready": True, "ca_certificate": str(ca), "gateway_certificate": str(cert)}
        if action == "tls.install-trust":
            if not bool(params.get("confirm", False)): raise ValueError("instalação da CA requer confirmação")
            ca, _cert, _key = ensure_material(self.data_dir / "tls"); install_trust(ca); return {"installed": True}
        raise ValueError("unknown mini-apps method")

    def _admin(self, request: web.Request) -> None:
        if not hmac.compare_digest(request.headers.get("X-Onmyoji-Token", ""), self.token): raise web.HTTPUnauthorized()

    async def admin_request(self, request: web.Request) -> web.StreamResponse:
        self._admin(request); app_id = request.match_info.get("app_id")
        try:
            if request.method == "POST" and request.path.endswith("/apps"): return web.json_response(self.create(await request.json()))
            if request.method == "GET" and request.path.endswith("/apps"): return web.json_response(self.list())
            if request.method == "GET" and request.path.endswith("/events"): return web.json_response(self.events(request.query.get("id"), int(request.query.get("limit", 100))))
            if request.method == "GET": return web.json_response(self.inspect(str(app_id)))
            if request.method == "PATCH": return web.json_response(self.update(str(app_id), await request.json()))
            if request.method == "DELETE": return web.json_response(self.delete(str(app_id)))
            action = request.match_info.get("action")
            if request.method == "POST" and action == "publish": return web.json_response(self.publish(str(app_id)))
            if request.method == "POST" and action == "unpublish": return web.json_response(self.unpublish(str(app_id)))
            if request.method == "POST" and action == "reload": return web.json_response(self.reload(str(app_id)))
        except KeyError: raise web.HTTPNotFound()
        except (ValueError, json.JSONDecodeError) as error: raise web.HTTPBadRequest(text=str(error))
        raise web.HTTPMethodNotAllowed(request.method, [])

    async def admin_status(self, request: web.Request) -> web.StreamResponse:
        self._admin(request); return web.json_response(self.rpc("mini-apps.status", {}))

    async def admin_tunnel(self, request: web.Request) -> web.StreamResponse:
        self._admin(request)
        try:
            if request.method == "GET": return web.json_response(self.tunnel.status())
            action = request.match_info.get("action")
            values = await request.json() if request.can_read_body else {}
            if action == "configure": return web.json_response(self.tunnel.configure(values))
            if action == "provision": return web.json_response(self.tunnel.provision(bool(values.get("confirm", False))))
            if action == "start": return web.json_response(self.tunnel.start())
            if action == "stop": return web.json_response(self.tunnel.stop())
            if action == "restart": self.tunnel.stop(); return web.json_response(self.tunnel.start())
            if action == "install-cloudflared": return web.json_response(self.tunnel.install_cloudflared(bool(values.get("confirm", False))))
        except TunnelError as error: raise web.HTTPBadRequest(text=str(error))
        raise web.HTTPMethodNotAllowed(request.method, [])

    async def _http_main(self) -> None:
        app = web.Application(client_max_size=MAX_BODY_BYTES)
        async def start_cleanup(_app: web.Application) -> None:
            async def worker() -> None:
                while True:
                    await asyncio.sleep(15)
                    await self.cleanup()
            _app[CLEANUP_TASK_KEY] = asyncio.create_task(worker())
        async def stop_cleanup(_app: web.Application) -> None:
            task = _app.get(CLEANUP_TASK_KEY)
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
        app.on_startup.append(start_cleanup); app.on_cleanup.append(stop_cleanup)
        app.router.add_route("*", "/internal/v1/apps", self.admin_request)
        app.router.add_route("GET", "/internal/v1/events", self.admin_request)
        app.router.add_route("*", "/internal/v1/apps/{app_id}", self.admin_request)
        app.router.add_route("POST", "/internal/v1/apps/{app_id}/{action:publish|unpublish|reload}", self.admin_request)
        app.router.add_route("GET", "/internal/v1/status", self.admin_status)
        app.router.add_route("GET", "/internal/v1/tunnel", self.admin_tunnel)
        app.router.add_route("POST", "/internal/v1/tunnel/{action:configure|provision|start|stop|restart|install-cloudflared}", self.admin_tunnel)
        app.router.add_route("*", "/a/{app_id}/{tail:.*}", self.public_request)
        app.router.add_route("*", "/a/{app_id}", self.public_request)
        tls_dir = self.data_dir / "tls"; ensure_material(tls_dir)
        self.http_runner = web.AppRunner(app); await self.http_runner.setup(); site = web.TCPSite(self.http_runner, "127.0.0.1", self.http_port, ssl_context=server_context(tls_dir)); await site.start()

    def start_http(self) -> None:
        ready = threading.Event()
        def worker() -> None:
            self.http_loop = asyncio.new_event_loop(); asyncio.set_event_loop(self.http_loop)
            try: self.http_loop.run_until_complete(self._http_main())
            finally: ready.set()
            self.http_loop.run_forever()
        self.http_thread = threading.Thread(target=worker, name="onmyoji-miniapps-http", daemon=True); self.http_thread.start(); ready.wait(5)
        if not self.http_runner: raise RuntimeError("Mini Apps HTTP não iniciou")

    def stop_http(self) -> None:
        if self.http_loop:
            if self.http_runner: asyncio.run_coroutine_threadsafe(self.http_runner.cleanup(), self.http_loop).result(5)
            self.http_loop.call_soon_threadsafe(self.http_loop.stop)
        if self.http_thread: self.http_thread.join(5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--data-dir", type=Path, required=True); parser.add_argument("--onmyoji-root", type=Path, required=True); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, required=True); parser.add_argument("--token", required=True); parser.add_argument("--http-port", type=int, default=39391); args = parser.parse_args(argv)
    gateway = MiniAppsGateway(args.onmyoji_root, args.data_dir, args.token, args.http_port); gateway.start_http()
    if gateway.tunnel.status().get("provisioned"):
        try: gateway.tunnel.start(); gateway._event("tunnel.started")
        except Exception as error: gateway._event("tunnel.failed", error_code="startup", details=str(error))
    stopped = threading.Event()
    def handler(method: str, params: dict[str, Any]) -> Any:
        if method == "ping": return {"service": "mini-apps", "state": "running"}
        if method == "shutdown": stopped.set(); return {"state": "stopping"}
        return gateway.rpc(method, params)
    try: RpcServer(args.host, args.port, args.token, handler).serve_forever(stopped)
    finally:
        gateway.tunnel.stop(); gateway._event("tunnel.stopped"); gateway.stop_http()
    return 0


if __name__ == "__main__": raise SystemExit(main())
