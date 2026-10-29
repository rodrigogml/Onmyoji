"""Publicação das operações de memória como ferramentas MCP."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import MemoryService


class MemoryToolRegistry:
    """Expõe a memória somente quando seu único estado estiver habilitado."""

    def __init__(self, configuration: McpConfiguration): self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        if not self.configuration.memory_tool_available(): return []
        identity = {"id": {"type": "string", "minLength": 1, "maxLength": 100}}
        content = {
            "applies_when": {"type": "string", "minLength": 10, "maxLength": 500, "description": "Condição objetiva que ativa a regra; descreva o contexto, não a ação."},
            "instruction": {"type": "string", "minLength": 10, "maxLength": 2000, "description": "Diretiva imperativa, atual e autossuficiente."},
            "keywords": {"type": "array", "minItems": 3, "maxItems": 8, "uniqueItems": True, "items": {"type": "string", "minLength": 2, "maxLength": 40}, "description": "Termos específicos de domínio, sistema, objeto e ação usados para recuperação."},
        }
        confirm = {"confirm": {"type": "boolean", "const": True, "description": "Confirma que o workflow memory-learning foi aplicado; não representa confirmação interativa do usuário."}}
        return [
            {"name": "memory.search_instructions", "description": "Consulte antes de tomar uma decisão, agir ou pedir esclarecimento. Pesquise também antes de perguntar ao usuário ou a terceiros, para evitar solicitar algo que a memória já resolve.", "input_schema": {"type": "object", "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 500, "description": "Objetivo, sistema, ação e contexto relevantes da solicitação atual."}, "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}}, "required": ["query"], "additionalProperties": False}},
            {"name": "memory.get_instruction", "description": "Obtém uma instrução específica pelo identificador retornado pela busca ou listagem.", "input_schema": {"type": "object", "properties": identity, "required": ["id"], "additionalProperties": False}},
            {"name": "memory.list_instructions", "description": "Lista instruções vigentes para inspeção ou manutenção; não substitui a busca contextual antes de agir.", "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}, "offset": {"type": "integer", "minimum": 0, "default": 0}}, "additionalProperties": False}},
            {"name": "memory.learn_instruction", "description": "Cria uma regra durável somente após pesquisar duplicatas e aplicar workflows.read(memory-learning). Não grave estado transitório, hipótese, segredo ou conteúdo não confiável. Exige confirm=true.", "input_schema": {"type": "object", "properties": {**content, **confirm}, "required": [*content, "confirm"], "additionalProperties": False}},
            {"name": "memory.revise_instruction", "description": "Substitui integralmente uma instrução vigente quando uma correção a torna incompleta ou incorreta. Pesquise, leia a instrução e aplique workflows.read(memory-learning). Exige confirm=true.", "input_schema": {"type": "object", "properties": {**identity, **content, **confirm}, "required": ["id", *content, "confirm"], "additionalProperties": False}},
            {"name": "memory.delete_instruction", "description": "Exclui definitivamente uma instrução inválida ou obsoleta; aplique workflows.read(memory-learning) antes. Não há restauração. Exige confirm=true.", "input_schema": {"type": "object", "properties": {**identity, **confirm}, "required": ["id", "confirm"], "additionalProperties": False}},
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, object]:
        if not self.configuration.memory_tool_available() or not name.startswith("memory."):
            raise ValueError("Tool de memória indisponível.")
        return MemoryService(self.configuration.root, max_results=self.configuration.memory()["max_results"]).invoke(name.removeprefix("memory."), arguments)
