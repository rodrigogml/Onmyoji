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
