"""Ferramentas explícitas para Gateway e Cloudflare Tunnel independentes."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable

from ...rpc import RpcError, call
from ...supervisor import endpoint


class PublicationMcpError(ValueError):
    """A operação administrativa foi rejeitada ou está indisponível."""

    def __init__(self, message: str, code: str = "invalid_request", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.details = code, details

    def as_dict(self) -> dict[str, Any]:
        result = {"code": self.code, "message": str(self)}
        if self.details: result["details"] = self.details
        return result


class PublicationToolRegistry:
    """Expõe administração autenticada sem revelar tokens de transporte."""

    def __init__(self, root: Path, rpc: Callable[..., Any] = call, endpoint_reader: Callable[..., Any] = endpoint):
        self.root, self.rpc, self.endpoint_reader = root.resolve(), rpc, endpoint_reader

    @staticmethod
    def _tools() -> dict[str, tuple[str, bool, dict[str, Any], list[str]]]:
        tools = {
            "gateway.status": ("Consulta disponibilidade local, listeners e publicação externa.", False, {}, []),
            "gateway.configure": ("Configura a porta HTTPS local do Gateway; reinicie para aplicar.", True, {"http_port": {"type": "integer", "minimum": 1, "maximum": 65535}}, ["http_port"]),
            "gateway.listeners_list": ("Lista as reservas de primeiro segmento do Gateway.", False, {}, []),
            "cloudflare_tunnel.status": ("Consulta o Cloudflare Tunnel independente das Mini Apps.", False, {}, []),
            "cloudflare_tunnel.configure": ("Atualiza a configuração do Tunnel preservando IDs e referências KeePass existentes.", True, {"values": {"type": "object"}}, ["values"]),
            "cloudflare_tunnel.provision": ("Provisiona o hostname único, ingress e DNS; retorna operation_id para acompanhamento.", True, {}, []),
            "cloudflare_tunnel.inspect_publication": ("Descobre a publicação existente por DNS/conta e retorna uma prévia para recuperação, sem alterar a Cloudflare; acompanhe pelo operation_id.", False, {}, []),
            "cloudflare_tunnel.adopt_publication": ("Reutiliza a publicação da prévia confirmada, atualizando a origem e TLS do Gateway; retorna operation_id.", True, {"preview_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"}}, ["preview_id"]),
            "cloudflare_tunnel.rollback_publication": ("Restaura a configuração remota anterior de uma recuperação confirmada, recusando mudanças posteriores; retorna operation_id.", True, {"preview_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"}}, ["preview_id"]),
            "cloudflare_tunnel.install_cloudflared": ("Instala cloudflared oficial; retorna operation_id para acompanhamento.", True, {"install_directory": {"type": "string"}, "version": {"type": "string"}, "adopt": {"type": "boolean"}}, []),
            "cloudflare_tunnel.update_cloudflared": ("Atualiza cloudflared oficial; retorna operation_id para acompanhamento.", True, {"install_directory": {"type": "string"}, "version": {"type": "string"}, "adopt": {"type": "boolean"}}, []),
            "cloudflare_tunnel.rollback_cloudflared": ("Restaura a versão anterior preservada do cloudflared.", True, {}, []),
            "cloudflare_tunnel.operation_status": ("Consulta resultado ou falha sanitizada de uma operação longa.", False, {"operation_id": {"type": "string", "minLength": 1}}, ["operation_id"]),
        }
        for action in ("inspect", "enable", "disable", "remove", "restore"):
            tools["gateway.listeners_" + action] = ("Administra uma reserva de path pelo Gateway: " + action + ".", action != "inspect", {"path_prefix": {"type": "string", "minLength": 1}, "owner": {"type": "string"}}, ["path_prefix"])
        for namespace in ("gateway", "cloudflare_tunnel"):
            for action in ("start", "stop", "restart", "enable", "disable"):
                tools[namespace + "." + action] = ("Administra o serviço pelo Supervisor: " + action + ".", True, {}, [])
        return tools

    def definitions(self) -> list[dict[str, Any]]:
        result = []
        for name, (description, writes, properties, required) in self._tools().items():
            properties, required = dict(properties), list(required)
            if writes:
                properties["confirm"] = {"type": "boolean", "const": True}
                required.append("confirm")
            result.append({"name": name, "description": description, "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}})
        return result

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        definition = self._tools().get(name)
        if definition is None or not isinstance(arguments, dict):
            raise PublicationMcpError("Ferramenta ou argumentos de publicação inválidos.")
        _description, writes, properties, required = definition
        if set(arguments) - {*properties, "confirm"}:
            raise PublicationMcpError("Argumentos de publicação desconhecidos.")
        if writes and arguments.get("confirm") is not True:
            raise PublicationMcpError("Confirme a operação de publicação com confirm=true.", "confirmation_required")
        if any(key not in arguments for key in required):
            raise PublicationMcpError("Argumentos obrigatórios de publicação ausentes.")
        namespace, action = name.split(".", 1)
        service = "cloudflare-tunnel" if namespace == "cloudflare_tunnel" else "gateway"
        params = dict(arguments)
        if action in {"start", "stop", "restart", "enable", "disable"}:
            method, params = action, {"service": service}
        elif action == "configure" and service == "cloudflare-tunnel":
            if not isinstance(arguments["values"], dict): raise PublicationMcpError("values deve ser um objeto JSON.")
            method, params = service + ".configure", {**arguments["values"], "confirm": True}
        else:
            action = action.replace("listeners_", "listeners.").replace("install_cloudflared", "install-cloudflared").replace("update_cloudflared", "update-cloudflared").replace("rollback_cloudflared", "rollback-cloudflared").replace("operation_status", "operations.status")
            action = {"inspect_publication": "inspect-publication", "adopt_publication": "adopt-publication", "rollback_publication": "rollback-publication"}.get(action, action)
            method = service + "." + action
        try:
            host, port, token = self.endpoint_reader(self.root)
            return self.rpc(host, port, token, method, params, timeout=20 if method in {"start", "stop", "restart"} else 5)
        except RpcError as error:
            code = error.code if isinstance(error.code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", error.code) else "daemon_rejected"
            prefix = error.details.get("path_prefix") if isinstance(error.details, dict) else None
            details = {"path_prefix": prefix} if isinstance(prefix, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", prefix) else None
            raise PublicationMcpError("Publicação externa indisponível ou operação recusada pelo daemon.", code, details) from error
        except Exception as error:
            raise PublicationMcpError("Publicação externa indisponível ou operação recusada pelo daemon.", "daemon_unavailable") from error
