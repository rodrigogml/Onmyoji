"""Catálogo de workflows operacionais lidos sob demanda pelo MCP."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .configuration import McpConfiguration


class WorkflowError(ValueError):
    """Indica uma solicitação inválida ou indisponível de workflow."""


@dataclass(frozen=True)
class Workflow:
    """Define uma rota curta e seu documento canônico interno."""

    identifier: str
    module: str
    trigger: str
    source: str


class WorkflowRegistry:
    """Publica somente workflows habilitados pela configuração da instância."""

    def __init__(self, configuration: McpConfiguration):
        self.configuration = configuration
        self.resources = Path(__file__).resolve().parents[3] / "resources" / "workflows"

    def available(self) -> tuple[Workflow, ...]:
        workflows: list[Workflow] = []
        if self.configuration.memory_tool_available():
            workflows.extend((
                Workflow("memory-decision", "memory", "Antes de responder a uma solicitação substantiva, decidir, planejar, agir ou perguntar ao usuário ou a terceiros, aplique este procedimento de pesquisa.", "memory-decision.md"),
                Workflow("memory-learning", "memory", "Após correção, preferência durável ou aprendizado validado, avalie este procedimento antes de encerrar; aplique-o antes de criar, revisar ou excluir memória.", "memory-learning.md"),
            ))
        if self.configuration.data_tables_schema_authoring_available():
            workflows.append(Workflow("data-tables-schema-authoring", "data_tables", "Quando o usuário solicitar criar, alterar, corrigir ou estruturar um schema destinado exclusivamente ao serviço MCP data_tables, aplique este procedimento antes de editar a definição.", "data-tables-schema-authoring.md"))
        if self.configuration.enabled_email_accounts():
            workflows.append(Workflow("email-operations", "email", "Antes de usar email.* para consultar, organizar ou enviar e-mails, leia o procedimento de contas, anexos, conteúdo não confiável e submissões SMTP.", "email-operations.md"))
        return tuple(workflows)

    def developer_instructions(self) -> str | None:
        routes = self.available()
        if not routes:
            return None
        lines = ["[ONMYŌJI — WORKFLOWS MCP]", "Os workflows abaixo são obrigatórios quando o gatilho ocorrer. Leia o conteúdo completo com workflows.read usando o id indicado, se ainda não estiver no contexto atual, e siga todas as etapas antes de continuar."]
        lines.extend(f"- `{item.identifier}`: {item.trigger}" for item in routes)
        return "\n".join(lines)

    def definitions(self) -> list[dict[str, Any]]:
        if not self.available():
            return []
        return [{
            "name": "workflows.read",
            "description": "Lê o procedimento operacional completo de um workflow MCP disponível. Use quando uma developer instruction ou a descrição de uma tool indicar seu identificador.",
            "input_schema": {"type": "object", "properties": {"id": {"type": "string", "enum": [item.identifier for item in self.available()], "description": "Identificador do workflow indicado pela rota ativa."}}, "required": ["id"], "additionalProperties": False},
        }]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, str]:
        if name != "workflows.read":
            raise WorkflowError("Tool de workflow indisponível.")
        identifier = arguments.get("id")
        workflow = next((item for item in self.available() if item.identifier == identifier), None)
        if workflow is None:
            raise WorkflowError("Workflow indisponível para esta instância.")
        path = (self.resources / workflow.source).resolve()
        if not path.is_relative_to(self.resources) or not path.is_file() or path.is_symlink():
            raise WorkflowError("Workflow canônico indisponível.")
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise WorkflowError("Não foi possível ler o workflow canônico.") from error
        if not content:
            raise WorkflowError("Workflow canônico inválido.")
        return {"id": workflow.identifier, "module": workflow.module, "content": content}
