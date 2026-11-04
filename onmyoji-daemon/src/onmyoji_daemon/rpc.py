from __future__ import annotations

import json
import socket
import threading
from typing import Any, Callable


class RpcError(RuntimeError):
    """Falha RPC com código e detalhes opcionais, compatível com erros textuais."""

    def __init__(self, message: str, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.details = code, details


class RpcServer:
    def __init__(self, host: str, port: int, token: str, handler: Callable[[str, dict[str, Any]], Any]):
        self.host, self.token, self.handler = host, token, handler
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
            with connection:
                connection.settimeout(10)
                try:
                    raw = connection.makefile("rb").readline(1_048_576)
                    request = json.loads(raw.decode("utf-8"))
                    if request.get("token") != self.token: raise RpcError("unauthorized")
                    value = self.handler(str(request["method"]), request.get("params") or {})
                    response = {"ok": True, "result": value}
                except Exception as error:
                    response = {"ok": False, "error": str(error)}
                    code = getattr(error, "code", None)
                    details = getattr(error, "details", None)
                    if code: response["error_code"] = code
                    if isinstance(details, dict): response["error_details"] = details
                try:
                    connection.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                except OSError:
                    # O cliente pode cancelar uma operação; isso não encerra o servidor.
                    continue

    def close(self) -> None:
        self.socket.close()


def call(host: str, port: int, token: str, method: str, params: dict[str, Any] | None = None, timeout: float = 5) -> Any:
    request = json.dumps({"token": token, "method": method, "params": params or {}}, ensure_ascii=False).encode("utf-8") + b"\n"
    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.sendall(request); response = json.loads(connection.makefile("rb").readline(1_048_576).decode("utf-8"))
    if not response.get("ok"):
        raise RpcError(str(response.get("error") or "remote failure"), response.get("error_code"), response.get("error_details"))
    return response.get("result")
