import importlib.util
from pathlib import Path
import sys


def setup_module():
    path = Path(__file__).resolve().parents[1] / "setupMcp.py"
    specification = importlib.util.spec_from_file_location("setup_mcp_mercado_test", path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_modules_are_selected_manually_and_can_be_toggled(monkeypatch):
    module = setup_module()
    answers = iter(["  mercadolivre.account", "  mercadopago.payments", "✓ mercadolivre.account", "Concluir seleção"])
    monkeypatch.setattr(module, "flow_choice", lambda *args: next(answers))
    monkeypatch.setattr(module, "note", lambda *args: None)
    assert module.choose_mercado_modules([]) == ["mercadopago.payments"]


def test_referenced_application_is_detected_before_removal():
    module = setup_module()
    data = {"profiles": {"pessoal": {"mercadolivre": {"application": "principal"}}, "outro": {"mercadopago": {"application": "principal"}}}}
    assert module._mercado_app_references(data, "mercadolivre", "principal") == ["pessoal"]
    assert module._mercado_app_references(data, "mercadopago", "principal") == ["outro"]
