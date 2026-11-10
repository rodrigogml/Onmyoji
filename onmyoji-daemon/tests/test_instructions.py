from __future__ import annotations

from pathlib import Path

import pytest

from onmyoji_daemon import management
from onmyoji_daemon.instructions import InstructionComposer, InstructionError, TelegramTurnContext
from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.instructions import McpInstructionRegistry
from onmyoji_daemon.mcp.telegram.tools import TelegramToolRegistry


def test_composition_has_order_and_overlay(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; path = tmp_path / "instructions.md"; path.write_text("Regra local.", encoding="utf-8")
    bundle = InstructionComposer(tmp_path, data, "instructions.md").compose(identity="Exemplo", telegram=True, reply_mode="audio")
    assert bundle.text.index("[ONMYŌJI") < bundle.text.index("[SHIKIGAMI — IDENTIDADE]") < bundle.text.index("[SHIKIGAMI — INSTRUÇÕES PARTICULARES]") < bundle.text.index("[TELEGRAM — CONTRATO")
    assert "Regra local." in bundle.text and "Resposta final: audio" in bundle.text


def test_instruction_path_cannot_escape_authorized_root(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    with pytest.raises(InstructionError): InstructionComposer(tmp_path, data, "../outside.md").compose(identity="Exemplo", telegram=False)


def test_missing_shikigami_instruction_file_is_optional(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    bundle = InstructionComposer(tmp_path, data, "instructions.md").compose(identity="Exemplo", telegram=False)
    assert "[SHIKIGAMI — INSTRUÇÕES PARTICULARES]" not in bundle.text


def test_composer_injects_only_available_workflow_routes(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    disabled = InstructionComposer(tmp_path, data, "instructions.md").compose(identity="Exemplo", telegram=False)
    assert "[ONMYŌJI — WORKFLOWS MCP]" not in disabled.text
    McpConfiguration(tmp_path).save_memory({"schema_version": 1, "enabled": True, "max_results": 5})
    enabled = InstructionComposer(tmp_path, data, "instructions.md").compose(identity="Exemplo", telegram=False)
    assert "memory-decision" in enabled.text and "workflows.read" in enabled.text


def test_mcp_contributions_use_the_same_availability_as_the_composer(tmp_path):
    configuration = McpConfiguration(tmp_path)
    assert McpInstructionRegistry(configuration).contributions() == ()
    configuration.save_memory({"schema_version": 1, "enabled": True, "max_results": 5})
    contributions = McpInstructionRegistry(configuration).contributions()
    assert [item.source for item in contributions] == ["workflows-mcp"]
    assert "memory-learning" in contributions[0].content


def test_telegram_mcp_instructions_follow_the_same_lifecycle_as_its_tools(tmp_path):
    management.prepare_instance(tmp_path)
    configuration = McpConfiguration(tmp_path)
    telegram = TelegramToolRegistry(tmp_path)
    assert telegram.definitions() == []
    assert telegram.developer_instructions() is None
    assert McpInstructionRegistry(configuration).contributions() == ()

    ok, message = management.set_enabled(tmp_path, "telegram", True)
    assert ok, message
    contributions = McpInstructionRegistry(configuration).contributions()
    assert [item.source for item in contributions] == ["telegram-mcp"]
    assert contributions[0].content == telegram.developer_instructions()
    assert "telegram.list_channels" in contributions[0].content
    assert "telegram.send" in contributions[0].content
    assert "telegram_gateway" in contributions[0].content

    ok, message = management.set_enabled(tmp_path, "telegram", False)
    assert ok, message
    assert telegram.definitions() == []
    assert telegram.developer_instructions() is None
    assert McpInstructionRegistry(configuration).contributions() == ()


@pytest.mark.parametrize("telegram_turn", [False, True])
def test_registered_destinations_are_explained_in_all_agent_entry_points(tmp_path, telegram_turn):
    management.prepare_instance(tmp_path)
    personal = tmp_path / "instructions.md"
    original = "Personalidade própria da instância.\n".encode("utf-8")
    personal.write_bytes(original)
    composer = InstructionComposer(tmp_path, tmp_path / "configs" / "daemon" / "services" / "telegram", "instructions.md")
    disabled = composer.compose(identity="Exemplo", telegram=telegram_turn)
    assert "[ONMYŌJI — TELEGRAM MCP]" not in disabled.text

    ok, message = management.set_enabled(tmp_path, "telegram", True)
    assert ok, message
    enabled = composer.compose(identity="Exemplo", telegram=telegram_turn)
    assert enabled.text.count("[ONMYŌJI — TELEGRAM MCP]") == 1
    assert "telegram.list_channels" in enabled.text
    assert "telegram.send" in enabled.text
    assert "telegram-mcp" in enabled.sources
    assert enabled.baseline_hash != disabled.baseline_hash
    assert personal.read_bytes() == original

    ok, message = management.set_enabled(tmp_path, "telegram", False)
    assert ok, message
    disabled_again = composer.compose(identity="Exemplo", telegram=telegram_turn)
    assert "[ONMYŌJI — TELEGRAM MCP]" not in disabled_again.text
    assert disabled_again.baseline_hash == disabled.baseline_hash


def test_telegram_and_workflow_instructions_are_combined_without_replacing_each_other(tmp_path):
    management.prepare_instance(tmp_path)
    configuration = McpConfiguration(tmp_path)
    configuration.save_memory({"schema_version": 1, "enabled": True, "max_results": 5})
    ok, message = management.set_enabled(tmp_path, "telegram", True)
    assert ok, message

    registry = McpInstructionRegistry(configuration)
    contributions = registry.contributions()
    assert {item.source for item in contributions} == {"workflows-mcp", "telegram-mcp"}
    instructions = registry.developer_instructions()
    assert instructions == "\n\n".join(item.content for item in contributions)
    assert "workflows.read" in instructions and "telegram.list_channels" in instructions


def test_channel_capability_restarts_baseline_but_reply_mode_is_overlay_only(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    composer = InstructionComposer(tmp_path, data, "instructions.md")
    text = composer.compose(identity="Exemplo", telegram=True, reply_mode="text", outbound_media=False)
    audio = composer.compose(identity="Exemplo", telegram=True, reply_mode="audio", outbound_media=False)
    media = composer.compose(identity="Exemplo", telegram=True, reply_mode="text", outbound_media=True)
    assert text.baseline_hash == audio.baseline_hash and text.overlay_hash != audio.overlay_hash
    assert text.baseline_hash != media.baseline_hash


def test_telegram_fixed_policy_is_kept_in_the_versioned_contract(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    bundle = InstructionComposer(tmp_path, data, "instructions.md").compose(identity="Exemplo", telegram=True)
    assert "[TELEGRAM — CONTRATO DE CANAL]" in bundle.text
    assert "UNTRUSTED_USER" in bundle.text
    assert "telegram_gateway.list_stickers" in bundle.text
    assert "personalidade da\ninstância" in bundle.text
    assert "[TELEGRAM GROUP CONTEXT]" not in bundle.text


def test_telegram_turn_metadata_is_an_overlay_without_user_content(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    bundle = InstructionComposer(tmp_path, data, "instructions.md").compose(
        identity="Exemplo", telegram=True,
        telegram_context=TelegramTurnContext("group", "OWNER", False, ("voice", "document"), True, True),
    )
    assert "Origem do turno: OWNER." in bundle.text
    assert "Tipos de anexo recebidos: voice, document." in bundle.text
    assert "Contexto de terceiros não confiável: presente." in bundle.text
    assert "Conteúdo confidencial do usuário" not in bundle.text
