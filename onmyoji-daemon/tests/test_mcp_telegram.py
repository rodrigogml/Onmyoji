"""Contrato do Telegram publicado pelo MCP, sem acesso ao token do bot."""
from __future__ import annotations

from pathlib import Path

import pytest

from onmyoji_daemon import management
from onmyoji_daemon.mcp.telegram.service import TelegramMcpError, TelegramMcpService
from onmyoji_daemon.mcp.telegram.tools import TelegramToolRegistry
from onmyoji_daemon.supervisor import Supervisor


TELEGRAM_TOOLS = {"telegram.list_channels", "telegram.send", "telegram.send_location", "telegram.remove_channel"}


def test_tools_remain_hidden_when_enable_requires_daemon_installation(tmp_path: Path) -> None:
    ok, _message = management.set_enabled(tmp_path, "telegram", True)

    assert not ok
    assert TelegramToolRegistry(tmp_path).definitions() == []


@pytest.mark.parametrize("legacy_enabled", [None, False, True])
def test_tools_follow_gateway_lifecycle_and_ignore_legacy_flag(tmp_path: Path, legacy_enabled: bool | None) -> None:
    management.prepare_instance(tmp_path)
    config = tmp_path / "configs" / "daemon" / "services" / "telegram" / "config.toml"
    if legacy_enabled is not None:
        config.parent.mkdir(parents=True)
        config.write_text(f"enabled = {str(legacy_enabled).lower()}\n", encoding="utf-8")
    registry = TelegramToolRegistry(tmp_path)
    assert registry.definitions() == []

    ok, message = management.set_enabled(tmp_path, "telegram", True)
    assert ok, message
    assert {tool["name"] for tool in registry.definitions()} == TELEGRAM_TOOLS

    ok, message = management.set_enabled(tmp_path, "telegram", False)
    assert ok, message
    assert registry.definitions() == []
    with pytest.raises(TelegramMcpError, match="não está habilitada"):
        registry.call("telegram.list_channels", {})

    ok, message = management.set_enabled(tmp_path, "telegram", True)
    assert ok, message
    assert {tool["name"] for tool in registry.definitions()} == TELEGRAM_TOOLS
    if legacy_enabled is None:
        assert not config.exists()
    else:
        assert config.read_text(encoding="utf-8") == f"enabled = {str(legacy_enabled).lower()}\n"


def test_tools_follow_enable_and_disable_persisted_by_supervisor(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path)
    registry = TelegramToolRegistry(tmp_path)
    try:
        assert registry.definitions() == []
        assert supervisor.handle("enable", {"service": "telegram"})["enabled"]
        assert {tool["name"] for tool in registry.definitions()} == TELEGRAM_TOOLS
        assert not supervisor.handle("disable", {"service": "telegram"})["enabled"]
        assert registry.definitions() == []
    finally:
        supervisor.rpc.close()


@pytest.mark.parametrize("state", [
    None, b"{", b"\xff", b"null", b"[]", b"{}", b'{"telegram": null}',
    b'{"telegram": []}', b'{"telegram": true}', b'{"telegram": {}}',
    b'{"telegram": {"enabled": false}}',
])
def test_tools_remain_hidden_for_missing_or_invalid_service_state(tmp_path: Path, state: bytes | None) -> None:
    management.prepare_instance(tmp_path)
    config = tmp_path / "configs" / "daemon" / "services" / "telegram" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("enabled = true\n", encoding="utf-8")
    if state is not None:
        (tmp_path / "configs" / "daemon" / "services.json").write_bytes(state)
    registry = TelegramToolRegistry(tmp_path)

    assert not registry.available()
    assert registry.definitions() == []
    assert registry.developer_instructions() is None
    with pytest.raises(TelegramMcpError, match="não está habilitada"):
        registry.call("telegram.list_channels", {})


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
