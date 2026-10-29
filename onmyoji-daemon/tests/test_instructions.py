from __future__ import annotations

from pathlib import Path

import pytest

from onmyoji_daemon.instructions import InstructionComposer, InstructionError
from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.instructions import McpInstructionRegistry


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
    assert "[TELEGRAM GROUP CONTEXT]" not in bundle.text
