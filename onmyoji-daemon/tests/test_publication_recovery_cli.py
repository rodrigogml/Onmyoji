"""Acompanhamento do diagnóstico remoto e das recuperações confirmadas pelo CLI."""
from __future__ import annotations

import json

import pytest

from onmyoji_daemon import cli
from onmyoji_daemon.mcp.publication.tools import PublicationMcpError, PublicationToolRegistry
from onmyoji_daemon.rpc import RpcError


def test_inspection_waits_with_progress_and_does_not_request_remote_writes(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "rpc-private"))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    calls = []
    preview = {"status": "existing", "hostname": "tsuki.example.com", "tunnel_id": "existing-tunnel", "preview_id": "a" * 32}
    def rpc(_host, _port, _token, method, params, **_kwargs):
        calls.append((method, params))
        if method == "cloudflare-tunnel.operations.status":
            return {"operation_id": "inspection-1", "state": "succeeded", "result": preview}
        return {"operation_id": "inspection-1", "state": "running", "progress": "consultando DNS e Tunnel existentes"}
    monkeypatch.setattr(cli, "call", rpc)

    assert cli.main(["--onmyoji-root", ".", "cloudflare-tunnel", "inspect-publication", "--wait"]) == 0
    assert calls == [("cloudflare-tunnel.inspect-publication", {}), ("cloudflare-tunnel.operations.status", {"operation_id": "inspection-1"})]
    captured = capsys.readouterr()
    assert "consultando DNS e Tunnel existentes" in captured.err
    assert json.loads(captured.out)["result"] == preview
    assert "rpc-private" not in captured.out + captured.err


@pytest.mark.parametrize("action", ["adopt-publication", "rollback-publication"])
def test_recovery_passes_exact_preview_and_explicit_confirmation(monkeypatch, capsys, action):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "rpc-private"))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    calls = []
    def rpc(_host, _port, _token, method, params, **_kwargs):
        calls.append((method, params))
        return {"operation_id": "recovery-1", "state": "succeeded" if method.endswith("operations.status") else "queued"}
    monkeypatch.setattr(cli, "call", rpc)

    assert cli.main(["--onmyoji-root", ".", "cloudflare-tunnel", action, "--preview-id", "a" * 32, "--confirm", "--wait"]) == 0
    assert calls == [("cloudflare-tunnel." + action, {"preview_id": "a" * 32, "confirm": True}), ("cloudflare-tunnel.operations.status", {"operation_id": "recovery-1"})]
    assert json.loads(capsys.readouterr().out)["state"] == "succeeded"


def test_recovery_without_confirmation_does_not_synthesize_approval(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "rpc-private"))
    calls = []
    def rpc(_host, _port, _token, method, params, **_kwargs):
        calls.append((method, params))
        raise RpcError("Confirme a reutilização da publicação.", "confirmation_required")
    monkeypatch.setattr(cli, "call", rpc)

    assert cli.main(["--onmyoji-root", ".", "cloudflare-tunnel", "adopt-publication", "--preview-id", "a" * 32]) == 2
    assert calls == [("cloudflare-tunnel.adopt-publication", {"preview_id": "a" * 32, "confirm": False})]
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "confirmation_required"


def test_failed_remote_inspection_preserves_stage_code_and_action(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "rpc-private"))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    failure = {"operation_id": "inspection-1", "state": "failed", "progress": "consultando DNS", "error_code": "cloudflare_http_error", "error": "Cloudflare retornou HTTP 403. Confira DNS > Edit no API Token."}
    monkeypatch.setattr(cli, "call", lambda *_args, **_kwargs: failure)

    assert cli.main(["--onmyoji-root", ".", "cloudflare-tunnel", "inspect-publication", "--wait"]) == 2
    assert json.loads(capsys.readouterr().out) == failure


def test_recovery_requires_preview_argument_before_accessing_daemon(monkeypatch, capsys):
    contacted = []
    monkeypatch.setattr(cli, "endpoint", lambda _root: contacted.append(True))
    with pytest.raises(SystemExit) as error:
        cli.main(["--onmyoji-root", ".", "cloudflare-tunnel", "adopt-publication", "--confirm"])
    assert error.value.code == 2
    assert not contacted
    assert "--preview-id" in capsys.readouterr().err


def test_wait_reports_activity_when_cloudflare_keeps_the_same_stage(monkeypatch, capsys):
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    moments = iter([0, 0, 16])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(moments))
    operation = {"operation_id": "inspection-1", "state": "running", "progress": "consultando DNS"}
    responses = iter([operation, {"operation_id": "inspection-1", "state": "succeeded"}])
    monkeypatch.setattr(cli, "call", lambda *_args, **_kwargs: next(responses))

    assert cli._wait_publication_operation("127.0.0.1", 1234, "private", operation)["state"] == "succeeded"
    assert "Ainda em andamento: consultando DNS" in capsys.readouterr().err


def test_mcp_discovery_is_read_only_and_recovery_requires_confirmation(tmp_path):
    registry = PublicationToolRegistry(tmp_path)
    definitions = {item["name"]: item for item in registry.definitions()}
    assert definitions["cloudflare_tunnel.inspect_publication"]["input_schema"]["required"] == []
    for action in ("adopt_publication", "rollback_publication"):
        schema = definitions["cloudflare_tunnel." + action]["input_schema"]
        assert set(schema["required"]) == {"preview_id", "confirm"}
        assert schema["properties"]["confirm"] == {"type": "boolean", "const": True}
        with pytest.raises(PublicationMcpError, match="confirm=true"):
            registry.call("cloudflare_tunnel." + action, {"preview_id": "a" * 32})


@pytest.mark.parametrize("action", ["inspect_publication", "adopt_publication", "rollback_publication"])
def test_mcp_forwards_recovery_method_and_preview_without_leaking_transport(tmp_path, action):
    calls = []
    registry = PublicationToolRegistry(tmp_path, rpc=lambda *args, **_kwargs: calls.append((args[3], args[4])) or {"operation_id": "queued-1", "state": "queued"}, endpoint_reader=lambda _root: ("127.0.0.1", 1234, "private"))
    arguments = {} if action == "inspect_publication" else {"preview_id": "a" * 32, "confirm": True}
    result = registry.call("cloudflare_tunnel." + action, arguments)
    assert calls == [("cloudflare-tunnel." + action.replace("_", "-"), arguments)]
    assert result == {"operation_id": "queued-1", "state": "queued"}
