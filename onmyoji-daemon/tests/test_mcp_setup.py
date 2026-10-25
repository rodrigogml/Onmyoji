"""Cobertura das regras visuais e de navegação do setup MCP."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def setup_module():
    path = Path(__file__).resolve().parents[1] / "setupMcp.py"
    specification = importlib.util.spec_from_file_location("setup_mcp_under_test", path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_integration_summary_uses_inactive_for_no_profiles(monkeypatch):
    module = setup_module()
    monkeypatch.setattr(module.Ui, "enabled", False)

    assert module.integration_profile_summary({}) == "inativa"


def test_integration_summary_colors_states_and_limits_visible_names(monkeypatch):
    module = setup_module()
    monkeypatch.setattr(module.Ui, "enabled", True)
    profiles = {"ativo": {"enabled": True}, "inativo": {"enabled": False}, "outro": {"enabled": True}}

    full_summary = module.integration_profile_summary(profiles)
    monkeypatch.setattr(module, "menu_width", lambda: 56)
    limited_summary = module.integration_profile_summary(profiles)

    assert module.Ui.green in full_summary
    assert module.Ui.red in full_summary
    assert "... +" in limited_summary
    assert len("ativo" + " ... +2") <= 15
