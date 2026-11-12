"""Tratamento das operações Git interativas do setup."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def setup_module():
    path = Path(__file__).resolve().parents[1] / "setupOnmyoji.py"
    spec = importlib.util.spec_from_file_location("setup_git_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def denied_push() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["git", "push"],
        128,
        "",
        "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.\n",
    )


def successful_push() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git", "push"], 0, "Branch publicada.\n", "")


def prepare_push(setup_module, monkeypatch, action):
    monkeypatch.setattr(setup_module, "_git_remote_url", lambda *_args: "git@github.com:example/instance.git")
    monkeypatch.setattr(setup_module, "_git_output", lambda *_args: "main")
    monkeypatch.setattr(setup_module, "linux_current_user", lambda: "shikigami-example")
    monkeypatch.setattr(setup_module.getpass, "getuser", lambda: "shikigami-example")
    monkeypatch.setattr(setup_module, "flow_choice", action)


def test_github_public_key_failure_explains_registration_and_asks_how_to_continue(setup_module, monkeypatch, tmp_path, capsys):
    prepare_push(setup_module, monkeypatch, lambda *_args: None)
    monkeypatch.setattr(setup_module, "_git", lambda *_args, **_kwargs: denied_push())

    published, detail = setup_module.push_git_origin(tmp_path)

    output = capsys.readouterr().out
    assert not published
    assert "origin e a branch foram preservados" in detail
    assert "recusou a autenticação SSH" in output
    assert "shikigami-example" in output
    assert "https://github.com/settings/keys" in output
    assert "ssh -T git@github.com" in output


def test_github_public_key_failure_can_retry_after_registration(setup_module, monkeypatch, tmp_path):
    choice = "Tentar publicar novamente após cadastrar a chave"
    prepare_push(setup_module, monkeypatch, lambda *_args: choice)
    attempts = iter([denied_push(), successful_push()])
    monkeypatch.setattr(setup_module, "_git", lambda *_args, **_kwargs: next(attempts))

    published, detail = setup_module.push_git_origin(tmp_path)

    assert published
    assert detail == "Branch publicada."


def test_github_public_key_failure_can_replace_origin_url_and_retry(setup_module, monkeypatch, tmp_path):
    choice = "Informar outra URL para o origin"
    prepare_push(setup_module, monkeypatch, lambda *_args: choice)
    monkeypatch.setattr(setup_module, "prompt", lambda _label: "https://github.com/example/instance.git")
    calls = []

    def run_git(_root, arguments, **_kwargs):
        calls.append(arguments)
        if len(calls) == 1:
            return denied_push()
        if arguments[:3] == ["remote", "set-url", "origin"]:
            return subprocess.CompletedProcess(["git", *arguments], 0, "", "")
        return successful_push()

    monkeypatch.setattr(setup_module, "_git", run_git)

    published, _detail = setup_module.push_git_origin(tmp_path)

    assert published
    assert ["remote", "set-url", "origin", "https://github.com/example/instance.git"] in calls


def test_non_authentication_push_failure_keeps_the_original_git_diagnostic(setup_module, monkeypatch, tmp_path):
    prepare_push(setup_module, monkeypatch, lambda *_args: pytest.fail("Não deve perguntar como prosseguir"))
    rejected = subprocess.CompletedProcess(["git", "push"], 1, "", "remote: Repository not found.\n")
    monkeypatch.setattr(setup_module, "_git", lambda *_args, **_kwargs: rejected)

    published, detail = setup_module.push_git_origin(tmp_path)

    assert not published
    assert detail == "remote: Repository not found."
