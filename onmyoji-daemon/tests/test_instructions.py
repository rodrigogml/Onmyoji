from __future__ import annotations

from pathlib import Path

import pytest

from onmyoji_daemon.instructions import InstructionComposer, InstructionError


def test_composition_has_order_and_overlay(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"; path = tmp_path / "configs" / "daemon" / "instructions" / "shikigami.md"; path.parent.mkdir(parents=True); path.write_text("Regra local.", encoding="utf-8")
    bundle = InstructionComposer(tmp_path, data, "shikigami.md").compose(identity="Lavelinha", telegram=True, reply_mode="audio")
    assert bundle.text.index("[ONMYŌJI") < bundle.text.index("[SHIKIGAMI — IDENTIDADE]") < bundle.text.index("[SHIKIGAMI — INSTRUÇÕES PARTICULARES]") < bundle.text.index("[TELEGRAM — CONTRATO")
    assert "Regra local." in bundle.text and "Resposta final: audio" in bundle.text


def test_instruction_path_cannot_escape_authorized_root(tmp_path):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    with pytest.raises(InstructionError): InstructionComposer(tmp_path, data, "../outside.md").compose(identity="Akuma", telegram=False)
