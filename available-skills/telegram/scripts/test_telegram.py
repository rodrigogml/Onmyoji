"""Contrato da skill outbound: não espera respostas nem acessa Telegram diretamente."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


def load_cli():
    source = Path(__file__).with_name("telegram.py")
    spec = importlib.util.spec_from_file_location("telegram_skill_cli", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_list_filters_by_name_without_remote_discovery(monkeypatch):
    cli = load_cli()
    monkeypatch.setattr(cli, "endpoint", lambda _: ("127.0.0.1", 12, "token"))
    called = []
    def rpc(host, port, token, method, params=None, timeout=5):
        called.append(method)
        return [{"id": "9", "name": "Proprietário"},
                {"id": "group:-1000:7", "name": "Financeiro"}]
    result = cli.invoke("list", argparse.Namespace(query="finan"), rpc=rpc)
    assert result == {"ok": True, "channels": [{"id": "group:-1000:7", "name": "Financeiro"}]}
    assert called == ["telegram.channels.list"]


def test_send_reports_receipt_not_response(monkeypatch):
    cli = load_cli()
    monkeypatch.setattr(cli, "endpoint", lambda _: ("127.0.0.1", 12, "token"))
    received = {}
    def rpc(host, port, token, method, params=None, timeout=5):
        received.update({"method": method, "params": params, "timeout": timeout})
        return {"sent": True, "message_id": 120, "channel": params["channel"]}
    args = argparse.Namespace(channel="9", text="Pergunta?", file=None, kind="document", new_topic="")
    result = cli.invoke("send", args, rpc=rpc)
    assert result == {"ok": True, "sent": True, "message_id": 120, "channel": "9"}
    assert received["method"] == "telegram.outbound.send"
    assert received["params"] == {"channel": "9", "text": "Pergunta?"}
    assert received["timeout"] == 120


def test_remove_only_calls_catalog_removal(monkeypatch):
    cli = load_cli()
    monkeypatch.setattr(cli, "endpoint", lambda _: ("127.0.0.1", 12, "token"))
    received = []
    def rpc(host, port, token, method, params=None, timeout=5):
        received.append((method, params))
        return {"removed": True, "channel": params["channel"]}
    result = cli.invoke("remove", argparse.Namespace(channel="9"), rpc=rpc)
    assert result["removed"]
    assert received == [("telegram.channels.remove", {"channel": "9"})]


def test_send_without_content_refuses_operation(monkeypatch):
    cli = load_cli()
    monkeypatch.setattr(cli, "endpoint", lambda _: ("127.0.0.1", 12, "token"))
    args = argparse.Namespace(channel="9", text="", file=None, kind="document", new_topic="")
    try:
        cli.invoke("send", args, rpc=lambda *_args, **_kw: (_ for _ in ()).throw(AssertionError("RPC foi chamado")))
    except ValueError as error:
        assert "Informe" in str(error)
    else:
        raise AssertionError("Deveria rejeitar envio vazio")
