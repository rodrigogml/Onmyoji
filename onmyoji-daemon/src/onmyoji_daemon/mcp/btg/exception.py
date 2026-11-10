"""Erros funcionais sanitizados da integração BTG MCP."""


class BtgError(ValueError):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
