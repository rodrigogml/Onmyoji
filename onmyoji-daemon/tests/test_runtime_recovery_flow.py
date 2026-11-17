from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from onmyoji_daemon import runtime, runtime_recovery


@pytest.fixture
def selected_python(tmp_path: Path, monkeypatch) -> Path:
    executable = tmp_path / "python"
    executable.touch()
    monkeypatch.setattr(runtime, "python", lambda _root: executable)
    monkeypatch.setattr(runtime, "is_isolated", lambda _root: False)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="posix", environ=os.environ, geteuid=lambda: 1000))
    return executable


def test_global_runtime_resumes_after_each_recovered_distribution(tmp_path: Path, monkeypatch, selected_python: Path) -> None:
    """Bloqueadores distintos são recuperados antes de validar o conjunto completo."""
    outcomes = iter([(1, "uninstall-no-record-file cffi"), (1, "uninstall-no-record-file cryptography"), (0, "Installed")])
    installations = []
    recoveries = []
    validations = []

    def install(executable, arguments, **options):
        installations.append((executable, arguments, options))
        return next(outcomes)

    def recover(root, executable, requirements, detail, progress, install, recovered):
        recoveries.append((root, executable, requirements, detail, set(recovered)))
        recovered.add(detail.rsplit(" ", 1)[-1])
        return True

    monkeypatch.setattr(runtime, "_install", install)
    monkeypatch.setattr(runtime_recovery, "recover_distribution", recover)
    monkeypatch.setattr(runtime, "run_with_progress", lambda command, **options: validations.append((command, options)) or (0, ""))

    assert runtime.ensure(tmp_path, ["example>=2"], "import example") == "Runtime Python único do Shikigami pronto."
    assert len(installations) == 3
    assert all(call[0] == selected_python and call[1] == installations[0][1] for call in installations)
    assert [call[3] for call in recoveries] == ["uninstall-no-record-file cffi", "uninstall-no-record-file cryptography"]
    assert [call[4] for call in recoveries] == [set(), {"cffi"}]
    assert all(call[:3] == (tmp_path, selected_python, ["example>=2"]) for call in recoveries)
    assert len(validations) == 1
    assert validations[0][0] == [str(selected_python), "-I", "-B", "-c", "import example"]


def test_global_recovery_reuses_installation_authorization_and_validates_without_sudo(tmp_path: Path, monkeypatch, selected_python: Path) -> None:
    """Resolução e instalação do bloqueador usam a autorização da operação original."""
    installations = []
    validations = []
    progress = [].append

    def install(executable, arguments, **options):
        installations.append((executable, arguments, options))
        return (1, "uninstall-no-record-file cffi") if len(installations) == 1 else (0, "")

    def recover(root, executable, requirements, detail, callback_progress, install, recovered):
        assert callback_progress == progress
        assert install(["install", "--dry-run", "--report", "report.json"], "Resolvendo dependências.") == (0, "")
        assert install(["install", "--ignore-installed", "--no-deps", "cffi==2"], "Instalando cffi.") == (0, "")
        recovered.add("cffi")
        return True

    monkeypatch.setattr(runtime, "_install", install)
    monkeypatch.setattr(runtime_recovery, "recover_distribution", recover)
    monkeypatch.setattr(runtime, "run_with_progress", lambda command, **options: validations.append((command, options)) or (0, ""))

    runtime.ensure(tmp_path, ["example>=2"], "import example", progress=progress, use_sudo=True)

    assert len(installations) == 4
    assert all(executable == selected_python for executable, _arguments, _options in installations)
    assert all(options["use_sudo"] is True and options["progress"] == progress for _executable, _arguments, options in installations)
    assert all(options["environment"]["PIP_BREAK_SYSTEM_PACKAGES"] == "1" for _executable, _arguments, options in installations)
    assert installations[-1][1] == installations[0][1]
    assert "--ignore-installed" not in installations[-1][1]
    assert validations[0][0][0] == str(selected_python)
    assert "PIP_BREAK_SYSTEM_PACKAGES" not in validations[0][1]["environment"]
    assert validations[0][1]["progress"] == progress


def test_global_runtime_does_not_repeat_an_unrecoverable_installation(tmp_path: Path, monkeypatch, selected_python: Path) -> None:
    """Uma recusa de recuperação preserva a falha original sem validar o runtime."""
    calls = []
    monkeypatch.setattr(runtime, "_install", lambda *args, **kwargs: calls.append(args) or (1, "No matching distribution found"))
    monkeypatch.setattr(runtime_recovery, "recover_distribution", lambda *args: False)
    monkeypatch.setattr(runtime, "run_with_progress", lambda *args, **kwargs: pytest.fail("Uma instalação incompleta não pode ser validada."))

    with pytest.raises(runtime.RuntimeError, match="No matching distribution found"):
        runtime.ensure(tmp_path, ["example>=2"], "import example")
    assert len(calls) == 1


def test_global_runtime_stops_when_the_same_distribution_remains_blocked(tmp_path: Path, monkeypatch, selected_python: Path) -> None:
    """O conjunto compartilhado de recuperações impede repetir o mesmo bloqueador."""
    calls = []
    recoveries = []

    def recover(root, executable, requirements, detail, progress, install, recovered):
        recoveries.append(set(recovered))
        if "cffi" in recovered:
            return False
        recovered.add("cffi")
        return True

    monkeypatch.setattr(runtime, "_install", lambda *args, **kwargs: calls.append(args) or (1, "uninstall-no-record-file cffi"))
    monkeypatch.setattr(runtime_recovery, "recover_distribution", recover)
    monkeypatch.setattr(runtime, "run_with_progress", lambda *args, **kwargs: pytest.fail("Uma instalação incompleta não pode ser validada."))

    with pytest.raises(runtime.RuntimeError, match="uninstall-no-record-file cffi"):
        runtime.ensure(tmp_path, ["example>=2"], "import example")
    assert len(calls) == 2
    assert recoveries == [set(), {"cffi"}]


def test_global_runtime_preserves_recovery_failure(tmp_path: Path, monkeypatch, selected_python: Path) -> None:
    """Falhas da instalação do pacote protegido não são confundidas com recuperação concluída."""
    calls = []

    def recover(*args):
        raise runtime.RuntimeError("Não foi possível instalar o pacote protegido.")

    monkeypatch.setattr(runtime, "_install", lambda *args, **kwargs: calls.append(args) or (1, "uninstall-no-record-file cffi"))
    monkeypatch.setattr(runtime_recovery, "recover_distribution", recover)
    monkeypatch.setattr(runtime, "run_with_progress", lambda *args, **kwargs: pytest.fail("Uma recuperação incompleta não pode ser validada."))

    with pytest.raises(runtime.RuntimeError, match="Não foi possível instalar o pacote protegido"):
        runtime.ensure(tmp_path, ["example>=2"], "import example")
    assert len(calls) == 1


@pytest.mark.parametrize("isolated, platform_name", [(True, "posix"), (False, "nt")])
def test_runtime_does_not_recover_distribution_packages_outside_global_posix(tmp_path: Path, monkeypatch, selected_python: Path, isolated: bool, platform_name: str) -> None:
    """Instalações isoladas e Windows conservam o tratamento normal de falhas do pip."""
    calls = []
    monkeypatch.setattr(runtime, "is_isolated", lambda _root: isolated)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name=platform_name, environ=os.environ, geteuid=lambda: 1000))
    monkeypatch.setattr(runtime, "_install", lambda *args, **kwargs: calls.append(kwargs) or (1, "uninstall-no-record-file cffi"))
    monkeypatch.setattr(runtime_recovery, "recover_distribution", lambda *args: pytest.fail("Este modo não recupera pacotes da distribuição."))
    monkeypatch.setattr(runtime, "run_with_progress", lambda *args, **kwargs: pytest.fail("Uma instalação incompleta não pode ser validada."))

    with pytest.raises(runtime.RuntimeError, match="uninstall-no-record-file cffi"):
        runtime.ensure(tmp_path, ["example>=2"], "import example", use_sudo=True)
    assert len(calls) == 1
    assert calls[0]["use_sudo"] is False


def test_successful_recovery_still_requires_successful_runtime_validation(tmp_path: Path, monkeypatch, selected_python: Path) -> None:
    """A recuperação não transforma uma validação malsucedida em instalação concluída."""
    outcomes = iter([(1, "uninstall-no-record-file cffi"), (0, "")])
    monkeypatch.setattr(runtime, "_install", lambda *args, **kwargs: next(outcomes))
    monkeypatch.setattr(runtime_recovery, "recover_distribution", lambda *args: True)
    monkeypatch.setattr(runtime, "run_with_progress", lambda *args, **kwargs: (1, "No module named example"))

    with pytest.raises(runtime.RuntimeError, match="No module named example"):
        runtime.ensure(tmp_path, ["example>=2"], "import example")
