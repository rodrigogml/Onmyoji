"""Contrato CLI do teste público temporário, sem acessar o domínio real."""
from __future__ import annotations

import json

import pytest

from onmyoji_daemon import cli
from onmyoji_daemon.rpc import RpcError


@pytest.mark.parametrize("confirmed", [False, True])
def test_start_preserves_explicit_confirmation(monkeypatch, capsys, confirmed):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "rpc-private"))
    calls = []
    result = {"test_id": "attempt-1", "url": "https://app.example.com/internal/publication-test/transient-nonce", "state": "waiting", "expires_in_seconds": 120}
    monkeypatch.setattr(cli, "call", lambda *args, **_kwargs: calls.append((args[3], args[4])) or result)

    command = ["--onmyoji-root", ".", "gateway", "publication-test", "start"]
    if confirmed:
        command.append("--confirm")
    assert cli.main(command) == 0
    assert calls == [("gateway.publication-test.start", {"confirm": confirmed})]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == result
    assert "rpc-private" not in captured.out + captured.err


@pytest.mark.parametrize("action,state", [("status", "waiting"), ("status", "confirmed"), ("status", "expired"), ("cancel", "cancelled")])
def test_attempt_commands_preserve_identity(monkeypatch, capsys, action, state):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "rpc-private"))
    calls = []
    result = {"test_id": "attempt-1", "state": state}
    monkeypatch.setattr(cli, "call", lambda *args, **_kwargs: calls.append((args[3], args[4])) or result)

    assert cli.main(["--onmyoji-root", ".", "gateway", "publication-test", action, "attempt-1"]) == 0
    assert calls == [("gateway.publication-test." + action, {"test_id": "attempt-1"})]
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize("action", ["status", "cancel"])
def test_attempt_id_is_required_before_contacting_daemon(monkeypatch, capsys, action):
    contacted = []
    monkeypatch.setattr(cli, "endpoint", lambda _root: contacted.append(True))
    with pytest.raises(SystemExit) as error:
        cli.main(["--onmyoji-root", ".", "gateway", "publication-test", action])
    assert error.value.code == 2
    assert not contacted
    assert "test_id" in capsys.readouterr().err


def test_unready_publication_reports_domain_error_without_private_details(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "rpc-private"))
    def rpc(*_args, **_kwargs):
        raise RpcError("Inicie Gateway e Tunnel antes de testar.", "publication_unavailable", {"token": "rpc-private"})
    monkeypatch.setattr(cli, "call", rpc)

    assert cli.main(["--onmyoji-root", ".", "gateway", "publication-test", "start", "--confirm"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {"error": {"code": "publication_unavailable", "message": "Inicie Gateway e Tunnel antes de testar."}}
    assert "rpc-private" not in captured.out + captured.err
