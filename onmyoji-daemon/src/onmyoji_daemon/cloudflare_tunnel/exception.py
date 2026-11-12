"""Erros sanitizados da publicação Cloudflare da instância."""
from __future__ import annotations


class TunnelError(RuntimeError):
    """Falha de domínio identificada por código estável, sem credenciais."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code if message is not None else "tunnel_error"
        super().__init__(message if message is not None else code)
