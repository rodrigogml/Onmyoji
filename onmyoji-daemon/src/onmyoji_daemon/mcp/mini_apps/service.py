"""Ponte MCP para o contrato administrativo do gateway Mini Apps."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable
from ...rpc import call
from ...supervisor import endpoint

class MiniAppsMcpError(ValueError): pass

class MiniAppsMcpService:
    """Encaminha somente parâmetros estruturados ao daemon local autenticado."""
    def __init__(self, root: Path, rpc: Callable[..., Any] = call, endpoint_reader: Callable[[Path], tuple[str, int, str]] = endpoint):
        self.root, self.rpc, self.endpoint_reader = root.resolve(), rpc, endpoint_reader
    def invoke(self, action: str, params: dict[str, Any] | None = None) -> Any:
        try:
            host, port, token = self.endpoint_reader(self.root)
            timeout = 200 if action.startswith("oauth.") else 30 if action == "tls.install-trust" else 5
            return self.rpc(host, port, token, "mini-apps." + action, params or {}, timeout=timeout)
        except Exception as error:
            raise MiniAppsMcpError("Gateway Mini Apps indisponível ou recusou a operação.") from error
