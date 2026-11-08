"""Erros sanitizados da ponte MCP de revisão de arquivos."""
from __future__ import annotations

from typing import Any


class FileReviewMcpError(ValueError):
    """Representa rejeição de contrato ou indisponibilidade sem expor segredos RPC."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict[str, Any]:
        """Retorna somente código estável e mensagem segura para o cliente MCP."""
        return {"code": self.code, "message": str(self)}
