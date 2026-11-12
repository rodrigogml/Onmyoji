"""Contrato do MCP com o runtime central do Shikigami."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from onmyoji_daemon.mcp import runtime


def configure(root: Path, mode: str, executable: str = "") -> None:
    path = root / "configs" / "onmyoji-system.toml"
    path.parent.mkdir(parents=True)
    path.write_text("[python]\nmode = " + json.dumps(mode) + "\nbase_python = \"\"\nexecutable = " + json.dumps(executable) + "\n", encoding="utf-8")


def test_mcp_uses_root_venv_for_isolated_instances(tmp_path: Path) -> None:
    configure(tmp_path, "isolated")
    assert runtime.mcp_runtime_directory(tmp_path) == tmp_path / "venv"
    assert runtime.mcp_runtime_python(tmp_path).is_relative_to(tmp_path / "venv")


def test_mcp_uses_selected_global_python_without_venv(tmp_path: Path) -> None:
    configure(tmp_path, "global", sys.executable)
    assert runtime.mcp_runtime_python(tmp_path) == Path(sys.executable)
    assert not runtime.mcp_runtime_python(tmp_path).is_relative_to(tmp_path / "venv")


def test_mcp_runtime_requires_setuptools_for_advanced_ocr(tmp_path: Path, monkeypatch) -> None:
    configure(tmp_path, "isolated")
    captured = {}
    monkeypatch.setattr(runtime.shared_runtime, "ensure", lambda _root, requirements, probe, _progress: captured.update(requirements=requirements, probe=probe) or "pronto")

    assert runtime.ensure_mcp_runtime(tmp_path) == "pronto"
    assert runtime.SETUPTOOLS_REQUIREMENT in captured["requirements"]
    assert "import setuptools" in captured["probe"]
