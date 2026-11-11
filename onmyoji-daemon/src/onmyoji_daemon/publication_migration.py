"""Migração explícita do Tunnel legado, com checkpoints duráveis para rollback.

A configuração original permanece intacta. O rollback restaura a infraestrutura
remota e exige reinstalar a versão anterior para reativar seu proprietário antigo.
"""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import time
import tomllib
from typing import Any, Callable
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlsplit

from .config_publication import publish_instance_file
from .rpc import call
from .supervisor import endpoint


class MigrationError(ValueError):
    """A migração não pode continuar sem corrigir o pré-requisito informado."""


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle: return ctypes.get_last_error() == 5
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally: kernel.CloseHandle(ctypes.c_void_p(handle))
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: return True
    return True


def _verify_forwarding(status: dict[str, Any]) -> None:
    listeners = {item.get("path_prefix"): item for item in status.get("listeners", [])}
    if not status.get("ready") or any(not listeners.get(key, {}).get("ready") or listeners[key].get("owner") != "mini-apps" for key in ("a", "oauth")):
        raise MigrationError("Gateway ainda não encaminha os listeners a e oauth do Mini Apps.")
    context = ssl.create_default_context(cafile=str(status["ca_file"]))
    request = urllib.request.Request(str(status["http_url"]).rstrip("/") + "/oauth/result/__onmyoji_migration_probe__")
    try:
        with urllib.request.urlopen(request, context=context, timeout=5) as response:
            body = response.read(4096)
            if response.status != 404 or b"Autoriz" not in body: raise MigrationError("Gateway retornou uma resposta inesperada ao validar encaminhamento.")
    except urllib.error.HTTPError as error:
        if error.code != 404 or b"Autoriz" not in error.read(4096): raise MigrationError("Gateway não confirmou o encaminhamento do callback OAuth.") from error
    except (OSError, ValueError) as error:
        raise MigrationError("Não foi possível validar HTTPS e encaminhamento do Gateway.") from error


def _verify_cloudflared(executable: str) -> str:
    """Confirma o executável legado antes de qualquer parada ou mudança remota.

    Lança ``MigrationError`` se o arquivo estiver ausente, sem permissão, não
    responder em dez segundos ou não se identificar como cloudflared.
    """
    resolved = shutil.which(executable)
    if not resolved:
        raise MigrationError("Executável cloudflared ausente ou sem permissão; corrija-o antes da migração.")
    try:
        checked = subprocess.run([resolved, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MigrationError("Não foi possível validar o executável cloudflared antes da migração.") from error
    version = re.search(r"\bcloudflared version (\d{4}\.\d+\.\d+)\b", checked.stdout or "")
    if checked.returncode != 0 or version is None:
        raise MigrationError("O executável configurado não confirmou uma versão válida do cloudflared.")
    return version[1]


class PublicationMigration:
    """Reutiliza o Tunnel existente sem duplicar cloudflared ou alterar URLs.

    ``apply`` e ``rollback`` exigem confirmação. Falhas deixam um manifesto para
    inspeção e rollback explícito; uma migração incompleta nunca é reaplicada
    automaticamente sobre um estado parcialmente alterado.
    """

    def __init__(self, root: Path, rpc: Callable[[str, dict[str, Any]], Any] | None = None,
                 client_factory: Callable[..., Any] | None = None,
                 probe: Callable[[dict[str, Any]], None] = _verify_forwarding,
                 process_alive: Callable[[int], bool] = _process_alive,
                 token_provider: Callable[[dict[str, Any]], str] | None = None,
                 readiness_timeout: float = 45,
                 progress: Callable[[str], None] | None = None,
                 executable_probe: Callable[[str], str] = _verify_cloudflared):
        self.root = root.resolve()
        self.source = self.root / "configs" / "daemon" / "services" / "mini-apps" / "tunnel.toml"
        self.destination = self.root / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "tunnel.toml"
        self.manifest = self.destination.parent / "migration.json"
        self.rpc = rpc or self._rpc
        self.client_factory, self.probe, self.process_alive = client_factory, probe, process_alive
        self.token_provider, self.readiness_timeout = token_provider, readiness_timeout
        self.progress = progress or (lambda _message: None)
        self.executable_probe = executable_probe

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        host, port, token = endpoint(self.root)
        return call(host, port, token, method, params, timeout=20)

    def _saved(self) -> dict[str, Any]:
        try:
            value = json.loads(self.manifest.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema_version") != 1:
                raise ValueError("unsupported migration manifest")
            return value
        except FileNotFoundError: return {}
        except (ValueError, OSError) as error: raise MigrationError("Manifesto de migração inválido; não foi alterado.") from error

    @contextmanager
    def _exclusive(self):
        """Impede dois cortes simultâneos; o lock transitório fica em tmp/."""
        directory = self.root / "tmp"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "publication-migration.lock"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try: owner = int(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error: raise MigrationError("Lock de migração existente; confirme que não há outro operador antes de removê-lo.") from error
            if owner <= 0 or _process_alive(owner):
                raise MigrationError("Outra migração ou rollback está em execução nesta instância.")
            path.unlink()
            try: descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as error: raise MigrationError("Outra migração assumiu a instância; tente novamente após sua conclusão.") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(str(os.getpid()))
        try: yield
        finally: path.unlink(missing_ok=True)

    def _checkpoint(self, data: dict[str, Any], state: str) -> None:
        data.update(state=state, updated_at=time.time())
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        temporary_root = self.root / "tmp" / "publication-migration"
        temporary_root.mkdir(parents=True, exist_ok=True)
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=temporary_root, suffix=".json", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.flush(); os.fsync(output.fileno())
        try: publish_instance_file(self.root, temporary, self.manifest)
        finally: temporary.unlink(missing_ok=True)
        self.progress("Migração: " + state)

    def inspect(self) -> dict[str, Any]:
        saved = self._saved()
        return {"legacy_configured": self.source.is_file(), "destination_configured": self.destination.is_file(),
                "state": saved.get("state", "not-started"), "manifest": str(self.manifest),
                "rollback_requires_previous_version": bool(saved.get("rollback_requires_previous_version"))}

    def _legacy(self, source_bytes: bytes | None = None) -> dict[str, Any]:
        try: values = tomllib.loads((source_bytes if source_bytes is not None else self.source.read_bytes()).decode("utf-8"))
        except (OSError, ValueError) as error: raise MigrationError("Configuração legada do Tunnel ausente ou inválida.") from error
        required = ("account_id", "zone_id", "hostname", "tunnel_id", "credential_profile", "credential_entry_path")
        if type(values.get("schema_version")) is not int or values["schema_version"] != 1 or any(not isinstance(values.get(key), str) or not values[key].strip() for key in required) or not isinstance(values.get("tunnel_name", ""), str):
            raise MigrationError("A migração exige um Tunnel legado já provisionado e uma referência KeePass completa.")
        if any(any(word in key.lower() for word in ("token", "password", "secret")) for key in values):
            raise MigrationError("Configuração legada contém um segredo direto; use referências KeePass antes de migrar.")
        values["hostname"] = values["hostname"].strip().lower()
        return values

    def _client(self, values: dict[str, Any]):
        from .cloudflare_tunnel import CloudflareClient, TunnelDefinition
        from .mcp.credentials import KeePassCredentialResolver
        definition = TunnelDefinition(values["account_id"], values["zone_id"], values["hostname"], values["tunnel_id"], values.get("tunnel_name", "onmyoji-mini-apps"))
        definition.validate()
        token = self.token_provider(values) if self.token_provider else KeePassCredentialResolver(self.root).resolve(values["credential_profile"], values["credential_entry_path"], values.get("credential_field", "password"))
        return (self.client_factory or CloudflareClient)(token), definition

    def _wait(self, method: str, predicate: Callable[[dict[str, Any]], bool], message: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.readiness_timeout
        next_notice = time.monotonic() + 5
        while True:
            try:
                value = self.rpc(method, {})
                if isinstance(value, dict) and predicate(value): return value
            except (OSError, RuntimeError):
                pass
            if time.monotonic() >= deadline: raise MigrationError(message)
            if time.monotonic() >= next_notice:
                self.progress("Aguardando confirmação de prontidão da publicação...")
                next_notice = time.monotonic() + 5
            time.sleep(0.2)

    @staticmethod
    def _safe_remote(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if any(word in key.lower() for word in ("token", "password", "secret", "authorization")):
                    raise MigrationError("Ingress remoto contém dados sensíveis e não pode integrar o manifesto de rollback.")
                PublicationMigration._safe_remote(item)
        elif isinstance(value, list):
            for item in value: PublicationMigration._safe_remote(item)
        elif isinstance(value, str) and "://" in value:
            try:
                parsed = urlsplit(value)
                sensitive_query = any(any(word in key.lower() for word in ("token", "password", "secret", "code", "state")) for key, _item in parse_qsl(parsed.query))
            except ValueError as error: raise MigrationError("Ingress remoto contém uma URL inválida.") from error
            if parsed.username or parsed.password or sensitive_query:
                raise MigrationError("Ingress remoto contém credenciais em URL e não pode integrar o manifesto.")

    def apply(self, confirm: bool = False) -> dict[str, Any]:
        if not confirm: raise MigrationError("A migração exige confirmação explícita.")
        with self._exclusive():
            try: return self._apply()
            except MigrationError: raise
            except Exception as error: raise MigrationError("Não foi possível validar a migração; confira conectividade, credenciais e o manifesto antes de tentar novamente.") from error

    def _apply(self) -> dict[str, Any]:
        saved = self._saved()
        if saved.get("state") == "completed": return self.inspect()
        if saved and saved.get("state") != "rolled-back": raise MigrationError("Há uma migração anterior; consulte o manifesto e execute rollback antes de tentar novamente.")
        source_bytes = self.source.read_bytes()
        values = self._legacy(source_bytes)
        if self.destination.exists():
            try: existing = tomllib.loads(self.destination.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error: raise MigrationError("Configuração de destino inválida; não foi sobrescrita.") from error
            self._safe_remote(existing)
            if any(existing.get(key) != value for key, value in values.items()):
                raise MigrationError("Configuração independente conflita com a legada; nenhum arquivo foi sobrescrito.")
        services = self.rpc("list-services", {})
        if isinstance(services, dict): services = services.get("services", [])
        if not isinstance(services, list) or not {"gateway", "cloudflare-tunnel", "mini-apps"}.issubset({item.get("name") for item in services if isinstance(item, dict)}):
            raise MigrationError("Atualize e reinicie o Daemon para registrar Gateway e Cloudflare Tunnel antes de migrar.")
        previous = {item["name"]: {"enabled": bool(item.get("enabled")), "running": item.get("state") == "running"} for item in services if isinstance(item, dict) and item.get("name") in {"gateway", "cloudflare-tunnel", "mini-apps"}}
        if previous.get("cloudflare-tunnel", {}).get("running"):
            raise MigrationError("Pare o serviço Cloudflare Tunnel independente antes de migrar.")
        old_pid = None
        if previous.get("mini-apps", {}).get("running"):
            diagnosis = self.rpc("mini-apps.diagnose", {})
            old_pid = (diagnosis.get("tunnel") or {}).get("pid")
        client, definition = self._client(values)
        remote = client.get_configuration(definition)
        remote = {"config": remote.get("config", remote)}
        self._safe_remote(remote)
        ingress = remote["config"].get("ingress", [])
        named = [rule for rule in ingress if isinstance(rule, dict) and rule.get("hostname")]
        if len(named) != 1 or named[0].get("hostname") != definition.hostname or named[0].get("path") or len(ingress) != 2 or ingress[-1].get("service") != "http_status:404":
            raise MigrationError("O Tunnel remoto possui regras incompatíveis ou compartilhadas; nenhuma migração foi iniciada.")
        dns_before = client.inspect_dns(definition, definition.tunnel_id)
        self._safe_remote(dns_before)
        self.progress("Validando o executável cloudflared antes do corte...")
        executable_version = self.executable_probe(str(values.get("cloudflared_executable") or "cloudflared"))
        if self.source.read_bytes() != source_bytes:
            raise MigrationError("A configuração legada mudou durante o diagnóstico; execute novamente a migração.")
        previous_manifest = None
        if saved:
            from .cloudflare_tunnel.configuration import write_local_json
            history = self.destination.parent / "migration-history" / f"{time.time_ns()}.json"
            write_local_json(self.root, history, saved)
            previous_manifest = str(history)
        data = {"schema_version": 1, "source_sha256": sha256(source_bytes).hexdigest(), "remote_configuration": remote,
                "validated_cloudflared_version": executable_version,
                "previous_services": previous, "destination_preexisting": self.destination.exists(), "remote_switch_attempted": False,
                "destination_config": self.destination.read_text(encoding="utf-8") if self.destination.exists() else None,
                "dns_before": dns_before, "dns_created": None, "dns_creation_attempted": False, "previous_manifest": previous_manifest}
        self._checkpoint(data, "prepared")
        try:
            self.rpc("stop", {"service": "mini-apps"})
            if old_pid and self.process_alive(int(old_pid)):
                raise MigrationError("O cloudflared legado ainda está ativo; a migração não iniciou outro processo.")
            self._checkpoint(data, "legacy-stopped")
            from .cloudflare_tunnel import TunnelConfiguration
            TunnelConfiguration(self.root).configure(values, validate_credentials=False)
            self.rpc("start", {"service": "mini-apps"})
            self.rpc("start", {"service": "gateway"})
            def gateway_ready(status: dict[str, Any]) -> bool:
                listeners = {item.get("path_prefix"): item for item in status.get("listeners", [])}
                return bool(status.get("ready") and all(listeners.get(key, {}).get("ready") and listeners[key].get("enabled") and listeners[key].get("owner") == "mini-apps" for key in ("a", "oauth")))
            gateway = self._wait("gateway.status", gateway_ready, "Gateway não confirmou os listeners a e oauth; execute rollback explícito.")
            self.probe(gateway)
            if self.source.read_bytes() != source_bytes:
                raise MigrationError("A configuração legada mudou antes do corte; restaure-a e execute rollback explícito.")
            if not dns_before:
                data["dns_creation_attempted"] = True
                self._checkpoint(data, "creating-dns")
                data["dns_created"] = client.ensure_dns(definition, definition.tunnel_id, True)
                self._checkpoint(data, "dns-created")
            data["remote_switch_attempted"] = True
            expected = json.loads(json.dumps(remote))
            expected["config"]["ingress"] = [{"hostname": definition.hostname, "service": gateway["http_url"], "originRequest": {"caPool": gateway["ca_file"], "noTLSVerify": False}}, {"service": "http_status:404"}]
            data["expected_remote_configuration"] = expected
            self._checkpoint(data, "switching-ingress")
            client.configure_ingress(definition, definition.tunnel_id, gateway["http_url"], True, gateway["ca_file"], expected_previous=remote)
            applied = client.get_configuration(definition)
            applied = {"config": applied.get("config", applied)}
            self._safe_remote(applied)
            if applied != expected:
                raise MigrationError("O ingress remoto mudou durante o corte; confira o manifesto antes do rollback.")
            data["applied_remote_configuration"] = applied
            TunnelConfiguration(self.root).configure({"gateway_url": gateway["http_url"], "ca_file": gateway["ca_file"], "provisioned_gateway_url": gateway["http_url"], "provisioned_ca_file": gateway["ca_file"]}, validate_credentials=False)
            self._checkpoint(data, "ingress-switched")
            for service in ("gateway", "cloudflare-tunnel"):
                self.rpc("enable", {"service": service})
            started = self.rpc("start", {"service": "cloudflare-tunnel"})
            if not isinstance(started, dict) or started.get("state") != "running":
                raise MigrationError("O serviço independente não confirmou início; execute rollback explícito.")
            self._wait("cloudflare-tunnel.status", lambda value: value.get("running") is True and value.get("ingress_ready") is True and value.get("connected") is True, "Conexão independente ainda não foi confirmada; consulte o manifesto e execute rollback se necessário.")
            self.probe(self.rpc("gateway.status", {}))
            if not client.inspect_dns(definition, definition.tunnel_id):
                raise MigrationError("O DNS público não foi confirmado após o corte; consulte o manifesto antes do rollback.")
            self._checkpoint(data, "completed")
            return self.inspect()
        except Exception as error:
            data["failed_stage"] = data.get("state")
            self._checkpoint(data, "failed")
            if isinstance(error, MigrationError): raise
            raise MigrationError("Migração interrompida; consulte o manifesto e execute rollback explícito.") from error

    def rollback(self, confirm: bool = False) -> dict[str, Any]:
        if not confirm: raise MigrationError("O rollback exige confirmação explícita.")
        with self._exclusive():
            try: return self._rollback()
            except MigrationError: raise
            except Exception as error: raise MigrationError("Rollback não concluído; preserve o manifesto e confira a infraestrutura antes de tentar novamente.") from error

    def _rollback(self) -> dict[str, Any]:
        data = self._saved()
        if not data: raise MigrationError("Não há manifesto de migração para restaurar.")
        if data.get("state") == "rolled-back": return self.inspect()
        source_bytes = self.source.read_bytes()
        values = self._legacy(source_bytes)
        if sha256(source_bytes).hexdigest() != data.get("source_sha256"):
            raise MigrationError("A configuração legada mudou desde a migração; restaure-a antes do rollback remoto.")
        client = definition = None
        restore_remote = False
        if data.get("dns_creation_attempted") and not data.get("dns_created") and not data.get("dns_before"):
            client, definition = self._client(values)
            if client.inspect_dns(definition, definition.tunnel_id):
                raise MigrationError("A criação DNS não retornou um ID confirmado; verifique sua propriedade antes de remover o registro ou repetir o rollback.")
        if data.get("remote_switch_attempted"):
            if client is None: client, definition = self._client(values)
            current = client.get_configuration(definition)
            current = {"config": current.get("config", current)}
            if current == data["remote_configuration"]:
                restore_remote = False
            elif current == data.get("applied_remote_configuration", data.get("expected_remote_configuration")):
                restore_remote = True
            else:
                raise MigrationError("O ingress remoto foi alterado fora desta migração; nenhuma restauração foi aplicada.")
        self._checkpoint(data, "rolling-back")
        self.rpc("stop", {"service": "cloudflare-tunnel"})
        self.rpc("stop", {"service": "gateway"})
        if restore_remote:
            client.set_configuration(definition, data["remote_configuration"], True)
        if data.get("dns_created"):
            if client is None: client, definition = self._client(values)
            client.delete_managed_dns(definition, definition.tunnel_id, str(data["dns_created"]["id"]), True)
        from .cloudflare_tunnel.configuration import atomic_text
        if data.get("destination_preexisting"):
            atomic_text(self.root, self.destination, str(data["destination_config"]))
        else:
            self.destination.unlink(missing_ok=True)
        for service, state in data.get("previous_services", {}).items():
            self.rpc("enable" if state["enabled"] else "disable", {"service": service})
        data["rollback_requires_previous_version"] = True
        data["operator_instruction"] = "Reinstale a versão anterior e reinicie o daemon para reativar o controlador legado; o rollback da infraestrutura não iniciou o proprietário antigo."
        self._checkpoint(data, "rolled-back")
        return {**self.inspect(), "instruction": data["operator_instruction"]}
