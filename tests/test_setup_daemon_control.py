"""Contrato da UI e do CLI para o controle central do daemon."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from onmyoji_daemon import cli


@pytest.fixture
def setup_module():
    path = Path(__file__).resolve().parents[1] / "setupOnmyoji.py"
    spec = importlib.util.spec_from_file_location("setup_daemon_control_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_menu(module, tmp_path, monkeypatch, answers, *, service=None, active=True, service_active=None, reachable=True, code=0, dedicated=False, uid=0):
    project = tmp_path / "onmyoji-daemon"
    project.mkdir()
    (project / "setupDaemon.py").touch()
    daemon = tmp_path / "configs" / "daemon"
    daemon.mkdir(parents=True)
    (daemon / "daemon.toml").write_text("", encoding="utf-8")
    if service is not None:
        (daemon / "service.json").write_text(json.dumps(service), encoding="utf-8")
    runtime = tmp_path / "venv" / "bin" / "python"
    profile = {"mode": "dedicated", "validated": True, "account": "shikigami", "home": str(tmp_path), "setup_python": "/usr/bin/python3"} if dedicated else {}
    responses = iter(answers)
    calls, reports, delegated = [], [], []
    real_os = module.os

    class LinuxOs:
        name = "posix"
        def geteuid(self): return uid
        def __getattr__(self, name): return getattr(real_os, name)

    def run(arguments, **options):
        calls.append((arguments, options))
        if "daemon-status" in arguments:
            status = {"mode": "service" if service is not None else "process", "active": active, "reachable": reachable, "process": None}
            if service_active is not None:
                status["service_active"] = service_active
            output = json.dumps(status)
        elif "daemon-restart" in arguments:
            return subprocess.CompletedProcess(arguments, code, stdout="Nova execução confirmada." if code == 0 else "O daemon anterior continua respondendo.", stderr="")
        elif "list-services" in arguments:
            return subprocess.CompletedProcess(arguments, 0, stdout="[]", stderr="")
        else:
            output = "Operação concluída."
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr(module, "os", LinuxOs())
    monkeypatch.setattr(module, "load_execution_profile", lambda _root: profile)
    monkeypatch.setattr(module, "linux_current_user", lambda: "instalador")
    monkeypatch.setattr(module.python_runtime, "python", lambda _root: runtime)
    monkeypatch.setattr(module, "account_command", lambda account, home, args, environment: delegated.append((account, list(args))) or args)
    monkeypatch.setattr(module, "prompt", lambda _label: next(responses))
    monkeypatch.setattr(module, "result", lambda ok, message: reports.append((ok, message)))
    monkeypatch.setattr(module.subprocess, "run", run)
    module.daemon_menu(tmp_path)
    return calls, reports, delegated


@pytest.mark.parametrize("service", [None, {"name": "onmyoji", "scope": "user"}])
def test_configuration_restart_uses_one_central_action_even_if_rpc_was_unavailable(setup_module, tmp_path, monkeypatch, service):
    calls, reports, _ = run_menu(
        setup_module, tmp_path, monkeypatch, ["t", "1", "1", "", "x", "x", "x"],
        service=service, active=True, reachable=False,
    )
    commands = [arguments for arguments, _options in calls]
    assert sum("daemon-restart" in command for command in commands) == 1
    assert not any(set(command) & {"process-stop", "process-start", "service-restart"} for command in commands)
    assert any(ok and message == "Nova execução confirmada." for ok, message in reports)


def test_service_menu_reports_failed_confirmation_as_failure(setup_module, tmp_path, monkeypatch):
    calls, reports, _ = run_menu(
        setup_module, tmp_path, monkeypatch, ["2", "2", "3", "x", "x", "x"],
        service={"name": "onmyoji", "scope": "user"}, code=2,
    )
    assert sum("daemon-restart" in arguments for arguments, _options in calls) == 1
    assert reports == [(False, "O daemon anterior continua respondendo.")]


def test_service_menu_distinguishes_inactive_manager_from_a_responding_orphan(setup_module, tmp_path, monkeypatch, capsys):
    calls, _reports, _delegated = run_menu(
        setup_module, tmp_path, monkeypatch, ["2", "2", "3", "x", "x", "x"],
        service={"name": "onmyoji", "scope": "user"}, active=True, service_active=False, reachable=True, code=2,
    )
    output = capsys.readouterr().out
    assert "Iniciar serviço" in output and "Parar serviço" not in output
    assert sum("daemon-restart" in arguments for arguments, _options in calls) == 1
    assert not any("service-restart" in arguments for arguments, _options in calls)


@pytest.mark.parametrize("scope,uid,confirmation,expected_sudo", [("system", 1000, ["s"], True), ("system", 0, [], False), ("user", 1000, [], False)])
def test_service_menu_authorizes_sudo_only_for_system_service(setup_module, tmp_path, monkeypatch, scope, uid, confirmation, expected_sudo):
    calls, _reports, delegated = run_menu(
        setup_module, tmp_path, monkeypatch, ["2", "2", "3", *confirmation, "x", "x", "x"],
        service={"name": "onmyoji", "scope": scope}, dedicated=True, uid=uid,
    )
    controls = [arguments for arguments, _options in calls if "daemon-restart" in arguments]
    assert len(controls) == 1
    assert ("--use-sudo" in controls[0]) is expected_sudo
    assert not any("daemon-restart" in arguments for _account, arguments in delegated)
    assert any("daemon-status" in arguments for _account, arguments in delegated)


def test_local_restart_preserves_the_operational_account(setup_module, tmp_path, monkeypatch):
    _calls, _reports, delegated = run_menu(
        setup_module, tmp_path, monkeypatch, ["t", "1", "1", "", "x", "x", "x"], dedicated=True,
    )
    controls = [(account, arguments) for account, arguments in delegated if "daemon-restart" in arguments]
    assert len(controls) == 1 and controls[0][0] == "shikigami"
    assert controls[0][1][0] == str(tmp_path / "venv" / "bin" / "python")


@pytest.mark.parametrize("operation,action", [("daemon-start", "start"), ("daemon-stop", "stop"), ("daemon-restart", "restart"), ("daemon-force-stop", "force")])
@pytest.mark.parametrize("success", [False, True])
def test_cli_delegates_daemon_lifecycle_and_preserves_confirmation_result(tmp_path, monkeypatch, capsys, operation, action, success):
    calls = []
    monkeypatch.setattr(cli.management, "daemon_action", lambda root, selected, use_sudo=False: (calls.append((root, selected, use_sudo)) or success, "Confirmação do controle central."))
    assert cli.main(["--onmyoji-root", str(tmp_path), operation, "--use-sudo"]) == (0 if success else 2)
    assert calls == [(tmp_path.resolve(), action, True)]
    assert capsys.readouterr().out.strip() == "Confirmação do controle central."


def test_cli_exposes_daemon_status_as_json_without_parsing_localized_service_text(tmp_path, monkeypatch, capsys):
    value = {"mode": "service", "active": True, "reachable": False, "process": None}
    monkeypatch.setattr(cli.management, "daemon_status", lambda _root: value)
    assert cli.main(["--onmyoji-root", str(tmp_path), "daemon-status"]) == 0
    assert json.loads(capsys.readouterr().out) == value
