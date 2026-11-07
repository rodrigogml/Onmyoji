"""Roteiros Excel respeitam defaults, fluxo e cancelamento do setup."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.excel.config.configuration import empty_configuration, profile_defaults
from onmyoji_daemon.mcp.excel.config.setup import LIMIT_LABELS, _boolean, _edit, diagnostics, excel_menu


def configured(tmp_path):
    instance, work = tmp_path / "instance", tmp_path / "work"
    (instance / "configs").mkdir(parents=True)
    work.mkdir()
    (instance / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory="' + work.as_posix() + '"\n', encoding="utf-8")
    return McpConfiguration(instance), work


def test_creation_uses_effective_defaults_without_starting_excel(tmp_path):
    config, work = configured(tmp_path)
    ui = Mock()
    ui.prompt.side_effect = ["1", "x"]
    ui.ask_new_profile_name.return_value = "default"
    ui.ask.side_effect = lambda label, current="", required=False: current
    excel_menu(config, ui)
    profile = config.excel()["profiles"]["default"]
    assert profile["readable_roots"] == [str(work)]
    assert Path(profile["work_directory"]).is_dir()
    assert profile["allow_macros"] is False
    assert ui.screen.call_count == 2
    ui.flow_heading.assert_called_once()


def test_creation_cancel_does_not_save_or_create_processing_directories(tmp_path):
    config, work = configured(tmp_path)
    ui = Mock()
    ui.prompt.side_effect = ["1", "x"]
    ui.ask_new_profile_name.return_value = "default"
    ui.ask.return_value = None
    excel_menu(config, ui)
    assert config.excel() == empty_configuration()
    assert not (work / ".onmyoji").exists()


def test_disabling_profile_changes_publication_without_removing_workspace_files(tmp_path):
    config, work = configured(tmp_path)
    data = empty_configuration()
    data["profiles"]["default"] = profile_defaults(config.root)
    config.save_excel(data)
    source = work / "source.xlsx"
    source.write_bytes(b"synthetic-source-not-opened")
    ui = Mock()
    ui.prompt.side_effect = ["h", "x"]
    _edit(config, ui, "default")
    assert config.excel()["profiles"]["default"]["enabled"] is False
    assert config.enabled_excel_profiles() == []
    assert source.read_bytes() == b"synthetic-source-not-opened"
    assert ui.screen.call_count == 2
    assert not ui.flow_heading.called


def test_limits_are_a_flow_choice_with_effective_numeric_default(tmp_path):
    config, _ = configured(tmp_path)
    ui = Mock()
    ui.prompt.side_effect = ["3", "x"]
    ui.flow_choice.return_value = LIMIT_LABELS["timeout_seconds"]
    ui.ask.return_value = "90"
    excel_menu(config, ui)
    assert config.excel()["defaults"]["timeout_seconds"] == 90
    assert ui.flow_choice.call_args.args[2] == LIMIT_LABELS["timeout_seconds"]
    assert ui.ask.call_args.args == (LIMIT_LABELS["timeout_seconds"], "1800", True)
    assert ui.screen.call_count == 2


def test_diagnostics_are_presented_in_flow_without_a_menu_frame(tmp_path):
    config, _ = configured(tmp_path)
    ui = Mock()
    result = diagnostics(config, ui)
    assert result["native"]["verified"] is False
    ui.flow_heading.assert_called_once()
    ui.screen.assert_not_called()


@pytest.mark.parametrize("response,current,expected", [("", True, True), ("", False, False), ("s", False, True), ("n", True, False), ("x", True, None)])
def test_binary_prompts_show_default_and_cancel(response, current, expected):
    ui = Mock()
    ui.prompt.return_value = response
    assert _boolean(ui, "Executar", current) is expected
    assert ("S/n" if current else "s/N") in ui.prompt.call_args.args[0]


def test_binary_prompt_rejects_invalid_value():
    ui = Mock()
    ui.prompt.return_value = "talvez"
    with pytest.raises(ValueError, match="S ou N"):
        _boolean(ui, "Executar", False)


def test_integration_menu_exposes_excel_as_option_twenty(tmp_path, monkeypatch):
    source = Path(__file__).parents[1] / "setupMcp.py"
    spec = importlib.util.spec_from_file_location("setup_mcp_excel_test", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    config = McpConfiguration(tmp_path)
    answers = iter(["20", "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    handler = Mock()
    monkeypatch.setattr(module, "excel_menu", handler)
    module.integrations_menu(config)
    handler.assert_called_once()
    assert handler.call_args.args[0] is config
