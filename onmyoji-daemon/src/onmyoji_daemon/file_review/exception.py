"""Falhas de contrato da revisão de arquivos."""


class FileReviewError(ValueError):
    """Informa código estável e status HTTP sem expor conteúdo dos documentos."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status
