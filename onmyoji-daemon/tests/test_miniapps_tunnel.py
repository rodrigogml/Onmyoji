from __future__ import annotations

import pytest

from onmyoji_daemon.miniapps_tunnel import CloudflareClient, TunnelController, TunnelDefinition, TunnelError


def test_tunnel_definition_requires_account_zone_and_hostname():
    with pytest.raises(TunnelError): TunnelDefinition("", "zone", "apps.example.com").validate()
    with pytest.raises(TunnelError): TunnelDefinition("account", "zone", "localhost").validate()


def test_cloudflare_writes_require_explicit_confirmation():
    client = CloudflareClient("test-token")
    definition = TunnelDefinition("account", "zone", "apps.example.com")
    with pytest.raises(TunnelError): client.create_tunnel(definition, False)
    with pytest.raises(TunnelError): client.configure_ingress(definition, "tunnel", "https://localhost:39391", False)
    with pytest.raises(TunnelError): client.ensure_dns(definition, "tunnel", False)


def test_tunnel_controller_persists_only_configuration_references(tmp_path):
    controller = TunnelController(tmp_path, tmp_path / "state", "https://localhost:39391")
    state = controller.configure({"account_id": "account", "zone_id": "zone", "hostname": "apps.example.com", "keepass_profile": "local", "token_entry": "APIs/Tunnel", "cloudflared_executable": "cloudflared"})
    assert state["configured"] and not state["provisioned"]
    saved = (tmp_path / "state" / "tunnel.toml").read_text(encoding="utf-8")
    assert "APIs/Tunnel" in saved and "token =" not in saved


def test_tunnel_configuration_uses_valid_toml_for_windows_paths(tmp_path):
    controller = TunnelController(tmp_path, tmp_path / "state", "https://localhost:39391")
    controller.configure({"account_id": "account", "zone_id": "zone", "hostname": "apps.example.com", "keepass_profile": "local\"profile", "token_entry": "APIs\\Tunnel", "cloudflared_executable": r"C:\\Program Files\\cloudflared.exe"})
    settings = controller.settings()
    assert settings["keepass_profile"] == 'local"profile'
    assert settings["token_entry"] == "APIs\\Tunnel"


def test_ingress_is_limited_to_local_tls_hop(monkeypatch):
    received = {}
    client = CloudflareClient("test-token")
    monkeypatch.setattr(client, "_call", lambda method, path, body=None: received.update(method=method, path=path, body=body))
    client.configure_ingress(TunnelDefinition("account", "zone", "apps.example.com"), "tunnel", "https://localhost:39391", True)
    ingress = received["body"]["config"]["ingress"][0]
    assert ingress["service"] == "https://localhost:39391"
    assert ingress["originRequest"] == {"noTLSVerify": True}


def test_cloudflared_download_urls_cover_windows_and_linux():
    assert TunnelController.cloudflared_download_url("Windows", "AMD64").endswith("cloudflared-windows-amd64.exe")
    assert TunnelController.cloudflared_download_url("Linux", "aarch64").endswith("cloudflared-linux-arm64")
