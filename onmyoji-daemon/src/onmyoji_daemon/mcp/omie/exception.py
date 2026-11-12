"""Exceções de domínio da integração Omie MCP."""


class OmieError(ValueError):
    """Erro funcional sanitizado da integração Omie."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def as_dict(self) -> dict:
        """Retorna o diagnóstico público, sem detalhes da resposta remota."""
        return {"code": self.code, "message": str(self), "details": self.details}
