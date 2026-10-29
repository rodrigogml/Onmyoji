"""Contrato comum de inicialização do Codex."""
from __future__ import annotations

from pathlib import Path

from onmyoji_daemon import codex_execution


def data(workspace: Path) -> dict[str, object]:
    return {
        "executable": "codex",
        "project_directory": str(workspace),
        "model": "gpt-test",
        "model_reasoning_effort": "medium",
        "sandbox_mode": "workspace-write",
        "approval_policy": "never",
        "additional_writable_directories": ["reports"],
    }


def test_direct_cli_and_app_server_share_instance_context(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"; workspace.mkdir()
    launch = codex_execution.CodexLaunch.from_data(tmp_path, data(workspace))
    reconciled = []
    monkeypatch.setattr(codex_execution.McpReconciliation, "reconcile_if_pending", lambda _self, registration: reconciled.append(registration) or True)

    direct, direct_cwd, direct_environment = launch.direct_launch()
    command, cwd, environment = launch.app_server_launch()

    assert direct == ["codex", "--add-dir", "reports"]
    assert direct_cwd == cwd and direct_environment["CODEX_HOME"] == environment["CODEX_HOME"]
    assert command == ["codex", "app-server", "--stdio"]
    assert cwd == workspace.resolve()
    assert environment["CODEX_HOME"] == str(tmp_path.resolve())
    assert launch.model == "gpt-test" and launch.effort == "medium"
    assert launch.sandbox == "workspace-write" and launch.approval == "never"
    assert len(reconciled) == 2


def test_interactive_launcher_uses_selected_daemon_runtime(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"; workspace.mkdir()
    runtime = tmp_path / "runtime" / "python"
    monkeypatch.setattr(codex_execution, "runtime_ready", lambda _root: True)
    monkeypatch.setattr(codex_execution, "daemon_python", lambda _root: runtime)

    command, cwd, environment = codex_execution.CodexLaunch.from_data(tmp_path, data(workspace)).interactive_launcher()

    assert command == [str(runtime), "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(tmp_path.resolve()), "interactive"]
    assert cwd == workspace.resolve()
    assert environment["CODEX_HOME"] == str(tmp_path.resolve())
    assert environment["PYTHONPATH"].split(";")[0].endswith("onmyoji-daemon\\src")
