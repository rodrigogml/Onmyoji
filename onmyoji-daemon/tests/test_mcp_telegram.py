"""Contrato do Telegram publicado pelo MCP, sem acesso ao token do bot."""
from __future__ import annotations

from pathlib import Path

import pytest

from onmyoji_daemon.mcp.telegram.service import TelegramMcpError, TelegramMcpService
from onmyoji_daemon.mcp.telegram.tools import TelegramToolRegistry


def enabled(root: Path) -> None:
    path = root / "shikigami" / "daemon" / "telegram.toml"; path.parent.mkdir(parents=True)
    path.write_text("enabled = true\n", encoding="utf-8")


def test_tools_require_enabled_gateway(tmp_path: Path) -> None:
    assert TelegramToolRegistry(tmp_path).definitions() == []
    enabled(tmp_path)
    assert {tool["name"] for tool in TelegramToolRegistry(tmp_path).definitions()} == {"telegram.list_channels", "telegram.send", "telegram.remove_channel"}


def test_list_filters_registered_channels_without_exposing_rpc_details(tmp_path: Path) -> None:
    calls = []
    service = TelegramMcpService(tmp_path, lambda *args, **kwargs: calls.append((args, kwargs)) or [{"id": "group:1:0", "name": "Financeiro"}, {"id": "group:2:0", "name": "Operação"}], lambda _root: ("127.0.0.1", 1, "secret"))
    assert service.list_channels("fin") == {"channels": [{"id": "group:1:0", "name": "Financeiro"}]}
    assert calls[0][0][3] == "telegram.channels.list"


def test_external_writes_require_confirmation_and_delegate_to_gateway(tmp_path: Path) -> None:
    calls = []
    service = TelegramMcpService(tmp_path, lambda *args, **kwargs: calls.append((args, kwargs)) or {"message_id": 10}, lambda _root: ("127.0.0.1", 1, "secret"))
    with pytest.raises(TelegramMcpError, match="confirm=true"):
        service.send("group:1:0", "Olá")
    assert service.send("group:1:0", "Olá", confirm=True) == {"message_id": 10}
    assert calls[0][0][3] == "telegram.outbound.send"
    with pytest.raises(TelegramMcpError, match="confirm=true"):
        service.remove_channel("group:1:0", False)
