"""Reconciliação de listeners autenticados pelos processos conhecidos do Daemon."""
from __future__ import annotations

import shutil
import socket
import ssl
import threading
import time
import tomllib
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ..rpc import RpcError
from ..gateway.exception import GatewayError
from ..gateway.repository import validate_prefix, validate_target
from ..cloudflare_tunnel.configuration import TunnelDefinition
from ..cloudflare_tunnel.exception import TunnelError


class PublicationCoordinator:
    """Associa declarações à identidade do Supervisor, nunca a um owner informado.

    RPCs ocupados conservam a declaração confirmada da mesma geração. O lock
    serializa sincronização e invalidação para impedir a reativação de destinos
    antigos depois de uma parada. Nenhum consumidor chama o Supervisor de volta.
    """

    def __init__(self, root: Path, services: dict, request: Callable[..., Any], administrative_ports: set[int] | None = None):
        self.root, self.services, self.request = root, services, request
        self.administrative_ports = set(administrative_ports or ())
        self.lock = threading.RLock()
        self.descriptions: dict[str, tuple[str, dict, float]] = {}
        self.confirmations: dict[str, tuple[str, str, float]] = {}
        self.activations: set[tuple[str, str, str]] = set()
        self.tunnel: dict[str, Any] = {}
        self.tunnel_generation = ""

    @staticmethod
    def alive(service: Any) -> bool:
        return bool(service and service.state == "running" and service.process and service.process.poll() is None)

    def invalidate(self, owner: str, generation: str) -> None:
        """Invalida uma geração antes de pará-la, preservando suas reservas."""
        with self.lock:
            if self.alive(self.services.get("gateway")) and owner != "gateway":
                try:
                    self.request("gateway", "gateway.publication.unavailable", {"owner": owner, "generation": generation}, timeout=1, skip_busy=True)
                except (OSError, RuntimeError, ValueError):
                    pass
            self.descriptions.pop(owner, None)
            self.confirmations.pop(owner, None)

    def _offline_tunnel(self) -> dict[str, Any]:
        path = self.root / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "tunnel.toml"
        try:
            settings = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        executable = str(settings.get("cloudflared_executable") or "cloudflared")
        return {"configured": bool(settings), "provisioned": bool(settings.get("tunnel_id")),
                "hostname": settings.get("hostname"), "running": False, "connected": False,
                "cloudflared_available": bool(shutil.which(executable) or Path(executable).is_file())}

    def validate_description(self, owner: str, description: dict) -> dict:
        """Restringe a declaração ao contrato HTTP autorizado no ServiceSpec.

        Lança ``invalid_publication`` para destino externo, porta administrativa,
        prefixo não autorizado ou CA fora da área TLS do próprio serviço.
        """
        spec = self.services[owner].spec
        if not isinstance(description, dict):
            raise RpcError("Declaração de publicação inválida.", "invalid_publication")
        listeners = description.get("listeners")
        if type(description.get("schema_version")) is not int or description["schema_version"] != 1 or not isinstance(listeners, list) or len(listeners) > 128:
            raise RpcError("Declaração de publicação inválida.", "invalid_publication")
        checked, prefixes = [], set()
        for item in listeners:
            if not isinstance(item, dict) or "owner" in item or "service_id" in item:
                raise RpcError("Identidade de publicação deve ser atribuída pelo Supervisor.", "invalid_publication")
            prefix = item.get("path_prefix")
            try:
                validate_prefix(prefix)
            except GatewayError as error:
                raise RpcError("Prefixo não autorizado para este serviço.", "invalid_publication") from error
            if prefix in prefixes or (spec.publication_prefixes and prefix not in spec.publication_prefixes) or type(item.get("ready")) is not bool:
                raise RpcError("Prefixo não autorizado para este serviço.", "invalid_publication")
            prefixes.add(prefix)
            try:
                target_url = validate_target(item.get("target"))
                target = urlsplit(target_url)
                administrative_ports = self.administrative_ports | {service.port for service in self.services.values() if service.port}
                valid = (target.scheme in {"http", "https"} and target.hostname in {"localhost", "127.0.0.1", "::1"}
                         and target.port == spec.publication_port and target.port not in administrative_ports
                         and not target.username and not target.password and target.path in {"", "/"}
                         and not target.query and not target.fragment)
            except (ValueError, GatewayError):
                valid = False
            if not valid:
                raise RpcError("Endpoint HTTP não autorizado para este serviço.", "invalid_publication")
            result = {"path_prefix": prefix, "target": target_url, "ready": item["ready"]}
            if target.scheme == "https":
                ca_file = Path(str(item.get("ca_file") or "")).resolve()
                expected_tls_root = self.root / "configs" / "daemon" / "services" / owner / "tls"
                tls_root = expected_tls_root.resolve()
                if tls_root != expected_tls_root or not ca_file.is_relative_to(tls_root) or ca_file.name != "ca.pem" or not ca_file.is_file():
                    raise RpcError("CA do listener não pertence ao serviço.", "invalid_publication")
                result["ca_file"] = str(ca_file)
            checked.append(result)
        return {"schema_version": 1, "listeners": checked}

    @staticmethod
    def _transport_ready(listener: dict) -> bool:
        if not listener["ready"]:
            return False
        target = urlsplit(listener["target"])
        try:
            with socket.create_connection((str(target.hostname), int(target.port)), timeout=0.5) as connection:
                if target.scheme == "https":
                    context = ssl.create_default_context(cafile=listener["ca_file"])
                    with context.wrap_socket(connection, server_hostname=target.hostname):
                        pass
            return True
        except (OSError, ValueError):
            return False

    def reconcile(self) -> None:
        """Atualiza publicações sem aguardar serviços ocupados com RPC de negócio."""
        gateway_service = self.services.get("gateway")
        tunnel_service = self.services.get("cloudflare-tunnel")
        tunnel_generation = tunnel_service.generation if tunnel_service else ""
        if tunnel_generation != self.tunnel_generation:
            self.tunnel = self._offline_tunnel()
            self.tunnel_generation = tunnel_generation
        if self.alive(tunnel_service):
            try:
                state = self.request("cloudflare-tunnel", "cloudflare-tunnel.status", {}, timeout=1, skip_busy=True)
                if isinstance(state, dict) and tunnel_generation == tunnel_service.generation and self.alive(tunnel_service):
                    self.tunnel = state
                elif tunnel_generation != tunnel_service.generation or not self.alive(tunnel_service):
                    self.tunnel = self._offline_tunnel()
            except (OSError, RuntimeError, ValueError):
                self.tunnel = {**self.tunnel, "running": False, "connected": None}
        else:
            self.tunnel = self._offline_tunnel()
        hostname = self.tunnel.get("hostname")
        if hostname:
            try:
                TunnelDefinition("known", "known", hostname).validate()
            except TunnelError:
                self.tunnel = {**self.tunnel, "hostname": None, "connected": False, "ingress_ready": False,
                               "error_code": "invalid_hostname", "last_error": "Hostname público inválido."}
                hostname = None
        origin = "https://" + hostname if hostname else None
        gateway: dict = {}
        gateway_generation = gateway_service.generation if gateway_service else ""
        if self.alive(gateway_service):
            try:
                status = self.request("gateway", "gateway.status", {}, timeout=1, skip_busy=True)
                if isinstance(status, dict) and gateway_generation == gateway_service.generation and self.alive(gateway_service):
                    gateway = status
            except (OSError, RuntimeError, ValueError):
                pass
        if gateway.get("ready") and not (isinstance(gateway.get("http_url"), str) and isinstance(gateway.get("ca_file"), str)):
            gateway = {}
        if gateway.get("ready"):
            activation = (tunnel_generation, gateway_generation, str(gateway.get("http_url")))
            if self.alive(tunnel_service) and activation not in self.activations:
                try:
                    activated = self.request("cloudflare-tunnel", "cloudflare-tunnel.activate", {"gateway_url": gateway["http_url"], "ca_file": gateway["ca_file"]}, timeout=1, skip_busy=True)
                    if activated is not None:
                        self.activations.add(activation)
                except (OSError, RuntimeError, ValueError):
                    pass
            try:
                self.request("gateway", "gateway.publication.context", {"public_origin": origin, "tunnel": self.tunnel}, timeout=1, skip_busy=True)
            except (OSError, RuntimeError, ValueError):
                pass
        for owner, service in self.services.items():
            if not service.spec.publishes_http:
                continue
            generation = service.generation
            if not self.alive(service):
                self.invalidate(owner, generation)
                continue
            cached = self.descriptions.get(owner)
            error_code = last_error = None
            if not cached or cached[0] != generation or time.monotonic() - cached[2] >= 5:
                try:
                    description = self.request(owner, service.spec.namespace + "publication.describe", {}, timeout=1, skip_busy=True)
                    if description is not None:
                        description = self.validate_description(owner, description)
                        with self.lock:
                            if generation == service.generation and self.alive(service):
                                cached = (generation, description, time.monotonic())
                                self.descriptions[owner] = cached
                except (OSError, RuntimeError, ValueError):
                    error_code, last_error = "publication_unconfirmed", "Não foi possível confirmar a declaração do serviço."
            listeners = []
            if gateway.get("ready") and cached and cached[0] == generation:
                healthy: dict[str, bool] = {}
                for item in cached[1]["listeners"]:
                    if item["target"] not in healthy:
                        healthy[item["target"]] = self._transport_ready(item)
                    listeners.append({**item, "ready": healthy[item["target"]] and item["ready"]})
                with self.lock:
                    if generation != service.generation or not self.alive(service) or gateway_generation != gateway_service.generation:
                        continue
                    try:
                        result = self.request("gateway", "gateway.publication.sync", {"owner": owner, "generation": generation, "listeners": listeners, "available": True}, timeout=1, skip_busy=True)
                        if isinstance(result, dict):
                            listeners = result.get("listeners", listeners)
                        elif isinstance(result, list):
                            listeners = result
                        if isinstance(result, (dict, list)):
                            self.confirmations[owner] = (generation, gateway_generation, time.time())
                    except (OSError, RuntimeError, ValueError) as error:
                        listeners = []
                        error_code = "prefix_conflict" if isinstance(error, RpcError) and error.code == "prefix_conflict" else "publication_unconfirmed"
                        last_error = "O Gateway recusou a declaração por conflito de prefixo." if error_code == "prefix_conflict" else "Não foi possível confirmar os listeners no Gateway."
            confirmation = self.confirmations.get(owner)
            confirmed_at = confirmation[2] if confirmation and confirmation[:2] == (generation, gateway_generation) else None
            if not gateway.get("ready"):
                error_code, last_error = "gateway_unavailable", "O Gateway HTTP está indisponível."
            snapshot = {"public_origin": origin, "gateway_ready": bool(gateway.get("ready")),
                        "public_ready": bool(gateway.get("ready") and self.tunnel.get("provisioned") and self.tunnel.get("ingress_ready") and self.tunnel.get("running") and self.tunnel.get("connected") is True),
                        "listeners": listeners, "tunnel": self.tunnel, "checked_at": time.time(),
                        "confirmed_at": confirmed_at, "error_code": error_code, "last_error": last_error}
            with self.lock:
                if generation == service.generation and self.alive(service):
                    try:
                        self.request(owner, service.spec.namespace + "publication.update", snapshot, timeout=1, skip_busy=True)
                    except (OSError, RuntimeError, ValueError):
                        pass
