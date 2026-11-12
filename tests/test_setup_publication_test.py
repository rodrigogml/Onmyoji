from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


GATEWAY = {"ready": True, "public_origin": "https://apps.example.com"}
TUNNEL = {"configured": True, "provisioned": True, "running": True, "connected": True, "ingress_ready": True}
TEST_ID = "a" * 32
TEST_URL = "https://apps.example.com/internal/publication-test/temporary-link"
STARTED = {"test_id": TEST_ID, "url": TEST_URL, "expires_in_seconds": 120, "state": "waiting"}


@pytest.fixture
def setup_ui(tmp_path):
    spec = importlib.util.spec_from_file_location("setup_publication_test", Path(__file__).parents[1] / "setupOnmyoji.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    project = tmp_path / "onmyoji-daemon"
    project.mkdir()
    (project / "setupDaemon.py").write_text("", encoding="utf-8")
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("", encoding="utf-8")
    return module, tmp_path


def run_publication_test(setup_ui, responses, *, cancelled=False):
    """Percorre a opção pública real, substituindo apenas terminal, relógio e CLI."""
    module, root = setup_ui
    answers = iter(["p", "t", "x", "x"])
    elapsed = [0.0]
    output = io.StringIO()

    def sleep(seconds):
        elapsed[0] += seconds

    results = [value if isinstance(value, (BaseException, subprocess.CompletedProcess)) else subprocess.CompletedProcess([], 0, json.dumps(value), "") for value in responses]
    with patch.object(module, "prompt", side_effect=lambda _label: next(answers)), patch.object(module, "screen") as screens, patch.object(module.subprocess, "run", side_effect=results) as run, patch.object(module, "publication_test_cancelled", return_value=cancelled), patch.object(module.time, "monotonic", side_effect=lambda: elapsed[0]), patch.object(module.time, "sleep", side_effect=sleep), patch.object(module.sys, "stdout", output):
        module.daemon_menu(root)
    commands = [call.args[0][call.args[0].index("--onmyoji-root") + 2:] for call in run.call_args_list]
    assert all(call.args[0] in {"Daemon", "Publicação externa"} for call in screens.call_args_list)
    assert all("mini-apps" not in command for command in commands)
    return commands, output.getvalue()


def test_publication_link_waits_until_gateway_confirms_browser_access(setup_ui):
    commands, output = run_publication_test(setup_ui, [GATEWAY, TUNNEL, STARTED, {"state": "waiting"}, {"state": "confirmed"}])

    assert commands == [
        ["gateway", "status"], ["cloudflare-tunnel", "status"],
        ["gateway", "publication-test", "start", "--confirm"],
        ["gateway", "publication-test", "status", TEST_ID],
        ["gateway", "publication-test", "status", TEST_ID],
    ]
    assert TEST_URL in output
    assert "Acesso público confirmado" in output
    assert "X cancela" in output


@pytest.mark.parametrize("field", ["ready", "public_origin"])
def test_publication_test_explains_missing_gateway_prerequisite_without_creating_link(setup_ui, field):
    gateway = {**GATEWAY, field: None}

    commands, output = run_publication_test(setup_ui, [gateway, TUNNEL])

    assert commands == [["gateway", "status"], ["cloudflare-tunnel", "status"]]
    assert "Gateway HTTPS pronto" in output
    assert "opção 4" in output
    assert "Acesso público confirmado" not in output


@pytest.mark.parametrize("field", list(TUNNEL))
def test_publication_test_requires_each_tunnel_prerequisite(setup_ui, field):
    commands, output = run_publication_test(setup_ui, [GATEWAY, {**TUNNEL, field: False}])

    assert commands == [["gateway", "status"], ["cloudflare-tunnel", "status"]]
    assert "Tunnel provisionado, conectado e com ingress confirmado" in output
    assert TEST_URL not in output


def test_publication_test_x_cancels_the_gateway_session(setup_ui):
    commands, output = run_publication_test(setup_ui, [GATEWAY, TUNNEL, STARTED, {"state": "cancelled"}], cancelled=True)

    assert commands[-1] == ["gateway", "publication-test", "cancel", TEST_ID]
    assert all(command[2:3] != ["status"] for command in commands if "publication-test" in command)
    assert "Teste de acesso público cancelado" in output


@pytest.mark.parametrize("state", ["expired", "cancelled"])
def test_publication_test_reports_terminal_gateway_state_without_claiming_success(setup_ui, state):
    commands, output = run_publication_test(setup_ui, [GATEWAY, TUNNEL, STARTED, {"state": state}])

    assert commands[-1] == ["gateway", "publication-test", "status", TEST_ID]
    assert "Acesso público confirmado" not in output
    assert ("expirou sem confirmação" if state == "expired" else "Teste de acesso público cancelado") in output


def test_publication_test_local_deadline_cancels_a_session_still_waiting(setup_ui):
    started = {**STARTED, "expires_in_seconds": 1}
    commands, output = run_publication_test(setup_ui, [GATEWAY, TUNNEL, started, {"state": "waiting"}, {"state": "waiting"}, {"state": "cancelled"}])

    assert commands[-1] == ["gateway", "publication-test", "cancel", TEST_ID]
    assert "expirou sem confirmação" in output
    assert "Acesso público confirmado" not in output


def test_publication_test_checks_confirmation_once_more_at_local_deadline(setup_ui):
    started = {**STARTED, "expires_in_seconds": 1}
    commands, output = run_publication_test(setup_ui, [GATEWAY, TUNNEL, started, {"state": "waiting"}, {"state": "confirmed"}])

    assert commands[-1] == ["gateway", "publication-test", "status", TEST_ID]
    assert "Acesso público confirmado" in output
    assert "expirou sem confirmação" not in output


@pytest.mark.parametrize("change", [
    {"url": None}, {"url": "http://apps.example.com/test"}, {"url": "https://apps.example.com/test\nsecret"},
    {"expires_in_seconds": 0}, {"expires_in_seconds": 121}, {"expires_in_seconds": True}, {"state": "confirmed"},
])
def test_publication_test_rejects_invalid_start_contract_and_cancels_created_session(setup_ui, change):
    commands, output = run_publication_test(setup_ui, [GATEWAY, TUNNEL, {**STARTED, **change}, {"state": "cancelled"}])

    assert commands[-1] == ["gateway", "publication-test", "cancel", TEST_ID]
    assert "Não foi possível confirmar o teste público" in output
    assert TEST_URL not in output


@pytest.mark.parametrize("identifier", [None, "", "not-an-id", "a" * 33])
def test_publication_test_does_not_forward_invalid_test_identifier(setup_ui, identifier):
    commands, output = run_publication_test(setup_ui, [GATEWAY, TUNNEL, {**STARTED, "test_id": identifier}])

    assert commands[-1] == ["gateway", "publication-test", "start", "--confirm"]
    assert "Não foi possível confirmar o teste público" in output


def test_publication_test_cancels_when_polling_returns_an_unknown_state(setup_ui):
    commands, output = run_publication_test(setup_ui, [GATEWAY, TUNNEL, STARTED, {"state": None}, {"state": "cancelled"}])

    assert commands[-1] == ["gateway", "publication-test", "cancel", TEST_ID]
    assert "Não foi possível confirmar o teste público" in output


@pytest.mark.parametrize("response", [None, [], subprocess.CompletedProcess([], 0, "invalid JSON", ""), subprocess.CompletedProcess([], 2, "", "token=private-secret"), OSError("private-secret")])
def test_publication_test_reports_sanitized_unavailable_status(setup_ui, response):
    commands, output = run_publication_test(setup_ui, [response])

    assert commands == [["gateway", "status"]]
    assert "Não foi possível confirmar o teste público" in output
    assert "private-secret" not in output


def test_publication_test_ctrl_c_cancels_the_waiting_session(setup_ui):
    commands, output = run_publication_test(setup_ui, [GATEWAY, TUNNEL, STARTED, KeyboardInterrupt(), {"state": "cancelled"}])

    assert commands[-1] == ["gateway", "publication-test", "cancel", TEST_ID]
    assert "Teste de acesso público cancelado" in output


def test_publication_test_keeps_error_recoverable_when_cancel_rpc_is_unavailable(setup_ui):
    commands, output = run_publication_test(setup_ui, [GATEWAY, TUNNEL, STARTED, OSError("private-secret"), OSError("cancel-secret")])

    assert commands[-1] == ["gateway", "publication-test", "cancel", TEST_ID]
    assert "Não foi possível confirmar o teste público" in output
    assert "private-secret" not in output
    assert "cancel-secret" not in output


def test_cancel_key_does_not_read_redirected_input(setup_ui):
    module, _ = setup_ui
    with patch.object(module.sys, "stdin", io.StringIO("x\n")):
        assert module.publication_test_cancelled() is False


@pytest.mark.parametrize("characters, expected", [([], False), (["a"], False), (["X"], True), (["\x1b"], True)])
def test_cancel_key_reads_windows_console_without_background_input(setup_ui, characters, expected):
    module, _ = setup_ui
    values = iter(characters)
    keyboard = SimpleNamespace(kbhit=MagicMock(side_effect=[True] * len(characters) + [False]), getwch=lambda: next(values))
    terminal = MagicMock()
    terminal.isatty.return_value = True
    with patch.object(module.sys, "stdin", terminal), patch.object(module.os, "name", "nt"), patch.dict(sys.modules, {"msvcrt": keyboard}):
        assert module.publication_test_cancelled() is expected


@pytest.mark.parametrize("ready, value, expected", [(False, "x\n", False), (True, "X\n", True), (True, "\n", False)])
def test_cancel_key_checks_available_posix_lines_without_blocking(setup_ui, ready, value, expected):
    module, _ = setup_ui
    terminal = MagicMock()
    terminal.isatty.return_value = True
    terminal.readline.return_value = value
    selector = SimpleNamespace(select=MagicMock(return_value=([terminal] if ready else [], [], [])))
    with patch.object(module.sys, "stdin", terminal), patch.object(module.os, "name", "posix"), patch.dict(sys.modules, {"select": selector}):
        assert module.publication_test_cancelled() is expected
    assert terminal.readline.call_count == int(ready)
