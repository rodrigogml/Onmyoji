from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "setupOnmyoji.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_pairing_setup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Clock:
    def __init__(self):
        self.elapsed = 0.0
        self.sleeps = []

    def monotonic(self):
        self.elapsed += 0.01
        return self.elapsed

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds


@pytest.fixture
def clock(monkeypatch):
    selected = Clock()
    monkeypatch.setattr(MODULE, "time", SimpleNamespace(monotonic=selected.monotonic, sleep=selected.sleep))
    return selected


def completed(command, value=None, *, code=0, error="", output=None):
    return subprocess.CompletedProcess(command, code, json.dumps(value) if output is None else output, error)


def services(state="running", *, enabled=True, error=None):
    return [{"name": "telegram", "enabled": enabled, "state": state, "last_error": error}]


def test_pairing_waits_for_telegram_startup_without_waiting_for_other_services(tmp_path, clock):
    """O controle do daemon pode responder enquanto seus serviços ainda iniciam."""
    commands = []
    progress = []
    states = iter([services("starting"), services() + [{"name": "mini-apps", "state": "starting", "enabled": True}]])

    def execute(command):
        commands.append(command)
        if command[-1] == "list-services":
            return completed(command, next(states))
        if command[-1] == "status":
            return completed(command, {"listener": "running"})
        assert command[-1] == "pair-request"
        return completed(command, {"pin": "123456", "expires_at": 1234})

    request, message = MODULE.request_telegram_pairing(tmp_path, execute, timeout=2, progress=progress.append)

    assert request["pin"] == "123456"
    assert message == ""
    assert [command[-1] for command in commands] == ["list-services", "list-services", "status", "pair-request"]
    assert clock.sleeps and progress
    assert all(command[:3] == [sys.executable, "-m", "onmyoji_daemon.cli"] for command in commands)
    assert all(command[3:5] == ["--onmyoji-root", str(tmp_path)] for command in commands)


def test_pairing_control_timeout_does_not_claim_the_os_daemon_is_stopped(tmp_path, clock):
    """RPC indisponível não comprova que o serviço do sistema operacional parou."""
    commands = []

    def execute(command):
        commands.append(command)
        return completed(command, code=2, error="Erro: acesso ao endpoint recusado token=hidden-value")

    request, message = MODULE.request_telegram_pairing(tmp_path, execute, timeout=0.5)

    assert request is None
    assert "daemon" in message.casefold()
    assert "recusado" in message.casefold()
    assert "hidden-value" not in message
    assert "inicie o daemon" not in message.casefold()
    assert all(command[-1] == "list-services" for command in commands)
    assert clock.elapsed >= 0.5


def test_pairing_reports_disabled_telegram_while_the_daemon_is_reachable(tmp_path, clock):
    commands = []

    def execute(command):
        commands.append(command)
        return completed(command, services("stopped", enabled=False))

    request, message = MODULE.request_telegram_pairing(tmp_path, execute)

    assert request is None
    assert "telegram" in message.casefold()
    assert "desabilit" in message.casefold()
    assert [command[-1] for command in commands] == ["list-services"]
    assert clock.sleeps == []


def test_pairing_reports_gateway_failure_and_sanitizes_its_cause(tmp_path, clock):
    failure = "KeePass indisponível token=hidden-token Bearer hidden-bearer https://api.telegram.org/bot123456:hidden-bot/getMe"
    commands = []

    def execute(command):
        commands.append(command)
        return completed(command, services("failed", error=failure))

    request, message = MODULE.request_telegram_pairing(tmp_path, execute)

    assert request is None
    assert "telegram" in message.casefold() and "KeePass" in message
    assert all(secret not in message for secret in ("hidden-token", "hidden-bearer", "hidden-bot"))
    assert "inicie o daemon" not in message.casefold()
    assert [command[-1] for command in commands] == ["list-services"]


def test_pairing_does_not_request_a_pin_before_an_enabled_gateway_starts(tmp_path, clock):
    commands = []

    def execute(command):
        commands.append(command)
        return completed(command, services("stopped"))

    request, message = MODULE.request_telegram_pairing(tmp_path, execute, timeout=0.5)

    assert request is None
    assert "telegram" in message.casefold()
    assert "inicie o daemon" not in message.casefold()
    assert all(command[-1] == "list-services" for command in commands)


def test_pairing_requires_a_responding_gateway_listener(tmp_path, clock):
    commands = []

    def execute(command):
        commands.append(command)
        if command[-1] == "list-services":
            return completed(command, services())
        assert command[-1] == "status"
        return completed(command, code=2, error="Erro: conexão do gateway recusada")

    request, message = MODULE.request_telegram_pairing(tmp_path, execute, timeout=0.5)

    assert request is None
    assert "telegram" in message.casefold()
    assert "recusada" in message.casefold()
    assert "pair-request" not in [command[-1] for command in commands]


def test_pairing_waits_for_the_gateway_listener_even_when_the_process_is_running(tmp_path, clock):
    status_count = 0
    pair_count = 0

    def execute(command):
        nonlocal status_count, pair_count
        if command[-1] == "list-services":
            return completed(command, services())
        if command[-1] == "status":
            status_count += 1
            return completed(command, {"listener": "starting" if status_count == 1 else "running"})
        pair_count += 1
        return completed(command, {"pin": "123456"})

    request, message = MODULE.request_telegram_pairing(tmp_path, execute, timeout=2)

    assert request["pin"] == "123456" and message == ""
    assert status_count == 2 and pair_count == 1


def test_pairing_failure_after_readiness_preserves_the_real_error_without_retrying(tmp_path, clock):
    pair_count = 0

    def execute(command):
        nonlocal pair_count
        if command[-1] == "list-services":
            return completed(command, services())
        if command[-1] == "status":
            return completed(command, {"listener": "running"})
        pair_count += 1
        return completed(command, code=2, output="stdout sem o diagnóstico real", error="Erro: pareamento recusado por limite da operação")

    request, message = MODULE.request_telegram_pairing(tmp_path, execute)

    assert request is None
    assert "limite da operação" in message
    assert "stdout sem" not in message
    assert "inicie o daemon" not in message.casefold()
    assert pair_count == 1


def test_failed_pairing_response_does_not_expose_a_pin_in_stdout(tmp_path, clock):
    """Um PIN devolvido junto da falha não aparece como diagnóstico operacional."""
    def execute(command):
        if command[-1] == "list-services":
            return completed(command, services())
        if command[-1] == "status":
            return completed(command, {"listener": "running"})
        return completed(command, {"pin": "123456", "error": "pareamento recusado"}, code=2)

    request, message = MODULE.request_telegram_pairing(tmp_path, execute)

    assert request is None
    assert "123456" not in message
    assert "pareamento recusado" in message


@pytest.mark.parametrize("output", ["invalid-json", "{}", '{"pin": 123456}', '{"pin": "invalid"}', '{"pin": "1234567"}'])
def test_pairing_requires_a_valid_pin_response(tmp_path, clock, output):
    def execute(command):
        if command[-1] == "list-services":
            return completed(command, services())
        if command[-1] == "status":
            return completed(command, {"listener": "running"})
        return completed(command, output=output)

    request, message = MODULE.request_telegram_pairing(tmp_path, execute)

    assert request is None
    assert "pareamento" in message.casefold()
    assert "Traceback" not in message


@pytest.mark.parametrize("output", ["invalid-json", "{}", "null"])
def test_invalid_supervisor_status_never_issues_a_pairing_request(tmp_path, clock, output):
    commands = []

    def execute(command):
        commands.append(command)
        return completed(command, output=output)

    request, message = MODULE.request_telegram_pairing(tmp_path, execute, timeout=0.5)

    assert request is None and message
    assert all(command[-1] == "list-services" for command in commands)


def test_missing_telegram_registration_is_distinct_from_an_unreachable_daemon(tmp_path, clock):
    commands = []

    def execute(command):
        commands.append(command)
        return completed(command, [{"name": "mini-apps", "enabled": True, "state": "running"}])

    request, message = MODULE.request_telegram_pairing(tmp_path, execute)

    assert request is None
    assert "telegram" in message.casefold()
    assert "inicie o daemon" not in message.casefold()
    assert all(command[-1] == "list-services" for command in commands)


def test_owner_pairing_uses_the_selected_runtime_under_the_operational_account(tmp_path, monkeypatch, capsys):
    """O teste de readiness e a criação do PIN usam a mesma identidade do gateway."""
    project = tmp_path / "onmyoji-daemon"
    project.mkdir()
    (project / "setupDaemon.py").touch()
    daemon = tmp_path / "configs" / "daemon"
    daemon.mkdir(parents=True)
    (daemon / "daemon.toml").write_text("", encoding="utf-8")
    executable = tmp_path / "venv" / "bin" / "python"
    profile = {"mode": "dedicated", "validated": True, "account": "shikigami", "home": str(tmp_path), "setup_python": "/usr/bin/python3"}
    delegated = []
    answers = iter(["t", "4", "1", "x", "x", "x"])

    def run(command, **_options):
        if command[-1] == "list-services":
            return completed(command, services())
        if command[-1] == "status":
            return completed(command, {"listener": "running"})
        if command[-1] == "pair-request":
            return completed(command, {"pin": "123456"})
        return completed(command, {})

    monkeypatch.setattr(MODULE, "load_execution_profile", lambda _root: profile)
    monkeypatch.setattr(MODULE, "linux_current_user", lambda: "instalador")
    monkeypatch.setattr(MODULE.python_runtime, "python", lambda _root: executable)
    monkeypatch.setattr(MODULE, "account_command", lambda account, home, command, environment: delegated.append((account, command)) or ["as-dedicated", *command])
    monkeypatch.setattr(MODULE, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(MODULE.subprocess, "run", run)

    MODULE.daemon_menu(tmp_path)

    requests = [(account, command) for account, command in delegated if command[-1] in {"list-services", "status", "pair-request"}]
    assert [command[-1] for _account, command in requests] == ["list-services", "status", "pair-request"]
    assert all(account == "shikigami" and command[0] == str(executable) for account, command in requests)
    output = capsys.readouterr().out
    assert "/pair 123456" in output
    assert "Inicie o daemon e habilite o Telegram antes de parear" not in output
