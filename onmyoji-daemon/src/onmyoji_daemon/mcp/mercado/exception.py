"""Erros sanitizados da integração Mercado."""
from __future__ import annotations

from typing import Any


class MercadoError(ValueError):
    """Representa falha funcional sem incluir segredos ou payloads remotos."""

    def __init__(self, code: str, message: str, **context: Any):
        super().__init__(message)
        self.code = code
        self.context = {key: value for key, value in context.items() if value is not None}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), **self.context}
