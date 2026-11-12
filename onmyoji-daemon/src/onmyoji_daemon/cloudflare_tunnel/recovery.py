"""Descoberta, adoção confirmada e rollback de uma publicação remota existente."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from .client import CloudflareClient, gateway_ingress, validated_ingress
from .configuration import TunnelConfiguration, TunnelDefinition, validate_origin, write_local_json
from .exception import TunnelError


def _safe_snapshot(value: Any) -> None:
    """Recusa segredos em configuração remota antes de exibir ou persistir backup."""
    if isinstance(value, dict):
        for key, item in value.items():
            if any(part in str(key).lower() for part in ("token", "password", "secret", "authorization")):
                raise TunnelError("sensitive_remote_config", "A configuração remota contém campos sensíveis e não pode integrar o backup do setup. Revise o Tunnel na Cloudflare antes de adotá-lo.")
            _safe_snapshot(item)
    elif isinstance(value, list):
        for item in value:
            _safe_snapshot(item)
    elif isinstance(value, str) and "://" in value:
        try:
            parsed = urlsplit(value)
            unsafe = parsed.username is not None or parsed.password is not None or bool(parsed.query or parsed.fragment)
        except ValueError:
            unsafe = True
        if unsafe:
            raise TunnelError("sensitive_remote_config", "Uma URL remota contém credenciais ou parâmetros; revise-a na Cloudflare antes de criar o backup da publicação.")


# Valores padrão documentados do cloudflared podem ser materializados na leitura
# posterior ao PUT. Não normalize CA, host, origem nem opções não reconhecidas.
_ORIGIN_DEFAULTS = {
    "noTLSVerify": (False,),
    "http2Origin": (False,),
    "matchSNItoHost": (False,),
    "disableChunkedEncoding": (False,),
    "noHappyEyeballs": (False,),
    "originServerName": ("",),
    "httpHostHeader": ("",),
    "proxyType": ("",),
    "connectTimeout": (30, "30s"),
    "tlsTimeout": (10, "10s"),
    "keepAliveTimeout": (90, "90s", "1m30s"),
    "keepAliveConnections": (100,),
    "tcpKeepAlive": (30, "30s"),
}


def _normalized_config(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Normaliza apenas padrões de origem que não alteram a semântica."""
    value = deepcopy(snapshot)
    config = value.get("config")
    if not isinstance(config, dict):
        return value

    def clean_origin(item: dict[str, Any]) -> None:
        origin = item.get("originRequest")
        if not isinstance(origin, dict):
            return
        for key, defaults in _ORIGIN_DEFAULTS.items():
            current = origin.get(key)
            if key in origin and any(type(current) is type(default) and current == default for default in defaults):
                origin.pop(key)
        if not origin:
            item.pop("originRequest", None)

    clean_origin(config)
    for rule in config.get("ingress", []):
        if isinstance(rule, dict):
            clean_origin(rule)
    return value


def _effective_tls_safe(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """A falta do false explícito nunca pode herdar um true global."""
    original = expected.get("config", {})
    observed = actual.get("config", {})
    if not isinstance(original, dict) or not isinstance(observed, dict):
        return False
    expected_ingress = original.get("ingress", [])
    actual_ingress = observed.get("ingress", [])
    if not isinstance(expected_ingress, list) or not isinstance(actual_ingress, list):
        return False
    if len(expected_ingress) != len(actual_ingress):
        return False
    global_origin = observed.get("originRequest")
    globally_disabled = isinstance(global_origin, dict) and global_origin.get("noTLSVerify") is True
    for expected_rule, actual_rule in zip(expected_ingress, actual_ingress):
        if not isinstance(expected_rule, dict) or not isinstance(actual_rule, dict):
            return False
        expected_origin = expected_rule.get("originRequest")
        actual_origin = actual_rule.get("originRequest")
        if isinstance(expected_origin, dict) and expected_origin.get("noTLSVerify") is False:
            # A API pode omitir o false default, exceto se isso deixar um true
            # global efetivo sobre a rota.
            if globally_disabled and not (isinstance(actual_origin, dict) and actual_origin.get("noTLSVerify") is False):
                return False
    return True


def _remote_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return _effective_tls_safe(expected, actual) and _normalized_config(expected) == _normalized_config(actual)


def _safe_difference_paths(expected: dict[str, Any], actual: dict[str, Any]) -> str:
    """Exibe apenas caminhos de campos divergentes, nunca dados da Cloudflare."""
    missing = object()
    left, right = _normalized_config(expected), _normalized_config(actual)
    paths: list[str] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if len(paths) >= 6:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                label = str(key) if isinstance(key, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", key) else "campo"
                walk(a.get(key, missing), b.get(key, missing), f"{path}.{label}" if path else label)
                if len(paths) >= 6:
                    return
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                paths.append(path + ".quantidade")
            for index, (ai, bi) in enumerate(zip(a, b)):
                walk(ai, bi, f"{path}[{index}]")
                if len(paths) >= 6:
                    return
        elif type(a) is not type(b) or a != b:
            paths.append(path)

    walk(left, right, "")
    if not _effective_tls_safe(expected, actual) and "config.ingress[0].originRequest.noTLSVerify" not in paths:
        paths.append("config.ingress[0].originRequest.noTLSVerify")
    return ", ".join(paths[:6]) if paths else "config.ingress[0].originRequest.noTLSVerify"


def _dns_snapshot(record: dict[str, Any] | None) -> dict[str, Any] | None:
    return {key: record.get(key) for key in ("id", "type", "name", "content", "proxied")} if record is not None else None


class PublicationRecovery:
    """Mantém snapshots locais sem tokens e exige nova comparação antes de escrever.

    O snapshot é uma autorização para um hostname e uma origem específica, válida
    por quinze minutos. Publicações compartilhadas ou alterações concorrentes
    exigem intervenção do operador, sem sobrescrita automática.
    """

    def __init__(self, configuration: TunnelConfiguration, client: CloudflareClient):
        self.configuration, self.client = configuration, client
        self.directory = configuration.path.parent / "publication-recovery"

    @staticmethod
    def _definition(settings: dict[str, Any]) -> TunnelDefinition:
        return TunnelDefinition(*(str(settings.get(key, "")) for key in ("account_id", "zone_id", "hostname", "tunnel_id", "tunnel_name")))

    def _save(self, identifier: str, data: dict[str, Any]) -> None:
        _safe_snapshot(data)
        write_local_json(self.configuration.root, self.directory / f"{identifier}.json", data)

    def _read(self, identifier: Any) -> dict[str, Any]:
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise TunnelError("invalid_preview", "Identificador de prévia inválido; consulte novamente a publicação no wizard.")
        try:
            data = json.loads((self.directory / f"{identifier}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise TunnelError("invalid_preview", "A prévia da publicação não está disponível; consulte novamente pelo wizard.") from error
        if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("preview_id") != identifier:
            raise TunnelError("invalid_preview", "Backup da publicação inválido; nenhuma alteração foi feita.")
        _safe_snapshot(data)
        return data

    @staticmethod
    def _remote_config(remote: Any, hostname: str) -> dict[str, Any]:
        config = remote.get("config") if isinstance(remote, dict) else None
        ingress = validated_ingress(config)
        named = [rule for rule in ingress if rule.get("hostname")]
        unnamed = [rule for rule in ingress if not rule.get("hostname")]
        if len(named) > 1 or any(rule.get("hostname") != hostname or rule.get("path") for rule in named) or len(unnamed) > 1 or any(rule != {"service": "http_status:404"} for rule in unnamed) or (unnamed and ingress[-1] is not unnamed[0]):
            raise TunnelError("ingress_conflict", "O Tunnel contém outras publicações ou filtros de caminho. O setup preservou todas as regras; use um Tunnel exclusivo para este hostname ou ajuste as regras na Cloudflare antes de reutilizá-lo.")
        value = {"config": deepcopy(config)}
        _safe_snapshot(value)
        return value

    def inspect(self, settings: dict[str, Any], progress) -> dict[str, Any]:
        definition = self._definition(settings)
        definition.validate()
        validate_origin(settings["gateway_url"], settings.get("ca_file", ""))
        records, matches, token_status = self.client.preflight_access(definition, progress)
        progress("consultando DNS e procurando publicação existente na conta Cloudflare")
        found, dns = self.client.discover_tunnel(definition, records=records, matches=matches)
        if found is None:
            return {"status": "new", "hostname": definition.hostname, "new_origin": settings["gateway_url"],
                    "token_verification": token_status,
                    "message": "Nenhum DNS ou Tunnel com este nome foi encontrado; o provisionamento criará uma nova publicação após confirmação."}
        progress("validando a configuração remota do Tunnel encontrado")
        remote = self._remote_config(self.client.get_configuration(found), found.hostname)
        progress("validando acesso ao token de execução; o API Token permanece somente em memória")
        self.client.tunnel_token(found.account_id, found.tunnel_id)
        identifier = uuid4().hex
        desired = deepcopy(remote)
        desired["config"]["ingress"] = gateway_ingress(remote["config"], settings["gateway_url"], settings["ca_file"])
        desired["config"]["ingress"][0] = {"hostname": found.hostname, **desired["config"]["ingress"][0]}
        current = next((rule.get("service") for rule in remote["config"].get("ingress", []) if rule.get("hostname")), None)
        data = {"schema_version": 1, "preview_id": identifier, "created_at": time.time(), "state": "prepared",
                "settings_before": deepcopy(settings), "tunnel_id": found.tunnel_id, "tunnel_name": found.tunnel_name,
                "remote_before": remote, "remote_expected": desired, "dns_before": _dns_snapshot(dns),
                "ca_sha256": sha256(Path(settings["ca_file"]).read_bytes()).hexdigest()}
        self._save(identifier, data)
        return {"status": "existing", "preview_id": identifier, "hostname": found.hostname, "tunnel_id": found.tunnel_id,
                "token_verification": token_status,
                "tunnel_name": found.tunnel_name, "configured_tunnel_id": definition.tunnel_id,
                "current_origin": current, "new_origin": settings["gateway_url"],
                "dns_action": "reuse" if dns else "create", "configuration_changes": remote != desired,
                "message": "Publicação existente encontrada. O setup pode reutilizar o Tunnel e aplicar a origem e a CA deste Gateway, sem excluir o Tunnel. A leitura e o token de execução foram validados; permissões de escrita serão confirmadas pela API ao aplicar."}

    def _confirm_remote(self, definition: TunnelDefinition, expected: dict[str, Any], progress, *, restoring: bool = False) -> None:
        """Confirma HTTPS, CA, ingress único e configuração preservada após PUT.

        Tolera padrões documentados e leituras brevemente antigas sem aceitar
        alterações de origem/TLS ou regras adicionais.
        """
        last = None
        for attempt in range(4):
            if attempt:
                progress("aguardando atualização da configuração remota do Tunnel")
                time.sleep(0.4 * attempt)
            last = self._remote_config(self.client.get_configuration(definition), definition.hostname)
            if _remote_matches(expected, last):
                return
        mismatch = _safe_difference_paths(expected, last)
        action = "restauração" if restoring else "origem HTTPS e CA"
        raise TunnelError(
            "recovery_verification_failed",
            f"A Cloudflare aceitou a escrita, mas a leitura não confirmou {action}. "
            f"Campos divergentes (sem valores): {mismatch}. "
            "O backup foi preservado e a confirmação local não foi gravada; não repita a escrita sem revisar a publicação.",
        )

    def apply(self, identifier: str, settings: dict[str, Any], progress) -> dict[str, Any]:
        data = self._read(identifier)
        if data.get("state") != "prepared" or time.time() - data.get("created_at", 0) > 900:
            raise TunnelError("stale_preview", "A prévia expirou ou já foi usada; consulte novamente a publicação e confirme a nova prévia.")
        if settings != data["settings_before"]:
            raise TunnelError("stale_preview", "A configuração local mudou após a prévia; consulte e confirme novamente antes de aplicar.")
        validate_origin(settings["gateway_url"], settings.get("ca_file", ""))
        if sha256(Path(settings["ca_file"]).read_bytes()).hexdigest() != data["ca_sha256"]:
            raise TunnelError("stale_preview", "A CA do Gateway mudou após a prévia; consulte novamente antes de substituir a origem.")
        definition = self._definition({**settings, "tunnel_id": data["tunnel_id"], "tunnel_name": data["tunnel_name"]})
        progress("revalidando DNS, Tunnel e origem remota contra a prévia confirmada")
        found, dns = self.client.discover_tunnel(definition)
        if found != definition or _dns_snapshot(dns) != data["dns_before"] or self._remote_config(self.client.get_configuration(definition), definition.hostname) != data["remote_before"]:
            raise TunnelError("stale_preview", "A publicação Cloudflare mudou após a prévia; nenhuma regra foi substituída. Consulte e confirme novamente.")
        data["state"] = "applying"
        self._save(identifier, data)
        try:
            if dns is None:
                progress("criando o CNAME ausente para o Tunnel reutilizado")
                # A prévia autorizou somente um DNS ausente. POST recusa um
                # registro criado por outro operador entre consulta e escrita.
                created = self.client._create_dns_record(definition, definition.tunnel_id)
                data["dns_created"] = _dns_snapshot(created)
                self._save(identifier, data)
            progress("substituindo a origem e a CA da publicação confirmada")
            data["remote_write_attempted"] = True
            self._save(identifier, data)
            self.client.configure_ingress(definition, definition.tunnel_id, settings["gateway_url"], True, settings["ca_file"], expected_previous=data["remote_before"])
            self._confirm_remote(definition, data["remote_expected"], progress)
            progress("confirmando o CNAME após a atualização da origem")
            if _dns_snapshot(self.client.inspect_dns(definition, definition.tunnel_id)) != data.get("dns_created", data["dns_before"]):
                raise TunnelError("recovery_verification_failed", "O DNS mudou durante a atualização da origem. O backup foi preservado e o estado local não foi confirmado; revise a publicação Cloudflare.")
            progress("gravando o ID reutilizado e a confirmação da nova origem")
            updates = {"tunnel_id": definition.tunnel_id, "tunnel_name": definition.tunnel_name,
                       "provisioned_gateway_url": settings["gateway_url"], "provisioned_ca_file": settings["ca_file"]}
            # Persiste o estado local esperado antes da troca do TOML; um erro
            # de disco após a troca ainda permite reconhecer e reverter ambos.
            data["settings_after"] = {**settings, **updates}
            self._save(identifier, data)
            saved = self.configuration.configure(updates, validate_credentials=False)
            data.update(state="applied", settings_after=saved)
            self._save(identifier, data)
            return {"preview_id": identifier, "hostname": definition.hostname, "tunnel_id": definition.tunnel_id,
                    "message": "Tunnel existente reutilizado; origem HTTPS e CA confirmadas pela Cloudflare. Backup disponível para restauração explícita."}
        except Exception:
            data["state"] = "failed"
            self._save(identifier, data)
            raise

    def rollback(self, identifier: str, settings: dict[str, Any], progress) -> dict[str, Any]:
        data = self._read(identifier)
        if data.get("state") not in {"applied", "failed", "applying", "rolling_back"}:
            raise TunnelError("invalid_preview", "Este backup não corresponde a uma substituição aplicada; nenhuma restauração foi feita.")
        allowed_settings = [data.get("settings_after", data["settings_before"])]
        if data["state"] != "applied":
            allowed_settings.append(data["settings_before"])
        if settings not in allowed_settings:
            raise TunnelError("stale_preview", "A configuração local mudou desde a substituição; confira o backup antes de restaurar.")
        definition = self._definition({**settings, "tunnel_id": data["tunnel_id"], "tunnel_name": data["tunnel_name"]})
        progress("comparando a publicação atual com o backup antes de restaurar")
        remote = self._remote_config(self.client.get_configuration(definition), definition.hostname)
        expected_dns = data.get("dns_created", data["dns_before"])
        dns = self.client.inspect_dns(definition, definition.tunnel_id)
        before_matches = _remote_matches(data["remote_before"], remote)
        after_matches = _remote_matches(data["remote_expected"], remote)
        dns_already_removed = data["state"] == "rolling_back" and data.get("dns_created") and dns is None and before_matches
        if (_dns_snapshot(dns) != expected_dns and not dns_already_removed) or not (before_matches or after_matches):
            raise TunnelError("stale_preview", "A publicação foi alterada fora desta operação; nenhuma restauração automática foi aplicada.")
        data["state"] = "rolling_back"
        self._save(identifier, data)
        if not before_matches:
            progress("restaurando a configuração remota anterior")
            self.client.set_configuration(definition, data["remote_before"], True)
            self._confirm_remote(definition, data["remote_before"], progress, restoring=True)
        if data.get("dns_created") and not dns_already_removed:
            progress("removendo somente o CNAME criado por esta substituição")
            self.client.delete_managed_dns(definition, definition.tunnel_id, data["dns_created"]["id"], True)
        if data.get("dns_created"):
            data.pop("dns_created")
            self._save(identifier, data)
        progress("restaurando os identificadores e a configuração local anterior")
        with self.configuration.lock:
            self.configuration._persist(data["settings_before"])
        data["state"] = "rolled_back"
        self._save(identifier, data)
        return {"preview_id": identifier, "hostname": definition.hostname,
                "message": "Configuração anterior restaurada. O backup permanece disponível; a publicação antiga depende de sua origem anterior estar em execução."}
