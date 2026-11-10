"""Contribuições de instruções declaradas pelos módulos MCP ativos."""
from __future__ import annotations

from dataclasses import dataclass

from .configuration import McpConfiguration
from .telegram.tools import TelegramToolRegistry
from .workflows import WorkflowRegistry


@dataclass(frozen=True)
class McpInstructionContribution:
    """Um bloco curto de instruções publicado por um módulo MCP ativo."""

    source: str
    content: str


class McpInstructionRegistry:
    """Reúne somente instruções dos módulos disponíveis na instância.

    A disponibilidade é decidida pelo próprio registro do módulo. Assim, um
    serviço sem ferramentas ativas nunca deixa instruções residuais no Codex.
    """

    def __init__(self, configuration: McpConfiguration):
        self.configuration = configuration

    def contributions(self) -> tuple[McpInstructionContribution, ...]:
        contributions = []
        workflow_instructions = WorkflowRegistry(self.configuration).developer_instructions()
        if workflow_instructions:
            contributions.append(McpInstructionContribution("workflows-mcp", workflow_instructions))
        telegram_instructions = TelegramToolRegistry(self.configuration.root).developer_instructions()
        if telegram_instructions:
            contributions.append(McpInstructionContribution("telegram-mcp", telegram_instructions))
        return tuple(contributions)

    def developer_instructions(self) -> str | None:
        """Compõe o bloco a ser entregue no handshake e no App Server."""
        contributions = self.contributions()
        if not contributions:
            return None
        return "\n\n".join(item.content for item in contributions)
