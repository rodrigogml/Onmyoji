"""Registro do MCP Onmyōji no Codex CLI."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from onmyoji_daemon.mcp import codex


def completed(returncode: int = 0, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def test_ensure_registers_absent_server_with_daemon_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = tmp_path / "runtime" / "python"
    monkeypatch.setattr(codex, "runtime_ready", lambda _root: True)
    monkeypatch.setattr(codex, "daemon_python", lambda _root: runtime)
    calls: list[tuple[list[str], dict]] = []

    def run(command: list[str], **kwargs):
        calls.append((command, kwargs))
        return completed(stdout="[]") if command[1:4] == ["mcp", "list", "--json"] else completed()

    monkeypatch.setattr(codex.subprocess, "run", run)

    registration = codex.CodexMcpRegistration(tmp_path, "codex")
    assert "registrado" in registration.ensure()
    assert calls[1][0] == [
        "codex", "mcp", "add", "onmyoji", "--env", f"PYTHONPATH={registration.source_directory}", "--",
        str(runtime), "-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(tmp_path.resolve()),
    ]
    assert calls[1][1]["env"]["CODEX_HOME"] == str(tmp_path.resolve())


def test_ensure_keeps_matching_registration_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = tmp_path / "runtime" / "python"
    monkeypatch.setattr(codex, "runtime_ready", lambda _root: True)
    monkeypatch.setattr(codex, "daemon_python", lambda _root: runtime)
    registration = codex.CodexMcpRegistration(tmp_path, "codex")
    existing = {
        "name": "onmyoji", "transport": {
            "type": "stdio", "command": str(runtime),
            "args": ["-m", "onmyoji_daemon.mcp.server", "--onmyoji-root", str(tmp_path.resolve())],
            "env": {"PYTHONPATH": str(registration.source_directory)},
        },
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(codex.subprocess, "run", lambda command, **_kwargs: calls.append(command) or completed(stdout=json.dumps([existing])))

    assert "já está válido" in registration.ensure()
    assert len(calls) == 1


def test_ensure_refuses_conflicting_registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex, "runtime_ready", lambda _root: True)
    monkeypatch.setattr(codex, "daemon_python", lambda _root: tmp_path / "runtime" / "python")
    conflict = {"name": "onmyoji", "transport": {"type": "stdio", "command": "python", "args": []}}
    monkeypatch.setattr(codex.subprocess, "run", lambda *_args, **_kwargs: completed(stdout=json.dumps([conflict])))

    with pytest.raises(codex.CodexMcpError, match="configuração diferente"):
        codex.CodexMcpRegistration(tmp_path, "codex").ensure()
