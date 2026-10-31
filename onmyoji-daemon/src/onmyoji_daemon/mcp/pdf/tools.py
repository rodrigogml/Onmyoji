"""Registro público das operações documentais de PDF."""
from ..document_processing.tools import DocumentToolRegistry


class PdfToolRegistry(DocumentToolRegistry):
    def __init__(self, configuration):
        super().__init__(configuration, "pdf")
