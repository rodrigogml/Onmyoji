"""Contrato comum de inicialização do Codex."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

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

    assert direct[:3] == ["codex", "--config", "check_for_update_on_startup=false"] and direct[-2:] == ["--add-dir", "reports"]
    assert direct[3] == "--config"
    key, value = direct[4].split("=", 1)
    assert key == "developer_instructions" and "[ONMYŌJI — INSTRUÇÕES GERAIS]" in json.loads(value)
    assert direct_cwd == cwd and direct_environment["CODEX_HOME"] == environment["CODEX_HOME"]
    assert command == ["codex", "--config", "check_for_update_on_startup=false", "app-server", "--stdio"]
    assert cwd == workspace.resolve()
    assert environment["CODEX_HOME"] == str(tmp_path.resolve())
    assert launch.model == "gpt-test" and launch.effort == "medium"
    assert launch.sandbox == "workspace-write" and launch.approval == "never"
    assert len(reconciled) == 2


def test_all_codex_entrypoints_synchronize_shikigami_skills(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"; workspace.mkdir()
    launch = codex_execution.CodexLaunch.from_data(tmp_path, data(workspace))
    synchronized: list[Path] = []
    monkeypatch.setattr(codex_execution.ShikigamiSkillMaterializer, "synchronize", lambda _self, target: synchronized.append(target) or False)
    monkeypatch.setattr(codex_execution.McpReconciliation, "reconcile_if_pending", lambda *_args: False)
    monkeypatch.setattr(codex_execution, "runtime_ready", lambda _root: True)
    monkeypatch.setattr(codex_execution, "daemon_python", lambda _root: tmp_path / "runtime" / "python")

    launch.direct_launch()
    launch.app_server_launch()
    launch.interactive_launcher()

    assert synchronized == [workspace.resolve(), workspace.resolve(), workspace.resolve()]


def test_all_codex_entrypoints_gate_and_synchronize_optional_agents(tmp_path: Path, monkeypatch) -> None:
    from onmyoji_daemon import codex_capabilities

    workspace = tmp_path / "workspace"; workspace.mkdir()
    launch = codex_execution.CodexLaunch.from_data(tmp_path, data(workspace))
    state = tmp_path / "configs" / "shikigami" / "agents.toml"; state.parent.mkdir(parents=True)
    state.write_text("schema_version=1\n[agents.reviewer]\nenabled=true\n", encoding="utf-8")
    certificate = {"supported": False, "reason": "g4_unverified"}
    gated: list[tuple[Path, str, Path]] = []
    synchronized: list[tuple[Path, object]] = []
    monkeypatch.setattr(codex_capabilities, "load_profile_capability", lambda root, executable, workspace: gated.append((root, executable, workspace)) or certificate)
    monkeypatch.setattr(codex_execution.ShikigamiAgentMaterializer, "synchronize", lambda _self, workspace, *, capability: synchronized.append((workspace, capability)) or False)
    monkeypatch.setattr(codex_execution.McpReconciliation, "reconcile_if_pending", lambda *_args: False)
    monkeypatch.setattr(codex_execution, "runtime_ready", lambda _root: True)
    monkeypatch.setattr(codex_execution, "daemon_python", lambda _root: tmp_path / "runtime" / "python")

    launch.direct_launch(); launch.app_server_launch(); launch.interactive_launcher()

    assert gated == [(tmp_path.resolve(), "codex", workspace.resolve())] * 3
    assert synchronized == [(workspace.resolve(), certificate)] * 3


def test_no_profiles_does_not_probe_executable_or_create_runtime(tmp_path: Path, monkeypatch) -> None:
    from onmyoji_daemon import codex_capabilities

    workspace = tmp_path / "workspace"; workspace.mkdir()
    monkeypatch.setattr(codex_capabilities, "load_profile_capability", lambda *_args: pytest.fail("não deve calcular fingerprint sem perfis"))
    assert codex_execution.CodexLaunch.from_data(tmp_path, data(workspace)).synchronize_shikigami_agents() is False
    assert not (workspace / ".codex").exists()


@pytest.mark.parametrize("entrypoint", ["direct_launch", "app_server_launch", "interactive_launcher"])
def test_agent_distribution_failure_blocks_each_managed_opening(tmp_path: Path, monkeypatch, entrypoint: str) -> None:
    workspace = tmp_path / "workspace"; workspace.mkdir()
    launch = codex_execution.CodexLaunch.from_data(tmp_path, data(workspace))
    monkeypatch.setattr(codex_execution, "runtime_ready", lambda _root: True)
    def fail(*_args, **_kwargs):
        raise RuntimeError("G4 não comprovado")
    monkeypatch.setattr(codex_execution.ShikigamiAgentMaterializer, "synchronize", fail)

    with pytest.raises(codex_execution.CodexExecutionError, match="G4 não comprovado"):
        getattr(launch, entrypoint)()


def test_shikigami_skill_sync_failure_blocks_codex_startup(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"; workspace.mkdir()
    launch = codex_execution.CodexLaunch.from_data(tmp_path, data(workspace))
    def fail_synchronization(*_args: object) -> bool:
        raise RuntimeError("skill inválida")

    monkeypatch.setattr(codex_execution.ShikigamiSkillMaterializer, "synchronize", fail_synchronization)

    with pytest.raises(codex_execution.CodexExecutionError, match="skill inválida"):
        launch.direct_launch()


def test_interactive_launcher_uses_selected_daemon_runtime(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"; workspace.mkdir()
    runtime = tmp_path / "runtime" / "python"
    monkeypatch.setattr(codex_execution, "runtime_ready", lambda _root: True)
    monkeypatch.setattr(codex_execution, "daemon_python", lambda _root: runtime)
    monkeypatch.setattr(codex_execution.McpReconciliation, "reconcile_if_pending", lambda _self, _registration: False)

    command, cwd, environment = codex_execution.CodexLaunch.from_data(tmp_path, data(workspace)).interactive_launcher()

    assert command == [str(runtime), "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(tmp_path.resolve()), "interactive"]
    assert cwd == workspace.resolve()
    assert environment["CODEX_HOME"] == str(tmp_path.resolve())
    assert environment["PYTHONPATH"].split(";")[0].endswith("onmyoji-daemon\\src")
