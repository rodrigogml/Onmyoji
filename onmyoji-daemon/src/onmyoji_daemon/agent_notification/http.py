"""Ingresso HTTP local e sem autenticação para eventos do agente."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit

from ..rpc import RpcError
from ..telegram_persistence import GatewayPersistenceError


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, gateway, max_request_bytes: int, long_limit: int):
        self.gateway = gateway
        self.max_request_bytes = max_request_bytes
        self.long_limit = threading.BoundedSemaphore(long_limit)
        self.request_queue_size = max(16, long_limit)
        super().__init__(("127.0.0.1", port), _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_arguments):
        return

    def _write(self, status: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _error(self, status: int, code: str) -> None:
        self._write(status, {"error": {"code": code}})

    def do_POST(self) -> None:
        target = urlsplit(self.path)
        query = parse_qs(target.query, keep_blank_values=True)
        if target.path != "/v1/events" or set(query) - {"wait"} or len(query.get("wait", ["true"])) != 1 or query.get("wait", ["true"])[0] not in {"true", "false"}:
            self._error(400, "invalid_request")
            return
        if self.headers.get("Transfer-Encoding"):
            self._error(400, "invalid_request")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(400, "invalid_request")
            return
        if length < 0:
            self._error(400, "invalid_request")
            return
        if length > self.server.max_request_bytes:
            self._error(413, "request_too_large")
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            self._error(400, "invalid_json")
            return
        wait = query.get("wait", ["true"])[0] == "true"
        permit = self.server.long_limit if wait else None
        if permit is not None and not permit.acquire(blocking=False):
            self._error(429, "capacity_exceeded")
            return
        try:
            result = self.server.gateway._notification_submit(event, wait=wait)
        except RpcError as error:
            status = 400 if error.code in {"invalid_event", "invalid_argument"} else 429 if error.code == "capacity_exceeded" else 503
            self._error(status, error.code or "service_unavailable")
            return
        except GatewayPersistenceError:
            self._error(429, "capacity_exceeded")
            return
        except (OSError, RuntimeError):
            self._error(503, "service_unavailable")
            return
        finally:
            if permit is not None:
                permit.release()
        self._write(200 if wait else 202, result)

    def do_GET(self) -> None:
        self._error(400, "invalid_request")

    do_DELETE = do_GET
    do_PATCH = do_GET
    do_PUT = do_GET


class NotificationHttpServer:
    """Executa o listener loopback em thread independente do RPC interno."""

    def __init__(self, port: int, gateway, max_request_bytes: int = 1_048_576, long_limit: int = 32):
        self.server = _Server(port, gateway, max_request_bytes, long_limit)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="agent-events-http")

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
