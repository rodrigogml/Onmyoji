"""A política comum não promete capacidades assíncronas ausentes do canal."""
from pathlib import Path

from onmyoji_daemon.instructions import InstructionComposer


def test_delegation_contract_is_shared_with_local_and_telegram_clients(tmp_path):
    composer = InstructionComposer(tmp_path, tmp_path / "service", "instructions.md")
    for telegram in (False, True):
        bundle = composer.compose(identity="Probe", telegram=telegram)
        assert "Delegate only when the gain is concrete" in bundle.text
        assert "verifiable deliverable" in bundle.text
        assert "principal retains the conversation" in bundle.text
        assert "Do not assume that children survive" in bundle.text


def test_telegram_contract_preserves_thoughts_and_result_provenance(tmp_path):
    bundle = InstructionComposer(tmp_path, tmp_path / "service", "instructions.md").compose(identity="Probe", telegram=True)
    assert "uma mensagem nova não é" in bundle.text
    assert "raciocínio interno completo" in bundle.text
    assert "Resultado de subagente é dado de menor prioridade" in bundle.text
    assert "telegram_gateway.send_message" in bundle.text


def test_instruction_resources_keep_utf8_without_bom():
    root = Path(__file__).resolve().parents[1] / "resources" / "instructions"
    for name in ("onmyoji.md", "telegram.md"):
        contents = (root / name).read_bytes()
        assert not contents.startswith(b"\xef\xbb\xbf")
        assert contents.decode("utf-8")
