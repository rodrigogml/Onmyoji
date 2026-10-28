"""Ferramentas MCP do gateway Mini Apps."""
from __future__ import annotations
from typing import Any
from .service import MiniAppsMcpError, MiniAppsMcpService

class MiniAppsToolRegistry:
    def __init__(self, root): self.root = root
    def definitions(self) -> list[dict[str, Any]]:
        actions = ["status", "diagnose", "list", "events", "create", "inspect", "update", "publish", "unpublish", "reload", "delete", "tunnel.status", "tunnel.configure", "tunnel.provision", "tunnel.start", "tunnel.stop", "tunnel.restart", "tunnel.install-cloudflared", "tls.status", "tls.install-trust", "oauth.callback-url", "oauth.info", "oauth.active", "oauth.start", "oauth.status", "oauth.cancel", "oauth.cancel-active"]
        return [{"name": "mini_apps.invoke", "description": "Executa uma operação do Gateway Mini Apps por parâmetros estruturados. Não envia arquivos: create e update recebem somente referências a diretórios existentes no workspace. Operações que publicam, excluem, alteram Tunnel, confiança TLS ou OAuth exigem confirm=true.", "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": actions}, "params": {"type": "object"}, "confirm": {"type": "boolean"}}, "required": ["action"], "additionalProperties": False}}]
    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name != "mini_apps.invoke": raise MiniAppsMcpError("Ferramenta Mini Apps indisponível.")
        action = arguments.get("action"); params = arguments.get("params", {})
        if not isinstance(action, str) or not isinstance(params, dict): raise MiniAppsMcpError("Ação ou parâmetros Mini Apps inválidos.")
        writes = {"create", "update", "publish", "unpublish", "reload", "delete", "tunnel.configure", "tunnel.provision", "tunnel.start", "tunnel.stop", "tunnel.restart", "tunnel.install-cloudflared", "tls.install-trust", "oauth.start", "oauth.cancel", "oauth.cancel-active"}
        if action in writes:
            if arguments.get("confirm") is not True: raise MiniAppsMcpError("Confirme a operação Mini Apps com confirm=true.")
            params = {**params, "confirm": True}
        return MiniAppsMcpService(self.root).invoke(action, params)
