"""Erros públicos do processamento local de documentos."""
from __future__ import annotations


class DocumentError(ValueError):
    """Expõe um código estável e uma mensagem operacional sem dados privados."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise DocumentError(code, message)
