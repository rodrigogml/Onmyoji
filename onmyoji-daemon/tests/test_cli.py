from __future__ import annotations

from onmyoji_daemon import cli
from onmyoji_daemon.rpc import RpcError


def test_cli_reports_remote_failure_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "token"))
    monkeypatch.setattr(cli, "call", lambda *_args, **_kwargs: (_ for _ in ()).throw(RpcError("mini-apps is not running")))

    result = cli.main(["--onmyoji-root", ".", "mini-apps", "tunnel", "status"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == "Erro: mini-apps is not running\n"
    assert "Traceback" not in captured.err


def test_cli_treats_failed_service_start_as_failure(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "token"))
    monkeypatch.setattr(cli, "call", lambda *_args, **_kwargs: {"state": "failed", "last_error": "No module named dependency"})

    result = cli.main(["--onmyoji-root", ".", "start", "mini-apps"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == "Erro: No module named dependency\n"


def test_cli_routes_miniapps_diagnose_to_the_gateway(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "token"))
    received = {}
    monkeypatch.setattr(cli, "call", lambda _host, _port, _token, method, params: received.update(method=method, params=params) or {"local_ready": True})

    result = cli.main(["--onmyoji-root", ".", "mini-apps", "diagnose"])

    captured = capsys.readouterr()
    assert result == 0
    assert received == {"method": "mini-apps.diagnose", "params": {}}
    assert '"local_ready": true' in captured.out


def test_cli_allows_extra_time_for_local_ca_installation(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "token"))
    received = {}
    monkeypatch.setattr(cli, "call", lambda _host, _port, _token, method, params, timeout: received.update(method=method, params=params, timeout=timeout) or {"installed": True})

    result = cli.main(["--onmyoji-root", ".", "mini-apps", "tls", "install-trust", "--confirm"])

    captured = capsys.readouterr()
    assert result == 0
    assert received == {"method": "mini-apps.tls.install-trust", "params": {"confirm": True}, "timeout": 30}
    assert '"installed": true' in captured.out
