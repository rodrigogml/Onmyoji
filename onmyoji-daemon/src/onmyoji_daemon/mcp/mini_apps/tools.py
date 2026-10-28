"""Ferramentas MCP explícitas para o gateway Mini Apps."""
from __future__ import annotations

from typing import Any

from .service import MiniAppsMcpError, MiniAppsMcpService


class MiniAppsToolRegistry:
    """Publica uma ferramenta por operação administrativa, sem transferência de arquivos."""

    _TOOLS = {
        "status": ("status", "Consulta o estado do Gateway Mini Apps e a quantidade de publicações.", False, {}),
        "diagnose": ("diagnose", "Diagnostica pré-requisitos locais e públicos das Mini Apps.", False, {}),
        "list": ("list", "Lista as Mini Apps publicadas.", False, {}),
        "events": ("events", "Lista eventos recentes; informe id para restringir a uma Mini App.", False, {"id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}),
        "create": ("create", "Cria o registro de uma Mini App a partir de referências existentes no workspace; não envia arquivos.", True, {}),
        "inspect": ("inspect", "Consulta uma Mini App pelo id.", False, {"id": {"type": "string", "minLength": 1}}),
        "update": ("update", "Altera os metadados de uma Mini App existente; não envia arquivos.", True, {"id": {"type": "string", "minLength": 1}, "values": {"type": "object"}}),
        "publish": ("publish", "Publica uma Mini App já registrada.", True, {"id": {"type": "string", "minLength": 1}}),
        "unpublish": ("unpublish", "Remove uma Mini App da publicação sem apagar seu código no workspace.", True, {"id": {"type": "string", "minLength": 1}}),
        "reload": ("reload", "Recarrega uma Mini App publicada.", True, {"id": {"type": "string", "minLength": 1}}),
        "delete": ("delete", "Exclui o registro de uma Mini App; não apaga arquivos no workspace.", True, {"id": {"type": "string", "minLength": 1}}),
        "tunnel_status": ("tunnel.status", "Consulta o estado do Cloudflare Tunnel das Mini Apps.", False, {}),
        "tunnel_configure": ("tunnel.configure", "Configura o Cloudflare Tunnel das Mini Apps.", True, {}),
        "tunnel_provision": ("tunnel.provision", "Cria ou atualiza Tunnel, ingress e DNS configurados.", True, {}),
        "tunnel_start": ("tunnel.start", "Inicia o Cloudflare Tunnel configurado.", True, {}),
        "tunnel_stop": ("tunnel.stop", "Para o Cloudflare Tunnel configurado.", True, {}),
        "tunnel_restart": ("tunnel.restart", "Reinicia o Cloudflare Tunnel configurado.", True, {}),
        "tunnel_install_cloudflared": ("tunnel.install-cloudflared", "Instala o executável cloudflared oficial.", True, {}),
        "tls_status": ("tls.status", "Consulta os certificados TLS locais do Gateway.", False, {}),
        "tls_install_trust": ("tls.install-trust", "Instala a CA local como confiável nesta máquina.", True, {}),
        "oauth_callback_url": ("oauth.callback-url", "Obtém a URI de callback OAuth do BTG MCP.", False, {}),
        "oauth_info": ("oauth.info", "Consulta a configuração OAuth remota do perfil BTG.", False, {"profile": {"type": "string", "minLength": 1}}),
        "oauth_active": ("oauth.active", "Consulta a autorização OAuth BTG ativa para um perfil.", False, {"profile": {"type": "string", "minLength": 1}}),
        "oauth_start": ("oauth.start", "Inicia a autorização OAuth remota do perfil BTG.", True, {"profile": {"type": "string", "minLength": 1}}),
        "oauth_status": ("oauth.status", "Consulta uma sessão OAuth BTG.", False, {"session_id": {"type": "string", "minLength": 1}, "profile": {"type": "string"}}),
        "oauth_cancel": ("oauth.cancel", "Cancela uma sessão OAuth BTG pendente.", True, {"session_id": {"type": "string", "minLength": 1}, "profile": {"type": "string"}}),
        "oauth_cancel_active": ("oauth.cancel-active", "Cancela a autorização OAuth BTG ativa de um perfil.", True, {"profile": {"type": "string", "minLength": 1}}),
    }

    def __init__(self, root):
        self.root = root

    def definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for name, (_action, description, writes, properties) in self._TOOLS.items():
            schema_properties = dict(properties)
            if writes:
                schema_properties["confirm"] = {"type": "boolean", "description": "Obrigatório e deve ser true para alterar estado."}
            required = [key for key, value in properties.items() if value.get("minLength") == 1]
            if writes:
                required.append("confirm")
            definitions.append({"name": "mini_apps." + name, "description": description, "input_schema": {"type": "object", "properties": schema_properties, "required": required, "additionalProperties": True}})
        return definitions

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        key = name.removeprefix("mini_apps.")
        definition = self._TOOLS.get(key)
        if definition is None or not name.startswith("mini_apps."):
            raise MiniAppsMcpError("Ferramenta Mini Apps indisponível.")
        action, _description, writes, _properties = definition
        if not isinstance(arguments, dict):
            raise MiniAppsMcpError("Argumentos Mini Apps inválidos.")
        if writes and arguments.get("confirm") is not True:
            raise MiniAppsMcpError("Confirme a operação Mini Apps com confirm=true.")
        return MiniAppsMcpService(self.root).invoke(action, dict(arguments))
