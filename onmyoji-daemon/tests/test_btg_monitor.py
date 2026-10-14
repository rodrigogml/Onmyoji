"""O monitor do daemon só consulta perfis habilitados e não expõe dados financeiros."""
import json
from types import SimpleNamespace
import pytest
from onmyoji_daemon.btg_payments_monitor import Monitor
from onmyoji_daemon.registry import btg_monitor_command

def test_monitor_only_dispatches_check_to_enabled_profiles(tmp_path, monkeypatch):
    config = tmp_path / "configs" / "btg-empresas.toml"
    config.parent.mkdir()
    config.write_text('[profiles.active]\nmonitor_enabled=true\n[profiles.off]\nmonitor_enabled=false\n', encoding="utf-8")
    calls = []
    def run(command, **kwargs):
        calls.append((command, json.loads(kwargs["input"])))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "data": {"checked": [{"id": "payment", "lastError": None}]}}))
    monkeypatch.setattr("onmyoji_daemon.btg_payments_monitor.subprocess.run", run)
    monitor = Monitor(tmp_path)
    monitor.cycle()
    assert len(calls) == 1 and calls[0][1] == {"version": 1, "operation": "monitor.check"}
    assert calls[0][0][-1] == "active"
    status = monitor.rpc("btg-monitor.status", {})
    assert status["profiles"]["active"]["checkedCount"] == 1
    assert "payment" not in json.dumps(status)
    with pytest.raises(ValueError):
        monitor.rpc("btg-monitor.submit", {})

def test_monitor_config_error_is_reported_without_raw_content(tmp_path):
    config = tmp_path / "configs" / "btg-empresas.toml"
    config.parent.mkdir()
    config.write_text('secret=invalid', encoding="utf-8")
    monitor = Monitor(tmp_path)
    monitor.cycle()
    assert monitor.health["configError"] == "invalid_config"
    assert "secret" not in json.dumps(monitor.health)
    assert "btg_payments_monitor" in " ".join(btg_monitor_command(tmp_path))

def test_supervisor_can_start_and_stop_btg_monitor(tmp_path):
    from onmyoji_daemon.supervisor import Supervisor
    supervisor = Supervisor(tmp_path)
    try:
        assert supervisor.start("btg-payments-monitor")["state"] == "running"
        assert supervisor.handle("btg-monitor.status", {})["profiles"] == {}
    finally:
        supervisor.stop("btg-payments-monitor")

def test_cli_exposes_monitor_health_without_bank_mutation(monkeypatch, capsys):
    from onmyoji_daemon import cli
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", 1234, "test-token"))
    calls = []
    def call(host, port, token, method, params, **kwargs):
        calls.append(method)
        return {"profiles": {}}
    monkeypatch.setattr(cli, "call", call)
    assert cli.main(["--onmyoji-root", ".", "btg-monitor", "status"]) == 0
    assert calls == ["btg-monitor.status"]
