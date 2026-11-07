"""Erros funcionais sanitizados da integração BIS10CMD."""
from __future__ import annotations

from typing import Any


class Bis10CmdError(ValueError):
    """Preserva código e dados funcionais até o transporte MCP."""

    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.data = data

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.data is not None:
            result["data"] = self.data
        return result
