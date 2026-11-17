from __future__ import annotations

from pathlib import Path

import pytest

from onmyoji_daemon import management, runtime


@pytest.mark.parametrize("use_sudo", [False, True])
@pytest.mark.parametrize("show_progress", [False, True])
def test_daemon_runtime_preparation_passes_installation_authorization(tmp_path: Path, monkeypatch, use_sudo: bool, show_progress: bool) -> None:
    """Instalações autorizadas usam o runtime central e o mesmo contrato de validação."""
    monkeypatch.setattr(management, "daemon_python", lambda _root: tmp_path / "python")
    monkeypatch.setattr(management, "_runtime_ready", lambda _executable: False)
    monkeypatch.setattr(management, "runtime_requirements", lambda: ["example>=1,<2"])
    received = {}

    def ensure(root, requirements, probe, progress, *, use_sudo=False):
        received.update(root=root, requirements=requirements, probe=probe, progress=progress, use_sudo=use_sudo)
        return "Runtime validado."

    monkeypatch.setattr(management.shared_runtime, "ensure", ensure)

    assert management.ensure_runtime(tmp_path, show_progress=show_progress, use_sudo=use_sudo) == (True, "Runtime validado.")
    assert received == {
        "root": tmp_path,
        "requirements": ["example>=1,<2"],
        "probe": management._RUNTIME_PROBE,
        "progress": print if show_progress else None,
        "use_sudo": use_sudo,
    }


@pytest.mark.parametrize("repair", [False, True])
def test_daemon_runtime_check_only_never_installs(tmp_path: Path, monkeypatch, repair: bool) -> None:
    """Check-only não instala nem com autorização de sudo e reparação solicitada."""
    monkeypatch.setattr(management, "daemon_python", lambda _root: tmp_path / "python")
    monkeypatch.setattr(management, "_runtime_ready", lambda _executable: False)
    monkeypatch.setattr(management.shared_runtime, "ensure", lambda *_args, **_kwargs: pytest.fail("Uma verificação não pode instalar bibliotecas."))

    ready, message = management.ensure_runtime(tmp_path, allow_install=False, repair=repair, use_sudo=True)

    assert ready is False
    assert "não possui os componentes necessários" in message


@pytest.mark.parametrize("allow_install, repair", [(False, False), (False, True), (True, False)])
def test_ready_daemon_runtime_does_not_install_when_checking_or_without_repair(tmp_path: Path, monkeypatch, allow_install: bool, repair: bool) -> None:
    """Check-only confirma um runtime válido mesmo quando há reparação solicitada."""
    monkeypatch.setattr(management, "daemon_python", lambda _root: tmp_path / "python")
    monkeypatch.setattr(management, "_runtime_ready", lambda _executable: True)
    monkeypatch.setattr(management.shared_runtime, "ensure", lambda *_args, **_kwargs: pytest.fail("O runtime válido não precisa de instalação."))

    assert management.ensure_runtime(tmp_path, allow_install=allow_install, repair=repair, use_sudo=True) == (True, "Runtime Python do Shikigami está pronto.")


def test_ready_daemon_runtime_can_be_repaired_explicitly(tmp_path: Path, monkeypatch) -> None:
    """A reparação explícita reinstala bibliotecas mesmo após uma validação positiva."""
    monkeypatch.setattr(management, "daemon_python", lambda _root: tmp_path / "python")
    monkeypatch.setattr(management, "_runtime_ready", lambda _executable: True)
    monkeypatch.setattr(management, "runtime_requirements", lambda: ["example"])
    calls = []
    monkeypatch.setattr(management.shared_runtime, "ensure", lambda *args, **kwargs: calls.append((args, kwargs)) or "Runtime reparado.")

    assert management.ensure_runtime(tmp_path, repair=True, use_sudo=True) == (True, "Runtime reparado.")
    assert len(calls) == 1
    assert calls[0][1] == {"use_sudo": True}


def test_daemon_runtime_returns_installation_failure_without_validation_success(tmp_path: Path, monkeypatch) -> None:
    """Recusa administrativa permanece uma falha e preserva a orientação do runtime."""
    monkeypatch.setattr(management, "daemon_python", lambda _root: tmp_path / "python")
    monkeypatch.setattr(management, "_runtime_ready", lambda _executable: False)
    monkeypatch.setattr(management, "runtime_requirements", lambda: ["example"])

    def refused(*_args, **_kwargs):
        raise runtime.RuntimeError("sudo não autorizou a instalação global.")

    monkeypatch.setattr(management.shared_runtime, "ensure", refused)

    assert management.ensure_runtime(tmp_path, use_sudo=True) == (False, "sudo não autorizou a instalação global.")
