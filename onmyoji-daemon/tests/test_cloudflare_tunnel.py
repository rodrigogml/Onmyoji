"""Contratos de configuração, Cloudflare, processo e operações do Tunnel."""
from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time
from unittest.mock import Mock
import urllib.error

import pytest

from onmyoji_daemon.cloudflare_tunnel import CloudflareClient, TunnelConfiguration, TunnelDefinition, TunnelError, TunnelProcess
from onmyoji_daemon.cloudflare_tunnel.configuration import validate_origin
from onmyoji_daemon.cloudflare_tunnel.operations import TunnelOperations
from onmyoji_daemon.mcp.credentials import CredentialError


def configuration(tmp_path):
    resolver = Mock()
    resolver.password_exists.return_value = True
    config = TunnelConfiguration(tmp_path, credentials=resolver)
    config.configure({"account_id": "account", "zone_id": "zone", "hostname": "gateway.example.com", "credential_profile": "vault", "credential_entry_path": "APIs/Tunnel"})
    return config, resolver


def wait_operation(manager, operation):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = manager.status(operation["operation_id"])
        if result["state"] not in {"queued", "running"}:
            return result
        time.sleep(0.01)
    raise AssertionError("A operação não terminou.")


@pytest.mark.parametrize("values", [
    (None, "zone", "gateway.example.com"), ("", "zone", "gateway.example.com"),
    ("account/other", "zone", "gateway.example.com"), ("account", None, "gateway.example.com"),
    ("account", "zone", None), ("account", "zone", "localhost"), ("account", "zone", "*.example.com"),
    ("account", "zone", "Bad.example.com"), ("account", "zone", ".example.com"),
    ("account", "zone", "bad..example.com"), ("account", "zone", "-bad.example.com"),
])
def test_definition_rejects_missing_or_ambiguous_values(values):
    with pytest.raises(TunnelError):
        TunnelDefinition(*values).validate()


def test_definition_rejects_invalid_optional_identifiers():
    with pytest.raises(TunnelError):
        TunnelDefinition("account", "zone", "gateway.example.com", None).validate()
    with pytest.raises(TunnelError):
        TunnelDefinition("account", "zone", "gateway.example.com", tunnel_name=None).validate()
    with pytest.raises(TunnelError):
        TunnelDefinition("account", "zone", "gateway.example.com", tunnel_id="a/b").validate()


def test_configuration_preserves_ids_refs_and_windows_paths(tmp_path):
    config, resolver = configuration(tmp_path)
    config.configure({"tunnel_id": "existing", "tunnel_name": "Nome da instância", "cloudflared_executable": r"C:\Program Files\cloudflared.exe"})
    config.configure({"credential_entry_path": "APIs\\Outra entrada"})
    saved = config.settings()
    assert saved["tunnel_id"] == "existing"
    assert saved["tunnel_name"] == "Nome da instância"
    assert saved["credential_profile"] == "vault"
    assert saved["credential_entry_path"] == "APIs\\Outra entrada"
    assert saved["cloudflared_executable"] == r"C:\Program Files\cloudflared.exe"
    assert not config.path.read_bytes().startswith(b"\xef\xbb\xbf")
    resolver.password_exists.assert_called_with("vault", "APIs\\Outra entrada")
    assert not list((tmp_path / "tmp").iterdir())


@pytest.mark.parametrize("values", [None, {"token": "private"}, {"account_id": None}, {"schema_version": True}, {"timeout_seconds": True}, {"timeout_seconds": 0}, {"timeout_seconds": 121}, {"credential_field": "title"}, {"cloudflared_executable": ""}])
def test_configuration_rejects_invalid_or_secret_values_without_mutating(tmp_path, values):
    config, _ = configuration(tmp_path)
    previous = config.path.read_bytes()
    with pytest.raises(TunnelError):
        config.configure(values)
    assert config.path.read_bytes() == previous


def test_configuration_credential_failure_does_not_persist_secrets(tmp_path):
    config, resolver = configuration(tmp_path)
    previous = config.path.read_bytes()
    resolver.password_exists.return_value = False
    with pytest.raises(TunnelError, match="não contém"):
        config.configure({"tunnel_id": "new"})
    resolver.password_exists.side_effect = CredentialError("failure", "password=private-token")
    with pytest.raises(TunnelError) as captured:
        config.configure({"tunnel_id": "new"})
    assert "private-token" not in str(captured.value)
    assert config.path.read_bytes() == previous


def test_configuration_invalidate_origin_confirmation_on_identity_change(tmp_path):
    config, _ = configuration(tmp_path)
    config.configure({"tunnel_id": "old", "provisioned_gateway_url": "https://localhost:39390", "provisioned_ca_file": "ca.pem"})
    config.configure({"hostname": "other.example.com"})
    assert "provisioned_gateway_url" not in config.settings()
    assert config.settings()["tunnel_id"] == "old"


def test_configuration_equivalent_hostname_does_not_lose_origin_confirmation(tmp_path):
    config, _ = configuration(tmp_path)
    config.configure({"tunnel_id": "existing", "provisioned_gateway_url": "https://localhost:39390", "provisioned_ca_file": "ca.pem"})
    config.configure({"hostname": " Gateway.Example.Com "})
    assert config.settings()["provisioned_gateway_url"] == "https://localhost:39390"


def test_settings_handle_missing_invalid_and_unsupported_files(tmp_path):
    config = TunnelConfiguration(tmp_path)
    assert config.settings() == {}
    config.path.parent.mkdir(parents=True)
    config.path.write_text("invalid = [", encoding="utf-8")
    with pytest.raises(TunnelError) as captured:
        config.settings()
    assert captured.value.code == "invalid_config"
    config.path.write_text("schema_version = 2\n", encoding="utf-8")
    with pytest.raises(TunnelError) as captured:
        config.settings()
    assert captured.value.code == "unsupported_config"


@pytest.mark.parametrize("url", [None, 123, "http://localhost:39390", "https://example.com:39390", "https://localhost", "https://localhost:0", "https://u:p@localhost:39390", "https://@localhost:39390", "https://localhost:39390/path", "https://localhost:39390?x=y", "https://localhost:39390/#fragment"])
def test_origin_rejects_external_or_ambiguous_targets(url):
    with pytest.raises(TunnelError):
        validate_origin(url, "", require_ca=False)


def test_origin_requires_existing_ca_and_accepts_loopback(tmp_path):
    with pytest.raises(TunnelError):
        validate_origin("https://127.0.0.1:39390", "relative.pem")
    ca = tmp_path / "ca.pem"
    ca.write_text("certificate", encoding="utf-8")
    validate_origin("https://127.0.0.1:39390", str(ca))
    with pytest.raises(TunnelError):
        validate_origin("https://127.0.0.1:39390", 123)


def test_cloudflare_writes_require_confirmation_before_network():
    client = CloudflareClient("test-token")
    client._call = Mock(side_effect=AssertionError("não deve acessar rede"))
    definition = TunnelDefinition("account", "zone", "gateway.example.com", "tunnel")
    for action in (
        lambda: client.create_tunnel(definition, False),
        lambda: client.configure_ingress(definition, "tunnel", "https://localhost:39390", False, "ca.pem"),
        lambda: client.ensure_dns(definition, "tunnel", False),
        lambda: client.set_configuration(definition, {"config": {}}, False),
        lambda: client.delete_managed_dns(definition, "tunnel", "record", False),
    ):
        with pytest.raises(TunnelError) as captured:
            action()
        assert captured.value.code == "confirmation_required"
    client._call.assert_not_called()


def test_ingress_has_one_origin_verified_tls_and_final_fallback(tmp_path):
    client = CloudflareClient("test-token")
    ca = tmp_path / "ca.pem"; ca.write_text("ca", encoding="utf-8")
    calls = []
    client._call = lambda method, path, body=None: calls.append((method, path, body)) or ({"config": {"warp-routing": {"enabled": False}, "ingress": [{"hostname": "gateway.example.com", "service": "https://localhost:39391"}, {"service": "http_status:404"}]}} if method == "GET" else None)
    definition = TunnelDefinition("account", "zone", "gateway.example.com", "tunnel")
    previous = {"config": {"warp-routing": {"enabled": False}, "ingress": [{"hostname": "gateway.example.com", "service": "https://localhost:39391"}, {"service": "http_status:404"}]}}
    client.configure_ingress(definition, "tunnel", "https://127.0.0.1:39390", True, str(ca), expected_previous=previous)
    applied = calls[-1][2]["config"]
    assert applied["ingress"] == [{"hostname": "gateway.example.com", "service": "https://127.0.0.1:39390", "originRequest": {"caPool": str(ca), "noTLSVerify": False}}, {"service": "http_status:404"}]
    assert applied["warp-routing"] == {"enabled": False}


def test_ingress_refuses_unconfirmed_origin_and_changed_migration_snapshot(tmp_path):
    client = CloudflareClient("test-token")
    ca = tmp_path / "ca.pem"; ca.write_text("ca", encoding="utf-8")
    previous = {"config": {"ingress": [{"hostname": "gateway.example.com", "service": "https://localhost:39391"}, {"service": "http_status:404"}]}}
    client._call = Mock(return_value=previous)
    definition = TunnelDefinition("account", "zone", "gateway.example.com", "tunnel")
    for expected in (None, {"config": {"ingress": []}}):
        with pytest.raises(TunnelError) as captured:
            client.configure_ingress(definition, "tunnel", "https://localhost:39390", True, str(ca), expected_previous=expected)
        assert captured.value.code == "ingress_conflict"
    assert all(call.args[0] == "GET" for call in client._call.call_args_list)


def test_ingress_reuses_exact_configuration_without_remote_write(tmp_path):
    client = CloudflareClient("test-token")
    ca = tmp_path / "ca.pem"; ca.write_text("ca", encoding="utf-8")
    ingress = [{"hostname": "gateway.example.com", "service": "https://localhost:39390", "originRequest": {"caPool": str(ca), "noTLSVerify": False}}, {"service": "http_status:404"}]
    client._call = Mock(return_value={"config": {"ingress": ingress}})
    client.configure_ingress(TunnelDefinition("account", "zone", "gateway.example.com", "tunnel"), "tunnel", "https://localhost:39390", True, str(ca))
    assert client._call.call_count == 1


@pytest.mark.parametrize("ingress", [
    [{"hostname": "other.example.com", "service": "http://localhost:12"}],
    [{"hostname": "gateway.example.com", "path": "/other", "service": "http://localhost:12"}],
    [{"service": "http://localhost:12"}],
    [{"service": "http_status:404"}, {"hostname": "gateway.example.com", "service": "http://localhost:12"}],
    [None], "bad",
])
def test_ingress_never_discards_unrecognized_rules(tmp_path, ingress):
    client = CloudflareClient("test-token")
    ca = tmp_path / "ca.pem"; ca.write_text("ca", encoding="utf-8")
    client._call = Mock(return_value={"config": {"ingress": ingress}})
    with pytest.raises(TunnelError):
        client.configure_ingress(TunnelDefinition("account", "zone", "gateway.example.com", "tunnel"), "tunnel", "https://localhost:39390", True, str(ca))
    assert all(call.args[0] == "GET" for call in client._call.call_args_list)


def test_dns_reuses_compatible_record_without_writing():
    client = CloudflareClient("test-token")
    record = {"id": "record", "type": "CNAME", "content": "tunnel.cfargotunnel.com.", "proxied": True}
    client._call = Mock(return_value=[record])
    assert client.ensure_dns(TunnelDefinition("account", "zone", "gateway.example.com"), "tunnel", True) == record
    assert client._call.call_args.args[0] == "GET"
    assert client._call.call_count == 1


@pytest.mark.parametrize("records", [
    [{"id": "record", "type": "A", "content": "127.0.0.1", "proxied": True}],
    [{"id": "record", "type": "CNAME", "content": "other.cfargotunnel.com", "proxied": True}],
    [{"id": "record", "type": "CNAME", "content": "tunnel.cfargotunnel.com", "proxied": False}],
    [{"type": "CNAME"}, {"type": "CNAME"}],
])
def test_dns_conflict_never_writes(records):
    client = CloudflareClient("test-token"); client._call = Mock(return_value=records)
    with pytest.raises(TunnelError) as captured:
        client.ensure_dns(TunnelDefinition("account", "zone", "gateway.example.com"), "tunnel", True)
    assert captured.value.code == "dns_conflict"
    assert client._call.call_count == 1


def test_dns_creation_and_owned_removal_are_explicit():
    client = CloudflareClient("test-token")
    record = {"id": "record", "type": "CNAME", "content": "tunnel.cfargotunnel.com", "proxied": True}
    client._call = Mock(side_effect=[[], record])
    definition = TunnelDefinition("account", "zone", "gateway.example.com", "tunnel")
    assert client.ensure_dns(definition, "tunnel", True) == record
    assert client._call.call_args.args[0] == "POST"
    client._call = Mock(side_effect=[[record], {"id": "record"}])
    client.delete_managed_dns(definition, "tunnel", "record", True)
    assert client._call.call_args.args[:2] == ("DELETE", "/zones/zone/dns_records/record")
    client._call = Mock(return_value=[{**record, "id": "another"}])
    with pytest.raises(TunnelError):
        client.delete_managed_dns(definition, "tunnel", "record", True)
    assert client._call.call_count == 1


def test_cloudflare_creation_does_not_adopt_named_tunnel_implicitly():
    client = CloudflareClient("test-token"); client._call = Mock(return_value=[{"id": "existing"}])
    with pytest.raises(TunnelError) as captured:
        client.create_tunnel(TunnelDefinition("account", "zone", "gateway.example.com"), True)
    assert captured.value.code == "tunnel_name_conflict"
    client._call.assert_called_once()


def test_cloudflare_runtime_token_only_comes_from_token_endpoint():
    client = CloudflareClient("api-token"); client._call = Mock(return_value="runtime-token")
    assert client.tunnel_token("account", "tunnel") == "runtime-token"
    client._call.assert_called_once_with("GET", "/accounts/account/cfd_tunnel/tunnel/token")
    client._call.return_value = None
    with pytest.raises(TunnelError):
        client.tunnel_token("account", "tunnel")


def test_client_errors_never_include_remote_response_secrets(monkeypatch):
    client = CloudflareClient("api-token")
    error = urllib.error.HTTPError("https://api.cloudflare.com", 403, "token=private", {}, None)
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.client.urllib.request.urlopen", Mock(side_effect=error))
    with pytest.raises(TunnelError) as captured:
        client.status("account", "tunnel")
    assert "403" in str(captured.value)
    assert "private" not in str(captured.value)


def test_process_passes_runtime_token_only_in_child_environment(tmp_path, monkeypatch):
    binary = tmp_path / "cloudflared.exe"; binary.touch()
    received = {}
    child = SimpleNamespace(stdout=iter(()), poll=lambda: None, pid=123)
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.process.subprocess.Popen", lambda command, **kwargs: received.update(command=command, **kwargs) or child)
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.process.time.sleep", lambda _: None)
    monkeypatch.setenv("ONMYOJI_SUPERVISOR_TOKEN", "rpc-private")
    monkeypatch.setenv("OPENAI_API_KEY", "api-private")
    monkeypatch.setenv("TUNNEL_TOKEN_FILE", "private-token-file")
    monkeypatch.setenv("TUNNEL_CONFIG", "unexpected-config")
    process = TunnelProcess(str(binary), "runtime-private")
    process.start(); process.start()
    assert "runtime-private" not in str(received["command"])
    assert received["env"]["TUNNEL_TOKEN"] == "runtime-private"
    assert not {"ONMYOJI_SUPERVISOR_TOKEN", "OPENAI_API_KEY", "TUNNEL_TOKEN_FILE", "TUNNEL_CONFIG"} & received["env"].keys()
    assert "--no-autoupdate" in received["command"]
    assert process._safe_output("ERR https://example.com/oauth?code=private state=private token=runtime-private") == "ERR [url redacted] state=[redacted] token=[redacted]"


def test_process_failure_is_sanitized_and_reports_separate_readiness(tmp_path, monkeypatch):
    binary = tmp_path / "cloudflared.exe"; binary.touch()
    child = SimpleNamespace(stdout=iter([" ERR https://example.com?code=private token=runtime-private\n"]), poll=lambda: 1, returncode=1)
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.process.subprocess.Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.process.time.sleep", lambda _: None)
    process = TunnelProcess(str(binary), "runtime-private")
    with pytest.raises(TunnelError):
        process.start()
    assert process.status()["running"] is False
    assert process.status()["connected"] is False
    assert "private" not in str(process.status())


def test_operations_reject_concurrent_mutations_and_sanitize_unknown_failure(tmp_path):
    manager = TunnelOperations(tmp_path, tmp_path / "configs" / "tunnel")
    entered, release = threading.Event(), threading.Event()
    def work(progress):
        progress("verificando")
        entered.set()
        release.wait(3)
        raise RuntimeError("token=private")
    operation = manager.start("install", work)
    assert entered.wait(2)
    with pytest.raises(TunnelError) as captured:
        manager.start("other", lambda _: None)
    assert captured.value.code == "operation_in_progress"
    release.set()
    result = wait_operation(manager, operation)
    assert result["state"] == "failed" and result["error_code"] == "operation_failed"
    assert "private" not in manager.path.read_text(encoding="utf-8")
    assert not list((tmp_path / "tmp").iterdir())


def test_operations_mark_unfinished_work_interrupted_after_restart(tmp_path):
    path = tmp_path / "configs" / "tunnel" / "operations.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "operations": {"old": {"operation_id": "old", "state": "running"}}}), encoding="utf-8")
    manager = TunnelOperations(tmp_path, path.parent)
    assert manager.status("old")["state"] == "interrupted"
    with pytest.raises(TunnelError) as captured:
        manager.status("missing")
    assert captured.value.code == "operation_not_found"
    manager.close()
    with pytest.raises(TunnelError):
        manager.start("late", lambda _: None)


def test_operations_succeed_and_preserve_result_after_restart(tmp_path):
    manager = TunnelOperations(tmp_path, tmp_path / "configs" / "tunnel")
    operation = manager.start("configure", lambda progress: {"configured": True})
    result = wait_operation(manager, operation)
    assert result["state"] == "succeeded" and result["result"] == {"configured": True}
    assert TunnelOperations(tmp_path, manager.path.parent).status(operation["operation_id"]) == result
