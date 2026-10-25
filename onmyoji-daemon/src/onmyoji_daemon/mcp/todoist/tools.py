"""Registro dinâmico das tools Todoist conforme perfis habilitados."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import OPERATIONS, TodoistService


_OPERATION_DESCRIPTIONS = {
    "get_user": "Obtém o usuário autenticado.",
    "get_task": "Obtém uma tarefa pelo identificador.",
    "create_task": "Cria uma tarefa.",
    "update_task": "Altera uma tarefa.",
    "complete_task": "Conclui uma tarefa.",
    "reopen_task": "Reabre uma tarefa concluída.",
    "delete_task": "Remove uma tarefa.",
    "filter_tasks": "Lista tarefas usando os filtros da API Todoist.",
    "list_projects": "Lista projetos.",
    "get_project": "Obtém um projeto.",
    "create_project": "Cria um projeto.",
    "update_project": "Altera um projeto.",
    "delete_project": "Remove um projeto.",
    "archive_project": "Arquiva um projeto.",
    "unarchive_project": "Desarquiva um projeto.",
    "list_archived_projects": "Lista projetos arquivados.",
    "list_sections": "Lista seções.",
    "get_section": "Obtém uma seção.",
    "create_section": "Cria uma seção.",
    "update_section": "Altera uma seção.",
    "delete_section": "Remove uma seção.",
    "archive_section": "Arquiva uma seção.",
    "unarchive_section": "Desarquiva uma seção.",
    "list_labels": "Lista etiquetas pessoais.",
    "get_label": "Obtém uma etiqueta pessoal.",
    "create_label": "Cria uma etiqueta pessoal.",
    "update_label": "Altera uma etiqueta pessoal.",
    "delete_label": "Remove uma etiqueta pessoal.",
    "list_shared_labels": "Lista etiquetas compartilhadas.",
    "rename_shared_label": "Renomeia uma etiqueta compartilhada.",
    "remove_shared_label": "Remove uma etiqueta compartilhada.",
    "list_comments": "Lista comentários.",
    "get_comment": "Obtém um comentário.",
    "create_comment": "Cria um comentário.",
    "update_comment": "Altera um comentário.",
    "delete_comment": "Remove um comentário.",
    "list_project_collaborators": "Lista colaboradores de um projeto.",
    "list_activities": "Lista atividades.",
    "list_reminders": "Lista lembretes.",
    "get_reminder": "Obtém um lembrete.",
    "create_reminder": "Cria um lembrete.",
    "update_reminder": "Altera um lembrete.",
    "delete_reminder": "Remove um lembrete.",
    "list_backups": "Lista backups.",
    "download_backup": "Baixa um backup em base64.",
    "get_email_settings": "Obtém configurações de e-mail.",
    "get_notification_settings": "Obtém configurações de notificações.",
    "revoke_token": "Revoga o token autenticado.",
}


class TodoistToolRegistry:
    """Aplica a regra de zero, um ou vários perfis à superfície MCP."""

    def __init__(self, configuration: McpConfiguration): self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_todoist_profiles()
        if not profiles: return []
        tools: list[dict[str, Any]] = []
        if len(profiles) > 1:
            tools.append({"name": "todoist.list_profiles", "description": "Lista os perfis Todoist habilitados e quando usar cada um.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        tools.append({"name": "todoist.list_tasks", "description": "Lista tarefas ativas." + self._profile_context(profiles), "input_schema": self._schema(profiles)})
        for operation, (_, _, destructive) in OPERATIONS.items():
            suffix = " Exige confirm=true porque altera ou remove dados." if destructive else ""
            tools.append({"name": "todoist." + operation, "description": _OPERATION_DESCRIPTIONS[operation] + suffix + self._profile_context(profiles), "input_schema": self._schema(profiles, destructive=destructive)})
        tools.append({"name": "todoist.sync", "description": "Executa comandos Sync API do Todoist." + self._profile_context(profiles), "input_schema": self._schema(profiles, sync=True)})
        tools.append({"name": "todoist.upload_file", "description": "Envia um arquivo local ao Todoist." + self._profile_context(profiles), "input_schema": self._schema(profiles, upload=True)})
        tools.append({"name": "todoist.delete_upload", "description": "Remove um upload do Todoist. Exige confirm=true." + self._profile_context(profiles), "input_schema": self._schema(profiles, delete_upload=True)})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_todoist_profiles()
        if name == "todoist.list_profiles" and len(profiles) > 1:
            return {"profiles": profiles}
        profile = self._profile_name(profiles, arguments)
        if name == "todoist.list_tasks" and profile:
            return TodoistService(self.configuration.root).list_tasks(profile)
        if name.startswith("todoist.") and profile:
            operation = name.removeprefix("todoist.")
            if operation in OPERATIONS or operation in {"sync", "upload_file", "delete_upload"}:
                return TodoistService(self.configuration.root).invoke(profile, operation, arguments)
        raise ValueError("Tool Todoist indisponível.")

    @staticmethod
    def _profile_context(profiles: list[dict[str, str]]) -> str:
        if len(profiles) == 1:
            profile = profiles[0]
            return f' Perfil ativo: "{profile["name"]}" — {profile["description"]}'
        return " Escolha o perfil adequado pelo campo profile; use todoist.list_profiles para consultar o contexto de cada perfil."

    @staticmethod
    def _profile_name(profiles: list[dict[str, str]], arguments: dict[str, Any]) -> str | None:
        if not profiles:
            return None
        name = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(name, str) or name not in {item["name"] for item in profiles}:
            raise ValueError("Perfil Todoist indisponível.")
        return name

    @staticmethod
    def _schema(profiles: list[dict[str, str]], *, destructive: bool = False, sync: bool = False, upload: bool = False, delete_upload: bool = False) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        if len(profiles) > 1:
            properties["profile"] = {"type": "string", "enum": [profile["name"] for profile in profiles]}
            required.append("profile")
        if sync:
            properties.update({"commands": {"type": "array", "description": "Comandos da Sync API."}, "sync_token": {"type": "string", "description": "Token Sync; use * para sincronização completa."}})
            required.append("commands")
        elif upload:
            properties.update({"file_path": {"type": "string", "description": "Caminho absoluto do arquivo local."}, "filename": {"type": "string", "description": "Nome opcional no Todoist."}})
            required.append("file_path")
        elif delete_upload:
            properties["upload_id"] = {"type": "string"}
            required += ["upload_id", "confirm"]
        else:
            properties.update({"params": {"type": "object", "description": "Parâmetros de rota, como task_id ou project_id."}, "query": {"type": "object", "description": "Parâmetros de consulta da API."}, "body": {"type": "object", "description": "Corpo JSON aceito pela API Todoist."}})
        if destructive:
            required.append("confirm")
        if destructive or delete_upload:
            properties["confirm"] = {"type": "boolean", "description": "Deve ser true para confirmar a alteração destrutiva."}
        schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        return schema
