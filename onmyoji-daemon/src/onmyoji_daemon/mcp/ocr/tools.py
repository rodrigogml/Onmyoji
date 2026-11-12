"""Registro público de OCR e análise de imagens."""
from ..document_processing.tools import DocumentToolRegistry


class OcrToolRegistry(DocumentToolRegistry):
    def __init__(self, configuration):
        super().__init__(configuration, "ocr")
