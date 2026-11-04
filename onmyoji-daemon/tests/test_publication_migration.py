"""Migração explícita, preservação do Tunnel e rollback com propriedade comprovada."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
import tomllib
from types import SimpleNamespace

import pytest

import onmyoji_daemon.publication_migration as migration_module
from onmyoji_daemon.publication_migration import MigrationError, PublicationMigration


def configured_migration(tmp_path: Path, *, dns_exists: bool = True):
    source = tmp_path / "configs" / "daemon" / "services" / "mini-apps" / "tunnel.toml"
    source.parent.mkdir(parents=True)
    source.write_text('schema_version = 1\naccount_id = "account"\nzone_id = "zone"\nhostname = "apps.example.com"\ntunnel_id = "existing-tunnel"\ntunnel_name = "onmyoji-mini-apps"\ncredential_profile = "existing-vault"\ncredential_entry_path = "APIs/Cloudflare/MiniApps"\ncredential_field = "password"\ncloudflared_executable = "cloudflared"\n', encoding="utf-8")
    ca = tmp_path / "configs" / "daemon" / "services" / "gateway" / "tls" / "ca.pem"
    ca.parent.mkdir(parents=True)
    ca.write_text("test CA", encoding="utf-8")
    calls = []
    original = {"config": {"ingress": [{"hostname": "apps.example.com", "service": "https://localhost:39391", "originRequest": {"noTLSVerify": True}}, {"service": "http_status:404"}]}}
    remote_state = deepcopy(original)
    dns_present = [dns_exists]
    record = {"id": "record-1", "type": "CNAME", "name": "apps.example.com", "content": "existing-tunnel.cfargotunnel.com", "proxied": True}
    class Client:
        def get_configuration(self, definition):
            calls.append(("remote.read", definition.tunnel_id))
            return deepcopy(remote_state)
        def inspect_dns(self, definition, tunnel_id):
            calls.append(("dns.inspect", tunnel_id))
            return record if dns_present[0] else None
        def ensure_dns(self, definition, tunnel_id, confirm):
            assert confirm is True
            calls.append(("dns.create", tunnel_id))
            dns_present[0] = True
            return record
        def configure_ingress(self, definition, tunnel_id, target, confirm, ca_file, *, expected_previous=None):
            assert confirm is True and ca_file == str(ca)
            assert expected_previous == remote_state
            calls.append(("remote.switch", target))
            remote_state["config"]["ingress"] = [{"hostname": definition.hostname, "service": target, "originRequest": {"caPool": ca_file, "noTLSVerify": False}}, {"service": "http_status:404"}]
        def set_configuration(self, definition, configuration, confirm):
            assert confirm is True and configuration == original
            calls.append(("remote.restore", definition.tunnel_id))
            remote_state.clear(); remote_state.update(deepcopy(configuration))
        def delete_managed_dns(self, definition, tunnel_id, record_id, confirm):
            assert confirm is True and record_id == record["id"]
            calls.append(("dns.delete", tunnel_id))
            dns_present[0] = False
    gateway = {"ready": True, "http_url": "https://localhost:39390", "ca_file": str(ca), "listeners": [{"path_prefix": prefix, "owner": "mini-apps", "enabled": True, "ready": True} for prefix in ("a", "oauth")]}
    def rpc(method, params):
        calls.append((method, params))
        if method == "list-services": return [{"name": name, "enabled": name == "mini-apps", "state": "running" if name == "mini-apps" else "stopped"} for name in ("mini-apps", "gateway", "cloudflare-tunnel")]
        if method == "mini-apps.diagnose": return {"tunnel": {"pid": 42}}
        if method == "gateway.status": return gateway
        if method == "cloudflare-tunnel.status": return {"running": True, "ingress_ready": True, "connected": True}
        return {"state": "running"}
    def probe(status):
        assert status == gateway
        calls.append(("probe", "forwarded"))
    migration = PublicationMigration(tmp_path, rpc=rpc, client_factory=lambda _token: Client(), probe=probe, process_alive=lambda _pid: False, token_provider=lambda _settings: "never-persist-this-token", readiness_timeout=0, executable_probe=lambda _executable: "2026.10.7")
    return migration, calls, source, gateway


def test_migration_requires_confirmation_before_any_action(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    with pytest.raises(MigrationError, match="confirmação"):
        migration.apply()
    assert calls == []
    assert not migration.destination.exists()


def test_invalid_executable_prevents_legacy_stop_and_remote_cutover(tmp_path):
    migration, calls, source, _gateway = configured_migration(tmp_path)
    original = source.read_bytes()
    def reject(executable):
        assert executable == "cloudflared"
        raise MigrationError("Executável ausente ou inválido.")
    migration.executable_probe = reject
    with pytest.raises(MigrationError, match="Executável"):
        migration.apply(True)
    assert not any(name in {"stop", "remote.switch", "dns.create"} for name, _params in calls)
    assert not migration.destination.exists() and not migration.manifest.exists()
    assert source.read_bytes() == original


def test_executable_probe_requires_cloudflared_version_without_echoing_output(monkeypatch):
    monkeypatch.setattr(migration_module.shutil, "which", lambda _executable: "/tools/cloudflared")
    commands = []
    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="cloudflared version 2026.10.7 (built 2026-10-20)")
    monkeypatch.setattr(migration_module.subprocess, "run", run)
    assert migration_module._verify_cloudflared("cloudflared") == "2026.10.7"
    assert commands[0][0] == ["/tools/cloudflared", "--version"]
    assert commands[0][1]["timeout"] == 10
    for returncode, output in ((1, "private-token"), (0, "private-token")):
        monkeypatch.setattr(migration_module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=returncode, stdout=output))
        with pytest.raises(MigrationError) as error:
            migration_module._verify_cloudflared("cloudflared")
        assert "private-token" not in str(error.value)


@pytest.mark.parametrize("failure", [PermissionError("private-token"), migration_module.subprocess.TimeoutExpired("private-token", 10)])
def test_executable_probe_sanitizes_execution_failure(monkeypatch, failure):
    monkeypatch.setattr(migration_module.shutil, "which", lambda _executable: "/tools/cloudflared")
    def run(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(migration_module.subprocess, "run", run)
    with pytest.raises(MigrationError) as error:
        migration_module._verify_cloudflared("cloudflared")
    assert "private-token" not in str(error.value)


def test_executable_probe_rejects_missing_binary_before_execution(monkeypatch):
    monkeypatch.setattr(migration_module.shutil, "which", lambda _executable: None)
    with pytest.raises(MigrationError, match="ausente"):
        migration_module._verify_cloudflared("missing")


def test_migration_preserves_identity_references_and_urls(tmp_path):
    migration, calls, source, _gateway = configured_migration(tmp_path)
    original = source.read_bytes()
    assert migration.apply(True)["state"] == "completed"
    values = tomllib.loads(migration.destination.read_text(encoding="utf-8"))
    assert values["tunnel_id"] == "existing-tunnel"
    assert values["hostname"] == "apps.example.com"
    assert values["tunnel_name"] == "onmyoji-mini-apps"
    assert values["credential_profile"] == "existing-vault"
    assert values["credential_entry_path"] == "APIs/Cloudflare/MiniApps"
    assert values["provisioned_gateway_url"] == "https://localhost:39390"
    assert source.read_bytes() == original
    assert calls.index(("stop", {"service": "mini-apps"})) < calls.index(("remote.switch", "https://localhost:39390"))
    assert calls.index(("probe", "forwarded")) < calls.index(("remote.switch", "https://localhost:39390"))
    assert "never-persist-this-token" not in migration.manifest.read_text(encoding="utf-8")
    assert "never-persist-this-token" not in migration.destination.read_text(encoding="utf-8")
    assert not any(name == "dns.create" for name, _params in calls)


def test_completed_migration_is_idempotent_without_starting_another_connector(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    migration.apply(True)
    count = len(calls)
    assert migration.apply(True)["state"] == "completed"
    assert len(calls) == count
    assert calls.count(("start", {"service": "cloudflare-tunnel"})) == 1


def test_conflicting_destination_is_rejected_without_writes(tmp_path):
    migration, calls, source, _gateway = configured_migration(tmp_path)
    migration.destination.parent.mkdir(parents=True)
    conflicting = source.read_text(encoding="utf-8").replace("existing-tunnel", "other-tunnel")
    migration.destination.write_text(conflicting, encoding="utf-8")
    with pytest.raises(MigrationError, match="conflita"):
        migration.apply(True)
    assert calls == []
    assert migration.destination.read_text(encoding="utf-8") == conflicting
    assert not migration.manifest.exists()


def test_old_connector_must_exit_before_new_owner_starts(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    migration.process_alive = lambda _pid: True
    with pytest.raises(MigrationError, match="legado ainda está ativo"):
        migration.apply(True)
    assert ("start", {"service": "cloudflare-tunnel"}) not in calls
    assert not any(name == "remote.switch" for name, _params in calls)
    assert migration.inspect()["state"] == "failed"


def test_unready_gateway_prevents_remote_cutover_and_records_stage(tmp_path):
    migration, calls, _source, gateway = configured_migration(tmp_path)
    gateway["listeners"][0]["ready"] = False
    with pytest.raises(MigrationError, match="listeners"):
        migration.apply(True)
    assert not any(name == "remote.switch" for name, _params in calls)
    assert json.loads(migration.manifest.read_text(encoding="utf-8"))["failed_stage"] == "legacy-stopped"


def test_rollback_restores_ingress_and_requires_previous_code_version(tmp_path):
    migration, calls, source, _gateway = configured_migration(tmp_path)
    migration.apply(True)
    result = migration.rollback(True)
    assert result["state"] == "rolled-back"
    assert result["rollback_requires_previous_version"] is True
    assert "versão anterior" in result["instruction"]
    assert ("remote.restore", "existing-tunnel") in calls
    assert not migration.destination.exists()
    assert source.exists()
    assert ("disable", {"service": "cloudflare-tunnel"}) in calls
    assert ("disable", {"service": "gateway"}) in calls
    count = len(calls)
    migration.rollback(True)
    assert len(calls) == count


def test_rollback_deletes_only_dns_created_by_this_migration(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path, dns_exists=False)
    migration.apply(True)
    assert ("dns.create", "existing-tunnel") in calls
    migration.rollback(True)
    assert ("dns.delete", "existing-tunnel") in calls


def test_rollback_refuses_a_changed_legacy_configuration_before_stopping_services(tmp_path):
    migration, calls, source, _gateway = configured_migration(tmp_path)
    migration.apply(True)
    count = len(calls)
    source.write_text(source.read_text(encoding="utf-8").replace("existing-tunnel", "different-tunnel"), encoding="utf-8")
    with pytest.raises(MigrationError, match="mudou"):
        migration.rollback(True)
    assert len(calls) == count


def test_new_daemon_registry_is_required_before_cutover(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    migration.rpc = lambda _method, _params: [{"name": "mini-apps", "enabled": True, "state": "running"}]
    with pytest.raises(MigrationError, match="Atualize e reinicie"):
        migration.apply(True)
    assert calls == []
    assert not migration.manifest.exists()


def test_concurrent_migration_is_rejected_before_rpc(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    directory = tmp_path / "tmp"; directory.mkdir()
    (directory / "publication-migration.lock").write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(MigrationError, match="Outra migração"):
        migration.apply(True)
    assert calls == []


def test_shared_remote_ingress_is_rejected_before_stopping_old_owner(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    client = migration.client_factory("fake")
    original = client.get_configuration
    def shared(definition):
        remote = original(definition)
        remote["config"]["ingress"].insert(0, {"hostname": "other.example.com", "service": "http://localhost:9000"})
        return remote
    client.get_configuration = shared
    migration.client_factory = lambda _token: client
    with pytest.raises(MigrationError, match="compartilhadas"):
        migration.apply(True)
    assert not any(name == "stop" for name, _params in calls)
    assert not migration.manifest.exists()


def test_remote_url_credentials_are_never_saved_for_rollback(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    client = migration.client_factory("fake")
    client.get_configuration = lambda _definition: {"config": {"ingress": [{"hostname": "apps.example.com", "service": "https://secret-user:private-password@localhost:39391"}, {"service": "http_status:404"}]}}
    migration.client_factory = lambda _token: client
    with pytest.raises(MigrationError, match="credenciais em URL"):
        migration.apply(True)
    assert not migration.manifest.exists()
    assert not any(name == "stop" for name, _params in calls)


def test_rollback_requires_confirmation_and_a_manifest(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    with pytest.raises(MigrationError, match="confirmação"):
        migration.rollback()
    with pytest.raises(MigrationError, match="Não há manifesto"):
        migration.rollback(True)
    assert calls == []


def test_source_change_during_preflight_is_rejected_before_cutover(tmp_path):
    migration, calls, source, _gateway = configured_migration(tmp_path)
    client = migration.client_factory("fake")
    original = client.get_configuration
    def changed(definition):
        source.write_text(source.read_text(encoding="utf-8").replace("existing-tunnel", "different-tunnel"), encoding="utf-8")
        return original(definition)
    client.get_configuration = changed
    migration.client_factory = lambda _token: client
    with pytest.raises(MigrationError, match="mudou durante"):
        migration.apply(True)
    assert not any(name == "stop" for name, _params in calls)
    assert not migration.manifest.exists()


def test_rollback_rejects_remote_drift_without_stopping_new_services(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    migration.apply(True)
    count = sum(name == "stop" for name, _params in calls)
    client = migration.client_factory("fake")
    client.get_configuration = lambda _definition: {"config": {"ingress": [{"hostname": "apps.example.com", "service": "https://localhost:45000"}, {"service": "http_status:404"}]}}
    migration.client_factory = lambda _token: client
    with pytest.raises(MigrationError, match="alterado fora"):
        migration.rollback(True)
    assert sum(name == "stop" for name, _params in calls) == count
    assert not any(name == "remote.restore" for name, _params in calls)


def test_new_explicit_attempt_after_rollback_archives_previous_manifest(tmp_path):
    migration, _calls, _source, _gateway = configured_migration(tmp_path)
    migration.apply(True)
    migration.rollback(True)
    assert migration.apply(True)["state"] == "completed"
    history = list((migration.destination.parent / "migration-history").glob("*.json"))
    assert len(history) == 1
    assert json.loads(history[0].read_text(encoding="utf-8"))["state"] == "rolled-back"


def test_remote_drift_during_preparation_is_not_overwritten(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    client = migration.client_factory("fake")
    def guarded(definition, tunnel_id, target, confirm, ca_file, *, expected_previous=None):
        assert expected_previous["config"]["ingress"][0]["service"] == "https://localhost:39391"
        raise MigrationError("O ingress remoto mudou durante a preparação.")
    client.configure_ingress = guarded
    migration.client_factory = lambda _token: client
    with pytest.raises(MigrationError, match="mudou durante"):
        migration.apply(True)
    assert not any(name == "remote.switch" for name, _params in calls)
    assert ("start", {"service": "cloudflare-tunnel"}) not in calls
    assert migration.inspect()["state"] == "failed"


def test_migration_does_not_complete_when_dns_disappears_after_cutover(tmp_path):
    migration, calls, _source, _gateway = configured_migration(tmp_path)
    client = migration.client_factory("fake")
    inspect = client.inspect_dns
    checks = []
    def disappearing(definition, tunnel_id):
        checks.append(tunnel_id)
        return inspect(definition, tunnel_id) if len(checks) == 1 else None
    client.inspect_dns = disappearing
    migration.client_factory = lambda _token: client
    with pytest.raises(MigrationError, match="DNS público"):
        migration.apply(True)
    assert migration.inspect()["state"] == "failed"
    assert ("start", {"service": "cloudflare-tunnel"}) in calls
    assert json.loads(migration.manifest.read_text(encoding="utf-8"))["failed_stage"] == "ingress-switched"
