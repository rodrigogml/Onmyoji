"""Exceções de domínio da integração Omie MCP."""


class OmieError(ValueError):
    """Erro funcional sanitizado da integração Omie."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
