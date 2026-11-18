"""O setup distingue configuração salva e disponibilidade do listener de eventos."""
import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def setup_module():
    spec = importlib.util.spec_from_file_location("setup_notification_health", Path(__file__).resolve().parents[1] / "setupOnmyoji.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def http_failure():
    return {"state": "failed", "host": "127.0.0.1", "port": 39392, "last_error": "HTTP de eventos indisponível em 127.0.0.1:39392: porta em uso."}


def test_notification_diagnostic_identifies_the_conflicting_listener(setup_module, tmp_path, monkeypatch, capsys):
    answers = iter(["4", "x"])
    monkeypatch.setattr(setup_module, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(setup_module, "local_daemon_rpc", lambda *_args, **_kwargs: {
        "checks": {"http_listener": False}, "counts": {}, "http": http_failure(),
    })

    setup_module.notification_menu(tmp_path)

    assert http_failure()["last_error"] in capsys.readouterr().out


def test_enabling_notifications_does_not_claim_success_when_only_telegram_is_running(setup_module, tmp_path, monkeypatch, capsys):
    answers = iter(["1", "x"])
    monkeypatch.setattr(setup_module, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(setup_module, "save_notification_configuration", lambda *_args: {})

    def rpc(_root, method, *_args):
        return {"http": http_failure()} if method == "notification.status" else {"state": "running"}

    monkeypatch.setattr(setup_module, "local_daemon_rpc", rpc)

    setup_module.notification_menu(tmp_path)

    output = capsys.readouterr().out
    assert "Configuração salva, mas o listener de eventos não está disponível" in output
    assert "127.0.0.1:39392" in output
    assert "Ingresso configurado; confira o diagnóstico." not in output


def test_saved_port_change_reports_listener_failure_and_daemon_restart(setup_module, tmp_path, monkeypatch, capsys):
    answers = iter(["2", "39392", "x"])
    monkeypatch.setattr(setup_module, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(setup_module, "save_notification_configuration", lambda *_args: {"http": http_failure(), "restart_required": True})

    setup_module.notification_menu(tmp_path)

    output = capsys.readouterr().out
    assert http_failure()["last_error"] in output
    assert "Reinicie o daemon para aplicar a nova porta." in output
