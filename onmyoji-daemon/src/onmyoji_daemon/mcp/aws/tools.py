"""Publicação dinâmica das ferramentas AWS."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import AwsService, OPERATIONS, WRITE_OPERATIONS


DESCRIPTIONS = {
    "identity_get": "Consulta a identidade e a conta da credencial AWS.",
    "s3_bucket_list": "Lista buckets S3 acessíveis.", "s3_bucket_location": "Consulta a região de um bucket S3.",
    "s3_object_list": "Lista objetos de um bucket S3.", "s3_object_head": "Consulta metadados de um objeto S3.",
    "s3_object_upload": "Envia um arquivo de uma raiz local autorizada ao S3.", "s3_object_download": "Baixa um objeto S3 para uma raiz local autorizada.",
    "s3_object_download_batch": "Baixa em paralelo os objetos indicados por um manifesto local autorizado.",
    "s3_object_copy": "Copia um objeto entre chaves ou buckets S3.", "s3_object_delete": "Exclui um objeto S3.",
    "iam_role_list": "Lista funções IAM.", "iam_role_create": "Cria uma função IAM.", "iam_role_policy_put": "Cria ou substitui uma política inline de função IAM.",
    "s3_batch_job_describe": "Consulta um job S3 Batch Operations.", "s3_batch_restore_create": "Cria um job S3 Batch para restauração.",
    "iam_user_get": "Consulta um usuário IAM.", "iam_user_create": "Cria um usuário IAM sem efeitos adicionais.",
    "iam_user_login_get": "Consulta o perfil de login de um usuário IAM.", "iam_user_login_create": "Cria o login de console usando senha de uma entrada KeePass.",
    "iam_user_mfa_list": "Lista dispositivos MFA de um usuário IAM.", "iam_user_mfa_provision": "Cria e ativa MFA virtual após salvar a semente no KeePass.",
    "iam_user_policy_list": "Lista políticas gerenciadas associadas ao usuário IAM.", "iam_user_policy_attach": "Associa uma política gerenciada ao usuário IAM.",
    "iam_user_inline_policy_list": "Lista políticas inline do usuário IAM.", "iam_user_inline_policy_get": "Consulta uma política inline do usuário IAM.",
    "iam_user_policy_put": "Cria ou substitui uma política inline do usuário IAM.", "iam_user_groups_list": "Lista grupos do usuário IAM.",
}

FIELDS: dict[str, tuple[list[str], dict[str, dict[str, Any]]]] = {
    "s3_bucket_location": (["bucket"], {}), "s3_object_list": (["bucket"], {"prefix": {"type": "string"}, "continuation_token": {"type": "string"}, "max_keys": {"type": "integer", "minimum": 1, "maximum": 1000}}),
    "s3_object_head": (["bucket", "key"], {}), "s3_object_upload": (["bucket", "key", "source"], {}),
    "s3_object_download": (["bucket", "key", "destination"], {"overwrite": {"type": "boolean"}}),
    "s3_object_download_batch": (["bucket", "manifest", "destination", "status_path", "failures_path", "workers"], {"workers": {"type": "integer", "minimum": 1, "maximum": 32}}),
    "s3_object_copy": (["bucket", "key", "source_bucket", "source_key"], {}), "s3_object_delete": (["bucket", "key"], {}),
    "s3_batch_job_describe": (["account_id", "job_id"], {}), "s3_batch_restore_create": (["account_id", "role_arn", "batch_operation", "manifest", "report"], {"batch_operation": {"type": "object"}, "manifest": {"type": "object"}, "report": {"type": "object"}}),
    "iam_role_create": (["role_name", "trust_policy"], {"trust_policy": {"type": "object"}}), "iam_role_policy_put": (["role_name", "policy_name", "policy_document"], {"policy_document": {"type": "object"}}),
    "iam_user_get": (["user_name"], {}), "iam_user_create": (["user_name"], {}), "iam_user_login_get": (["user_name"], {}),
    "iam_user_login_create": (["user_name", "password_vault_profile", "password_vault_entry_path"], {"password_reset_required": {"type": "boolean"}}),
    "iam_user_mfa_list": (["user_name"], {}), "iam_user_mfa_provision": (["user_name", "device_name", "password_vault_profile", "password_vault_entry_path"], {"recreate_unassigned": {"type": "boolean"}}),
    "iam_user_policy_list": (["user_name"], {}), "iam_user_policy_attach": (["user_name", "policy_arn"], {}),
    "iam_user_inline_policy_list": (["user_name"], {}), "iam_user_inline_policy_get": (["user_name", "policy_name"], {}),
    "iam_user_policy_put": (["user_name", "policy_name", "policy_document"], {"policy_document": {"type": "object"}}), "iam_user_groups_list": (["user_name"], {}),
}


class AwsToolRegistry:
    """Expõe a superfície AWS somente quando existe perfil habilitado válido."""

    def __init__(self, configuration: McpConfiguration): self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_aws_profiles()
        if not profiles: return []
        tools: list[dict[str, Any]] = []
        if len(profiles) > 1:
            tools.append({"name": "aws.list_profiles", "description": "Lista perfis AWS habilitados e seus contextos de uso.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation in sorted(OPERATIONS):
            suffix = " Exige confirm=true porque altera recursos ou arquivos." if operation in WRITE_OPERATIONS else ""
            tools.append({"name": "aws." + operation, "description": DESCRIPTIONS[operation] + suffix + self._context(profiles), "input_schema": self._schema(profiles, operation)})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_aws_profiles()
        if name == "aws.list_profiles" and len(profiles) > 1: return {"profiles": profiles}
        profile = self._profile(profiles, arguments); operation = name.removeprefix("aws.") if name.startswith("aws.") else ""
        if profile and operation in OPERATIONS: return AwsService(self.configuration.root).invoke(profile, operation, arguments)
        raise ValueError("Tool AWS indisponível.")

    @staticmethod
    def _context(profiles: list[dict[str, str]]) -> str:
        if len(profiles) == 1: return f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}'
        return " Escolha profile conforme aws.list_profiles e o contexto de cada perfil."

    @staticmethod
    def _profile(profiles: list[dict[str, str]], arguments: dict[str, Any]) -> str | None:
        if not profiles: return None
        name = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(name, str) or name not in {item["name"] for item in profiles}: raise ValueError("Perfil AWS indisponível.")
        return name

    @staticmethod
    def _schema(profiles: list[dict[str, str]], operation: str) -> dict[str, Any]:
        required, special = FIELDS.get(operation, ([], {})); properties: dict[str, Any] = {}
        mandatory = list(required)
        if len(profiles) > 1: properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}; mandatory.insert(0, "profile")
        properties["region"] = {"type": "string", "minLength": 1, "description": "Sobrescreve a região padrão do perfil nesta chamada."}
        for field in required:
            properties[field] = special.get(field, {"type": "string", "minLength": 1})
        for field, schema in special.items(): properties.setdefault(field, schema)
        if operation in WRITE_OPERATIONS: properties["confirm"] = {"type": "boolean", "const": True}; mandatory.append("confirm")
        schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        if mandatory: schema["required"] = mandatory
        return schema
