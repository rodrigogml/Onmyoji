"""Serviço administrativo e proprietário único do conector cloudflared."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import threading
from typing import Any

from ..mcp.credentials import CredentialError, KeePassCredentialResolver
from .client import CloudflareClient, gateway_ingress
from .configuration import TunnelConfiguration, TunnelDefinition, validate_origin
from .exception import TunnelError
from .installer import CloudflaredInstaller
from .operations import TunnelOperations
from .process import TunnelProcess
from .recovery import PublicationRecovery


class TunnelService:
    """Fornece RPC mesmo sem configuração e inicia filhos somente após ativação.

    O Supervisor conclui a contenção do processo e fornece a origem HTTPS do
    Gateway antes de ``activate``. Uma morte inesperada do conector termina o
    wrapper com falha; a recuperação pertence ao Supervisor, sem outro watchdog.
    """

    def __init__(self, root: Path, data_dir: Path, credentials: KeePassCredentialResolver | None = None):
        self.root, self.data_dir = root.resolve(), data_dir.resolve()
        if not self.data_dir.is_relative_to(self.root / "configs"):
            raise TunnelError("invalid_data_directory", "O estado persistente do Tunnel deve permanecer em configs/.")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.configuration = TunnelConfiguration(self.root, self.data_dir, credentials)
        self.operations = TunnelOperations(self.root, self.data_dir)
        self.stopped, self.failed = threading.Event(), threading.Event()
        self.closing = threading.Event()
        self.state_lock, self.start_lock = threading.RLock(), threading.Lock()
        self.process: TunnelProcess | None = None
        self.running_identity: tuple[str, str, str] | None = None
        self.origin: dict[str, str] | None = None
        self.last_error: str | None = None
        self.error_code: str | None = None
        self.activated = False
        self.monitor = threading.Thread(target=self._monitor, daemon=True, name="onmyoji-tunnel-monitor")
        self.monitor.start()

    def _token(self, settings: dict[str, Any]) -> str:
        try:
            token = self.configuration.credentials.resolve(settings["credential_profile"], settings["credential_entry_path"], settings.get("credential_field", "password"))
        except (CredentialError, KeyError, TypeError) as error:
            raise TunnelError("credential_unavailable", "O cofre MCP não retornou o token do Tunnel.") from error
        if not isinstance(token, str) or not token:
            raise TunnelError("credential_missing", "O cofre MCP não retornou o token do Tunnel.")
        return token

    def _client(self, settings: dict[str, Any]) -> CloudflareClient:
        return CloudflareClient(self._token(settings), timeout=settings.get("timeout_seconds", 30))

    @staticmethod
    def _definition(settings: dict[str, Any]) -> TunnelDefinition:
        if not TunnelConfiguration.is_configured(settings):
            raise TunnelError("incomplete_config", "Configure o Tunnel antes de provisioná-lo.")
        definition = TunnelDefinition(*(str(settings.get(key, "")) for key in ("account_id", "zone_id", "hostname", "tunnel_id", "tunnel_name")))
        definition.validate()
        return definition

    @staticmethod
    def _ingress_ready(settings: dict[str, Any]) -> bool:
        return bool(TunnelConfiguration.is_configured(settings) and settings.get("tunnel_id") and settings.get("ca_file") and
                    settings.get("provisioned_gateway_url") == settings.get("gateway_url") and
                    settings.get("provisioned_ca_file") == settings.get("ca_file"))

    def status(self) -> dict[str, Any]:
        """Distingue processo local, conexão Cloudflare e origem provisionada."""
        try:
            settings = self.configuration.settings()
        except TunnelError as error:
            return {"configured": False, "provisioned": False, "running": False, "connected": False, "ingress_ready": False,
                    "error_code": error.code, "last_error": str(error), "activated": self.activated}
        executable = str(settings.get("cloudflared_executable", "cloudflared"))
        with self.state_lock:
            process = self.process
            last_error, error_code = self.last_error, self.error_code
        state = process.status() if process is not None else {"running": False, "connected": False, "pid": None, "last_error": last_error}
        runtime_matches = not state["running"] or self.running_identity == self._identity(settings)
        return {"configured": TunnelConfiguration.is_configured(settings), "provisioned": TunnelConfiguration.is_configured(settings) and bool(settings.get("tunnel_id")), "hostname": settings.get("hostname"),
                "tunnel_id": settings.get("tunnel_id"), "cloudflared_executable": executable,
                "cloudflared_available": bool(shutil.which(executable) or Path(executable).is_file()),
                "gateway_url": settings.get("gateway_url"), "ca_file": settings.get("ca_file"),
                "ingress_ready": self._ingress_ready(settings) and runtime_matches, "activated": self.activated,
                "managed_version": settings.get("managed_version"), "previous_executable": settings.get("previous_executable"),
                "error_code": error_code, **state}

    @staticmethod
    def _identity(settings: dict[str, Any]) -> tuple[str, str, str]:
        return tuple(str(settings.get(key, "")) for key in ("account_id", "tunnel_id", "cloudflared_executable"))

    def _start_connector(self) -> dict[str, Any]:
        with self.start_lock:
            settings = self.configuration.settings()
            if TunnelConfiguration.is_configured(settings) and self.origin and any(settings.get(key) != value for key, value in self.origin.items()):
                settings = self.configuration.configure(self.origin, validate_credentials=False)
            if self.closing.is_set() or not self.activated or not TunnelConfiguration.is_configured(settings) or not self._ingress_ready(settings):
                return self.status()
            validate_origin(settings["gateway_url"], settings["ca_file"])
            with self.state_lock:
                if self.process is not None and self.process.process is not None and self.process.process.poll() is None and self.running_identity == self._identity(settings):
                    return self.status()
            token = self._client(settings).tunnel_token(settings["account_id"], settings["tunnel_id"])
            if self.closing.is_set():
                return self.status()
            with self.state_lock:
                previous, self.process = self.process, None
            if previous is not None:
                previous.stop()
            process = TunnelProcess(str(settings["cloudflared_executable"]), token)
            try:
                process.start()
            except Exception:
                process.stop()
                raise
            with self.state_lock:
                if self.closing.is_set():
                    process.stop()
                else:
                    self.process = process
                    self.running_identity = self._identity(settings)
                    self.last_error, self.error_code = None, None
            return self.status()

    def _stop_connector(self) -> None:
        with self.start_lock:
            with self.state_lock:
                process, self.process = self.process, None
            if process is not None:
                process.stop()

    def _activate(self, params: dict[str, Any]) -> dict[str, Any]:
        gateway_url, ca_file = params.get("gateway_url"), params.get("ca_file")
        validate_origin(gateway_url, ca_file)
        self.origin = {"gateway_url": gateway_url, "ca_file": ca_file}
        self.activated = True
        if self.operations.busy:
            return self.status()
        settings = self.configuration.settings()
        if TunnelConfiguration.is_configured(settings):
            self.configuration.configure(self.origin, validate_credentials=False)
        if not TunnelConfiguration.is_configured(settings) or not self._ingress_ready({**settings, **self.origin}):
            return self.status()

        def activate(progress):
            progress("iniciando conector autorizado pelo Supervisor")
            try:
                return self._start_connector()
            except TunnelError as error:
                with self.state_lock:
                    self.last_error, self.error_code = str(error), error.code
                self.failed.set()
                self.stopped.set()
                raise

        if self.process is None or self.running_identity != self._identity(settings):
            return self.operations.start("activate", activate)
        return self.status()

    def _configure(self, values: dict[str, Any], progress) -> dict[str, Any]:
        progress("validando configuração e referência KeePass")
        if self.origin:
            values = {**values, **self.origin}
        self.configuration.configure(values)
        return self.status()

    def _provision(self, progress) -> dict[str, Any]:
        settings = self.configuration.settings()
        definition = self._definition(settings)
        if self.origin:
            settings = self.configuration.configure(self.origin, validate_credentials=False)
        validate_origin(settings["gateway_url"], settings.get("ca_file", ""))
        client = self._client(settings)
        if not definition.tunnel_id:
            client.preflight_access(definition, progress)
            progress("consultando o DNS antes de criar um novo Tunnel")
            client.inspect_dns(definition, "")
            progress("criando Tunnel remoto")
            created = client.create_tunnel(definition, True)
            settings = self.configuration.configure({"tunnel_id": str(created["id"])}, validate_credentials=False)
            definition = self._definition(settings)
        progress("verificando hostname e DNS")
        previous = None
        if settings.get("provisioned_gateway_url") and settings.get("provisioned_ca_file"):
            remote = client.get_configuration(definition)
            expected = gateway_ingress(remote.get("config", {}), settings["provisioned_gateway_url"], settings["provisioned_ca_file"])
            expected[0] = {"hostname": definition.hostname, **expected[0]}
            if isinstance(remote, dict) and isinstance(remote.get("config"), dict) and remote["config"].get("ingress") == expected:
                previous = remote
        client.ensure_dns(definition, definition.tunnel_id, True)
        progress("configurando origem única do Gateway")
        client.configure_ingress(definition, definition.tunnel_id, settings["gateway_url"], True, settings["ca_file"], expected_previous=previous)
        self.configuration.configure({"provisioned_gateway_url": settings["gateway_url"], "provisioned_ca_file": settings["ca_file"]}, validate_credentials=False)
        if self.activated and not self.closing.is_set():
            progress("iniciando conector provisionado")
            self._start_with_recovery()
        return self.status()

    def _inspect_publication(self, progress) -> dict[str, Any]:
        settings = self.configuration.settings()
        self._definition(settings)
        if self.origin and any(settings.get(key) != value for key, value in self.origin.items()):
            settings = self.configuration.configure(self.origin, validate_credentials=False)
        progress("obtendo o API Token administrativo na referência KeePass configurada")
        return PublicationRecovery(self.configuration, self._client(settings)).inspect(settings, progress)

    def _recover_publication(self, params: dict[str, Any], progress, rollback: bool = False) -> dict[str, Any]:
        settings = self.configuration.settings()
        self._definition(settings)
        if not rollback and self.origin and any(settings.get(key) != value for key, value in self.origin.items()):
            raise TunnelError("stale_preview", "A origem do Gateway mudou após a prévia; consulte novamente pelo wizard.")
        recovery = PublicationRecovery(self.configuration, self._client(settings))
        if rollback:
            result = recovery.rollback(params.get("preview_id"), settings, progress)
            self._stop_connector()
        else:
            result = recovery.apply(params.get("preview_id"), settings, progress)
        if self.activated and not self.closing.is_set() and self._ingress_ready(self.configuration.settings()):
            progress("iniciando o conector com a publicação confirmada")
            self._start_with_recovery()
        return {**result, "status": self.status()}

    def _install(self, params: dict[str, Any], progress, rollback: bool = False, use_existing: bool = False) -> dict[str, Any]:
        installer = CloudflaredInstaller(self.configuration)
        previous_executable = str(self.configuration.settings().get("cloudflared_executable") or "cloudflared")
        if use_existing:
            progress("validando e selecionando o executável cloudflared existente")
            result = installer.use_existing(True, params.get("executable"))
            try:
                unchanged = Path(previous_executable).is_absolute() and Path(previous_executable).resolve() == Path(result["executable"])
            except OSError:
                unchanged = False
            if unchanged:
                return result
        elif rollback:
            result = installer.rollback(True)
        else:
            result = installer.install(True, params.get("install_directory"), params.get("adopt") is True, progress, params.get("version"))
        if self.activated and not self.closing.is_set() and self._ingress_ready(self.configuration.settings()):
            progress("reiniciando conector com o executável selecionado")
            self._stop_connector()
            self._start_with_recovery()
        return result

    def _start_with_recovery(self) -> dict[str, Any]:
        try:
            return self._start_connector()
        except TunnelError as error:
            with self.state_lock:
                self.last_error, self.error_code = str(error), error.code
            self.failed.set()
            self.stopped.set()
            raise

    def rpc(self, method: str, params: dict[str, Any]) -> Any:
        """Executa o contrato administrativo; mutações externas exigem confirm=true."""
        if not isinstance(params, dict):
            raise TunnelError("invalid_arguments", "Os parâmetros devem ser um objeto JSON.")
        action = method.removeprefix("cloudflare-tunnel.")
        if action == "status":
            return self.status()
        if action == "activate":
            return self._activate(params)
        if action == "operations.status":
            return self.operations.status(str(params.get("operation_id", "")))
        if action == "inspect-publication":
            return self.operations.start(action, self._inspect_publication)
        if action not in {"configure", "provision", "adopt-publication", "rollback-publication", "install-cloudflared", "update-cloudflared", "rollback-cloudflared", "use-cloudflared"}:
            raise TunnelError("unknown_method", "Operação Cloudflare Tunnel não suportada.")
        if params.get("confirm") is not True:
            raise TunnelError("confirmation_required", "A operação administrativa exige confirm=true.")
        if action == "configure":
            values = {key: value for key, value in params.items() if key != "confirm"}
            return self.operations.start(action, lambda progress: self._configure(values, progress))
        if action == "provision":
            return self.operations.start(action, self._provision)
        if action in {"adopt-publication", "rollback-publication"}:
            return self.operations.start(action, lambda progress: self._recover_publication(params, progress, action == "rollback-publication"))
        return self.operations.start(action, lambda progress: self._install(params, progress, action == "rollback-cloudflared", action == "use-cloudflared"))

    def _monitor(self) -> None:
        parent = os.environ.get("ONMYOJI_SUPERVISOR_PID", "")
        expected_parent = int(parent) if parent.isdecimal() else None
        while not self.closing.wait(0.1):
            if os.name != "nt" and expected_parent is not None and os.getppid() != expected_parent:
                self.stopped.set()
                return
            with self.state_lock:
                process = self.process
                if process is not None and process.process is not None and process.process.poll() is not None:
                    self.last_error = "O conector cloudflared encerrou inesperadamente."
                    self.error_code = "connector_exited"
                    self.failed.set()
                    self.stopped.set()
                    return

    def close(self) -> None:
        """Encerra o conector e marca operações pendentes sem repetir suas escritas."""
        self.closing.set()
        self.stopped.set()
        self.operations.close()
        self._stop_connector()
