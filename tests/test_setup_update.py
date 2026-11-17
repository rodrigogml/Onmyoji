"""Cenários de atualização com repositórios Git locais."""
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.parametrize("typed_message, expected_message", [
    ("Salva trabalho local", "Salva trabalho local"),
    ("", "Configurações do Shikigami"),
])
def test_commit_local_changes_before_merge(setup_module, repos, monkeypatch, typed_message, expected_message):
    _, local = repos
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Salvar apenas em commit")
    prompts = []

    def answer(label):
        prompts.append(label)
        return typed_message

    monkeypatch.setattr(setup_module, "prompt", answer)
    setup_module.update_upstream(local)
    assert prompts == ["Mensagem do commit [Configurações do Shikigami] (X = cancelar): "]
    assert (local / "new.txt").exists()
    assert not git(local, "status", "--porcelain")
    assert expected_message in git(local, "log", "--format=%s")


@pytest.mark.parametrize("typed_message, expected_message", [
    ("Salva trabalho local", "Salva trabalho local"),
    ("", "Configurações do Shikigami"),
])
def test_commit_and_push_origin_including_merge_result(setup_module, repos, monkeypatch, tmp_path, typed_message, expected_message):
    _, local = repos
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", str(origin))
    git(local, "remote", "add", "origin", str(origin))
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Salvar em commit e publicar no origin")
    monkeypatch.setattr(setup_module, "prompt", lambda _label: typed_message)
    setup_module.update_upstream(local)
    assert (local / "new.txt").exists()
    assert git(origin, "rev-parse", "refs/heads/main") == git(local, "rev-parse", "HEAD")
    assert expected_message in git(origin, "log", "--format=%s", "refs/heads/main")
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


@pytest.mark.parametrize("cancel", ["x", "X"])
def test_cancel_commit_message_preserves_local_changes(setup_module, repos, monkeypatch, cancel, capsys):
    _, local = repos
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "flow_choice", lambda *_args: "Salvar apenas em commit")
    monkeypatch.setattr(setup_module, "prompt", lambda _label: cancel)

    setup_module.update_upstream(local)

    assert "Atualização cancelada" in capsys.readouterr().out
    assert not (local / "new.txt").exists()
    assert (local / "local.txt").read_text(encoding="utf-8") == "local\n"
    assert "local.txt" in git(local, "status", "--porcelain")


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


@pytest.mark.parametrize("service, process, reachable, active, use_sudo", [
    ({}, {"pid": 42}, False, False, False),
    ({"name": "onmyoji-test", "scope": "user"}, {"pid": 42}, False, True, False),
    ({"name": "onmyoji-test", "scope": "system"}, {"pid": 42}, False, True, True),
    ({"name": "onmyoji-test", "scope": "user"}, None, True, False, False),
    ({"name": "onmyoji-test", "scope": "system"}, None, False, True, True),
])
def test_update_stops_the_active_daemon_with_its_registered_lifecycle(
    setup_module, repos, monkeypatch, service, process, reachable, active, use_sudo,
):
    _, local = repos
    events = []
    monkeypatch.setattr(setup_module, "ROOT", local)
    monkeypatch.setattr(setup_module, "_updated_setup_python", lambda _root: Path(sys.executable))
    monkeypatch.setattr(setup_module.daemon_management, "running_process", lambda _root: process)
    monkeypatch.setattr(setup_module.daemon_management, "daemon_reachable", lambda _root: reachable)
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: service)
    monkeypatch.setattr(setup_module.daemon_management, "service_is_active", lambda _root: active)
    monkeypatch.setattr(setup_module, "_authorize_update_service", lambda _root, **_kwargs: use_sudo)

    def stop_service(root, action, use_sudo=False):
        events.append(("service", root, action, use_sudo))
        return True, "Serviço parado."

    def stop_local(root):
        events.append(("local", root))
        return True, "Processo parado."

    def reopen(root, executable, *, restart_daemon, use_sudo=False):
        events.append(("setup", root, executable, restart_daemon, use_sudo))

    monkeypatch.setattr(setup_module.daemon_management, "service_action", stop_service)
    monkeypatch.setattr(setup_module.daemon_management, "stop_background", stop_local)
    monkeypatch.setattr(setup_module, "_restart_updated_setup", reopen)

    setup_module.update_upstream(local)

    assert (local / "new.txt").exists()
    assert events == [
        ("service", local, "stop", use_sudo) if service else ("local", local),
        ("setup", local, Path(sys.executable), True, use_sudo),
    ]


@pytest.mark.parametrize("service", [{}, {"name": "onmyoji-test", "scope": "user"}])
def test_update_does_not_start_a_previously_stopped_daemon(setup_module, repos, monkeypatch, service):
    _, local = repos
    reopened = []
    monkeypatch.setattr(setup_module, "ROOT", local)
    monkeypatch.setattr(setup_module, "_updated_setup_python", lambda _root: Path(sys.executable))
    monkeypatch.setattr(setup_module.daemon_management, "running_process", lambda _root: None)
    monkeypatch.setattr(setup_module.daemon_management, "daemon_reachable", lambda _root: False)
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: service)
    monkeypatch.setattr(setup_module.daemon_management, "service_is_active", lambda _root: False)
    monkeypatch.setattr(setup_module.daemon_management, "stop_background", lambda *_args: pytest.fail("Daemon já parado"))
    monkeypatch.setattr(setup_module.daemon_management, "service_action", lambda *_args, **_kwargs: pytest.fail("Serviço já parado"))
    monkeypatch.setattr(setup_module, "_authorize_update_service", lambda *_args, **_kwargs: pytest.fail("Sem operação privilegiada"))
    monkeypatch.setattr(
        setup_module, "_restart_updated_setup",
        lambda _root, _executable, *, restart_daemon, use_sudo=False: reopened.append((restart_daemon, use_sudo)),
    )

    setup_module.update_upstream(local)

    assert reopened == [(False, False)]


@pytest.mark.parametrize("service", [{}, {"name": "onmyoji-test", "scope": "user"}])
def test_update_does_not_migrate_when_daemon_stop_fails(setup_module, repos, monkeypatch, capsys, service):
    _, local = repos
    monkeypatch.setattr(setup_module, "ROOT", local)
    monkeypatch.setattr(setup_module, "_updated_setup_python", lambda _root: Path(sys.executable))
    monkeypatch.setattr(setup_module.daemon_management, "running_process", lambda _root: {"pid": 42})
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: service)
    monkeypatch.setattr(setup_module.daemon_management, "service_is_active", lambda _root: bool(service))
    monkeypatch.setattr(setup_module, "_authorize_update_service", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(setup_module.daemon_management, "stop_background", lambda *_args: (False, "Parada recusada"))
    monkeypatch.setattr(setup_module.daemon_management, "service_action", lambda *_args, **_kwargs: (False, "Parada recusada"))
    monkeypatch.setattr(setup_module, "_restart_updated_setup", lambda *_args, **_kwargs: pytest.fail("Migração com daemon ativo"))

    setup_module.update_upstream(local)

    assert "Parada recusada" in capsys.readouterr().out


def test_update_preserves_running_service_when_sudo_is_declined(setup_module, repos, monkeypatch, capsys):
    _, local = repos
    monkeypatch.setattr(setup_module, "ROOT", local)
    monkeypatch.setattr(setup_module, "_updated_setup_python", lambda _root: Path(sys.executable))
    monkeypatch.setattr(setup_module.daemon_management, "running_process", lambda _root: {"pid": 42})
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: {"name": "onmyoji-test", "scope": "system"})
    monkeypatch.setattr(setup_module.daemon_management, "service_is_active", lambda _root: True)
    monkeypatch.setattr(setup_module, "_authorize_update_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(setup_module.daemon_management, "service_action", lambda *_args, **_kwargs: pytest.fail("Sudo não autorizado"))
    monkeypatch.setattr(setup_module.daemon_management, "stop_background", lambda *_args: pytest.fail("Serviço registrado"))
    monkeypatch.setattr(setup_module, "_restart_updated_setup", lambda *_args, **_kwargs: pytest.fail("Atualização sem parada autorizada"))

    setup_module.update_upstream(local)

    assert "cancelada" in capsys.readouterr().out.casefold()


def test_update_reports_service_query_failure_before_migration(setup_module, repos, monkeypatch, capsys):
    _, local = repos
    monkeypatch.setattr(setup_module, "ROOT", local)
    monkeypatch.setattr(setup_module, "_updated_setup_python", lambda _root: Path(sys.executable))
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: {"name": "onmyoji-test", "scope": "system"})

    def unavailable(_root):
        raise OSError("systemctl indisponível")

    monkeypatch.setattr(setup_module.daemon_management, "service_is_active", unavailable)
    monkeypatch.setattr(setup_module.daemon_management, "service_action", lambda *_args, **_kwargs: pytest.fail("Consulta falhou"))
    monkeypatch.setattr(setup_module, "_restart_updated_setup", lambda *_args, **_kwargs: pytest.fail("Estado de execução desconhecido"))

    setup_module.update_upstream(local)

    assert "systemctl indisponível" in capsys.readouterr().out


@pytest.mark.parametrize("service, error", [
    ({}, OSError("Executável indisponível")),
    ({"name": "onmyoji-test", "scope": "user"}, subprocess.SubprocessError("Falha no comando")),
])
def test_update_reports_stop_execution_failure_before_migration(setup_module, repos, monkeypatch, capsys, service, error):
    _, local = repos
    monkeypatch.setattr(setup_module, "ROOT", local)
    monkeypatch.setattr(setup_module, "_updated_setup_python", lambda _root: Path(sys.executable))
    monkeypatch.setattr(setup_module.daemon_management, "running_process", lambda _root: {"pid": 42})
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: service)
    monkeypatch.setattr(setup_module.daemon_management, "service_is_active", lambda _root: bool(service))
    monkeypatch.setattr(setup_module, "_authorize_update_service", lambda *_args, **_kwargs: False)

    def unavailable(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(setup_module.daemon_management, "stop_background", unavailable)
    monkeypatch.setattr(setup_module.daemon_management, "service_action", unavailable)
    monkeypatch.setattr(setup_module, "_restart_updated_setup", lambda *_args, **_kwargs: pytest.fail("Daemon ainda ativo"))

    setup_module.update_upstream(local)

    assert str(error) in capsys.readouterr().out


@pytest.mark.parametrize("platform, scope, uid, authorized, expected", [
    ("nt", "system", 1000, False, False),
    ("posix", "user", 1000, False, False),
    ("posix", "system", 0, False, False),
    ("posix", "system", 1000, True, True),
])
def test_update_service_reuses_authorization_only_when_sudo_is_needed(
    setup_module, tmp_path, monkeypatch, platform, scope, uid, authorized, expected,
):
    monkeypatch.setattr(setup_module, "os", SimpleNamespace(name=platform, geteuid=lambda: uid))
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: {"name": "onmyoji-test", "scope": scope})
    monkeypatch.setattr(setup_module, "prompt", lambda _label: pytest.fail("Não deve pedir autorização novamente"))

    assert setup_module._authorize_update_service(tmp_path, use_sudo=authorized) is expected


@pytest.mark.parametrize("answer, expected", [("s", True), ("sim", True), ("", None), ("n", None), ("x", None)])
def test_update_service_confirms_sudo_before_changing_system_service(setup_module, tmp_path, monkeypatch, answer, expected):
    prompts = []
    monkeypatch.setattr(setup_module, "os", SimpleNamespace(name="posix", geteuid=lambda: 1000))
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: {"name": "onmyoji-test", "scope": "system"})

    def respond(label):
        prompts.append(label)
        return answer

    monkeypatch.setattr(setup_module, "prompt", respond)

    assert setup_module._authorize_update_service(tmp_path) is expected
    assert len(prompts) == 1 and "sudo" in prompts[0] and "[s/N]" in prompts[0]


@pytest.mark.parametrize("sudo_flag", [False, True])
def test_legacy_restart_flag_resumes_registered_service_after_migration(setup_module, tmp_path, monkeypatch, sudo_flag):
    events = []
    monkeypatch.setattr(setup_module, "ROOT", tmp_path)
    arguments = ["setupOnmyoji.py", "--resume-after-update", "--restart-daemon-after-update"]
    if sudo_flag:
        arguments.append("--update-service-sudo")
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(setup_module, "os", SimpleNamespace(name="posix", geteuid=lambda: 1000))
    monkeypatch.setattr(setup_module, "verify_state_layout", lambda _root: (events.append("migration") or True, "Layout confirmado"))
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: {"name": "onmyoji-test", "scope": "system"})
    monkeypatch.setattr(setup_module.daemon_management, "start_background", lambda *_args: pytest.fail("Serviço registrado"))
    monkeypatch.setattr(setup_module, "ensure_shikigami_configuration", lambda _root: (None, "Fim do teste"))

    def authorize(label):
        events.append("sudo-confirmation")
        assert "sudo" in label
        return "s"

    monkeypatch.setattr(setup_module, "prompt", authorize)

    def start_service(root, action, use_sudo=False):
        events.append(("service", root, action, use_sudo))
        return True, "Serviço iniciado"

    monkeypatch.setattr(setup_module.daemon_management, "service_action", start_service)

    setup_module.main()

    assert events == ["migration", *([] if sudo_flag else ["sudo-confirmation"]), ("service", tmp_path, "start", True)]


def test_failed_state_migration_never_restarts_daemon(setup_module, tmp_path, monkeypatch):
    monkeypatch.setattr(setup_module, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["setupOnmyoji.py", "--resume-after-update", "--restart-daemon-after-update"])
    monkeypatch.setattr(setup_module, "verify_state_layout", lambda _root: (False, "Estado não migrado"))
    monkeypatch.setattr(setup_module.daemon_management, "start_background", lambda *_args: pytest.fail("Estado não migrado"))
    monkeypatch.setattr(setup_module.daemon_management, "service_action", lambda *_args, **_kwargs: pytest.fail("Estado não migrado"))

    assert setup_module.main() == 2


def test_resume_local_daemon_uses_background_process(setup_module, tmp_path, monkeypatch):
    starts = []
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: {})
    monkeypatch.setattr(setup_module.daemon_management, "service_action", lambda *_args, **_kwargs: pytest.fail("Sem serviço registrado"))
    monkeypatch.setattr(setup_module.daemon_management, "start_background", lambda root: (starts.append(root) or True, "Processo iniciado"))

    assert setup_module._resume_updated_daemon(tmp_path) == (True, "Processo iniciado")
    assert starts == [tmp_path]


@pytest.mark.parametrize("service", [{}, {"name": "onmyoji-test", "scope": "user"}])
def test_resume_reports_operating_system_failures(setup_module, tmp_path, monkeypatch, service):
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: service)
    monkeypatch.setattr(setup_module, "_authorize_update_service", lambda *_args, **_kwargs: False)

    def unavailable(*_args, **_kwargs):
        raise OSError("Gerenciador indisponível")

    monkeypatch.setattr(setup_module.daemon_management, "start_background", unavailable)
    monkeypatch.setattr(setup_module.daemon_management, "service_action", unavailable)

    ok, message = setup_module._resume_updated_daemon(tmp_path)

    assert not ok and "Gerenciador indisponível" in message


@pytest.mark.parametrize("authorized, start_result", [
    (True, (True, "Serviço iniciado")),
    (True, (False, "Falha do systemctl")),
    (None, None),
])
def test_resume_service_preserves_sudo_permission_and_start_result(
    setup_module, tmp_path, monkeypatch, authorized, start_result,
):
    calls = []
    monkeypatch.setattr(setup_module.daemon_management, "service_info", lambda _root: {"name": "onmyoji-test", "scope": "system"})
    monkeypatch.setattr(setup_module.daemon_management, "start_background", lambda *_args: pytest.fail("Serviço registrado"))

    def authorize(root, *, use_sudo=False):
        calls.append(("authorize", root, use_sudo))
        return authorized

    def start(root, action, use_sudo=False):
        calls.append(("service", root, action, use_sudo))
        assert start_result is not None
        return start_result

    monkeypatch.setattr(setup_module, "_authorize_update_service", authorize)
    monkeypatch.setattr(setup_module.daemon_management, "service_action", start)

    ok, message = setup_module._resume_updated_daemon(tmp_path, use_sudo=True)

    if authorized is None:
        assert not ok and message
        assert calls == [("authorize", tmp_path, True)]
    else:
        assert (ok, message) == start_result
        assert calls == [("authorize", tmp_path, True), ("service", tmp_path, "start", True)]
