from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "setupOnmyoji.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_setup_telegram_commands", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

from onmyoji_daemon.telegram_commands import command_catalog_revision, owner_commands


def confirmed_commands(totp_enabled: bool = False) -> dict:
    return {
        "owners": 1, "verified": 1, "failed": 0,
        "expected": [item["command"] for item in owner_commands(totp_enabled)],
        "catalog_revision": command_catalog_revision(totp_enabled),
        "totp_enabled": totp_enabled,
    }


def run_setup(tmp_path, monkeypatch, response, *, returncode=0, wizard=False, local_totp=False):
    project = tmp_path / "onmyoji-daemon"
    project.mkdir()
    (project / "setupDaemon.py").touch()
    manifest = tmp_path / "configs" / "daemon" / "daemon.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('[app_server]\nenabled = true\n', encoding="utf-8")
    configuration = MODULE.telegram_configuration_path(tmp_path)
    configuration.parent.mkdir(parents=True, exist_ok=True)
    configuration.write_text(
        '[telegram]\nvault_profile = "default"\ntoken_entry = "APIs/Telegram:Example"\n'
        f'[totp]\nenabled = {str(local_totp).lower()}\n', encoding="utf-8",
    )
    answers = iter(["t", "w", "", "", "s", "", "x", "x"] if wizard else ["t", "4", "2", "x", "x", "x"])
    reports, calls = [], []

    def run(arguments, **_kwargs):
        calls.append(arguments)
        code, output = 0, []
        if "sync-commands" in arguments:
            code, output = returncode, response
        elif "profiles" in arguments:
            output = ["default"]
        elif "check-entry" in arguments:
            output = {"exists": True, "message": "Credencial configurada."}
        elif "pair-request" in arguments:
            output = {"pin": "123456"}
        elif "validation-json" in arguments:
            output = [{"label": "Owners pareados", "state": "ok", "detail": "1 owner pareado."}]
        return subprocess.CompletedProcess(arguments, code, stdout=json.dumps(output), stderr="")

    monkeypatch.setattr(MODULE, "load_execution_profile", lambda _root: {})
    monkeypatch.setattr(MODULE, "execution_diagnostics", lambda *_args: [])
    monkeypatch.setattr(MODULE, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(MODULE, "flow_choice", lambda _title, choices, _default: choices[0])
    monkeypatch.setattr(MODULE, "screen", lambda *_args: None)
    monkeypatch.setattr(MODULE, "result", lambda ok, detail: reports.append((ok, detail)))
    monkeypatch.setattr(MODULE.subprocess, "run", run)

    MODULE.daemon_menu(tmp_path)

    assert sum("sync-commands" in call for call in calls) == 1
    return reports


@pytest.mark.parametrize("totp_enabled", [False, True])
def test_telegram_command_menu_reports_success_only_for_the_current_local_catalog(tmp_path, monkeypatch, totp_enabled):
    reports = run_setup(tmp_path, monkeypatch, confirmed_commands(totp_enabled), local_totp=totp_enabled)

    assert reports == [(True, "Comandos privados do catálogo atual confirmados para 1/1 owner(s).")]


@pytest.mark.parametrize("changes", [
    {"verified": 0, "failed": 1},
    {"owners": 0, "verified": 0},
    {"expected": ["new", "config", "channel", "usage"]},
    {"catalog_revision": "previous-version"},
    {"pending": True},
])
def test_telegram_command_menu_rejects_incomplete_or_stale_confirmation_even_with_exit_zero(tmp_path, monkeypatch, changes):
    response = confirmed_commands()
    response.update(changes)

    reports = run_setup(tmp_path, monkeypatch, response)

    assert len(reports) == 1 and reports[0][0] is False


def test_telegram_command_menu_checks_totp_against_the_local_configuration(tmp_path, monkeypatch):
    reports = run_setup(tmp_path, monkeypatch, confirmed_commands(True), local_totp=False)

    assert len(reports) == 1 and reports[0][0] is False


def test_telegram_command_menu_reports_an_active_sync_without_requesting_a_restart(tmp_path, monkeypatch):
    response = confirmed_commands()
    response["in_progress"] = True

    reports = run_setup(tmp_path, monkeypatch, response, returncode=2)

    assert len(reports) == 1 and reports[0][0] is False
    assert "em andamento" in reports[0][1]
    assert "Reinicie" not in reports[0][1]


@pytest.mark.parametrize("response,returncode", [([], 0), (None, 0), (confirmed_commands(), 2)])
def test_telegram_command_menu_rejects_invalid_response_or_failed_cli(tmp_path, monkeypatch, response, returncode):
    reports = run_setup(tmp_path, monkeypatch, response, returncode=returncode)

    assert len(reports) == 1 and reports[0][0] is False


@pytest.mark.parametrize("confirmed", [False, True])
def test_telegram_wizard_requires_confirmed_commands_before_its_final_diagnosis(tmp_path, monkeypatch, capsys, confirmed):
    response = confirmed_commands()
    if not confirmed:
        response.update({"verified": 0, "failed": 1})

    reports = run_setup(tmp_path, monkeypatch, response, wizard=True)

    output = capsys.readouterr().out
    assert ("10. Diagnóstico final" in output) is confirmed
    assert any(ok and detail == "Telegram configurado e pronto." for ok, detail in reports) is confirmed
    if not confirmed:
        assert reports[-1][0] is False
