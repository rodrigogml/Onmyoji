"""Contratos administrativos do Gateway e Tunnel independentes de Mini Apps."""
from __future__ import annotations

import json

import pytest

from onmyoji_daemon import cli
from onmyoji_daemon.mcp.publication.tools import PublicationMcpError, PublicationToolRegistry
from onmyoji_daemon.rpc import RpcError


def test_cli_removes_legacy_tunnel_subcommand():
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--onmyoji-root", ".", "mini-apps", "tunnel", "status"])
    assert stopped.value.code == 2


def test_cli_gateway_configuration_includes_confirmation(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "private"))
    calls = []
    monkeypatch.setattr(cli, "call", lambda *args, **kwargs: calls.append((args[3], args[4])) or {"restart_required": True})
    assert cli.main(["--onmyoji-root", ".", "gateway", "configure", "--values", '{"http_port":39400}', "--confirm"]) == 0
    assert calls == [("gateway.configure", {"http_port": 39400, "confirm": True})]
    assert "private" not in capsys.readouterr().out


@pytest.mark.parametrize("values", ["null", "[]", "{invalid"])
def test_cli_rejects_invalid_gateway_configuration_before_rpc(monkeypatch, values, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "private"))
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--onmyoji-root", ".", "gateway", "configure", "--values", values])
    assert stopped.value.code == 2
    assert "Traceback" not in capsys.readouterr().err


def test_cli_waits_for_installation_and_preserves_selected_directory(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "private"))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    calls = []
    def rpc(_host, _port, _token, method, params, **_kwargs):
        calls.append((method, params))
        if method.endswith("operations.status"):
            return {"operation_id": "install-1", "state": "succeeded", "result": {"version": "2026.10.7"}}
        return {"operation_id": "install-1", "state": "running", "progress": "validando executável"}
    monkeypatch.setattr(cli, "call", rpc)
    assert cli.main(["--onmyoji-root", ".", "cloudflare-tunnel", "install-cloudflared", "--install-directory", "C:/Tools/Cloudflared", "--version", "2026.10.7", "--adopt", "--confirm", "--wait"]) == 0
    assert calls[0] == ("cloudflare-tunnel.install-cloudflared", {"confirm": True, "install_directory": "C:/Tools/Cloudflared", "version": "2026.10.7", "adopt": True})
    assert calls[1] == ("cloudflare-tunnel.operations.status", {"operation_id": "install-1"})
    captured = capsys.readouterr()
    assert "validando executável" in captured.err
    assert json.loads(captured.out)["state"] == "succeeded"


def test_cli_selects_existing_binary_through_serialized_operation(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "private"))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    calls = []
    def rpc(_host, _port, _token, method, params, **_kwargs):
        calls.append((method, params))
        return {"operation_id": "select-1", "state": "succeeded" if method.endswith("operations.status") else "queued"}
    monkeypatch.setattr(cli, "call", rpc)

    assert cli.main(["--onmyoji-root", ".", "cloudflare-tunnel", "use-cloudflared", "--executable", "C:/Tools/cloudflared.exe", "--confirm", "--wait"]) == 0
    assert calls == [("cloudflare-tunnel.use-cloudflared", {"confirm": True, "executable": "C:/Tools/cloudflared.exe"}), ("cloudflare-tunnel.operations.status", {"operation_id": "select-1"})]
    assert "private" not in capsys.readouterr().out


def test_mcp_separates_namespaces_and_exposes_installation_options(tmp_path):
    tools = {item["name"]: item for item in PublicationToolRegistry(tmp_path).definitions()}
    assert {"gateway.status", "gateway.listeners_restore", "cloudflare_tunnel.provision", "cloudflare_tunnel.operation_status"} <= tools.keys()
    assert not any(name.startswith("cloudflare.") for name in tools)
    assert "install_directory" in tools["cloudflare_tunnel.install_cloudflared"]["input_schema"]["properties"]
    assert "confirm" in tools["gateway.configure"]["input_schema"]["required"]


def test_mcp_requires_confirmation_and_rejects_unknown_arguments(tmp_path):
    registry = PublicationToolRegistry(tmp_path)
    with pytest.raises(PublicationMcpError, match="confirm=true"):
        registry.call("gateway.listeners_remove", {"path_prefix": "a"})
    with pytest.raises(PublicationMcpError, match="desconhecidos"):
        registry.call("gateway.status", {"owner": "another"})


def test_mcp_configure_forwards_confirmation_and_preserves_service_identity(tmp_path):
    calls = []
    registry = PublicationToolRegistry(tmp_path, rpc=lambda *args, **_kwargs: calls.append((args[3], args[4])) or {"state": "queued"}, endpoint_reader=lambda _root: ("127.0.0.1", 1, "private"))
    registry.call("cloudflare_tunnel.configure", {"values": {"credential_entry_path": "APIs/Existing"}, "confirm": True})
    registry.call("gateway.restart", {"confirm": True})
    assert calls == [("cloudflare-tunnel.configure", {"credential_entry_path": "APIs/Existing", "confirm": True}), ("restart", {"service": "gateway"})]


def test_mcp_operation_errors_do_not_expose_transport_details(tmp_path):
    def rpc(*_args, **_kwargs): raise RuntimeError("token=private-code")
    registry = PublicationToolRegistry(tmp_path, rpc=rpc, endpoint_reader=lambda _root: ("127.0.0.1", 1, "private"))
    with pytest.raises(PublicationMcpError) as failed:
        registry.call("cloudflare_tunnel.status", {})
    assert "private" not in str(failed.value)


def test_mcp_preserves_structured_conflict_without_arbitrary_remote_details(tmp_path):
    def rpc(*_args, **_kwargs): raise RpcError("Namespace reservado.", "prefix_conflict", {"path_prefix": "a", "token": "must-not-return"})
    registry = PublicationToolRegistry(tmp_path, rpc=rpc, endpoint_reader=lambda _root: ("127.0.0.1", 1, "private"))
    with pytest.raises(PublicationMcpError) as failed:
        registry.call("gateway.listeners_restore", {"path_prefix": "a", "owner": "mini-apps", "confirm": True})
    assert failed.value.as_dict()["code"] == "prefix_conflict"
    assert failed.value.as_dict()["details"] == {"path_prefix": "a"}
    assert "must-not-return" not in str(failed.value.as_dict())


def test_cli_preserves_structured_domain_error(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "private"))
    monkeypatch.setattr(cli, "call", lambda *_args, **_kwargs: (_ for _ in ()).throw(RpcError("Namespace reservado.", "prefix_conflict", {"path_prefix": "a"})))
    assert cli.main(["--onmyoji-root", ".", "gateway", "listeners", "restore", "a", "--owner", "mini-apps", "--confirm"]) == 2
    assert json.loads(capsys.readouterr().err) == {"error": {"code": "prefix_conflict", "message": "Namespace reservado.", "details": {"path_prefix": "a"}}}


@pytest.mark.parametrize("state,exit_code", [("succeeded", 0), ("failed", 2), ("interrupted", 2)])
def test_cli_configures_running_tunnel_through_serialized_operation(monkeypatch, capsys, state, exit_code):
    from onmyoji_daemon.cloudflare_tunnel import TunnelConfiguration
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "private"))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    local_writes, calls = [], []
    monkeypatch.setattr(TunnelConfiguration, "configure", lambda _self, values: local_writes.append(values))
    def rpc(_host, _port, _token, method, params, **_kwargs):
        calls.append((method, params))
        if method == "status": return {"state": "running"}
        if method == "cloudflare-tunnel.configure": return {"operation_id": "config-1", "state": "queued"}
        return {"operation_id": "config-1", "state": state}
    monkeypatch.setattr(cli, "call", rpc)
    assert cli.main(["--onmyoji-root", ".", "cloudflare-tunnel", "configure", "--values", '{"credential_entry_path":"APIs/Existing"}']) == exit_code
    assert calls == [("status", {"service": "cloudflare-tunnel"}), ("cloudflare-tunnel.configure", {"credential_entry_path": "APIs/Existing", "confirm": True}), ("cloudflare-tunnel.operations.status", {"operation_id": "config-1"})]
    assert local_writes == []
    assert json.loads(capsys.readouterr().out)["state"] == state


def test_cli_busy_tunnel_never_falls_back_to_unserialized_write(monkeypatch, capsys):
    from onmyoji_daemon.cloudflare_tunnel import TunnelConfiguration
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "private"))
    local_writes = []
    monkeypatch.setattr(TunnelConfiguration, "configure", lambda _self, values: local_writes.append(values))
    def rpc(_host, _port, _token, method, params, **_kwargs):
        if method == "status": return {"state": "running"}
        raise RpcError("Já existe uma operação administrativa em execução.", "operation_in_progress")
    monkeypatch.setattr(cli, "call", rpc)
    assert cli.main(["--onmyoji-root", ".", "cloudflare-tunnel", "configure", "--values", '{}']) == 2
    assert local_writes == []
    assert "em execução" in capsys.readouterr().err


def test_cli_can_configure_tunnel_before_daemon_is_started(tmp_path, monkeypatch, capsys):
    from onmyoji_daemon.cloudflare_tunnel import TunnelConfiguration
    def absent(_root): raise FileNotFoundError()
    monkeypatch.setattr(cli, "endpoint", absent)
    received = []
    monkeypatch.setattr(TunnelConfiguration, "configure", lambda _self, values: received.append(values) or {"tunnel_id": "preserved"})
    assert cli.main(["--onmyoji-root", str(tmp_path), "cloudflare-tunnel", "configure", "--values", '{"hostname":"apps.example.com"}']) == 0
    assert received == [{"hostname": "apps.example.com"}]
    assert json.loads(capsys.readouterr().out)["tunnel_id"] == "preserved"
