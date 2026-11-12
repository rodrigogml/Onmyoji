"""Confirmação do catálogo e prazos do RPC manual, sem Telegram ou espera real."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from onmyoji_daemon import cli
from onmyoji_daemon import supervisor as supervisor_module
from onmyoji_daemon.supervisor import Supervisor
from onmyoji_daemon.telegram_commands import command_catalog_revision, owner_commands, other_private_commands


def _current_summary(totp_enabled=False):
    return {
        "owners": 2,
        "verified": 2,
        "failed": 0,
        "expected": [item["command"] for item in owner_commands(totp_enabled)],
        "expected_other_private": [item["command"] for item in other_private_commands()],
        "other_private_verified": True,
        "catalog_revision": command_catalog_revision(totp_enabled),
        "totp_enabled": totp_enabled,
    }


def _stub_cli_rpc(monkeypatch, response):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 39900, "supervisor-token"))
    calls = []

    def transport(host, port, token, method, params, *, timeout):
        calls.append((host, port, token, method, params, timeout))
        return response

    monkeypatch.setattr(cli, "call", transport)
    return calls


@pytest.mark.parametrize("totp_enabled", [False, True])
def test_sync_cli_accepts_only_confirmed_current_catalog(tmp_path, monkeypatch, capsys, totp_enabled):
    response = _current_summary(totp_enabled)
    response["detail"] = "Confirmação de todos os proprietários"
    calls = _stub_cli_rpc(monkeypatch, response)

    result = cli.main(["--onmyoji-root", str(tmp_path), "telegram", "sync-commands"])

    assert result == 0
    assert json.loads(capsys.readouterr().out) == response
    assert calls == [("127.0.0.1", 39900, "supervisor-token", "telegram.sync-commands", {}, 60)]


@pytest.mark.parametrize("overrides,missing", [
    pytest.param({"verified": 1, "failed": 1}, None, id="partial-publication"),
    pytest.param({"owners": 0, "verified": 0}, None, id="no-owners"),
    pytest.param({"owners": "2"}, None, id="malformed-count"),
    pytest.param({"expected": ["new", "config", "channel", "usage"]}, None, id="old-command-list"),
    pytest.param({"catalog_revision": "previous-catalog"}, None, id="old-revision"),
    pytest.param({"other_private_verified": False}, None, id="unconfirmed-other-private-menu"),
    pytest.param({"expected_other_private": ["new"]}, None, id="outdated-other-private-menu"),
    pytest.param({"pending": True}, None, id="pending-publication"),
    pytest.param({"in_progress": True}, None, id="publication-in-progress"),
    pytest.param({}, "totp_enabled", id="missing-totp-mode"),
    pytest.param({"totp_enabled": 0}, None, id="non-boolean-totp-mode"),
])
def test_sync_cli_rejects_incomplete_or_outdated_receipts(tmp_path, monkeypatch, capsys, overrides, missing):
    response = _current_summary()
    response.update(overrides)
    if missing is not None:
        del response[missing]
    _stub_cli_rpc(monkeypatch, response)

    result = cli.main(["--onmyoji-root", str(tmp_path), "telegram", "sync-commands"])

    assert result == 2
    assert json.loads(capsys.readouterr().out) == response


@pytest.mark.parametrize("response", [None, []])
def test_sync_cli_rejects_non_object_receipts(tmp_path, monkeypatch, capsys, response):
    _stub_cli_rpc(monkeypatch, response)

    result = cli.main(["--onmyoji-root", str(tmp_path), "telegram", "sync-commands"])

    assert result == 2
    assert json.loads(capsys.readouterr().out) == response


@pytest.mark.parametrize("arguments,method,params", [
    (["status"], "telegram.status", {}),
    (["owners"], "telegram.owners", {}),
    (["pair-request", "--ttl", "120"], "telegram.pair-request", {"ttl_seconds": 120}),
])
def test_other_telegram_cli_commands_keep_timeout_and_result(tmp_path, monkeypatch, capsys, arguments, method, params):
    response = {"detail": "Resposta sem confirmação de catálogo"}
    calls = _stub_cli_rpc(monkeypatch, response)

    result = cli.main(["--onmyoji-root", str(tmp_path), "telegram", *arguments])

    assert result == 0
    assert json.loads(capsys.readouterr().out) == response
    assert calls == [("127.0.0.1", 39900, "supervisor-token", method, params, 5)]


@pytest.mark.parametrize("method,expected_timeout", [
    ("telegram.sync-commands", 60),
    ("telegram.status", 5),
    ("telegram.owners", 5),
    ("telegram.pair-request", 5),
])
def test_supervisor_forwards_telegram_sync_with_matching_timeout(tmp_path, monkeypatch, method, expected_timeout):
    monkeypatch.setattr(supervisor_module, "free_port", lambda: 39900)
    monkeypatch.setattr(supervisor_module, "RpcServer", lambda *_args, **_kwargs: SimpleNamespace(port=39900))
    supervisor = Supervisor(tmp_path)
    service = supervisor.services["telegram"]
    service.process = SimpleNamespace(poll=lambda: None)
    service.port, service.token = 39901, "telegram-token"
    payload = {"contract_test": "forwarded"}
    response = {"contract_test": "reply"}
    calls = []

    def transport(host, port, token, operation, params, *, timeout):
        calls.append((host, port, token, operation, params, timeout))
        return response

    monkeypatch.setattr(supervisor_module, "call", transport)

    assert supervisor.handle(method, payload) is response
    assert calls == [("127.0.0.1", 39901, "telegram-token", method, payload, expected_timeout)]
    assert calls[0][4] is payload
