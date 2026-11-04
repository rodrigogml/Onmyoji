"""Contratos de identidade, geração e reconciliação da publicação no Daemon."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from onmyoji_daemon.rpc import RpcError
from onmyoji_daemon.supervisor import Supervisor
from onmyoji_daemon.cloudflare_tunnel.configuration import TunnelConfiguration


def running(service, generation="one"):
    service.process = SimpleNamespace(poll=lambda: None)
    service.state, service.generation = "running", generation


def test_internal_publication_writes_cannot_be_forwarded_by_administrative_rpc(tmp_path):
    supervisor = Supervisor(tmp_path)
    for method in ("gateway.publication.sync", "gateway.publication.unavailable", "gateway.publication.context", "mini-apps.publication.update", "cloudflare-tunnel.activate"):
        with pytest.raises(RpcError) as caught:
            supervisor.handle(method, {"owner": "mini-apps"})
        assert caught.value.code == "internal_operation"
    supervisor.rpc.close()


def test_declarations_cannot_publish_another_port_prefix_or_ca(tmp_path):
    supervisor = Supervisor(tmp_path)
    coordinator = supervisor.publications
    ca = tmp_path / "configs/daemon/services/mini-apps/tls/ca.pem"
    ca.parent.mkdir(parents=True)
    ca.write_text("public-certificate", encoding="utf-8")
    listener = {"path_prefix": "a", "target": "https://127.0.0.1:39391", "ca_file": str(ca), "ready": True}
    valid = {"schema_version": 1, "listeners": [listener]}
    assert coordinator.validate_description("mini-apps", valid)["listeners"][0]["target"] == listener["target"]
    for patch in ({"target": "https://example.com:39391"}, {"target": "https://127.0.0.1:39390"}, {"path_prefix": "reikon"}, {"owner": "reikon"}, {"ca_file": str(tmp_path / "elsewhere.pem")}):
        with pytest.raises(RpcError) as caught:
            coordinator.validate_description("mini-apps", {"schema_version": 1, "listeners": [{**listener, **patch}]})
        assert caught.value.code == "invalid_publication"
    for malformed in (None, [], "invalid"):
        with pytest.raises(RpcError) as caught:
            coordinator.validate_description("mini-apps", malformed)
        assert caught.value.code == "invalid_publication"
    supervisor.rpc.close()


def test_busy_same_generation_publisher_keeps_confirmed_declaration(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path)
    gateway, mini = supervisor.services["gateway"], supervisor.services["mini-apps"]
    running(gateway)
    running(mini)
    listener = {"path_prefix": "a", "target": "http://127.0.0.1:39391", "ready": True}
    coordinator = supervisor.publications
    coordinator.descriptions["mini-apps"] = ("one", {"schema_version": 1, "listeners": [listener]}, 0)
    calls = []

    def request(name, method, params, **kwargs):
        calls.append((name, method, params))
        if method == "gateway.status":
            return {"ready": True, "http_url": "https://localhost:39390", "ca_file": "ca.pem"}
        if method == "gateway.publication.sync":
            return {"listeners": [{**item, "enabled": True} for item in params["listeners"]]}
        return None

    coordinator.request = request
    monkeypatch.setattr(coordinator, "_transport_ready", lambda _listener: True)
    coordinator.reconcile()
    sync = next(params for _name, method, params in calls if method == "gateway.publication.sync")
    assert sync["owner"] == "mini-apps" and sync["generation"] == "one"
    assert sync["listeners"] == [listener]
    supervisor.rpc.close()


def test_old_generation_description_is_never_applied(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path)
    coordinator = supervisor.publications
    running(supervisor.services["gateway"])
    mini = supervisor.services["mini-apps"]
    running(mini)
    ca = tmp_path / "configs/daemon/services/mini-apps/tls/ca.pem"
    ca.parent.mkdir(parents=True)
    ca.touch()
    methods = []

    def request(name, method, params, **kwargs):
        methods.append(method)
        if method == "gateway.status":
            return {"ready": True}
        if method == "mini-apps.publication.describe":
            mini.generation = "restarted"
            return {"schema_version": 1, "listeners": [{"path_prefix": "a", "target": "https://127.0.0.1:39391", "ca_file": str(ca), "ready": True}]}
        return None

    coordinator.request = request
    coordinator.reconcile()
    assert "gateway.publication.sync" not in methods
    assert "mini-apps.publication.update" not in methods
    supervisor.rpc.close()


def test_intentional_nonzero_exit_is_not_recovered(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path)
    service = supervisor.services["cloudflare-tunnel"]
    service.process = SimpleNamespace(poll=lambda: 1, returncode=1)
    service.enabled, service.intentional_stop = True, True
    calls = []
    monkeypatch.setattr(supervisor, "start", lambda name: calls.append(name))
    supervisor._recover_enabled_services_once()
    assert supervisor.status("cloudflare-tunnel")["state"] == "stopped"
    assert calls == []
    supervisor.rpc.close()


def test_outbound_reconciliation_skips_a_busy_service(tmp_path):
    supervisor = Supervisor(tmp_path)
    service = supervisor.services["mini-apps"]
    service.outbound_lock.acquire()
    try:
        assert supervisor._call_service("mini-apps", "mini-apps.publication.describe", {}, skip_busy=True) is None
    finally:
        service.outbound_lock.release()
        supervisor.rpc.close()


def test_publisher_cannot_target_supervisor_rpc_or_announce_ambiguous_contract(tmp_path):
    supervisor = Supervisor(tmp_path)
    mini = supervisor.services["mini-apps"]
    listener = {"path_prefix": "a", "target": "http://127.0.0.1:39391", "ready": True}
    try:
        for patch in ({"target": "http://127.0.0.1:\n39391"}, {"ready": "true"}, {"path_prefix": "internal"}, {"path_prefix": "../a"}):
            with pytest.raises(RpcError) as caught:
                supervisor.publications.validate_description("mini-apps", {"schema_version": 1, "listeners": [{**listener, **patch}]})
            assert caught.value.code == "invalid_publication"
        with pytest.raises(RpcError):
            supervisor.publications.validate_description("mini-apps", {"schema_version": True, "listeners": [listener]})
        mini.spec = replace(mini.spec, publication_port=supervisor.rpc.port)
        with pytest.raises(RpcError) as caught:
            supervisor.publications.validate_description("mini-apps", {"schema_version": 1, "listeners": [{**listener, "target": f"http://127.0.0.1:{supervisor.rpc.port}"}]})
        assert caught.value.code == "invalid_publication"
    finally:
        supervisor.rpc.close()


def test_late_gateway_status_from_old_generation_never_activates_tunnel(tmp_path):
    supervisor = Supervisor(tmp_path)
    gateway, tunnel = supervisor.services["gateway"], supervisor.services["cloudflare-tunnel"]
    running(gateway)
    running(tunnel)
    calls = []

    def request(name, method, params, **kwargs):
        calls.append(method)
        if method == "gateway.status":
            gateway.generation = "restarted"
            return {"ready": True, "http_url": "https://localhost:39390", "ca_file": "ca.pem"}
        return {}

    try:
        supervisor.publications.request = request
        supervisor.publications.reconcile()
        assert "cloudflare-tunnel.activate" not in calls
        assert "gateway.publication.sync" not in calls
    finally:
        supervisor.rpc.close()


def test_new_tunnel_generation_does_not_inherit_previous_connection(tmp_path):
    supervisor = Supervisor(tmp_path)
    tunnel = supervisor.services["cloudflare-tunnel"]
    running(tunnel, "new")
    coordinator = supervisor.publications
    coordinator.tunnel_generation = "old"
    coordinator.tunnel = {"hostname": "apps.example.com", "running": True, "connected": True}
    coordinator.request = lambda *_args, **_kwargs: None
    try:
        coordinator.reconcile()
        assert not coordinator.tunnel["connected"] and not coordinator.tunnel["running"]
    finally:
        supervisor.rpc.close()


def test_offline_tunnel_with_only_installed_binary_is_not_configured_or_provisioned(tmp_path):
    binary = tmp_path / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "bin" / "cloudflared.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"installed")
    configuration = TunnelConfiguration(tmp_path)
    configuration.configure_executable({"cloudflared_executable": str(binary), "managed_version": "2026.10.7"})
    supervisor = Supervisor(tmp_path)
    try:
        supervisor.publications.reconcile()
        status = supervisor.publications.tunnel
        assert status["cloudflared_available"]
        assert status["configured"] is False and status["provisioned"] is False
        assert status["running"] is False and status["connected"] is False
    finally:
        supervisor.rpc.close()


@pytest.mark.parametrize("field,value", [("tunnel_id", None), ("tunnel_id", []), ("tunnel_name", None), ("tunnel_name", []), ("hostname", 7)])
def test_configuration_completeness_rejects_invalid_definition_types(field, value):
    settings = {"schema_version": 1, "account_id": "account", "zone_id": "zone", "hostname": "gateway.example.com", "credential_profile": "vault", "credential_entry_path": "APIs/Tunnel", field: value}
    assert TunnelConfiguration.is_configured(settings) is False


def test_offline_tunnel_rejects_unsupported_schema_instead_of_reporting_provisioned(tmp_path):
    path = tmp_path / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "tunnel.toml"
    path.parent.mkdir(parents=True)
    path.write_text('schema_version = true\naccount_id = "account"\nzone_id = "zone"\nhostname = "gateway.example.com"\ncredential_profile = "vault"\ncredential_entry_path = "APIs/Tunnel"\ntunnel_id = "existing"\n', encoding="utf-8")
    supervisor = Supervisor(tmp_path)
    try:
        status = supervisor.publications._offline_tunnel()
        assert status["configured"] is False and status["provisioned"] is False
    finally:
        supervisor.rpc.close()


def test_publication_snapshot_reports_last_confirmation_and_sanitized_conflict(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path)
    gateway, mini = supervisor.services["gateway"], supervisor.services["mini-apps"]
    running(gateway)
    running(mini)
    coordinator = supervisor.publications
    listener = {"path_prefix": "a", "target": "http://127.0.0.1:39391", "ready": True}
    coordinator.descriptions["mini-apps"] = ("one", {"schema_version": 1, "listeners": [listener]}, float("inf"))
    snapshots, rejected = [], []

    def request(name, method, params, **kwargs):
        if method == "gateway.status":
            return {"ready": True, "http_url": "https://localhost:39390", "ca_file": "ca.pem"}
        if method == "gateway.publication.sync":
            if rejected:
                raise RpcError("token=must-not-leak", "prefix_conflict", {"token": "must-not-leak"})
            return {"listeners": [{**listener, "enabled": True}]}
        if method == "mini-apps.publication.update":
            snapshots.append(params)

    try:
        coordinator.request = request
        monkeypatch.setattr(coordinator, "_transport_ready", lambda _listener: True)
        coordinator.reconcile()
        confirmed = snapshots[-1]["confirmed_at"]
        assert confirmed and snapshots[-1]["last_error"] is None
        rejected.append(True)
        coordinator.reconcile()
        assert snapshots[-1]["confirmed_at"] == confirmed
        assert snapshots[-1]["error_code"] == "prefix_conflict"
        assert snapshots[-1]["listeners"] == []
        assert "must-not-leak" not in json.dumps(snapshots[-1])
        gateway.generation = "new"
        coordinator.reconcile()
        assert snapshots[-1]["confirmed_at"] is None
    finally:
        supervisor.rpc.close()


def test_supervisor_starts_tunnel_without_miniapps_or_configuration(tmp_path):
    supervisor = Supervisor(tmp_path)
    try:
        started = supervisor.start("cloudflare-tunnel")
        assert started["state"] == "running", started
        status = supervisor.handle("cloudflare-tunnel.status", {})
        assert not status["configured"] and not status["running"]
        assert supervisor.status("mini-apps")["state"] == "stopped"
        assert supervisor.start("cloudflare-tunnel")["pid"] == supervisor.status("cloudflare-tunnel")["pid"]
    finally:
        supervisor.stop("cloudflare-tunnel")
        supervisor.rpc.close()


def test_supervisor_publishes_real_miniapps_through_gateway(tmp_path):
    """Exercita subprocessos, RPC privado, TLS validado e publicação sem Cloudflare."""
    import asyncio
    import ssl
    from aiohttp import ClientSession, TCPConnector

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    instance = tmp_path / "instance"
    instance.mkdir()
    config = instance / "configs/onmyoji-system.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[codex]\nproject_directory = " + json.dumps(str(workspace)) + "\n", encoding="utf-8")
    app = workspace / "example"
    app.mkdir()
    (app / "app.py").write_text('from onmyoji_daemon.miniapps import HttpResponse\nclass App:\n    async def handle(self, request): return HttpResponse.json({"path": request.path})\ndef create_app(): return App()\n', encoding="utf-8")
    supervisor = Supervisor(instance)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        mini_port = probe.getsockname()[1]
    mini = supervisor.services["mini-apps"]
    original_mini_command = mini.spec.command
    mini.spec = replace(mini.spec, publication_port=mini_port, command=lambda data: original_mini_command(data) + ["--http-port", str(mini_port)])
    gateway = supervisor.services["gateway"]
    original_gateway_command = gateway.spec.command
    gateway.spec = replace(gateway.spec, command=lambda data: original_gateway_command(data) + ["--http-port", "0"])
    try:
        assert supervisor.start("mini-apps")["state"] == "running"
        assert supervisor.start("gateway")["state"] == "running"
        created = supervisor.handle("mini-apps.create", {"root_path": str(app), "entrypoint": "app.py", "auth": {"type": "basic", "username": "u", "password": "p"}})
        supervisor.publications.reconcile()
        status = supervisor.handle("gateway.status", {})
        assert {item["path_prefix"] for item in status["listeners"] if item["ready"]} == {"a", "oauth"}

        async def verify():
            context = ssl.create_default_context(cafile=status["ca_file"])
            async with ClientSession(connector=TCPConnector(ssl=context)) as session:
                async with session.get(status["http_url"] + "/a/" + created["id"] + "/items", headers={"Authorization": "Basic dTpw"}) as response:
                    assert response.status == 200, [item.get("details") for item in supervisor.handle("mini-apps.events", {})]
                    assert await response.json() == {"path": "/items"}
                async with session.get(status["http_url"] + "/internal/v1/status") as response:
                    assert response.status == 404

        asyncio.run(verify())
        supervisor.stop("mini-apps")
        listeners = supervisor.handle("gateway.listeners.list", {})["listeners"]
        assert all(not item["ready"] for item in listeners)
        assert supervisor.status("gateway")["state"] == "running"
    finally:
        supervisor.stop("mini-apps")
        supervisor.stop("gateway")
        supervisor.rpc.close()
