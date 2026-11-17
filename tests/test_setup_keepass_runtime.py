from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "setupOnmyoji.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_keepass_runtime_setup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_root_menu_places_keepass_before_codex_and_dispatches_independent_menus(tmp_path, monkeypatch):
    items = []
    opened = []
    answers = iter(["k", "m", "x"])
    monkeypatch.setattr(MODULE, "screen", lambda *_args: None)
    monkeypatch.setattr(MODULE, "item", lambda *args: items.append(args))
    monkeypatch.setattr(MODULE, "enabled_ids", lambda *_args: set())
    monkeypatch.setattr(MODULE, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(MODULE, "launch_keepass_menu", lambda root: opened.append(("keepass", root)))
    monkeypatch.setattr(MODULE, "launch_mcp_menu", lambda root: opened.append(("mcp", root)))
    monkeypatch.setattr(MODULE, "run_dedicated_menu", lambda *_args: pytest.fail("Cada destino decide quando delegar."))

    assert MODULE.menu([], tmp_path) == 0

    keys = [item[0] for item in items]
    assert keys.index("P.") < keys.index("K.") < keys.index("C.")
    assert next(item for item in items if item[0] == "K.")[1] == "KeePass"
    assert "vault" not in " ".join(next(item for item in items if item[0] == "M.")).casefold()
    assert opened == [("keepass", tmp_path), ("mcp", tmp_path)]


@pytest.fixture
def no_mcp_preparation(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("A configuração de cofres compartilhados não prepara o SDK MCP.")
    monkeypatch.setattr(MODULE, "ensure_mcp_runtime", unexpected)
    monkeypatch.setattr(MODULE, "mcp_runtime_ready", unexpected)
    monkeypatch.setattr(MODULE, "authorize_runtime_installation", unexpected)


@pytest.mark.parametrize("isolated, existing_runtime", [(False, True), (True, True), (True, False)])
def test_keepass_uses_selected_python_or_base_before_isolated_runtime_exists(tmp_path, monkeypatch, no_mcp_preparation, isolated, existing_runtime):
    """A criação do cofre precede a preparação de um runtime isolado ou MCP."""
    selected = tmp_path / "runtime-python"
    base = tmp_path / "base-python"
    base.touch()
    if existing_runtime:
        selected.touch()
    calls = []
    destinations = []
    monkeypatch.setattr(MODULE, "run_dedicated_menu", lambda root, destination: destinations.append((root, destination)) or False)
    monkeypatch.setattr(MODULE.python_runtime, "python", lambda _root: selected)
    monkeypatch.setattr(MODULE.python_runtime, "is_isolated", lambda _root: isolated)
    monkeypatch.setattr(MODULE.python_runtime, "base_python", lambda _root: base)
    monkeypatch.setattr(MODULE.subprocess, "run", lambda command, **options: calls.append((command, options)) or subprocess.CompletedProcess(command, 0))

    assert MODULE.launch_keepass_menu(tmp_path)

    executable = selected if existing_runtime else base
    assert calls == [([str(executable), "-B", str(tmp_path / "onmyoji-daemon" / "setupMcp.py"), "--onmyoji-root", str(tmp_path), "--action", "vault"], {"check": False})]
    assert destinations == [(tmp_path, "keepass")]


def test_keepass_delegates_to_operational_account_before_resolving_python(tmp_path, monkeypatch, no_mcp_preparation):
    destinations = []
    monkeypatch.setattr(MODULE, "run_dedicated_menu", lambda root, destination: destinations.append((root, destination)) or True)
    monkeypatch.setattr(MODULE.python_runtime, "python", lambda _root: pytest.fail("O Python será resolvido pela conta operacional."))
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("O menu dedicado já foi aberto."))
    assert MODULE.launch_keepass_menu(tmp_path)
    assert destinations == [(tmp_path, "keepass")]


@pytest.mark.parametrize("failure", ["missing-global", "missing-base", "configuration", "launch", "child-exit"])
def test_keepass_reports_failure_without_preparing_mcp(tmp_path, monkeypatch, capsys, no_mcp_preparation, failure):
    selected = tmp_path / "python"
    if failure not in {"missing-global", "missing-base"}:
        selected.touch()
    monkeypatch.setattr(MODULE, "run_dedicated_menu", lambda *_args: False)
    def python(_root):
        if failure == "configuration":
            raise MODULE.python_runtime.RuntimeError("Selecione o Python da instância.")
        return selected
    monkeypatch.setattr(MODULE.python_runtime, "python", python)
    monkeypatch.setattr(MODULE.python_runtime, "is_isolated", lambda _root: failure == "missing-base")
    monkeypatch.setattr(MODULE.python_runtime, "base_python", lambda _root: tmp_path / "missing-base")
    def run(command, **_options):
        if failure == "launch":
            raise OSError("execução recusada")
        return subprocess.CompletedProcess(command, 2)
    monkeypatch.setattr(MODULE.subprocess, "run", run)

    assert MODULE.launch_keepass_menu(tmp_path) is False
    output = capsys.readouterr().out
    assert "Traceback" not in output
    assert "KeePass" in output


def test_mcp_global_prepares_with_authorized_sudo_before_opening_dedicated_menu(tmp_path, monkeypatch):
    """O instalador prepara as bibliotecas globais antes de entregar o menu à conta dedicada."""
    events = []
    monkeypatch.setattr(MODULE.python_runtime, "is_isolated", lambda _root: False)
    monkeypatch.setattr(MODULE, "mcp_runtime_ready", lambda _root: False)
    monkeypatch.setattr(MODULE, "prompt", lambda _label: "")
    monkeypatch.setattr(MODULE, "authorize_runtime_installation", lambda root: events.append(("authorize", root)) or True)
    def ensure(root, **options):
        events.append(("install", root, options))
        return "Runtime MCP preparado."
    monkeypatch.setattr(MODULE, "ensure_mcp_runtime", ensure)
    monkeypatch.setattr(MODULE, "run_dedicated_menu", lambda root, destination: events.append(("delegate", root, destination)) or True)
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("O menu local não substitui a conta dedicada."))

    assert MODULE.launch_mcp_menu(tmp_path)
    assert events == [("authorize", tmp_path), ("install", tmp_path, {"progress": MODULE.note, "use_sudo": True}), ("delegate", tmp_path, "mcp")]


def test_mcp_isolated_runtime_is_prepared_by_dedicated_account(tmp_path, monkeypatch):
    destinations = []
    monkeypatch.setattr(MODULE.python_runtime, "is_isolated", lambda _root: True)
    monkeypatch.setattr(MODULE, "run_dedicated_menu", lambda root, destination: destinations.append((root, destination)) or True)
    monkeypatch.setattr(MODULE, "mcp_runtime_ready", lambda _root: pytest.fail("O runtime isolado pertence à conta dedicada."))
    monkeypatch.setattr(MODULE, "ensure_mcp_runtime", lambda *_args, **_kwargs: pytest.fail("O instalador não cria o ambiente isolado da conta dedicada."))

    assert MODULE.launch_mcp_menu(tmp_path)
    assert destinations == [(tmp_path, "mcp")]


def test_ready_global_mcp_runtime_opens_dedicated_menu_without_installation(tmp_path, monkeypatch):
    destinations = []
    monkeypatch.setattr(MODULE.python_runtime, "is_isolated", lambda _root: False)
    monkeypatch.setattr(MODULE, "mcp_runtime_ready", lambda _root: True)
    monkeypatch.setattr(MODULE, "run_dedicated_menu", lambda root, destination: destinations.append((root, destination)) or True)
    monkeypatch.setattr(MODULE, "prompt", lambda _label: pytest.fail("O runtime pronto não exige reparação."))
    monkeypatch.setattr(MODULE, "ensure_mcp_runtime", lambda *_args, **_kwargs: pytest.fail("Não há componentes pendentes."))

    assert MODULE.launch_mcp_menu(tmp_path)
    assert destinations == [(tmp_path, "mcp")]


@pytest.mark.parametrize("failure", ["authorization", "installation"])
def test_mcp_global_failure_does_not_open_dedicated_menu(tmp_path, monkeypatch, capsys, failure):
    calls = []
    monkeypatch.setattr(MODULE.python_runtime, "is_isolated", lambda _root: False)
    monkeypatch.setattr(MODULE, "mcp_runtime_ready", lambda _root: False)
    monkeypatch.setattr(MODULE, "prompt", lambda _label: "s")
    monkeypatch.setattr(MODULE, "authorize_runtime_installation", lambda _root: None if failure == "authorization" else True)
    def ensure(*args, **options):
        calls.append((args, options))
        raise MODULE.McpRuntimeError("A instalação global foi recusada.")
    monkeypatch.setattr(MODULE, "ensure_mcp_runtime", ensure)
    monkeypatch.setattr(MODULE, "run_dedicated_menu", lambda *_args: pytest.fail("Uma instalação incompleta não pode abrir o menu dedicado."))
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Uma instalação incompleta não pode abrir o menu local."))

    assert MODULE.launch_mcp_menu(tmp_path) is False
    assert len(calls) == (0 if failure == "authorization" else 1)
    if failure == "installation":
        assert "A instalação global foi recusada." in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["n", "x", "\x1b"])
def test_mcp_preparation_can_be_cancelled_without_installing_or_delegating(tmp_path, monkeypatch, answer):
    monkeypatch.setattr(MODULE.python_runtime, "is_isolated", lambda _root: False)
    monkeypatch.setattr(MODULE, "mcp_runtime_ready", lambda _root: False)
    monkeypatch.setattr(MODULE, "prompt", lambda _label: answer)
    monkeypatch.setattr(MODULE, "authorize_runtime_installation", lambda *_args: pytest.fail("A preparação foi cancelada."))
    monkeypatch.setattr(MODULE, "ensure_mcp_runtime", lambda *_args, **_kwargs: pytest.fail("A preparação foi cancelada."))
    monkeypatch.setattr(MODULE, "run_dedicated_menu", lambda *_args: pytest.fail("O menu foi cancelado antes da delegação."))

    assert MODULE.launch_mcp_menu(tmp_path) is False


@pytest.mark.parametrize("isolated", [False, True])
def test_mcp_current_account_opens_menu_in_selected_runtime(tmp_path, monkeypatch, isolated):
    executable = tmp_path / "runtime-python"
    executable.touch()
    calls = []
    monkeypatch.setattr(MODULE.python_runtime, "is_isolated", lambda _root: isolated)
    monkeypatch.setattr(MODULE, "mcp_runtime_ready", lambda _root: True)
    monkeypatch.setattr(MODULE, "mcp_runtime_python", lambda _root: executable)
    monkeypatch.setattr(MODULE, "run_dedicated_menu", lambda *_args: False)
    monkeypatch.setattr(MODULE.subprocess, "run", lambda command, **options: calls.append((command, options)) or subprocess.CompletedProcess(command, 0))

    assert MODULE.launch_mcp_menu(tmp_path)
    assert calls == [([str(executable), "-B", str(tmp_path / "onmyoji-daemon" / "setupMcp.py")], {"check": False})]
