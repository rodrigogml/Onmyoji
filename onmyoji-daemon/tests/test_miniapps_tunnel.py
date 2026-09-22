from __future__ import annotations

import pytest
import sys
import time

import onmyoji_daemon.miniapps_tunnel as module
from onmyoji_daemon.miniapps_tunnel import CloudflareClient, TunnelController, TunnelDefinition, TunnelError, TunnelProcess


def test_tunnel_definition_requires_account_zone_and_hostname():
    with pytest.raises(TunnelError): TunnelDefinition("", "zone", "apps.example.com").validate()
    with pytest.raises(TunnelError): TunnelDefinition("account", "zone", "localhost").validate()


def test_cloudflare_writes_require_explicit_confirmation():
    client = CloudflareClient("test-token")
    definition = TunnelDefinition("account", "zone", "apps.example.com")
    with pytest.raises(TunnelError): client.create_tunnel(definition, False)
    with pytest.raises(TunnelError): client.configure_ingress(definition, "tunnel", "https://localhost:39391", False)
    with pytest.raises(TunnelError): client.ensure_dns(definition, "tunnel", False)


def test_cloudflare_returns_the_runtime_token_only_from_its_tunnel_endpoint(monkeypatch):
    client = CloudflareClient("api-token")
    received = {}
    monkeypatch.setattr(client, "_call", lambda method, path, body=None: received.update(method=method, path=path) or "runtime-token")

    assert client.tunnel_token("account", "tunnel") == "runtime-token"
    assert received == {"method": "GET", "path": "/accounts/account/cfd_tunnel/tunnel/token"}


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


def test_tunnel_process_reports_sanitized_startup_failure():
    process = TunnelProcess(sys.executable, "private-token")
    with pytest.raises(TunnelError): process.start()
    time.sleep(0.05)
    assert "private-token" not in str(process.status())
    assert process.status()["last_error"]


def test_tunnel_controller_uses_a_runtime_token_without_persisting_it(tmp_path, monkeypatch):
    controller = TunnelController(tmp_path, tmp_path / "state", "https://localhost:39391")
    controller.configure({"account_id": "account", "zone_id": "zone", "hostname": "apps.example.com", "tunnel_id": "tunnel", "keepass_profile": "local", "token_entry": "APIs/Tunnel", "cloudflared_executable": "cloudflared"})
    monkeypatch.setattr(controller, "_token", lambda _settings: "api-token")
    received = {}
    class Client:
        def __init__(self, token): received["api_token"] = token
        def tunnel_token(self, account_id, tunnel_id): received.update(account_id=account_id, tunnel_id=tunnel_id); return "runtime-token"
    class Process:
        def __init__(self, executable, token): received.update(executable=executable, runtime_token=token)
        def start(self): pass
        def status(self): return {"running": True, "pid": 1, "last_error": None}
        def stop(self): pass
    monkeypatch.setattr(module, "CloudflareClient", Client)
    monkeypatch.setattr(module, "TunnelProcess", Process)

    assert controller.start()["running"]
    assert received == {"api_token": "api-token", "account_id": "account", "tunnel_id": "tunnel", "executable": "cloudflared", "runtime_token": "runtime-token"}
