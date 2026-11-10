from __future__ import annotations

import json
from ipaddress import ip_address
import socket
import select
import threading
from typing import Any, Callable

MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 128 * 1_048_576
_CONNECTION = threading.local()


def disconnected() -> bool:
    connection = getattr(_CONNECTION, "socket", None)
    if connection is None:
        return False
    try:
        return bool(select.select([connection], [], [], 0)[0]) and not connection.recv(1, socket.MSG_PEEK)
    except OSError:
        return True


def _readline(connection: socket.socket, maximum: int) -> bytes:
    raw = connection.makefile("rb").readline(maximum + 1)
    if len(raw) > maximum or not raw.endswith(b"\n"):
        raise RpcError("Requisição excede o limite ou está incompleta.", "request_too_large")
    return raw


class RpcError(RuntimeError):
    """Falha RPC com código e detalhes opcionais, compatível com erros textuais."""

    def __init__(self, message: str, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.details = code, details


class RpcServer:
    def __init__(self, host: str, port: int, token: str, handler: Callable[[str, dict[str, Any]], Any], *, long_methods: tuple[str, ...] = (), long_limit: int = 32, event_max_bytes: int = MAX_REQUEST_BYTES, on_rejection: Callable[[str], None] | None = None):
        if not ip_address(socket.gethostbyname(host)).is_loopback:
            raise ValueError("O transporte RPC do daemon aceita somente loopback.")
        self.host, self.token, self.handler = host, token, handler
        self.long_methods = long_methods
        self.long_limit = threading.BoundedSemaphore(long_limit)
        self.event_max_bytes = event_max_bytes
        self.on_rejection = on_rejection
        self.control_lock = threading.Lock()
        self.readers = threading.BoundedSemaphore(long_limit + 32)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((host, port)); self.socket.listen(16); self.socket.settimeout(0.25)
        self.port = int(self.socket.getsockname()[1])

    def serve_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try: connection, _address = self.socket.accept()
            except TimeoutError: continue
            except OSError:
                if stop.is_set(): return
                raise
            if not self.readers.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(target=self._serve_connection, args=(connection,), daemon=True).start()

    def _serve_connection(self, connection: socket.socket) -> None:
        acquired = False
        with connection:
            connection.settimeout(10)
            try:
                raw = _readline(connection, max(MAX_REQUEST_BYTES, self.event_max_bytes))
                request = json.loads(raw.decode("utf-8"))
                if not isinstance(request, dict) or request.get("token") != self.token:
                    raise RpcError("unauthorized", "unauthorized")
                method = request.get("method")
                params = request.get("params", {})
                if not isinstance(method, str) or not isinstance(params, dict):
                    raise RpcError("Requisição RPC inválida.", "invalid_request")
                maximum = self.event_max_bytes if method in self.long_methods else MAX_REQUEST_BYTES
                if len(raw) > maximum:
                    raise RpcError("Requisição excede o limite.", "request_too_large")
                _CONNECTION.socket = connection
                if method in self.long_methods:
                    acquired = self.long_limit.acquire(blocking=False)
                    if not acquired:
                        raise RpcError("Acompanhamentos temporariamente saturados; solicitação não aceita.", "capacity_exceeded")
                    value = self.handler(method, params)
                else:
                    with self.control_lock:
                        value = self.handler(method, params)
                response = {"ok": True, "result": value}
            except Exception as error:
                response = {"ok": False, "error": "Requisição RPC inválida." if isinstance(error, (UnicodeError, json.JSONDecodeError)) else str(error)}
                code, details = getattr(error, "code", None), getattr(error, "details", None)
                if isinstance(error, (UnicodeError, json.JSONDecodeError)):
                    code = "invalid_json"
                if self.on_rejection and code in {"invalid_json", "request_too_large", "invalid_request", "unauthorized", "capacity_exceeded"}:
                    try:
                        self.on_rejection(code)
                    except Exception:
                        # O registro não pode transformar rejeição em aceite.
                        pass
                if code: response["error_code"] = code
                if isinstance(details, dict): response["error_details"] = details
            finally:
                _CONNECTION.socket = None
                if acquired:
                    self.long_limit.release()
            try:
                connection.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            except OSError:
                pass
            finally:
                self.readers.release()

    def close(self) -> None:
        self.socket.close()


def call(host: str, port: int, token: str, method: str, params: dict[str, Any] | None = None, timeout: float | None = 5, *, abandon: Callable[[], bool] | None = None) -> Any:
    request = json.dumps({"token": token, "method": method, "params": params or {}}, ensure_ascii=False).encode("utf-8") + b"\n"
    with socket.create_connection((host, port), timeout=5 if timeout is None else timeout) as connection:
        connection.settimeout(timeout)
        connection.sendall(request)
        if abandon:
            while not select.select([connection], [], [], 0.25)[0]:
                if abandon():
                    raise RpcError("Acompanhamento encerrado; aceitação pode ter ocorrido.", "acceptance_unknown")
        try:
            raw = _readline(connection, MAX_RESPONSE_BYTES)
            response = json.loads(raw.decode("utf-8"))
            if not isinstance(response, dict):
                raise ValueError("invalid response")
        except (RpcError, UnicodeError, ValueError) as error:
            raise RpcError("Resposta interrompida; aceitação desconhecida." if method == "notification.submit" else "Resposta RPC inválida.", "acceptance_unknown" if method == "notification.submit" else "invalid_response") from error
    if not response.get("ok"):
        raise RpcError(str(response.get("error") or "remote failure"), response.get("error_code"), response.get("error_details"))
    return response.get("result")
