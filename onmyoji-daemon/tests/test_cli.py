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
