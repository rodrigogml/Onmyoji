"""Cliente Cloudflare para um Tunnel e um hostname, sem sobrescrever DNS alheio."""
from __future__ import annotations

import json
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .configuration import TunnelDefinition, validate_origin
from .exception import TunnelError


class CloudflareClient:
    def __init__(self, token: str, base_url: str = "https://api.cloudflare.com/client/v4", timeout: float = 30):
        if not token:
            raise TunnelError("credential_missing", "Token Cloudflare não informado.")
        self.token, self.base_url, self.timeout = token, base_url.rstrip("/"), timeout

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=payload, headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise TunnelError("cloudflare_http_error", f"Cloudflare retornou HTTP {error.code}.") from error
        except (OSError, ValueError) as error:
            raise TunnelError("cloudflare_unavailable", "Falha de comunicação com a Cloudflare.") from error
        if not isinstance(value, dict) or value.get("success") is not True:
            raise TunnelError("cloudflare_rejected", "Cloudflare não confirmou a operação.")
        return value.get("result")

    @staticmethod
    def _confirmed(confirm: bool) -> None:
        if confirm is not True:
            raise TunnelError("confirmation_required", "Operações de escrita Cloudflare exigem confirm=true.")

    def create_tunnel(self, definition: TunnelDefinition, confirm: bool) -> dict[str, Any]:
        definition.validate()
        self._confirmed(confirm)
        existing = self._call("GET", f"/accounts/{definition.account_id}/cfd_tunnel?" + urllib.parse.urlencode({"name": definition.tunnel_name, "is_deleted": "false"})) or []
        if existing:
            raise TunnelError("tunnel_name_conflict", "Já existe um Tunnel com esse nome; informe seu ID explicitamente para adotá-lo.")
        result = self._call("POST", f"/accounts/{definition.account_id}/cfd_tunnel", {"name": definition.tunnel_name, "config_src": "cloudflare"})
        if not isinstance(result, dict) or not result.get("id"):
            raise TunnelError("invalid_response", "Cloudflare não retornou o ID do Tunnel criado.")
        return result

    def get_configuration(self, definition: TunnelDefinition) -> dict[str, Any]:
        definition.validate()
        result = self._call("GET", f"/accounts/{definition.account_id}/cfd_tunnel/{definition.tunnel_id}/configurations")
        if not isinstance(result, dict):
            raise TunnelError("invalid_response", "Cloudflare não retornou a configuração do Tunnel.")
        return result

    def set_configuration(self, definition: TunnelDefinition, config: dict[str, Any], confirm: bool) -> None:
        definition.validate()
        self._confirmed(confirm)
        if not isinstance(config, dict) or not isinstance(config.get("config"), dict):
            raise TunnelError("invalid_ingress", "Configuração remota de ingress inválida.")
        self._call("PUT", f"/accounts/{definition.account_id}/cfd_tunnel/{definition.tunnel_id}/configurations", {"config": config["config"]})

    def configure_ingress(self, definition: TunnelDefinition, tunnel_id: str, local_url: str, confirm: bool, ca_file: str, *, expected_previous: dict[str, Any] | None = None) -> None:
        """Reutiliza a origem exata ou substitui somente um snapshot autorizado.

        ``ingress_conflict`` indica publicação desconhecida ou alteração remota
        posterior ao diagnóstico; nenhuma regra é sobrescrita nessa situação.
        """
        definition.validate()
        self._confirmed(confirm)
        validate_origin(local_url, ca_file)
        current = self.get_configuration(TunnelDefinition(definition.account_id, definition.zone_id, definition.hostname, tunnel_id, definition.tunnel_name))
        previous = current.get("config", {})
        ingress = previous.get("ingress", []) if isinstance(previous, dict) else None
        if not isinstance(ingress, list) or any(not isinstance(rule, dict) for rule in ingress):
            raise TunnelError("invalid_ingress", "Cloudflare retornou uma configuração de ingress inválida.")
        named = [rule for rule in ingress if rule.get("hostname")]
        unnamed = [rule for rule in ingress if not rule.get("hostname")]
        if len(named) > 1 or any(rule.get("hostname") != definition.hostname or rule.get("path") for rule in named) or len(unnamed) > 1 or any(rule != {"service": "http_status:404"} for rule in unnamed) or (unnamed and ingress[-1] is not unnamed[0]):
            raise TunnelError("ingress_conflict", "O Tunnel contém publicações adicionais; nenhuma regra foi substituída.")
        desired = [{"hostname": definition.hostname, "service": local_url, "originRequest": {"caPool": str(ca_file), "noTLSVerify": False}}, {"service": "http_status:404"}]
        if expected_previous is not None and (not isinstance(expected_previous, dict) or expected_previous.get("config") != previous):
            raise TunnelError("ingress_conflict", "A configuração remota mudou após o diagnóstico; nenhuma regra foi substituída.")
        if ingress == desired:
            return
        if named and expected_previous is None:
            raise TunnelError("ingress_conflict", "O hostname possui uma origem não confirmada; use a migração explícita para adotá-la.")
        config = {"config": {**previous, "ingress": desired}}
        self.set_configuration(TunnelDefinition(definition.account_id, definition.zone_id, definition.hostname, tunnel_id, definition.tunnel_name), config, True)

    def inspect_dns(self, definition: TunnelDefinition, tunnel_id: str) -> dict[str, Any] | None:
        """Consulta o hostname sem escrita e rejeita qualquer registro incompatível."""
        definition.validate()
        target = f"{tunnel_id}.cfargotunnel.com"
        records = self._call("GET", f"/zones/{definition.zone_id}/dns_records?" + urllib.parse.urlencode({"name": definition.hostname, "per_page": 100})) or []
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise TunnelError("invalid_response", "Cloudflare não confirmou a lista de registros DNS.")
        if records:
            if len(records) != 1 or records[0].get("type") != "CNAME" or str(records[0].get("content", "")).lower().rstrip(".") != target.lower() or records[0].get("proxied") is not True:
                raise TunnelError("dns_conflict", "O hostname possui um registro DNS incompatível; nenhuma substituição foi feita.")
            return dict(records[0])
        return None

    def ensure_dns(self, definition: TunnelDefinition, tunnel_id: str, confirm: bool) -> dict[str, Any]:
        """Reutiliza o CNAME compatível ou cria um novo, sem sobrescrever registros."""
        self._confirmed(confirm)
        existing = self.inspect_dns(definition, tunnel_id)
        if existing is not None:
            return existing
        target = f"{tunnel_id}.cfargotunnel.com"
        result = self._call("POST", f"/zones/{definition.zone_id}/dns_records", {"type": "CNAME", "name": definition.hostname, "content": target, "proxied": True})
        if not isinstance(result, dict) or not result.get("id"):
            raise TunnelError("invalid_response", "Cloudflare não confirmou o registro DNS.")
        return result

    def delete_managed_dns(self, definition: TunnelDefinition, tunnel_id: str, record_id: str, confirm: bool) -> None:
        """Remove somente o registro cujo ID e destino ainda comprovem a propriedade."""
        self._confirmed(confirm)
        if not isinstance(record_id, str) or not record_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in record_id):
            raise TunnelError("invalid_dns_record", "Identificador do registro DNS inválido.")
        existing = self.inspect_dns(definition, tunnel_id)
        if existing is None:
            return
        if existing.get("id") != record_id:
            raise TunnelError("dns_conflict", "O registro DNS mudou de proprietário; nenhuma remoção foi feita.")
        self._call("DELETE", f"/zones/{definition.zone_id}/dns_records/{record_id}")

    def status(self, account_id: str, tunnel_id: str) -> dict[str, Any]:
        return dict(self._call("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}"))

    def tunnel_token(self, account_id: str, tunnel_id: str) -> str:
        value = self._call("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token")
        if not isinstance(value, str) or not value:
            raise TunnelError("invalid_response", "Cloudflare não retornou o token de execução do Tunnel.")
        return value
