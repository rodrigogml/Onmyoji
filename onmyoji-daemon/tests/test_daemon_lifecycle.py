"""Contrato de controle único para processos locais e serviços do daemon."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from onmyoji_daemon import management


def register_service(root, *, scope="user"):
    path = management.service_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "shikigami-test", "root": str(root.resolve()), "scope": scope}), encoding="utf-8")


@pytest.mark.parametrize("action", ["start", "stop", "restart", "force"])
@pytest.mark.parametrize("use_sudo", [False, True])
def test_daemon_action_routes_registered_service_without_touching_local_process(tmp_path, monkeypatch, action, use_sudo):
    register_service(tmp_path)
    calls = []

    def service_action(root, operation, use_sudo=False):
        calls.append((root, operation, use_sudo))
        return False, "Falha do gerenciador do serviço"

    monkeypatch.setattr(management, "service_action", service_action)
    monkeypatch.setattr(management, "start_background", lambda *_args, **_kwargs: pytest.fail("Serviço não pode criar processo local"))
    monkeypatch.setattr(management, "stop_background", lambda *_args, **_kwargs: pytest.fail("Serviço não pode ser parado por PID"))

    assert management.daemon_action(tmp_path, action, use_sudo=use_sudo) == (False, "Falha do gerenciador do serviço")
    assert calls == [(tmp_path, action, use_sudo)]


@pytest.mark.parametrize("action, expected", [("start", "start"), ("stop", False), ("force", True)])
def test_daemon_action_routes_unregistered_daemon_to_local_control(tmp_path, monkeypatch, action, expected):
    calls = []
    monkeypatch.setattr(management, "service_action", lambda *_args, **_kwargs: pytest.fail("Não há serviço registrado"))

    def start(root):
        calls.append((root, "start"))
        return True, "Processo iniciado"

    def stop(root, force=False):
        calls.append((root, force))
        return True, "Processo parado"

    monkeypatch.setattr(management, "start_background", start)
    monkeypatch.setattr(management, "stop_background", stop)

    ok, _message = management.daemon_action(tmp_path, action)

    assert ok
    assert calls == [(tmp_path, expected)]


def test_daemon_action_rejects_unknown_action_without_lifecycle_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(management, "start_background", lambda *_args, **_kwargs: pytest.fail("Ação inválida"))
    monkeypatch.setattr(management, "stop_background", lambda *_args, **_kwargs: pytest.fail("Ação inválida"))
    monkeypatch.setattr(management, "service_action", lambda *_args, **_kwargs: pytest.fail("Ação inválida"))

    ok, message = management.daemon_action(tmp_path, "unknown")

    assert not ok and message


@pytest.mark.parametrize("force", [False, True])
def test_local_stop_refuses_to_signal_a_registered_service(tmp_path, monkeypatch, force):
    register_service(tmp_path)
    monkeypatch.setattr(management, "running_process", lambda *_args: pytest.fail("Não consultar PID para parar serviço"))
    monkeypatch.setattr(management, "_rpc", lambda *_args: pytest.fail("Não enviar shutdown por fora do gestor"))
    monkeypatch.setattr(management.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Não matar PID do serviço"))

    ok, message = management.stop_background(tmp_path, force=force)

    assert not ok and "serviço" in message.casefold()


@pytest.mark.parametrize("error, expected", [(PermissionError("outra conta"), True), (ProcessLookupError("encerrado"), False)])
def test_pid_probe_distinguishes_lack_of_permission_from_process_exit(monkeypatch, error, expected):
    calls = []

    def kill(pid, signal):
        calls.append((pid, signal))
        raise error

    monkeypatch.setattr(management, "os", SimpleNamespace(name="posix", kill=kill))

    assert management._pid_alive(321) is expected
    assert calls == [(321, 0)]


@pytest.mark.parametrize("registered, manager_active, process, reachable, expected_active", [
    (True, True, None, False, True),
    (True, False, None, False, False),
    (False, False, {"pid": 321, "instance": "local"}, False, True),
    (False, False, None, True, True),
    (False, False, None, False, False),
])
def test_daemon_status_separates_lifecycle_mode_activity_and_rpc(
    tmp_path, monkeypatch, registered, manager_active, process, reachable, expected_active,
):
    if registered:
        register_service(tmp_path)
    monkeypatch.setattr(management, "service_is_active", lambda _root: manager_active)
    monkeypatch.setattr(management, "running_process", lambda _root: process)
    monkeypatch.setattr(management, "daemon_reachable", lambda _root: reachable)

    status = management.daemon_status(tmp_path)

    assert status["mode"] == ("service" if registered else "process")
    assert status["active"] is expected_active
    assert status["reachable"] is reachable
    assert status["process"] == process


def identity(root, *, pid=456, instance="new-execution", **extra):
    return {
        "pid": pid, "root": str(root.resolve()), "instance": instance,
        "started_at": 100.0, "configuration_revision": management.configuration_revision(root),
        "setup_version": management.SETUP_VERSION, **extra,
    }


@pytest.mark.parametrize("changed_field, value", [
    (None, None), ("instance", "another-execution"), ("pid", 999),
    ("root", "another-root"), ("service", "another-service"),
])
def test_confirmed_daemon_requires_rpc_to_identify_the_recorded_process(tmp_path, monkeypatch, changed_field, value):
    process = identity(tmp_path)
    response = {"service": "onmyoji-daemon", "state": "running", **process}
    if changed_field:
        response[changed_field] = value
    monkeypatch.setattr(management, "_daemon_identity", lambda _root: process)
    monkeypatch.setattr(management, "endpoint", lambda _root: ("127.0.0.1", 1, "local-test-token"))
    monkeypatch.setattr(management, "call", lambda *_args, **_kwargs: response)

    confirmed = management._confirmed_daemon(tmp_path)

    assert confirmed == (None if changed_field else process)


def test_confirmed_daemon_rejects_legacy_ping_without_process_identity(tmp_path, monkeypatch):
    process = identity(tmp_path)
    monkeypatch.setattr(management, "_daemon_identity", lambda _root: process)
    monkeypatch.setattr(management, "endpoint", lambda _root: ("127.0.0.1", 1, "local-test-token"))
    monkeypatch.setattr(management, "call", lambda *_args, **_kwargs: {"service": "onmyoji-daemon", "state": "running"})

    assert management._confirmed_daemon(tmp_path) is None


@pytest.mark.parametrize("service", [False, True])
def test_wait_for_daemon_accepts_a_new_confirmed_execution(tmp_path, monkeypatch, service):
    process = identity(tmp_path)
    previous = identity(tmp_path, pid=123, instance="old-execution")
    monkeypatch.setattr(management, "_confirmed_daemon", lambda _root: process)
    monkeypatch.setattr(management, "_service_main_pid", lambda _root: process["pid"])
    monkeypatch.setattr(management, "service_is_active", lambda _root: True)
    monkeypatch.setattr(management, "_pid_alive", lambda pid: pid == process["pid"])

    ok, message = management._wait_for_daemon(tmp_path, previous=previous, service=service, timeout=0)

    assert ok and message


@pytest.mark.parametrize("case", [
    "same-execution", "old-code", "old-configuration", "old-pid-alive",
    "unconfirmed-rpc", "manager-pid-mismatch", "manager-inactive", "manager-unavailable",
])
def test_wait_for_service_rejects_false_restart_success(tmp_path, monkeypatch, case):
    process = identity(tmp_path)
    previous = identity(tmp_path, pid=123, instance="old-execution")
    manager_pid = process["pid"]
    manager_active = True
    if case == "same-execution":
        process = previous
        manager_pid = process["pid"]
    elif case == "old-code":
        process["setup_version"] = "1900.01.01.0"
    elif case == "old-configuration":
        process["configuration_revision"] = "old-configuration"
    elif case == "manager-pid-mismatch":
        manager_pid = 789
    elif case == "manager-inactive":
        manager_active = False
    elif case == "manager-unavailable":
        manager_pid = None
    monkeypatch.setattr(management, "_confirmed_daemon", lambda _root: None if case == "unconfirmed-rpc" else process)
    monkeypatch.setattr(management, "_service_main_pid", lambda _root: manager_pid)
    monkeypatch.setattr(management, "service_is_active", lambda _root: manager_active)
    monkeypatch.setattr(management, "_pid_alive", lambda pid: pid == process["pid"] or (case == "old-pid-alive" and pid == previous["pid"]))

    ok, message = management._wait_for_daemon(tmp_path, previous=previous, service=True, timeout=0)

    assert not ok and message


def test_new_execution_identity_can_confirm_pid_reuse(tmp_path, monkeypatch):
    process = identity(tmp_path)
    previous = identity(tmp_path, pid=process["pid"], instance="old-execution", started_at=99.0)
    monkeypatch.setattr(management, "_confirmed_daemon", lambda _root: process)
    monkeypatch.setattr(management, "_service_main_pid", lambda _root: process["pid"])
    monkeypatch.setattr(management, "service_is_active", lambda _root: True)
    monkeypatch.setattr(management, "_pid_alive", lambda _pid: True)

    ok, _message = management._wait_for_daemon(tmp_path, previous=previous, service=True, timeout=0)

    assert ok


@pytest.mark.parametrize("rpc_alive, pid_alive, manager_pid, manager_active, expected", [
    (False, False, 0, False, True),
    (True, False, 0, False, False),
    (False, True, 0, False, False),
    (False, False, 456, False, False),
    (False, False, 0, True, False),
    (False, False, None, False, False),
])
def test_wait_for_service_stop_requires_process_rpc_and_manager_to_stop(
    tmp_path, monkeypatch, rpc_alive, pid_alive, manager_pid, manager_active, expected,
):
    previous = identity(tmp_path, pid=123, instance="old-execution")
    monkeypatch.setattr(management, "daemon_reachable", lambda _root: rpc_alive)
    monkeypatch.setattr(management, "_confirmed_daemon", lambda _root: previous if rpc_alive else None)
    monkeypatch.setattr(management, "_pid_alive", lambda _pid: pid_alive)
    monkeypatch.setattr(management, "_service_main_pid", lambda _root: manager_pid)
    monkeypatch.setattr(management, "service_is_active", lambda _root: manager_active)

    ok, message = management._wait_for_daemon_stopped(tmp_path, previous, service=True, timeout=0)

    assert ok is expected and message


def test_local_restart_does_not_start_after_a_failed_stop(tmp_path, monkeypatch):
    process = identity(tmp_path, pid=123, instance="old-execution")
    monkeypatch.setattr(management, "_daemon_identity", lambda _root: process)
    monkeypatch.setattr(management, "stop_background", lambda *_args, **_kwargs: (False, "Processo anterior continua ativo"))
    monkeypatch.setattr(management, "start_background", lambda *_args, **_kwargs: pytest.fail("Processo anterior não saiu"))

    assert management.daemon_action(tmp_path, "restart") == (False, "Processo anterior continua ativo")


@pytest.mark.parametrize("failure", [None, "stop-command", "stop-state"])
def test_windows_restart_waits_for_stop_and_never_starts_after_stop_failure(tmp_path, monkeypatch, failure):
    register_service(tmp_path, scope="system")
    events = []
    monkeypatch.setattr(management, "os", SimpleNamespace(name="nt"))

    def run(command, **_kwargs):
        events.append(command[1])
        return SimpleNamespace(returncode=1 if failure == "stop-command" and command[1] == "stop" else 0, stdout="", stderr="Parada recusada")

    def wait(root, expected, **_kwargs):
        assert root == tmp_path
        events.append(expected)
        return not (failure == "stop-state" and expected == "STOPPED"), "Estado não confirmado"

    monkeypatch.setattr(management.subprocess, "run", run)
    monkeypatch.setattr(management, "_wait_for_windows_service_state", wait)

    ok, _message = management._run_service_action(tmp_path, "restart")

    assert ok is (failure is None)
    assert events == ({None: ["stop", "STOPPED", "start", "RUNNING"], "stop-command": ["stop"], "stop-state": ["stop", "STOPPED"]}[failure])


@pytest.mark.parametrize("scope, uid, use_sudo, prefix", [
    ("user", 1000, False, []),
    ("system", 0, False, []),
    ("system", 1000, True, ["sudo"]),
])
def test_linux_service_restart_preserves_scope_and_sudo_authorization(tmp_path, monkeypatch, scope, uid, use_sudo, prefix):
    register_service(tmp_path, scope=scope)
    calls = []
    monkeypatch.setattr(management, "os", SimpleNamespace(name="posix", geteuid=lambda: uid))
    monkeypatch.setattr(management.shutil, "which", lambda _name: "/usr/bin/sudo")

    def run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(management.subprocess, "run", run)

    ok, _message = management._run_service_action(tmp_path, "restart", use_sudo=use_sudo)

    assert ok
    assert calls == [[*prefix, "systemctl", *(["--user"] if scope == "user" else []), "restart", "shikigami-test"]]


@pytest.mark.parametrize("case", ["new-execution", "old-rpc", "orphan-rpc"])
def test_service_restart_does_not_equate_manager_acceptance_with_actual_restart(tmp_path, monkeypatch, case):
    register_service(tmp_path)
    previous = identity(tmp_path, pid=123, instance="old-execution")
    current = previous if case == "old-rpc" else identity(tmp_path)
    manager = {"pid": previous["pid"]}
    ticks = iter(range(0, 10000, 31))
    monkeypatch.setattr(management, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _seconds: None))
    monkeypatch.setattr(management, "_daemon_identity", lambda _root: previous)
    monkeypatch.setattr(management, "_confirmed_daemon", lambda _root: current)
    monkeypatch.setattr(management, "_service_main_pid", lambda _root: manager["pid"])
    monkeypatch.setattr(management, "service_is_active", lambda _root: True)
    monkeypatch.setattr(management, "_pid_alive", lambda pid: pid == current["pid"])

    def accepted(root, action, use_sudo=False):
        assert root == tmp_path and action == "restart"
        manager["pid"] = 789 if case == "orphan-rpc" else current["pid"]
        return True, "Solicitação aceita pelo gerenciador"

    monkeypatch.setattr(management, "_run_service_action", accepted)

    ok, message = management.service_action(tmp_path, "restart")

    assert ok is (case == "new-execution")
    assert message != "Solicitação aceita pelo gerenciador"


@pytest.mark.parametrize("scope, uid, use_sudo, prefix", [
    ("user", 1000, False, []),
    ("system", 0, False, []),
    ("system", 1000, True, ["sudo"]),
])
@pytest.mark.parametrize("case", ["already-stopped", "kill", "stop-failed", "query-unavailable", "kill-failed-running", "kill-failed-stopped"])
def test_forced_linux_stop_registers_stop_job_before_signalling_service(
    tmp_path, monkeypatch, scope, uid, use_sudo, prefix, case,
):
    register_service(tmp_path, scope=scope)
    commands = []
    pids = iter({
        "already-stopped": [0], "kill": [456], "stop-failed": [],
        "query-unavailable": [None], "kill-failed-running": [456, 456], "kill-failed-stopped": [456, 0],
    }[case])
    monkeypatch.setattr(management, "os", SimpleNamespace(name="posix", geteuid=lambda: uid))
    monkeypatch.setattr(management.shutil, "which", lambda _name: "/usr/bin/sudo")
    monkeypatch.setattr(management, "_service_main_pid", lambda _root: next(pids))

    def run(command, **_kwargs):
        commands.append(command)
        failed = ("stop" in command and case == "stop-failed") or ("kill" in command and case.startswith("kill-failed"))
        return SimpleNamespace(returncode=1 if failed else 0, stdout="", stderr="Operação recusada" if failed else "")

    monkeypatch.setattr(management.subprocess, "run", run)

    ok, message = management._run_service_action(tmp_path, "force", use_sudo=use_sudo)

    systemctl = [*prefix, "systemctl", *(["--user"] if scope == "user" else [])]
    expected = [[*systemctl, "--no-block", "stop", "shikigami-test"]]
    if case in {"kill", "kill-failed-running", "kill-failed-stopped"}:
        expected.append([*systemctl, "kill", "--signal=SIGKILL", "shikigami-test"])
    assert commands == expected
    assert ok is (case in {"already-stopped", "kill", "kill-failed-stopped"})
    assert message


@pytest.mark.parametrize("service", [False, True])
def test_wait_for_stop_does_not_ignore_a_replacement_process(tmp_path, monkeypatch, service):
    previous = identity(tmp_path, pid=123, instance="old-execution")
    replacement = identity(tmp_path, pid=456, instance="replacement")
    monkeypatch.setattr(management, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(management, "daemon_reachable", lambda _root: False)
    monkeypatch.setattr(management, "running_process", lambda _root: replacement)
    monkeypatch.setattr(management, "_service_main_pid", lambda _root: 0)
    monkeypatch.setattr(management, "service_is_active", lambda _root: False)

    ok, message = management._wait_for_daemon_stopped(tmp_path, previous, service=service, timeout=0)

    assert not ok and message


def test_local_stop_cannot_claim_success_while_rpc_has_no_identified_process(tmp_path, monkeypatch):
    monkeypatch.setattr(management, "running_process", lambda _root: None)
    monkeypatch.setattr(management, "daemon_reachable", lambda _root: True)
    monkeypatch.setattr(management, "_rpc", lambda *_args, **_kwargs: pytest.fail("Não enviar sinal sem processo identificado"))
    monkeypatch.setattr(management.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Não finalizar processo não identificado"))

    ok, message = management.stop_background(tmp_path)

    assert not ok and message


@pytest.mark.parametrize("action, code, expected_state", [("start", 1056, "RUNNING"), ("stop", 1062, "STOPPED")])
def test_windows_idempotent_control_still_requires_the_requested_state(tmp_path, monkeypatch, action, code, expected_state):
    register_service(tmp_path)
    observed = []
    monkeypatch.setattr(management, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(management.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=code, stdout="", stderr=""))

    def confirm(_root, state):
        observed.append(state)
        return True, state

    monkeypatch.setattr(management, "_wait_for_windows_service_state", confirm)

    ok, message = management._run_service_action(tmp_path, action)

    assert ok, message
    assert observed == [expected_state]
