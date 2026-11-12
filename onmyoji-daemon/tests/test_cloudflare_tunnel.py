"""Contratos de configuração, Cloudflare, processo e operações do Tunnel."""
from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time
from unittest.mock import MagicMock, Mock
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


@pytest.mark.parametrize("values", [None, {"token": "private"}, {"account_id": "account"}, {"hostname": "gateway.example.com"}, {"cloudflared_executable": ""}, {"managed_version": "invalid"}, {"managed_digest": "sha256:invalid"}, {"install_directory": "relative"}, {"previous_executable": None}])
def test_executable_metadata_rejects_identity_secrets_and_invalid_fields(tmp_path, values):
    resolver = Mock(side_effect=AssertionError("O instalador não consulta credenciais."))
    config = TunnelConfiguration(tmp_path, credentials=resolver)
    with pytest.raises(TunnelError):
        config.configure_executable(values)
    assert not config.path.exists()


def test_executable_metadata_does_not_relax_full_tunnel_configuration_validation(tmp_path):
    resolver = Mock()
    config = TunnelConfiguration(tmp_path, credentials=resolver)
    config.configure_executable({"cloudflared_executable": "cloudflared", "managed_version": "2026.10.7"})
    before = config.path.read_bytes()
    assert not config.is_configured(config.settings())
    with pytest.raises(TunnelError) as failure:
        config.configure({"hostname": "gateway.example.com"})
    assert failure.value.code == "incomplete_config"
    assert config.path.read_bytes() == before
    resolver.password_exists.assert_not_called()


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



@pytest.mark.parametrize("remote", [
    {}, {"config": None}, {"config": {}},
    {"config": {"ingress": None}}, {"config": {"ingress": []}},
])
def test_unconfigured_remote_tunnel_accepts_first_ingress_with_safe_fallback(tmp_path, remote):
    client = CloudflareClient("test-token")
    ca = tmp_path / "ca.pem"; ca.write_text("ca", encoding="utf-8")
    client._call = Mock(side_effect=lambda method, path, body=None: remote if method == "GET" else {})
    definition = TunnelDefinition("account", "zone", "gateway.example.com", "tunnel")
    client.configure_ingress(definition, "tunnel", "https://localhost:39390", True, str(ca))
    assert [call.args[0] for call in client._call.call_args_list] == ["GET", "PUT"]
    saved = client._call.call_args_list[-1].args[2]["config"]["ingress"]
    assert saved == [
        {"hostname": "gateway.example.com", "service": "https://localhost:39390",
         "originRequest": {"caPool": str(ca), "noTLSVerify": False}},
        {"service": "http_status:404"},
    ]


def test_malformed_remote_config_is_rejected_without_write(tmp_path):
    client = CloudflareClient("test-token")
    ca = tmp_path / "ca.pem"; ca.write_text("ca", encoding="utf-8")
    client._call = Mock(return_value={"config": "invalid"})
    with pytest.raises(TunnelError) as caught:
        client.configure_ingress(TunnelDefinition("account", "zone", "gateway.example.com", "tunnel"),
                                 "tunnel", "https://localhost:39390", True, str(ca))
    assert caught.value.code == "invalid_ingress"
    client._call.assert_called_once()


@pytest.mark.parametrize("remote,location", [
    ({"config": "private-password"}, "config é texto"),
    ({"config": {"ingress": {"service": "http_status:404"}}}, "config.ingress é objeto"),
    ({"config": {"ingress": [{"service": "http_status:404"}, "private-password"]}}, "config.ingress[1] é texto"),
    ({"config": {"ingress": [{"hostname": "gateway.example.com"}]}}, "config.ingress[0].service é ausente"),
    ({"config": {"ingress": [{"service": False}]}}, "config.ingress[0].service é booleano"),
    ({"config": {"ingress": [{"service": ""}]}}, "config.ingress[0].service é texto vazio"),
])
def test_invalid_remote_ingress_reports_safe_location_without_writing(tmp_path, remote, location):
    client = CloudflareClient("test-token")
    ca = tmp_path / "ca.pem"; ca.write_text("ca", encoding="utf-8")
    client._call = Mock(return_value=remote)
    with pytest.raises(TunnelError) as caught:
        client.configure_ingress(TunnelDefinition("account", "zone", "gateway.example.com", "tunnel"),
                                 "tunnel", "https://localhost:39390", True, str(ca))
    assert caught.value.code == "invalid_ingress"
    assert location in str(caught.value) and "private-password" not in str(caught.value)
    client._call.assert_called_once()
    assert client._call.call_args.args[0] == "GET"


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


def test_dns_create_81053_compares_filtered_and_unfiltered_zone_without_leaking_values():
    from onmyoji_daemon.cloudflare_tunnel.client import CloudflareHTTPError

    client = CloudflareClient("api-private")
    calls = []
    def call(method, path, body=None):
        calls.append((method, path))
        if method == "POST":
            raise CloudflareHTTPError(400, method, path, {"errors": [{"code": 81053, "message": "token=secret"}]})
        if path == "/zones/zone":
            return {"name": "example.com"}
        if "name=" in path:
            return []
        return [{"name": "gateway.example.com", "type": "A", "content": "private-dns-value"}]
    client._call = call
    with pytest.raises(TunnelError) as caught:
        client.ensure_dns(TunnelDefinition("account", "zone", "gateway.example.com"), "tunnel", True)
    message = str(caught.value)
    assert caught.value.code == "dns_conflict"
    assert "81053" in message and "tipos: A" in message and "não retornados pelo filtro" in message
    assert "private" not in message and "secret" not in message
    assert len([method for method, _ in calls if method == "POST"]) == 1
    assert any("page=1" in path for method, path in calls if method == "GET")


def test_dns_create_81053_nested_error_chain_and_no_visible_dns_is_not_misdiagnosed():
    from onmyoji_daemon.cloudflare_tunnel.client import CloudflareHTTPError

    client = CloudflareClient("api-private")
    calls = []
    def call(method, path, body=None):
        calls.append(method)
        if method == "POST":
            raise CloudflareHTTPError(400, method, path, {"errors": [
                {"code": 1004, "message": "password=private", "error_chain": [{"code": 81053, "message": "secret"}]}]})
        if path == "/zones/zone":
            return {"name": "example.com"}
        return []
    client._call = call
    with pytest.raises(TunnelError) as caught:
        client.ensure_dns(TunnelDefinition("account", "zone", "gateway.example.com"), "tunnel", True)
    assert caught.value.code == "dns_conflict"
    assert "81053" in str(caught.value) and "Listagem completa da zona não encontrou" in str(caught.value)
    assert "private" not in str(caught.value) and "secret" not in str(caught.value)
    assert calls.count("POST") == 1


def test_dns_create_81053_detects_wrong_zone_and_handles_forbidden_diagnostic():
    from onmyoji_daemon.cloudflare_tunnel.client import CloudflareHTTPError

    definition = TunnelDefinition("account", "zone", "gateway.example.com")
    for zone_response in ({"name": "different.org"}, CloudflareHTTPError(403, "GET", "/zones/zone", None)):
        client = CloudflareClient("api-private")
        def call(method, path, body=None):
            if method == "POST":
                raise CloudflareHTTPError(400, method, path, {"errors": [{"code": "81053"}]})
            if path == "/zones/zone":
                if isinstance(zone_response, Exception):
                    raise zone_response
                return zone_response
            return []
        client._call = call
        with pytest.raises(TunnelError) as caught:
            client.ensure_dns(definition, "tunnel", True)
        assert caught.value.code == "dns_conflict"
        if isinstance(zone_response, Exception):
            assert "Zone Read" in str(caught.value)
        else:
            assert "outro domínio" in str(caught.value)


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



@pytest.mark.parametrize("path,expected", [
    ("/zones/private-zone/dns_records?name=token-private", "/zones/{zone_id}/dns_records"),
    ("/accounts/private-account/cfd_tunnel?name=private", "/accounts/{account_id}/cfd_tunnel"),
])
def test_cloudflare_http_error_reports_endpoint_and_numeric_codes_only(monkeypatch, path, expected):
    client = CloudflareClient("api-private")
    payload = json.dumps({"success": False, "errors": [
        {"code": 9109, "message": "token=private-do-not-print"},
        {"code": "10000", "message": "password=private-do-not-print"},
        {"code": "private-key", "message": "secret"}]}).encode()
    error = urllib.error.HTTPError("https://api.cloudflare.com?private=secret", 403, "token=private", {}, io.BytesIO(payload))
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.client.urllib.request.urlopen", Mock(side_effect=error))
    with pytest.raises(TunnelError) as caught:
        client._call("GET", path)
    message = str(caught.value)
    assert caught.value.code == "cloudflare_http_error"
    assert expected in message and "HTTP 403" in message
    assert "9109" in message and "10000" in message
    assert "private" not in message and "secret" not in message
    if "/zones/" in path:
        assert "DNS > Edit" in message
    else:
        assert "Cloudflare Tunnel > Edit" in message


def test_cloudflare_response_rejection_only_exposes_numeric_codes(monkeypatch):
    client = CloudflareClient("api-private")
    reply = {"success": False, "errors": [{"code": 9999, "message": "token=private"}]}
    response = MagicMock()
    response.read.return_value = json.dumps(reply).encode()
    response.__enter__.return_value = response
    monkeypatch.setattr("onmyoji_daemon.cloudflare_tunnel.client.urllib.request.urlopen", Mock(return_value=response))
    with pytest.raises(TunnelError) as caught:
        client._call("GET", "/zones/zone/dns_records?token=private")
    assert caught.value.code == "cloudflare_rejected"
    assert "9999" in str(caught.value) and "private" not in str(caught.value)


def test_cloudflare_token_verification_falls_back_to_account_tokens():
    client = CloudflareClient("api-private")
    calls = []
    def call(method, path):
        calls.append((method, path))
        if path == "/user/tokens/verify":
            from onmyoji_daemon.cloudflare_tunnel.client import CloudflareHTTPError
            raise CloudflareHTTPError(403, method, path, {"errors": [{"code": 10000}]})
        return {"status": "active"}
    client._call = call
    assert client.verify_token("account") == "active"
    assert calls == [("GET", "/user/tokens/verify"), ("GET", "/accounts/account/tokens/verify")]


@pytest.mark.parametrize("status", ["expired", "disabled"])
def test_cloudflare_token_inactive_is_rejected_before_resource_calls(status):
    client = CloudflareClient("api-private")
    client._call = Mock(return_value={"status": status})
    with pytest.raises(TunnelError) as caught:
        client.preflight_access(TunnelDefinition("account", "zone", "gateway.example.com"), lambda _: None)
    assert caught.value.code == "cloudflare_token_inactive"
    assert client._call.call_count == 1


def test_cloudflare_preflight_checks_both_resources_despite_dns_forbidden():
    client = CloudflareClient("api-private")
    calls = []
    def call(method, path):
        calls.append((method, path))
        if path.endswith("/tokens/verify"):
            return {"status": "active"}
        if "/dns_records" in path:
            from onmyoji_daemon.cloudflare_tunnel.client import CloudflareHTTPError
            raise CloudflareHTTPError(403, method, path, {"errors": [{"code": 9109}]})
        if "/cfd_tunnel" in path:
            return []
        raise AssertionError(path)
    client._call = call
    with pytest.raises(TunnelError) as caught:
        client.preflight_access(TunnelDefinition("account", "zone", "gateway.example.com"), lambda _: None)
    assert caught.value.code == "cloudflare_access_failed"
    assert "DNS: FALHOU" in str(caught.value) and "Tunnels: OK" in str(caught.value)
    assert "HTTP 403" in str(caught.value) and "DNS > Edit" in str(caught.value)
    assert all(method == "GET" for method, _ in calls)
    assert len(calls) == 3


def test_cloudflare_preflight_reports_independent_tunnel_permission_failure():
    client = CloudflareClient("api-private")
    def call(method, path):
        if path.endswith("/tokens/verify"):
            return {"status": "active"}
        if "/dns_records" in path:
            return []
        if "/cfd_tunnel" in path:
            from onmyoji_daemon.cloudflare_tunnel.client import CloudflareHTTPError
            raise CloudflareHTTPError(403, method, path, {"errors": [{"code": 10000}]})
        raise AssertionError(path)
    client._call = call
    with pytest.raises(TunnelError) as caught:
        client.preflight_access(TunnelDefinition("account", "zone", "gateway.example.com"), lambda _: None)
    assert caught.value.code == "cloudflare_access_failed"
    assert "DNS: OK" in str(caught.value) and "Tunnels: FALHOU" in str(caught.value)
    assert "Cloudflare Tunnel > Edit" in str(caught.value)


def test_cloudflare_preflight_treats_denied_verify_endpoints_as_inconclusive():
    client = CloudflareClient("api-private")
    def call(method, path):
        if path.endswith("/tokens/verify"):
            from onmyoji_daemon.cloudflare_tunnel.client import CloudflareHTTPError
            raise CloudflareHTTPError(403, method, path, None)
        return []
    client._call = call
    dns, tunnels, token_status = client.preflight_access(TunnelDefinition("account", "zone", "gateway.example.com"), lambda _: None)
    assert (dns, tunnels, token_status) == ([], [], "unverified")


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
