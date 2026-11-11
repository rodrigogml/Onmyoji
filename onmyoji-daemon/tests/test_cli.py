from __future__ import annotations

from onmyoji_daemon import cli
from onmyoji_daemon.rpc import RpcError


def test_prepare_instance_runs_before_optional_runtime_dependencies_exist(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    source = Path(cli.__file__).resolve().parents[1]
    environment = dict(os.environ); environment["PYTHONPATH"] = str(source)
    root = tmp_path / "instance"; root.mkdir()

    completed = subprocess.run([sys.executable, "-S", "-B", "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "prepare-instance"], cwd=str(tmp_path), env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=30, check=False)

    assert completed.returncode == 0, completed.stderr
    assert "Manifesto do daemon preparado" in completed.stdout


def test_task_cli_is_read_only_and_routes_listing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "token"))
    received = {}
    monkeypatch.setattr(cli, "call", lambda *args, **kwargs: received.update(method=args[3], params=args[4]) or {"id": "task"})
    assert cli.main(["--onmyoji-root", ".", "task", "list", "--sort", "status", "--limit", "20"]) == 0
    assert received == {"method": "task.list", "params": {"sort": "status", "descending": False, "offset": 0, "limit": 20}}


def test_notification_cli_timeout_does_not_suggest_retry(monkeypatch, capsys):
    import io
    import sys
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "token"))
    import json
    from onmyoji_daemon.agent_notification.contract import uuid7
    value = {"schema_version": 1, "event_id": uuid7(), "prompt": "fixture", "response_channel": {"type": "telegram", "value": "fixture"}, "thread_policy": "reuse"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(value)))
    monkeypatch.setattr(cli, "call", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    assert cli.main(["--onmyoji-root", ".", "notification", "submit", "--file", "-", "--wait-timeout", "1"]) == 2
    assert "Não reenvie automaticamente" in capsys.readouterr().err


def test_notification_cli_validates_before_submitting(monkeypatch, capsys):
    import io
    import sys
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "token"))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"schema_version": 1}'))
    def unexpected_call(*args, **kwargs):
        raise AssertionError("invalid envelope must not be submitted")
    monkeypatch.setattr(cli, "call", unexpected_call)
    assert cli.main(["--onmyoji-root", ".", "notification", "submit", "--file", "-"]) == 2
    assert "Campo obrigatório ausente" in capsys.readouterr().err


def test_cli_reports_remote_failure_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "token"))
    monkeypatch.setattr(cli, "call", lambda *_args, **_kwargs: (_ for _ in ()).throw(RpcError("cloudflare-tunnel is not running")))

    result = cli.main(["--onmyoji-root", ".", "cloudflare-tunnel", "status"])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == "Erro: cloudflare-tunnel is not running\n"
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
    monkeypatch.setattr(cli, "call", lambda _host, _port, _token, method, params, **kwargs: received.update(method=method, params=params) or {"local_ready": True})

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
