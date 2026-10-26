"""O monitor do daemon só consulta perfis habilitados e não expõe dados financeiros."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
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


def test_monitor_mcp_uses_readonly_module_and_respects_profile_states(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.configuration import McpConfiguration
    from onmyoji_daemon.mcp.runtime import mcp_runtime_python
    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["v"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "not-a-vault", "linux": "/not-a-vault"}}
    credentials["profiles"]["v"] = {"vault": "v", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "test"}, "linux": {"mode": "command", "command": ["false"]}}}
    config.save_credentials(credentials)
    data = config.btg()
    template = {"enabled": True, "description": "Conta de teste", "environment": "sandbox", "company_id": "30306294000145", "credential_profile": "v", "credential_entry_path": "BTG", "monitor_enabled": True}
    data["profiles"] = {"active": template, "off": dict(template, enabled=False), "no-monitor": dict(template, monitor_enabled=False), "no-vault": dict(template, credential_profile="missing")}
    config.save_btg(data)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "data": {"checked": [{"id": "private-payment", "lastError": None}]}}))
    monkeypatch.setattr("onmyoji_daemon.btg_payments_monitor.subprocess.run", run)
    monitor = Monitor(tmp_path)
    monitor.cycle()
    assert len(calls) == 1
    command, options = calls[0]
    assert command[:3] == [str(mcp_runtime_python(tmp_path)), "-m", "onmyoji_daemon.mcp.btg.monitor"]
    assert command[-2:] == ["--profile", "active"] and "input" not in options
    health = monitor.rpc("btg-monitor.status", {})
    assert health["profiles"]["mcp:active"]["checkedCount"] == 1
    assert "private-payment" not in json.dumps(health)
    data["profiles"]["active"]["enabled"] = False
    config.save_btg(data)
    monitor.cycle()
    assert monitor.rpc("btg-monitor.status", {})["profiles"] == {}
    assert len(calls) == 1


def test_monitor_mcp_bad_config_does_not_block_legacy_profiles(tmp_path, monkeypatch):
    config = tmp_path / "configs" / "mcp" / "btg-empresas.toml"
    config.parent.mkdir(parents=True)
    config.write_text("private=invalid", encoding="utf-8")
    monitor = Monitor(tmp_path)
    monitor.cycle()
    assert monitor.health["mcpConfigError"] == "invalid_config"
    assert "private" not in json.dumps(monitor.health)


def test_mcp_monitor_worker_can_only_dispatch_a_read_cycle(tmp_path, monkeypatch, capsys):
    from onmyoji_daemon.mcp.btg import monitor as worker
    import sys
    service = SimpleNamespace(invoke=Mock(return_value={"checked": [], "busy": [], "hasMore": False}))
    monkeypatch.setattr(worker, "BtgService", lambda _root: service)
    monkeypatch.setattr(sys, "argv", ["monitor", "--onmyoji-root", str(tmp_path), "--profile", "active"])
    assert worker.main() == 0
    service.invoke.assert_called_once_with("active", "check_pending_payments", {})
    assert json.loads(capsys.readouterr().out)["ok"] is True
