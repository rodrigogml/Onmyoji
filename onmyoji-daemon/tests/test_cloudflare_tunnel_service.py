"""Ciclo de vida independente, ativação e recuperação do serviço Tunnel."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from onmyoji_daemon.cloudflare_tunnel import TunnelError, TunnelService
from onmyoji_daemon.mcp.credentials import CredentialError
from onmyoji_daemon.rpc import call


def wait_operation(service, operation):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = service.rpc("cloudflare-tunnel.operations.status", {"operation_id": operation["operation_id"]})
        if status["state"] not in {"queued", "running"}:
            return status
        time.sleep(0.01)
    raise AssertionError("A operação do serviço não terminou.")


class FakeProcess:
    instances = []

    def __init__(self, executable, token):
        self.executable, self.token = executable, token
        self.returncode = None
        self.process = None
        self.connected = True
        self.stops = 0
        self.instances.append(self)

    def start(self):
        self.process = SimpleNamespace(poll=lambda: self.returncode)

    def stop(self):
        self.stops += 1
        self.returncode = 0

    def status(self):
        running = self.process is not None and self.returncode is None
        return {"running": running, "connected": running and self.connected, "pid": 456 if running else None, "last_error": None}


@pytest.fixture
def service(tmp_path, monkeypatch):
    resolver = Mock()
    resolver.password_exists.return_value = True
    resolver.resolve.return_value = "api-private"
    result = TunnelService(tmp_path, tmp_path / "configs" / "daemon" / "services" / "cloudflare-tunnel", resolver)
    ca = tmp_path / "configs" / "gateway" / "ca.pem"
    ca.parent.mkdir(parents=True)
    ca.write_text("ca", encoding="utf-8")
    result.configuration.configure({"account_id": "account", "zone_id": "zone", "hostname": "gateway.example.com", "tunnel_id": "existing",
                                    "credential_profile": "vault", "credential_entry_path": "APIs/Tunnel", "gateway_url": "https://localhost:39390", "ca_file": str(ca)})
    client = Mock()
    client.tunnel_token.return_value = "runtime-private"
    client.create_tunnel.return_value = {"id": "created"}
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.service.CloudflareClient", Mock(return_value=client))
    FakeProcess.instances = []
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.service.TunnelProcess", FakeProcess)
    yield result, resolver, client, ca
    result.close()


def confirm_existing_origin(service, ca):
    service.configuration.configure({"provisioned_gateway_url": "https://localhost:39390", "provisioned_ca_file": str(ca)}, validate_credentials=False)


def activate(service, ca):
    return service.rpc("cloudflare-tunnel.activate", {"gateway_url": "https://localhost:39390", "ca_file": str(ca)})


def test_wrapper_is_administratively_ready_without_configuration(tmp_path):
    service = TunnelService(tmp_path, tmp_path / "configs" / "tunnel")
    try:
        assert not service.status()["configured"]
        assert not service.status()["running"]
        assert not service.failed.is_set()
    finally:
        service.close()


def test_use_existing_binary_is_serialized_and_requires_no_tunnel_credentials(tmp_path, monkeypatch):
    resolver = Mock(); resolver.password_exists.side_effect = AssertionError("Não consultar credencial."); resolver.resolve.side_effect = AssertionError("Não resolver credencial.")
    service = TunnelService(tmp_path, tmp_path / "configs" / "tunnel", resolver)
    binary = tmp_path / "cloudflared.exe"; binary.write_bytes(b"existing")
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.subprocess.run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="cloudflared version 2026.10.7", stderr=""))
    try:
        result = wait_operation(service, service.rpc("cloudflare-tunnel.use-cloudflared", {"confirm": True, "executable": str(binary)}))
        assert result["state"] == "succeeded" and result["operation"] == "use-cloudflared"
        assert service.configuration.settings()["cloudflared_executable"] == str(binary.resolve())
        assert not service.status()["configured"] and not service.status()["running"]
        assert binary.read_bytes() == b"existing"
        resolver.resolve.assert_not_called(); resolver.password_exists.assert_not_called()
    finally:
        service.close()


@pytest.mark.parametrize("same_selection", [True, False])
def test_selecting_existing_binary_restarts_connector_only_when_reference_changes(service, tmp_path, monkeypatch, same_selection):
    tunnel, resolver, _client, ca = service
    binary = tmp_path / "cloudflared.exe"; binary.write_bytes(b"existing")
    if same_selection:
        tunnel.configuration.configure_executable({"cloudflared_executable": str(binary.resolve())})
    confirm_existing_origin(tunnel, ca)
    tunnel.activated = True
    stop, start = Mock(), Mock()
    monkeypatch.setattr(tunnel, "_stop_connector", stop)
    monkeypatch.setattr(tunnel, "_start_with_recovery", start)
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.installer.subprocess.run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="cloudflared version 2026.10.7", stderr=""))
    resolver.reset_mock()

    result = wait_operation(tunnel, tunnel.rpc("cloudflare-tunnel.use-cloudflared", {"confirm": True, "executable": str(binary)}))

    assert result["state"] == "succeeded"
    assert stop.call_count == start.call_count == (0 if same_selection else 1)
    resolver.resolve.assert_not_called(); resolver.password_exists.assert_not_called()


def test_install_before_tunnel_configuration_is_available_and_does_not_start_connector(tmp_path, monkeypatch):
    resolver = Mock()
    resolver.password_exists.side_effect = AssertionError("Instalação não requer cofre.")
    resolver.resolve.side_effect = AssertionError("Instalação não resolve token.")
    service = TunnelService(tmp_path, tmp_path / "configs" / "tunnel", resolver)
    binary = tmp_path / "configs" / "tunnel" / "cloudflared.exe"
    binary.write_bytes(b"installed")
    ca = tmp_path / "configs" / "gateway-ca.pem"; ca.write_text("ca", encoding="utf-8")
    installer = Mock()
    def install(*_args):
        service.configuration.configure_executable({"cloudflared_executable": str(binary), "managed_version": "2026.10.7"})
        return {"executable": str(binary)}
    installer.install.side_effect = install
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.service.CloudflaredInstaller", Mock(return_value=installer))
    client, process = Mock(), Mock()
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.service.CloudflareClient", client)
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.service.TunnelProcess", process)
    try:
        activate(service, ca)
        result = wait_operation(service, service.rpc("cloudflare-tunnel.install-cloudflared", {"confirm": True}))
        assert result["state"] == "succeeded"
        assert service.status()["cloudflared_available"]
        assert not service.status()["configured"] and not service.status()["running"]
        assert "ca_file" not in service.configuration.settings() or service.configuration.settings()["ca_file"] == ""
        assert activate(service, ca)["configured"] is False
        incomplete = wait_operation(service, service.rpc("cloudflare-tunnel.provision", {"confirm": True}))
        assert incomplete["error_code"] == "incomplete_config"
        assert not service.failed.is_set() and not service.stopped.is_set()
        resolver.password_exists.assert_not_called(); resolver.resolve.assert_not_called()
        client.assert_not_called(); process.assert_not_called()
    finally:
        service.close()


def test_service_rejects_persistent_state_outside_configs(tmp_path):
    with pytest.raises(TunnelError):
        TunnelService(tmp_path, tmp_path / "workspace")


def test_provisioned_configuration_does_not_start_before_supervisor_activation(service):
    tunnel, resolver, client, ca = service
    confirm_existing_origin(tunnel, ca)
    assert tunnel.status()["ingress_ready"]
    assert tunnel.status()["running"] is False
    client.tunnel_token.assert_not_called()
    resolver.resolve.assert_not_called()


def test_activation_is_idempotent_and_does_not_persist_tokens(service):
    tunnel, resolver, client, ca = service
    confirm_existing_origin(tunnel, ca)
    operation = activate(tunnel, ca)
    result = wait_operation(tunnel, operation)
    assert result["state"] == "succeeded"
    assert tunnel.status()["running"] and tunnel.status()["connected"]
    activate(tunnel, ca)
    assert len(FakeProcess.instances) == 1
    assert FakeProcess.instances[0].token == "runtime-private"
    resolver.resolve.assert_called_once_with("vault", "APIs/Tunnel", "password")
    client.tunnel_token.assert_called_once_with("account", "existing")
    persisted = tunnel.configuration.path.read_text(encoding="utf-8") + tunnel.operations.path.read_text(encoding="utf-8")
    assert "runtime-private" not in persisted and "api-private" not in persisted


def test_activation_requires_explicit_valid_ca_and_never_skips_remote_provisioning(service):
    tunnel, resolver, client, ca = service
    with pytest.raises(TunnelError):
        tunnel.rpc("cloudflare-tunnel.activate", {"gateway_url": "http://localhost:39390", "ca_file": str(ca)})
    result = activate(tunnel, ca)
    assert not result["running"] and not result["ingress_ready"]
    client.tunnel_token.assert_not_called()


def test_confirmed_provision_reuses_existing_tunnel_and_starts_after_activation(service):
    tunnel, resolver, client, ca = service
    activate(tunnel, ca)
    result = wait_operation(tunnel, tunnel.rpc("cloudflare-tunnel.provision", {"confirm": True}))
    assert result["state"] == "succeeded"
    client.create_tunnel.assert_not_called()
    client.ensure_dns.assert_called_once()
    client.configure_ingress.assert_called_once()
    assert tunnel.status()["ingress_ready"] and tunnel.status()["running"]


def test_new_tunnel_preflight_denial_prevents_remote_creation(service):
    tunnel, _, client, _ = service
    tunnel.configuration.configure({"tunnel_id": ""}, validate_credentials=False)
    client.preflight_access.side_effect = TunnelError("cloudflare_access_failed", "DNS negado.")
    operation = tunnel.rpc("cloudflare-tunnel.provision", {"confirm": True})
    result = wait_operation(tunnel, operation)
    assert result["state"] == "failed" and result["error_code"] == "cloudflare_access_failed"
    client.inspect_dns.assert_not_called()
    client.create_tunnel.assert_not_called()
    assert not tunnel.configuration.settings()["tunnel_id"]


def test_new_tunnel_id_survives_partial_provisioning_failure_and_retry(service):
    tunnel, resolver, client, ca = service
    tunnel.configuration.configure({"tunnel_id": ""}, validate_credentials=False)
    client.configure_ingress.side_effect = TunnelError("ingress_conflict", "Regras adicionais impedem substituição.")
    failed = wait_operation(tunnel, tunnel.rpc("cloudflare-tunnel.provision", {"confirm": True}))
    assert failed["state"] == "failed" and failed["error_code"] == "ingress_conflict"
    assert tunnel.configuration.settings()["tunnel_id"] == "created"
    client.configure_ingress.side_effect = None
    succeeded = wait_operation(tunnel, tunnel.rpc("cloudflare-tunnel.provision", {"confirm": True}))
    assert succeeded["state"] == "succeeded"
    client.create_tunnel.assert_called_once()


def test_new_tunnel_is_not_created_when_hostname_is_already_taken(service):
    tunnel, _, client, _ = service
    tunnel.configuration.configure({"tunnel_id": ""}, validate_credentials=False)
    client.inspect_dns.side_effect = TunnelError("dns_conflict", "Hostname em uso.")
    result = wait_operation(tunnel, tunnel.rpc("cloudflare-tunnel.provision", {"confirm": True}))
    assert result["state"] == "failed" and result["error_code"] == "dns_conflict"
    client.create_tunnel.assert_not_called()


def test_offline_configuration_uses_live_gateway_origin_on_confirmed_provision(service):
    tunnel, _, client, ca = service
    tunnel.rpc("cloudflare-tunnel.activate", {"gateway_url": "https://localhost:45000", "ca_file": str(ca)})
    # Simula uma edição offline que reteve a porta padrão, enquanto o wrapper
    # já conhece a origem real anunciada pelo Supervisor.
    tunnel.configuration.configure({"gateway_url": "https://localhost:39390", "ca_file": ""}, validate_credentials=False)
    result = wait_operation(tunnel, tunnel.rpc("cloudflare-tunnel.provision", {"confirm": True}))
    assert result["state"] == "succeeded"
    assert client.configure_ingress.call_args.args[2] == "https://localhost:45000"


@pytest.mark.parametrize("remote_url,recognized", [("https://localhost:39390", True), ("https://localhost:39999", False)])
def test_origin_update_authorizes_only_last_confirmed_remote_ingress(service, remote_url, recognized):
    """Reprovisionar outra porta não autoriza adotar alterações remotas externas."""
    tunnel, _, client, ca = service
    confirm_existing_origin(tunnel, ca)
    remote = {"config": {"warp-routing": {"enabled": False}, "ingress": [
        {"hostname": "gateway.example.com", "service": remote_url, "originRequest": {"caPool": str(ca), "noTLSVerify": False}},
        {"service": "http_status:404"}]}}
    client.get_configuration.return_value = remote
    tunnel.configuration.configure({"gateway_url": "https://localhost:45000"}, validate_credentials=False)
    result = wait_operation(tunnel, tunnel.rpc("cloudflare-tunnel.provision", {"confirm": True}))
    assert result["state"] == "succeeded"
    assert client.configure_ingress.call_args.kwargs["expected_previous"] == (remote if recognized else None)


@pytest.mark.parametrize("action", ["configure", "provision", "install-cloudflared", "update-cloudflared", "rollback-cloudflared", "use-cloudflared"])
def test_mutating_rpc_requires_confirmation(service, action):
    tunnel, _, _, _ = service
    with pytest.raises(TunnelError) as captured:
        tunnel.rpc("cloudflare-tunnel." + action, {})
    assert captured.value.code == "confirmation_required"


def test_async_configure_preserves_identity_and_returns_operation(service):
    tunnel, _, _, _ = service
    operation = tunnel.rpc("cloudflare-tunnel.configure", {"confirm": True, "credential_entry_path": "APIs/Outra"})
    assert operation["operation_id"]
    assert wait_operation(tunnel, operation)["state"] == "succeeded"
    settings = tunnel.configuration.settings()
    assert settings["tunnel_id"] == "existing" and settings["credential_entry_path"] == "APIs/Outra"


def test_credential_error_does_not_expose_provider_details(service):
    tunnel, resolver, client, ca = service
    confirm_existing_origin(tunnel, ca)
    resolver.resolve.side_effect = CredentialError("private", "password=api-private")
    result = wait_operation(tunnel, activate(tunnel, ca))
    assert result["state"] == "failed"
    assert result["error_code"] == "credential_unavailable"
    assert "api-private" not in str(result)
    assert tunnel.failed.is_set() and tunnel.stopped.is_set()


def test_unexpected_connector_death_requests_supervisor_recovery(service):
    tunnel, _, _, ca = service
    confirm_existing_origin(tunnel, ca)
    wait_operation(tunnel, activate(tunnel, ca))
    FakeProcess.instances[0].returncode = 17
    assert tunnel.stopped.wait(2)
    assert tunnel.failed.is_set()
    assert tunnel.status()["error_code"] == "connector_exited"


def test_planned_shutdown_stops_child_without_reporting_failure(service):
    tunnel, _, _, ca = service
    confirm_existing_origin(tunnel, ca)
    wait_operation(tunnel, activate(tunnel, ca))
    process = FakeProcess.instances[0]
    tunnel.close()
    assert process.stops == 1
    assert not tunnel.failed.is_set()
    assert not tunnel.status()["running"]


def test_status_separates_connected_state_and_invalidates_changed_runtime_identity(service):
    tunnel, _, _, ca = service
    confirm_existing_origin(tunnel, ca)
    wait_operation(tunnel, activate(tunnel, ca))
    FakeProcess.instances[0].connected = False
    assert tunnel.status()["running"] and not tunnel.status()["connected"]
    tunnel.configuration.configure({"tunnel_id": "other"}, validate_credentials=False)
    assert not tunnel.status()["ingress_ready"]


def test_runtime_identity_transition_stops_previous_child_before_starting_new(service):
    tunnel, _, client, ca = service
    confirm_existing_origin(tunnel, ca)
    wait_operation(tunnel, activate(tunnel, ca))
    previous = FakeProcess.instances[0]
    tunnel.configuration.configure({"tunnel_id": "other", "provisioned_gateway_url": "https://localhost:39390", "provisioned_ca_file": str(ca)}, validate_credentials=False)
    wait_operation(tunnel, activate(tunnel, ca))
    assert previous.stops == 1
    assert len(FakeProcess.instances) == 2
    assert tunnel.status()["running"] and tunnel.status()["ingress_ready"]


def test_install_update_and_rollback_are_asynchronous(service, monkeypatch):
    tunnel, _, _, _ = service
    installer = Mock()
    installer.install.return_value = {"version": "2026.10.7"}
    installer.rollback.return_value = {"version": "2026.10.6"}
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.service.CloudflaredInstaller", Mock(return_value=installer))
    for action in ("install-cloudflared", "update-cloudflared"):
        operation = tunnel.rpc("cloudflare-tunnel." + action, {"confirm": True, "version": "2026.10.7", "adopt": True})
        assert wait_operation(tunnel, operation)["result"] == {"version": "2026.10.7"}
    operation = tunnel.rpc("cloudflare-tunnel.rollback-cloudflared", {"confirm": True})
    assert wait_operation(tunnel, operation)["result"] == {"version": "2026.10.6"}
    installer.rollback.assert_called_once_with(True)


def test_service_rejects_unknown_method_and_non_object_parameters(service):
    tunnel, _, _, _ = service
    with pytest.raises(TunnelError):
        tunnel.rpc("cloudflare-tunnel.unknown", {})
    with pytest.raises(TunnelError):
        tunnel.rpc("cloudflare-tunnel.status", None)


def test_parent_death_stops_posix_wrapper_without_spawning_orphan(tmp_path, monkeypatch):
    import onmyoji_daemon.cloudflare_tunnel.service as module
    parent = {"pid": 123}
    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix", environ={"ONMYOJI_SUPERVISOR_PID": "123"}, getppid=lambda: parent["pid"]))
    tunnel = TunnelService(tmp_path, tmp_path / "configs" / "tunnel")
    try:
        parent["pid"] = 999
        assert tunnel.stopped.wait(2)
        assert not tunnel.failed.is_set()
    finally:
        tunnel.close()


def test_entrypoint_responds_without_miniapps_configuration_or_cloudflared(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    source = Path(__file__).resolve().parents[1] / "src"
    temporary = tmp_path / "tmp"; temporary.mkdir()
    environment = {**os.environ, "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary)}
    process = subprocess.Popen([sys.executable, "-B", "-m", "onmyoji_daemon.cloudflare_tunnel", "--onmyoji-root", str(tmp_path), "--data-dir", str(tmp_path / "configs" / "tunnel"), "--port", str(port), "--token", "ephemeral-test"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                ping = call("127.0.0.1", port, "ephemeral-test", "ping", timeout=0.2)
                break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise AssertionError(process.communicate(timeout=2))
                time.sleep(0.02)
        assert ping["service"] == "cloudflare-tunnel"
        assert not call("127.0.0.1", port, "ephemeral-test", "cloudflare-tunnel.status")["configured"]
        call("127.0.0.1", port, "ephemeral-test", "shutdown")
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
