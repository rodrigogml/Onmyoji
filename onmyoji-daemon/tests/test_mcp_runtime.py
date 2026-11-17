"""Contrato do MCP com o runtime central do Shikigami."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("use_sudo", [False, True])
@pytest.mark.parametrize("show_progress", [False, True])
def test_mcp_installation_forwards_only_explicit_administrative_authorization(tmp_path: Path, monkeypatch, use_sudo: bool, show_progress: bool) -> None:
    """A autorização pertence à instalação solicitada, sem alterar o contrato legado."""
    calls = []
    progress = [].append if show_progress else None
    def ensure(*args, **kwargs):
        calls.append((args, kwargs))
        return "Runtime MCP validado."
    monkeypatch.setattr(runtime.shared_runtime, "ensure", ensure)

    assert runtime.ensure_mcp_runtime(tmp_path, progress=progress, use_sudo=use_sudo) == "Runtime MCP validado."
    assert calls == [((tmp_path, [runtime.SETUPTOOLS_REQUIREMENT, runtime.MCP_REQUIREMENT, runtime.OAUTH_REQUIREMENT, runtime.EMAIL_REQUIREMENT, runtime.VALIDATION_REQUIREMENT], runtime._probe(), progress), {"use_sudo": True} if use_sudo else {})]


def test_mcp_installation_preserves_central_runtime_failure(tmp_path: Path, monkeypatch) -> None:
    """Uma recusa do sudo continua sendo falha com a orientação do runtime central."""
    def refused(*args, **kwargs):
        raise runtime.shared_runtime.RuntimeError("sudo não autorizou a instalação global.")
    monkeypatch.setattr(runtime.shared_runtime, "ensure", refused)

    with pytest.raises(runtime.McpRuntimeError, match="sudo não autorizou a instalação global"):
        runtime.ensure_mcp_runtime(tmp_path, use_sudo=True)


def test_mcp_readiness_requires_an_existing_selected_python(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime, "mcp_runtime_python", lambda _root: tmp_path / "missing-python")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Um Python ausente não pode executar a consulta."))
    assert runtime.mcp_runtime_ready(tmp_path) is False


@pytest.mark.parametrize("returncode, expected", [(0, True), (1, False)])
def test_mcp_readiness_probes_the_selected_python_without_installing(tmp_path: Path, monkeypatch, returncode: int, expected: bool) -> None:
    executable = tmp_path / "python"
    executable.touch()
    calls = []
    monkeypatch.setattr(runtime, "mcp_runtime_python", lambda _root: executable)
    monkeypatch.setattr(runtime.subprocess, "run", lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, returncode))

    assert runtime.mcp_runtime_ready(tmp_path) is expected
    assert calls == [[str(executable), "-I", "-B", "-c", runtime._probe()]]


@pytest.mark.parametrize("error", [OSError("unavailable"), subprocess.TimeoutExpired("python", 10), runtime.shared_runtime.RuntimeError("Python não configurado.")])
def test_mcp_readiness_reports_an_unavailable_runtime_as_not_ready(tmp_path: Path, monkeypatch, error: Exception) -> None:
    executable = tmp_path / "python"
    executable.touch()
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(runtime, "mcp_runtime_python", fail if isinstance(error, runtime.shared_runtime.RuntimeError) else lambda _root: executable)
    monkeypatch.setattr(runtime.subprocess, "run", fail)
    assert runtime.mcp_runtime_ready(tmp_path) is False
