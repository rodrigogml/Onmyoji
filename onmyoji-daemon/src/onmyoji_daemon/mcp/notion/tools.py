"""Publicação dinâmica das ferramentas Notion."""
from __future__ import annotations

import re
from typing import Any

from ..configuration import McpConfiguration
from .service import NotionService, OPERATIONS


DESCRIPTIONS = {
    "blocks.append": "Adiciona blocos filhos a um bloco ou página.", "blocks.get": "Consulta um bloco.", "blocks.children": "Lista os filhos de um bloco.", "blocks.update": "Atualiza um bloco.", "blocks.delete": "Remove um bloco.",
    "pages.create": "Cria uma página.", "pages.get": "Consulta uma página.", "pages.move": "Move uma página.", "pages.update": "Atualiza propriedades de uma página.", "pages.trash": "Move uma página para a lixeira ou a restaura conforme o corpo.", "pages.property": "Consulta um item de propriedade de página.",
    "pages.markdown.get": "Obtém o Markdown aprimorado de uma página.", "pages.markdown.update": "Atualiza uma página por Markdown aprimorado.",
    "databases.create": "Cria um database.", "databases.get": "Consulta um database.", "databases.update": "Atualiza um database.",
    "data_sources.create": "Cria um data source.", "data_sources.get": "Consulta um data source.", "data_sources.update": "Atualiza um data source.", "data_sources.properties.update": "Atualiza propriedades do schema de um data source.", "data_sources.query": "Consulta páginas de um data source.", "data_sources.templates": "Lista templates de um data source.",
    "comments.create": "Cria um comentário.", "comments.get": "Consulta um comentário.", "comments.list": "Lista comentários.", "comments.update": "Atualiza um comentário.", "comments.delete": "Remove um comentário.",
    "views.create": "Cria uma view.", "views.get": "Consulta uma view.", "views.list": "Lista views de um database.", "views.update": "Atualiza uma view.", "views.delete": "Remove uma view.", "views.query.create": "Cria uma consulta persistida de view.", "views.query.get": "Consulta uma query de view.", "views.query.delete": "Remove uma query de view.",
    "search": "Pesquisa páginas e data sources compartilhados com a integração.", "users.list": "Lista usuários acessíveis.", "users.get": "Consulta um usuário.", "users.self": "Consulta o usuário ou bot da integração.", "custom_emojis.list": "Lista emojis personalizados.",
    "file_uploads.create": "Cria uma sessão de upload.", "file_uploads.send": "Envia um arquivo de uma raiz autorizada do perfil.", "file_uploads.complete": "Conclui um upload multipart.", "file_uploads.get": "Consulta um upload.", "file_uploads.list": "Lista uploads.",
    "async_tasks.get": "Consulta uma tarefa assíncrona.", "oauth.introspect": "Introspecta um token OAuth conforme o contrato público.", "oauth.revoke": "Revoga um token OAuth conforme o contrato público.",
}


class NotionToolRegistry:
    """Expõe toda a API Notion migrada quando há perfil habilitado."""

    def __init__(self, configuration: McpConfiguration):
        self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_notion_profiles()
        if not profiles:
            return []
        tools: list[dict[str, Any]] = []
        if len(profiles) > 1:
            tools.append({"name": "notion.list_profiles", "description": "Lista perfis Notion habilitados e seu contexto de uso.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation, (_, _, writes) in OPERATIONS.items():
            suffix = " Exige confirm=true porque altera conteúdo ou configuração." if writes else ""
            tools.append({"name": "notion." + operation, "description": DESCRIPTIONS[operation] + suffix + self._context(profiles), "input_schema": self._schema(profiles, operation, writes)})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_notion_profiles()
        if name == "notion.list_profiles" and len(profiles) > 1:
            return {"profiles": profiles}
        profile = self._profile(profiles, arguments)
        operation = name.removeprefix("notion.") if name.startswith("notion.") else ""
        if profile and operation in OPERATIONS:
            return NotionService(self.configuration.root).invoke(profile, operation, arguments)
        raise ValueError("Tool Notion indisponível.")

    @staticmethod
    def _context(profiles: list[dict[str, str]]) -> str:
        if len(profiles) == 1:
            return f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}'
        return " Escolha profile conforme notion.list_profiles e o contexto de cada perfil."

    @staticmethod
    def _profile(profiles: list[dict[str, str]], arguments: dict[str, Any]) -> str | None:
        if not profiles:
            return None
        name = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(name, str) or name not in {item["name"] for item in profiles}:
            raise ValueError("Perfil Notion indisponível.")
        return name

    @staticmethod
    def _schema(profiles: list[dict[str, str]], operation: str, writes: bool) -> dict[str, Any]:
        method, template, _ = OPERATIONS[operation]
        properties: dict[str, Any] = {}
        required: list[str] = []
        if len(profiles) > 1:
            properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}
            required.append("profile")
        identifiers = re.findall(r"\{([^}]+)\}", template)
        if identifiers:
            properties["params"] = {"type": "object", "properties": {key: {"type": "string", "minLength": 1} for key in identifiers}, "required": identifiers, "additionalProperties": False}
            required.append("params")
        if method == "GET":
            properties["query"] = {"type": "object", "description": "Filtros e paginação aceitos pelo endpoint Notion."}
        if method in {"POST", "PATCH"}:
            if operation == "file_uploads.send":
                properties["body"] = {"type": "object", "properties": {"file_path": {"type": "string", "minLength": 1}}, "required": ["file_path"], "additionalProperties": False}
                required.append("body")
            else:
                properties["body"] = {"type": "object", "description": "Corpo JSON aceito pelo endpoint Notion."}
        properties["paginate"] = {"type": "boolean", "description": "Agrega todas as páginas quando a resposta usa has_more e next_cursor."}
        if writes:
            properties["confirm"] = {"type": "boolean", "description": "Deve ser true para confirmar a alteração."}
            required.append("confirm")
        schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        return schema
