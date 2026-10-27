"""Contrato funcional da memória de instruções do MCP."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .store import MemoryStore, MemoryStoreError


class MemoryError(ValueError):
    """Erro sanitizado da memória de instruções."""


class MemoryService:
    """Executa somente operações explícitas sobre instruções vigentes."""

    def __init__(self, root: Path, *, max_results: int = 5):
        self.store = MemoryStore(root)
        self.max_results = max_results

    @staticmethod
    def _confirmed(arguments: dict[str, Any]) -> None:
        if arguments.get("confirm") is not True:
            raise MemoryError("A escrita exige confirm=true depois de aplicar o workflow de memória.")

    def invoke(self, operation: str, arguments: dict[str, Any]) -> dict[str, object]:
        try:
            if operation == "search_instructions":
                limit = arguments.get("limit", self.max_results)
                if not isinstance(limit, int) or not 1 <= limit <= 10: raise MemoryError("limit deve estar entre 1 e 10.")
                return self.store.search(arguments.get("query"), limit)
            if operation == "get_instruction": return {"instruction": self.store.get(arguments.get("id"))}
            if operation == "list_instructions":
                limit, offset = arguments.get("limit", 20), arguments.get("offset", 0)
                if not isinstance(limit, int) or not 1 <= limit <= 100 or not isinstance(offset, int) or offset < 0: raise MemoryError("Paginação inválida.")
                return self.store.list(limit, offset)
            self._confirmed(arguments)
            if operation == "learn_instruction": return self.store.learn(arguments.get("applies_when"), arguments.get("instruction"), arguments.get("keywords"))
            if operation == "revise_instruction": return self.store.revise(arguments.get("id"), arguments.get("applies_when"), arguments.get("instruction"), arguments.get("keywords"))
            if operation == "delete_instruction": return self.store.delete(arguments.get("id"))
            raise MemoryError("Operação de memória indisponível.")
        except MemoryStoreError as error:
            raise MemoryError(str(error)) from error
