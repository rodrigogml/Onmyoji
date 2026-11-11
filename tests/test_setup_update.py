"""Cenários de atualização com repositórios Git locais."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def setup_module():
    path = Path(__file__).resolve().parents[1] / "setupOnmyoji.py"
    spec = importlib.util.spec_from_file_location("setup_update_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def repos(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Teste")
    git(source, "config", "user.email", "test@example.invalid")
    (source / "base.txt").write_text("original\n", encoding="utf-8")
    git(source, "add", "base.txt")
    git(source, "commit", "-m", "base")
    local = tmp_path / "local"
    git(tmp_path, "clone", str(source), str(local))
    git(local, "config", "user.name", "Teste")
    git(local, "config", "user.email", "test@example.invalid")
    git(local, "remote", "rename", "origin", "upstream")
    (source / "new.txt").write_text("upstream\n", encoding="utf-8")
    git(source, "add", "new.txt")
    git(source, "commit", "-m", "upstream")
    return source, local


def test_clean_update_fetches_and_merges_main(setup_module, repos, capsys):
    source, local = repos
    setup_module.update_upstream(local)
    assert (local / "new.txt").read_text(encoding="utf-8") == "upstream\n"
    assert git(local, "rev-parse", "HEAD") == git(source, "rev-parse", "HEAD")
    assert "concluída" in capsys.readouterr().out


def test_stash_preserves_index_and_untracked_files(setup_module, repos, monkeypatch):
    _, local = repos
    (local / "base.txt").write_text("staged\n", encoding="utf-8")
    git(local, "add", "base.txt")
    (local / "base.txt").write_text("staged\nunstaged\n", encoding="utf-8")
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Guardar temporariamente em stash")
    setup_module.update_upstream(local)
    assert (local / "new.txt").exists()
    assert (local / "local.txt").read_text(encoding="utf-8") == "local\n"
    assert git(local, "diff", "--cached", "--name-only") == "base.txt"
    assert git(local, "diff", "--name-only") == "base.txt"
    assert not git(local, "stash", "list")


def test_restore_conflict_retains_stash(setup_module, repos, monkeypatch, capsys):
    source, local = repos
    (source / "base.txt").write_text("upstream\n", encoding="utf-8")
    git(source, "add", "base.txt")
    git(source, "commit", "-m", "modify")
    (local / "base.txt").write_text("local\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Guardar temporariamente em stash")
    setup_module.update_upstream(local)
    assert git(local, "stash", "list")
    assert "stash" in capsys.readouterr().out
    assert git(local, "diff", "--name-only", "--diff-filter=U") == "base.txt"


def test_merge_conflict_preserves_stash_for_later_recovery(setup_module, repos, monkeypatch, capsys):
    source, local = repos
    (source / "base.txt").write_text("upstream\n", encoding="utf-8")
    git(source, "add", "base.txt")
    git(source, "commit", "-m", "upstream conflict")
    (local / "base.txt").write_text("local committed\n", encoding="utf-8")
    git(local, "add", "base.txt")
    git(local, "commit", "-m", "local conflict")
    (local / "work.txt").write_text("pending\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Guardar temporariamente em stash")

    setup_module.update_upstream(local)

    assert git(local, "stash", "list")
    assert git(local, "diff", "--name-only", "--diff-filter=U") == "base.txt"
    assert "stash temporário foi preservado" in capsys.readouterr().out


def test_commit_local_changes_before_merge(setup_module, repos, monkeypatch):
    _, local = repos
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Salvar apenas em commit")
    monkeypatch.setattr(setup_module, "prompt", lambda _label: "Salva trabalho local")
    setup_module.update_upstream(local)
    assert (local / "new.txt").exists()
    assert not git(local, "status", "--porcelain")
    assert "Salva trabalho local" in git(local, "log", "--format=%s")


def test_commit_and_push_origin_including_merge_result(setup_module, repos, monkeypatch, tmp_path):
    _, local = repos
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", str(origin))
    git(local, "remote", "add", "origin", str(origin))
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Salvar em commit e publicar no origin")
    monkeypatch.setattr(setup_module, "prompt", lambda _label: "Salva trabalho local")
    setup_module.update_upstream(local)
    assert (local / "new.txt").exists()
    assert git(origin, "rev-parse", "refs/heads/main") == git(local, "rev-parse", "HEAD")
    assert "Salva trabalho local" in git(origin, "log", "--format=%s", "refs/heads/main")
    assert not git(local, "status", "--porcelain")


def test_configuration_is_stashed_and_restored_without_path_classification(setup_module, repos, monkeypatch):
    _, local = repos
    (local / "configs").mkdir()
    (local / "configs" / "secret").write_text("segredo", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Guardar temporariamente em stash")
    setup_module.update_upstream(local)
    assert (local / "new.txt").exists()
    assert (local / "configs" / "secret").read_text(encoding="utf-8") == "segredo"
    assert not git(local, "stash", "list")


def test_stash_preserves_configuration_and_restores_other_changes(setup_module, repos, monkeypatch):
    _, local = repos
    (local / "configs").mkdir()
    (local / "configs" / "secret").write_text("segredo", encoding="utf-8")
    (local / "base.txt").write_text("staged\n", encoding="utf-8")
    git(local, "add", "base.txt")
    (local / "base.txt").write_text("staged\nunstaged\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Guardar temporariamente em stash")
    setup_module.update_upstream(local)
    assert (local / "new.txt").exists()
    assert (local / "configs" / "secret").read_text(encoding="utf-8") == "segredo"
    assert git(local, "diff", "--cached", "--name-only") == "base.txt"
    assert git(local, "diff", "--name-only") == "base.txt"
    assert not git(local, "stash", "list")


def test_merge_keeps_modified_tracked_config_when_upstream_does_not_touch_it(setup_module, repos, monkeypatch):
    _, local = repos
    (local / "configs").mkdir()
    config = local / "configs" / "local.toml"
    config.write_text("original", encoding="utf-8")
    git(local, "add", "configs/local.toml")
    git(local, "commit", "-m", "Configuração rastreada anteriormente")
    config.write_text("alteração local", encoding="utf-8")
    (local / "base.txt").write_text("alteração de código", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Guardar temporariamente em stash")

    setup_module.update_upstream(local)

    assert (local / "new.txt").exists()
    assert config.read_text(encoding="utf-8") == "alteração local"
    assert (local / "base.txt").read_text(encoding="utf-8") == "alteração de código"
    assert "configs/local.toml" in git(local, "status", "--porcelain")
    assert not git(local, "stash", "list")


def test_commit_includes_every_pending_path(setup_module, repos, monkeypatch):
    _, local = repos
    (local / "configs").mkdir()
    (local / "configs" / "secret").write_text("segredo", encoding="utf-8")
    git(local, "add", "configs/secret")
    (local / "local.txt").write_text("local", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Salvar apenas em commit")
    monkeypatch.setattr(setup_module, "prompt", lambda _label: "Salva trabalho")
    setup_module.update_upstream(local)
    assert (local / "new.txt").exists()
    assert not git(local, "status", "--porcelain")
    committed = git(local, "show", "--format=", "--name-only", "HEAD~1")
    assert set(committed.splitlines()) == {"configs/secret", "local.txt"}


def test_stash_restores_staged_configuration_and_other_changes(setup_module, repos, monkeypatch):
    _, local = repos
    (local / "configs").mkdir()
    (local / "configs" / "secret").write_text("segredo", encoding="utf-8")
    git(local, "add", "configs/secret")
    (local / "base.txt").write_text("local", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Guardar temporariamente em stash")
    setup_module.update_upstream(local)
    assert (local / "new.txt").exists()
    assert git(local, "diff", "--cached", "--name-only") == "configs/secret"
    assert (local / "base.txt").read_text(encoding="utf-8") == "local"
    assert not git(local, "stash", "list")


def test_cancel_preserves_local_changes(setup_module, repos, monkeypatch):
    _, local = repos
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: None)
    setup_module.update_upstream(local)
    assert not (local / "new.txt").exists()
    assert (local / "local.txt").exists()


def test_main_menu_routes_update(setup_module, tmp_path, monkeypatch):
    answers = iter(["u", "x"])
    updates = []
    monkeypatch.setattr(setup_module, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(setup_module, "update_upstream", updates.append)
    setup_module.menu([], tmp_path)
    assert updates == [tmp_path]


def test_alert_is_amber_and_distinct_from_error(setup_module, monkeypatch, capsys):
    monkeypatch.setattr(setup_module.Ui, "enabled", True)
    setup_module.alert("Escolha uma opção.")
    output = capsys.readouterr().out
    assert "ALERTA" in output and setup_module.Ui.amber in output
    assert "ERRO" not in output
