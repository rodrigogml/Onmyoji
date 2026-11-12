"""Erros estruturados do contrato de publicação."""


class GatewayError(ValueError):
    """Recusa uma operação sem expor parâmetros ou dados de autenticação."""

    def __init__(self, code: str, message: str, path_prefix: str | None = None):
        super().__init__(message)
        self.code, self.path_prefix = code, path_prefix
        self.details = {"path_prefix": path_prefix} if path_prefix is not None else None

    def as_dict(self) -> dict:
        result = {"code": self.code, "message": str(self)}
        if self.path_prefix is not None:
            result["path_prefix"] = self.path_prefix
        return result
