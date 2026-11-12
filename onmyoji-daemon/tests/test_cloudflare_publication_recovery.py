"""Recuperação remota sem histórico local, autorização por prévia e rollback."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest

from onmyoji_daemon.cloudflare_tunnel import CloudflareClient, TunnelConfiguration, TunnelDefinition, TunnelError, TunnelService
from onmyoji_daemon.cloudflare_tunnel.recovery import PublicationRecovery


class RemoteCloudflare(CloudflareClient):
    def __init__(self):
        super().__init__("api-private")
        self.record = {"id": "dns-record", "type": "CNAME", "name": "gateway.example.com", "content": "old-id.cfargotunnel.com", "proxied": True}
        self.tunnel = {"id": "old-id", "name": "legacy-publication", "config_src": "cloudflare", "deleted_at": None}
        self.remote = {"config": {"warp-routing": {"enabled": False}, "ingress": [{"hostname": "gateway.example.com", "service": "https://localhost:39391"}, {"service": "http_status:404"}]}}
        self.calls = []
        self.found_by_name = []
        self.write_error = None

    def _call(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        if method != "GET" and self.write_error:
            raise self.write_error
        resource = urlsplit(path).path
        if resource in {"/user/tokens/verify", "/accounts/account/tokens/verify"}:
            return {"status": "active"}
        if resource == "/zones/zone/dns_records":
            if method == "GET":
                return [deepcopy(self.record)] if self.record else []
            if method == "POST":
                assert self.record is None
                self.record = {**body, "id": "created-dns"}
                return deepcopy(self.record)
        if resource == "/zones/zone/dns_records/created-dns" and method == "DELETE":
            self.record = None
            return {"id": "created-dns"}
        if resource == "/accounts/account/cfd_tunnel" and method == "GET":
            return deepcopy(self.found_by_name)
        if resource.endswith("/configurations"):
            if method == "PUT":
                self.remote = deepcopy(body)
            return deepcopy(self.remote)
        if resource.endswith("/token"):
            return "runtime-private"
        if resource == "/accounts/account/cfd_tunnel/old-id":
            return deepcopy(self.tunnel)
        raise AssertionError((method, path, body))


@pytest.fixture
def recovery(tmp_path):
    resolver = Mock()
    resolver.password_exists.return_value = True
    ca = tmp_path / "configs" / "gateway-ca.pem"
    ca.parent.mkdir(parents=True)
    ca.write_text("new private CA certificate", encoding="utf-8")
    config = TunnelConfiguration(tmp_path, credentials=resolver)
    config.configure({"account_id": "account", "zone_id": "zone", "hostname": "gateway.example.com",
                      "credential_profile": "vault", "credential_entry_path": "Tsuki/Serviço/CloudflareTunnel:APIToken",
                      "gateway_url": "https://localhost:39390", "ca_file": str(ca)})
    remote = RemoteCloudflare()
    return PublicationRecovery(config, remote), config, remote


def preview(recovery):
    manager, config, client = recovery
    return manager.inspect(config.settings(), lambda _: None)


def test_remote_dns_reconstructs_existing_publication_without_legacy_files(recovery):
    manager, config, client = recovery
    before = config.path.read_bytes()
    result = preview(recovery)
    assert result["status"] == "existing"
    assert result["tunnel_id"] == "old-id" and result["tunnel_name"] == "legacy-publication"
    assert result["current_origin"] == "https://localhost:39391" and result["new_origin"] == "https://localhost:39390"
    assert result["dns_action"] == "reuse"
    assert all(method == "GET" for method, *_ in client.calls)
    assert config.path.read_bytes() == before
    saved = (manager.directory / (result["preview_id"] + ".json")).read_text(encoding="utf-8")
    assert "api-private" not in saved and "runtime-private" not in saved


def test_inspection_preflight_does_not_repeat_dns_or_tunnel_listing(recovery):
    manager, config, client = recovery
    result = preview(recovery)
    assert result["status"] == "existing" and result["token_verification"] == "active"
    paths = [urlsplit(path).path for method, path, _ in client.calls if method == "GET"]
    assert paths.count("/zones/zone/dns_records") == 1
    assert paths.count("/accounts/account/cfd_tunnel") == 1
    assert "/user/tokens/verify" in paths
    assert all(method == "GET" for method, *_ in client.calls)


def test_confirmed_adoption_reuses_tunnel_and_dns_and_can_restore_backup(recovery):
    manager, config, client = recovery
    original_settings, original_remote, original_dns = config.settings(), deepcopy(client.remote), deepcopy(client.record)
    result = preview(recovery)
    applied = manager.apply(result["preview_id"], config.settings(), lambda _: None)
    assert applied["tunnel_id"] == "old-id"
    assert config.settings()["tunnel_id"] == "old-id"
    assert config.settings()["provisioned_gateway_url"] == "https://localhost:39390"
    assert client.remote["config"]["warp-routing"] == {"enabled": False}
    assert client.remote["config"]["ingress"][0]["originRequest"] == {"caPool": config.settings()["ca_file"], "noTLSVerify": False}
    assert client.record == original_dns
    assert [method for method, *_ in client.calls if method != "GET"] == ["PUT"]
    manager.rollback(result["preview_id"], config.settings(), lambda _: None)
    assert client.remote == original_remote and client.record == original_dns
    assert config.settings() == original_settings



@pytest.mark.parametrize("initial_remote", [
    {}, {"config": None}, {"config": {}},
    {"config": {"ingress": None}}, {"config": {"ingress": []}},
])
def test_unconfigured_tunnel_from_partial_provision_can_be_adopted_and_restored(recovery, initial_remote):
    manager, config, client = recovery
    client.remote = deepcopy(initial_remote)
    initial_settings = config.settings()
    inspected = preview(recovery)
    assert inspected["status"] == "existing" and inspected["current_origin"] is None
    manager.apply(inspected["preview_id"], config.settings(), lambda _: None)
    assert client.remote["config"]["ingress"][0]["hostname"] == "gateway.example.com"
    manager.rollback(inspected["preview_id"], config.settings(), lambda _: None)
    assert client.remote["config"]["ingress"] == [{"service": "http_status:404"}]
    assert config.settings() == initial_settings


def test_discovery_with_no_dns_and_no_matching_tunnel_is_new(recovery):
    manager, config, client = recovery
    client.record = None
    result = preview(recovery)
    assert result["status"] == "new" and "preview_id" not in result
    assert not manager.directory.exists()
    assert all(method == "GET" for method, *_ in client.calls)


def test_named_tunnel_without_dns_is_adopted_with_explicit_dns_creation_and_rollback(recovery):
    manager, config, client = recovery
    original = deepcopy(client.remote)
    client.record = None
    client.found_by_name = [deepcopy(client.tunnel)]
    result = preview(recovery)
    assert result["dns_action"] == "create"
    manager.apply(result["preview_id"], config.settings(), lambda _: None)
    assert client.record["id"] == "created-dns"
    manager.rollback(result["preview_id"], config.settings(), lambda _: None)
    assert client.record is None and client.remote == original
    assert not config.settings()["tunnel_id"]


@pytest.mark.parametrize("record", [
    {"type": "A", "content": "127.0.0.1", "proxied": True},
    {"type": "CNAME", "content": "external.example.com", "proxied": True},
    {"type": "CNAME", "content": "old-id.cfargotunnel.com", "proxied": False},
])
def test_unsupported_dns_is_actionable_without_writes(recovery, record):
    manager, config, client = recovery
    client.record = record
    with pytest.raises(TunnelError) as error:
        preview(recovery)
    assert error.value.code == "dns_conflict" and "Cloudflare" in str(error.value)
    assert all(method == "GET" for method, *_ in client.calls)


@pytest.mark.parametrize("changes", [{"config_src": "local"}, {"deleted_at": "2026-01-01"}, {"id": "another"}])
def test_unavailable_or_locally_managed_tunnels_are_not_adopted(recovery, changes):
    manager, config, client = recovery
    client.tunnel.update(changes)
    with pytest.raises(TunnelError) as error:
        preview(recovery)
    assert error.value.code == "unsupported_remote_tunnel"
    assert all(method == "GET" for method, *_ in client.calls)


def test_divergent_local_id_is_only_replaced_after_preview_confirmation(recovery):
    manager, config, client = recovery
    config.configure({"tunnel_id": "different"}, validate_credentials=False)
    result = preview(recovery)
    assert result["configured_tunnel_id"] == "different" and result["tunnel_id"] == "old-id"
    assert config.settings()["tunnel_id"] == "different" and all(method == "GET" for method, *_ in client.calls)
    manager.apply(result["preview_id"], config.settings(), lambda _: None)
    assert config.settings()["tunnel_id"] == "old-id"
    manager.rollback(result["preview_id"], config.settings(), lambda _: None)
    assert config.settings()["tunnel_id"] == "different"


@pytest.mark.parametrize("mutate", [
    lambda client: client.remote["config"]["ingress"].insert(0, {"hostname": "other.example.com", "service": "https://localhost:3000"}),
    lambda client: client.remote["config"]["ingress"][0].update(path="/foo"),
    lambda client: client.remote["config"]["ingress"][-1].update(service="https://external.example.com"),
])
def test_shared_or_filtered_ingress_is_preserved(recovery, mutate):
    manager, config, client = recovery
    mutate(client)
    original = deepcopy(client.remote)
    with pytest.raises(TunnelError) as error:
        preview(recovery)
    assert error.value.code == "ingress_conflict"
    assert client.remote == original and all(method == "GET" for method, *_ in client.calls)


@pytest.mark.parametrize("mutate", [
    lambda client: client.remote["config"].update(authorization="private-secret"),
    lambda client: client.remote["config"]["ingress"][0].update(service="https://u:private@localhost:3000"),
    lambda client: client.remote["config"]["ingress"][0].update(service="https://localhost:3000?key=private"),
])
def test_sensitive_remote_configuration_is_not_printed_or_saved(recovery, mutate):
    manager, config, client = recovery
    mutate(client)
    with pytest.raises(TunnelError) as error:
        preview(recovery)
    assert error.value.code == "sensitive_remote_config" and "private" not in str(error.value)
    assert not manager.directory.exists()


def test_inherited_sni_and_host_header_are_overridden_per_route_for_new_gateway(recovery):
    manager, config, client = recovery
    original = {"originServerName": "old-host.example.com", "httpHostHeader": "old-host.example.com", "connectTimeout": 20}
    client.remote["config"]["originRequest"] = original
    result = preview(recovery)
    manager.apply(result["preview_id"], config.settings(), lambda _: None)
    origin = client.remote["config"]["ingress"][0]["originRequest"]
    assert origin["originServerName"] == "localhost" and origin["httpHostHeader"] == "localhost:39390"
    assert client.remote["config"]["originRequest"] == original


def test_compatible_global_origin_settings_are_preserved(recovery):
    manager, config, client = recovery
    inherited = {"originServerName": "localhost", "httpHostHeader": "localhost:39390", "connectTimeout": 20}
    client.remote["config"]["originRequest"] = inherited
    result = preview(recovery)
    manager.apply(result["preview_id"], config.settings(), lambda _: None)
    assert client.remote["config"]["originRequest"] == inherited


@pytest.mark.parametrize("changed", ["dns", "remote", "local", "ca", "expired", "name"])
def test_changes_after_preview_require_new_confirmation_without_writes(recovery, changed):
    manager, config, client = recovery
    result = preview(recovery)
    if changed == "dns": client.record["id"] = "another-record"
    elif changed == "remote": client.remote["config"]["ingress"][0]["service"] = "https://localhost:40000"
    elif changed == "local": config.configure({"gateway_url": "https://localhost:41000"}, validate_credentials=False)
    elif changed == "ca": Path(config.settings()["ca_file"]).write_text("replacement CA", encoding="utf-8")
    elif changed == "name": client.tunnel["name"] = "renamed"
    elif changed == "expired":
        data = manager._read(result["preview_id"])
        data["created_at"] -= 1000
        manager._save(result["preview_id"], data)
    with pytest.raises(TunnelError) as error:
        manager.apply(result["preview_id"], config.settings(), lambda _: None)
    assert error.value.code == "stale_preview"
    assert all(method == "GET" for method, *_ in client.calls)


@pytest.mark.parametrize("identifier", [None, "", "../outside", "A" * 32, "f" * 32])
def test_invalid_or_missing_preview_is_rejected(recovery, identifier):
    manager, config, client = recovery
    with pytest.raises(TunnelError) as error:
        manager.apply(identifier, config.settings(), lambda _: None)
    assert error.value.code == "invalid_preview" and not client.calls


def test_preview_cannot_be_applied_twice(recovery):
    manager, config, client = recovery
    result = preview(recovery)
    manager.apply(result["preview_id"], config.settings(), lambda _: None)
    client.calls.clear()
    with pytest.raises(TunnelError) as error:
        manager.apply(result["preview_id"], config.settings(), lambda _: None)
    assert error.value.code == "stale_preview" and not client.calls


def test_denied_write_preserves_backup_for_explicit_recovery(recovery):
    manager, config, client = recovery
    settings = config.settings()
    result = preview(recovery)
    client.write_error = TunnelError("cloudflare_http_error", "Cloudflare retornou HTTP403: revise API Token.")
    with pytest.raises(TunnelError, match="HTTP403"):
        manager.apply(result["preview_id"], settings, lambda _: None)
    assert config.settings() == settings
    assert manager._read(result["preview_id"])["state"] == "failed"
    client.write_error = None
    manager.rollback(result["preview_id"], config.settings(), lambda _: None)
    assert manager._read(result["preview_id"])["state"] == "rolled_back"


def test_rollback_refuses_external_changes_after_adoption(recovery):
    manager, config, client = recovery
    result = preview(recovery)
    manager.apply(result["preview_id"], config.settings(), lambda _: None)
    client.remote["config"]["ingress"][0]["service"] = "https://localhost:42000"
    client.calls.clear()
    with pytest.raises(TunnelError) as error:
        manager.rollback(result["preview_id"], config.settings(), lambda _: None)
    assert error.value.code == "stale_preview" and all(method == "GET" for method, *_ in client.calls)


def test_rollback_can_resume_after_dns_removal_before_checkpoint(recovery):
    manager, config, client = recovery
    client.record = None
    client.found_by_name = [deepcopy(client.tunnel)]
    result = preview(recovery)
    manager.apply(result["preview_id"], config.settings(), lambda _: None)
    data = manager._read(result["preview_id"])
    client.remote = deepcopy(data["remote_before"])
    client.record = None
    data["state"] = "rolling_back"
    manager._save(result["preview_id"], data)
    manager.rollback(result["preview_id"], config.settings(), lambda _: None)
    assert manager._read(result["preview_id"])["state"] == "rolled_back"


def test_failed_local_checkpoint_after_settings_swap_can_restore_both_sides(recovery, monkeypatch):
    manager, config, client = recovery
    original = config.settings()
    result = preview(recovery)
    save = manager._save
    def fail_final_save(identifier, data):
        if data["state"] == "applied": raise OSError("disk failure")
        save(identifier, data)
    monkeypatch.setattr(manager, "_save", fail_final_save)
    with pytest.raises(OSError):
        manager.apply(result["preview_id"], config.settings(), lambda _: None)
    assert config.settings()["tunnel_id"] == "old-id"
    manager.rollback(result["preview_id"], config.settings(), lambda _: None)
    assert config.settings() == original


def test_dns_changed_during_put_does_not_confirm_local_provisioning(recovery, monkeypatch):
    manager, config, client = recovery
    original = config.settings()
    result = preview(recovery)
    call = client._call
    def change_dns_on_write(method, path, body=None):
        response = call(method, path, body)
        if method == "PUT": client.record["id"] = "replaced-dns"
        return response
    monkeypatch.setattr(client, "_call", change_dns_on_write)
    with pytest.raises(TunnelError) as error:
        manager.apply(result["preview_id"], config.settings(), lambda _: None)
    assert error.value.code == "recovery_verification_failed"
    assert config.settings() == original
    assert manager._read(result["preview_id"])["state"] == "failed"


def test_rpc_inspection_and_confirmed_recovery_are_serialized_operations(recovery, monkeypatch):
    manager, config, client = recovery
    tunnel = TunnelService(config.root, config.path.parent, config.credentials)
    monkeypatch.setattr(tunnel, "_client", lambda _settings: client)
    def wait(operation):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = tunnel.operations.status(operation["operation_id"])
            if state["state"] not in {"running", "queued"}: return state
            time.sleep(0.01)
        raise AssertionError("A operação não concluiu.")
    try:
        inspected = wait(tunnel.rpc("cloudflare-tunnel.inspect-publication", {}))
        assert inspected["state"] == "succeeded" and inspected["result"]["status"] == "existing"
        assert all(method == "GET" for method, *_ in client.calls)
        identifier = inspected["result"]["preview_id"]
        adopted = wait(tunnel.rpc("cloudflare-tunnel.adopt-publication", {"preview_id": identifier, "confirm": True}))
        assert adopted["state"] == "succeeded" and adopted["result"]["status"]["ingress_ready"]
        restored = wait(tunnel.rpc("cloudflare-tunnel.rollback-publication", {"preview_id": identifier, "confirm": True}))
        assert restored["state"] == "succeeded" and not config.settings()["tunnel_id"]
        assert "api-private" not in tunnel.operations.path.read_text(encoding="utf-8")
        assert "runtime-private" not in tunnel.operations.path.read_text(encoding="utf-8")
    finally:
        tunnel.close()


@pytest.mark.parametrize("action", ["adopt-publication", "rollback-publication"])
def test_recovery_mutations_require_explicit_confirmation_before_credential_resolution(tmp_path, action):
    resolver = Mock()
    tunnel = TunnelService(tmp_path, tmp_path / "configs" / "tunnel", resolver)
    try:
        with pytest.raises(TunnelError) as error:
            tunnel.rpc("cloudflare-tunnel." + action, {"preview_id": "0" * 32})
        assert error.value.code == "confirmation_required"
        resolver.resolve.assert_not_called()
        assert not tunnel.operations.records
    finally:
        tunnel.close()
