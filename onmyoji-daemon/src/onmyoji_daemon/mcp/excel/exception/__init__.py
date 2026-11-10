"""Falhas operacionais públicas da integração Excel."""
from __future__ import annotations

from ...document_processing.contracts import DocumentError


class ExcelExceptions(DocumentError):
    """Expõe código estável e mensagem sanitizada, sem dados da pasta de trabalho."""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def require(condition: bool, code: str, message: str) -> None:
    """Lança ``ExcelExceptions`` com o código informado quando a condição falha."""
    if not condition:
        raise ExcelExceptions(code, message)
