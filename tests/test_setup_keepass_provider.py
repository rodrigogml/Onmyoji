from __future__ import annotations

import copy
import errno
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "onmyoji-daemon" / "setupMcp.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_keepass_provider_setup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(MODULE, "os", SimpleNamespace(name="posix", environ=os.environ))
    monkeypatch.setattr(MODULE, "instance_identity", lambda _root: "Turing")


def command_auth():
    return {"linux": {"mode": "command", "command": ["secret-tool", "lookup", "profile", "default"]}}


@pytest.mark.parametrize("secret_tool", [False, True])
@pytest.mark.parametrize("session_bus", [False, True])
def test_new_linux_profiles_use_protected_files_regardless_of_session_services(tmp_path, monkeypatch, linux, secret_tool, session_bus):
    """A presença de CLI e D-Bus não seleciona um serviço que pode recusar gravações."""
    monkeypatch.setattr(MODULE.shutil, "which", lambda _name: "/usr/bin/secret-tool" if secret_tool else None)
    if session_bus:
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    else:
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    auth = MODULE.profile_auth("default", tmp_path)

    assert auth["linux"]["mode"] == "protected_file"
    assert Path(auth["linux"]["path"]).is_relative_to(tmp_path / "configs" / "mcp" / "secrets")


@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(1, ["secret-tool"], stderr="master-value must stay hidden"),
    OSError(errno.EACCES, "master-value must stay hidden"),
    subprocess.TimeoutExpired(["secret-tool"], 20, stderr="master-value must stay hidden"),
])
def test_secret_service_failure_can_use_protected_file_without_reasking_the_password(tmp_path, monkeypatch, capsys, linux, failure):
    """O backend muda somente depois de gravar a mesma senha já informada."""
    auth = command_auth()
    original = copy.deepcopy(auth)
    choices = []
    stored = []
    def unavailable(_command, **options):
        assert options["input"] == "master-value\n"
        raise failure
    def choose(title, options, default):
        choices.append((title, options, default))
        return "Usar arquivo local protegido"
    def store(path, password, *args, **kwargs):
        assert auth == original
        stored.append((path, password))
    monkeypatch.setattr(MODULE.subprocess, "run", unavailable)
    monkeypatch.setattr(MODULE, "flow_choice", choose)
    monkeypatch.setattr(MODULE, "store_protected_password", store)
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: pytest.fail("A senha já foi informada."))

    assert MODULE.save_password("default", auth, tmp_path, "master-value") is True

    assert len(stored) == 1
    assert stored[0][1] == "master-value"
    assert stored[0][0].is_relative_to(tmp_path / "configs" / "mcp" / "secrets")
    assert auth["linux"] == {"mode": "protected_file", "path": str(stored[0][0])}
    assert choices[0][1] == ["Tentar novamente no provedor do sistema", "Usar arquivo local protegido"]
    assert choices[0][2] == "Usar arquivo local protegido"
    output = capsys.readouterr().out
    assert "master-value" not in output
    assert "Traceback" not in output


def test_cancelled_provider_recovery_preserves_authentication_and_does_not_write(tmp_path, monkeypatch, linux):
    auth = command_auth()
    original = copy.deepcopy(auth)
    def unavailable(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["secret-tool"])
    monkeypatch.setattr(MODULE.subprocess, "run", unavailable)
    monkeypatch.setattr(MODULE, "flow_choice", lambda *_args: None)
    monkeypatch.setattr(MODULE, "store_protected_password", lambda *_args, **_kwargs: pytest.fail("Cancelamento não grava outro backend."))

    assert MODULE.save_password("default", auth, tmp_path, "master-value") is False
    assert auth == original


def test_secret_service_retry_reuses_the_password_and_keeps_the_original_provider(tmp_path, monkeypatch, linux):
    auth = command_auth()
    original = copy.deepcopy(auth)
    inputs = []
    def run(command, **options):
        inputs.append(options["input"])
        if len(inputs) == 1:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(MODULE.subprocess, "run", run)
    monkeypatch.setattr(MODULE, "flow_choice", lambda *_args: "Tentar novamente no provedor do sistema")
    monkeypatch.setattr(MODULE, "store_protected_password", lambda *_args, **_kwargs: pytest.fail("A nova tentativa não muda o provedor."))
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: pytest.fail("A senha já foi informada."))

    assert MODULE.save_password("default", auth, tmp_path, "master-value") is True
    assert inputs == ["master-value\n", "master-value\n"]
    assert auth == original


def test_failed_protected_file_fallback_does_not_change_the_configured_provider(tmp_path, monkeypatch, capsys, linux):
    auth = command_auth()
    original = copy.deepcopy(auth)
    def unavailable(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["secret-tool"])
    def denied(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "master-value must stay hidden")
    monkeypatch.setattr(MODULE.subprocess, "run", unavailable)
    monkeypatch.setattr(MODULE, "flow_choice", lambda *_args: "Usar arquivo local protegido")
    monkeypatch.setattr(MODULE, "store_protected_password", denied)

    assert MODULE.save_password("default", auth, tmp_path, "master-value") is False
    assert auth == original
    output = capsys.readouterr().out
    assert "master-value" not in output
    assert "Traceback" not in output
    assert "permiss" in output.casefold()


@pytest.mark.parametrize("failure, expected", [
    (PermissionError(errno.EPERM, "master-value must stay hidden"), "permiss"),
    (FileNotFoundError(errno.ENOENT, "master-value must stay hidden"), "indisponível"),
    (OSError(errno.EIO, "master-value must stay hidden"), "erro 5"),
    (OSError("master-value must stay hidden"), "falha de acesso"),
])
def test_existing_protected_file_failure_does_not_offer_another_provider(tmp_path, monkeypatch, capsys, linux, failure, expected):
    auth = {"linux": {"mode": "protected_file", "path": str(tmp_path / "configs" / "mcp" / "secrets" / "default.secret")}}
    def denied(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(MODULE, "store_protected_password", denied)
    monkeypatch.setattr(MODULE, "flow_choice", lambda *_args: pytest.fail("A falha de escrita precisa ser corrigida, sem trocar novamente o backend."))

    assert MODULE.save_password("default", auth, tmp_path, "master-value") is False
    output = capsys.readouterr().out
    assert "master-value" not in output
    assert expected in output.casefold()


def test_windows_provider_failure_does_not_offer_linux_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(MODULE, "os", SimpleNamespace(name="nt"))
    auth = {"windows": {"mode": "windows_credential_manager", "target": "Turing/KeePass/default"}}
    def denied(*_args):
        raise OSError(errno.EACCES, "master-value must stay hidden")
    monkeypatch.setattr(MODULE, "store_windows_password", denied)
    monkeypatch.setattr(MODULE, "flow_choice", lambda *_args: pytest.fail("A recuperação Linux não pertence ao Windows."))

    assert MODULE.save_password("default", auth, tmp_path, "master-value") is False
    assert "master-value" not in capsys.readouterr().out


def test_empty_password_never_calls_a_provider(tmp_path, monkeypatch, linux):
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: "")
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Uma senha vazia não pode ser salva."))
    monkeypatch.setattr(MODULE, "store_protected_password", lambda *_args, **_kwargs: pytest.fail("Uma senha vazia não pode ser salva."))
    assert MODULE.save_password("default", command_auth(), tmp_path) is False


def orphan_configuration(tmp_path):
    config = MagicMock(root=tmp_path)
    config.credentials.return_value = {"defaults": {"timeout_seconds": 30}, "vaults": {}, "profiles": {}}
    database = tmp_path / "configs" / "mcp" / "vaults" / "default.kdbx"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"existing-kdbx-must-not-change")
    return config, database


def prepare_orphan_flow(monkeypatch, auth, decisions):
    choices = iter(decisions)
    monkeypatch.setattr(MODULE, "ask_new_profile_name", lambda *_args: "default")
    monkeypatch.setattr(MODULE, "flow_choice", lambda *_args: next(choices))
    monkeypatch.setattr(MODULE, "select_keepassxc_command", lambda: ["keepassxc-cli"])
    monkeypatch.setattr(MODULE, "profile_auth", lambda *_args: auth)
    monkeypatch.setattr(MODULE, "ask", lambda *_args: "")
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Um cofre existente nunca é recriado."))


def test_orphan_database_can_be_associated_without_recreating_or_reconfirming_its_password(tmp_path, monkeypatch, linux):
    """Uma criação interrompida pode ser retomada sem sobrescrever o KDBX já criado."""
    config, database = orphan_configuration(tmp_path)
    auth = {"linux": {"mode": "protected_file", "path": str(tmp_path / "configs" / "mcp" / "secrets" / "default.secret")}}
    prepare_orphan_flow(monkeypatch, auth, ["Criar cofre KDBX local", "Associar o cofre existente"])
    passwords = []
    validations = []
    saved = []
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda label: passwords.append(label) or "master-value")
    monkeypatch.setattr(MODULE.KeePassCredentialResolver, "validate_vault_access", lambda *args: validations.append(args))
    monkeypatch.setattr(MODULE, "save_password", lambda *args: saved.append(args) or True)

    MODULE.create_vault_profile(config)

    assert database.read_bytes() == b"existing-kdbx-must-not-change"
    assert len(passwords) == 1
    assert len(validations) == len(saved) == 1
    assert validations[0][0]["database"]["linux"] == str(database)
    assert validations[0][2] == "master-value"
    assert saved[0] == ("default", auth, tmp_path, "master-value")
    config.save_credentials.assert_called_once()
    data = config.save_credentials.call_args.args[0]
    assert data["profiles"]["default"]["auth"] == auth
    assert data["vaults"]["default"]["database"]["linux"] == str(database)


def test_cancelled_orphan_recovery_does_not_ask_for_a_password_or_change_the_database(tmp_path, monkeypatch, linux):
    config, database = orphan_configuration(tmp_path)
    prepare_orphan_flow(monkeypatch, {}, ["Criar cofre KDBX local", None])
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: pytest.fail("O cancelamento precede a senha."))
    monkeypatch.setattr(MODULE, "save_password", lambda *_args: pytest.fail("O cancelamento não salva credenciais."))

    MODULE.create_vault_profile(config)

    assert database.read_bytes() == b"existing-kdbx-must-not-change"
    config.save_credentials.assert_not_called()


def test_invalid_orphan_password_does_not_create_a_profile_or_change_the_database(tmp_path, monkeypatch, linux):
    config, database = orphan_configuration(tmp_path)
    auth = {"linux": {"mode": "protected_file", "path": str(tmp_path / "configs" / "mcp" / "secrets" / "default.secret")}}
    prepare_orphan_flow(monkeypatch, auth, ["Criar cofre KDBX local", "Associar o cofre existente"])
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: "invalid-password")
    def invalid(*_args):
        raise MODULE.CredentialError("keepass_error", "Senha recusada.")
    monkeypatch.setattr(MODULE.KeePassCredentialResolver, "validate_vault_access", invalid)
    monkeypatch.setattr(MODULE, "save_password", lambda *_args: pytest.fail("Uma senha não validada não pode ser protegida."))

    MODULE.create_vault_profile(config)

    assert database.read_bytes() == b"existing-kdbx-must-not-change"
    config.save_credentials.assert_not_called()


@pytest.mark.parametrize("existing_database", [False, True])
def test_cancelled_entry_roots_stop_before_password_and_profile_changes(tmp_path, monkeypatch, linux, existing_database):
    """Cancelar a entrada livre não converte a escolha em acesso a todas as raízes."""
    if existing_database:
        config, database = orphan_configuration(tmp_path)
    else:
        config = MagicMock(root=tmp_path)
        config.credentials.return_value = {"defaults": {}, "vaults": {}, "profiles": {}}
        database = tmp_path / "configs" / "mcp" / "vaults" / "default.kdbx"
    original = copy.deepcopy(config.credentials.return_value)
    auth = {"linux": {"mode": "protected_file", "path": str(tmp_path / "configs" / "mcp" / "secrets" / "default.secret")}}
    decisions = ["Criar cofre KDBX local", "Associar o cofre existente"] if existing_database else ["Criar cofre KDBX local"]
    prepare_orphan_flow(monkeypatch, auth, decisions)
    monkeypatch.setattr(MODULE, "ask", lambda *_args: None)
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: pytest.fail("Cancelar as raízes precede a senha."))
    monkeypatch.setattr(MODULE, "save_password", lambda *_args: pytest.fail("Cancelar as raízes não grava credenciais."))

    MODULE.create_vault_profile(config)

    assert config.credentials.return_value == original
    config.save_credentials.assert_not_called()
    if existing_database:
        assert database.read_bytes() == b"existing-kdbx-must-not-change"
    else:
        assert not database.exists()


def test_new_database_creation_persists_the_protected_provider_after_secret_service_failure(tmp_path, monkeypatch, capsys, linux):
    """A recuperação da senha conclui o perfil do novo KDBX sem repetir sua criação."""
    config = MagicMock(root=tmp_path)
    config.credentials.return_value = {"defaults": {}, "vaults": {}, "profiles": {}}
    database = tmp_path / "configs" / "mcp" / "vaults" / "default.kdbx"
    auth = command_auth()
    calls = []
    passwords = []
    protected = []
    monkeypatch.setattr(MODULE, "ask_new_profile_name", lambda *_args: "default")
    monkeypatch.setattr(MODULE, "flow_choice", lambda title, *_args: "Criar cofre KDBX local" if title == "Cofre KeePassXC" else "Usar arquivo local protegido")
    monkeypatch.setattr(MODULE, "select_keepassxc_command", lambda: ["keepassxc-cli"])
    monkeypatch.setattr(MODULE, "profile_auth", lambda *_args: auth)
    monkeypatch.setattr(MODULE, "ensure_linux_secret_tool", lambda: True)
    monkeypatch.setattr(MODULE, "ask", lambda *_args: "")
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda label: passwords.append(label) or "master-value")
    monkeypatch.setattr(MODULE, "apply_shared_file_mode", lambda *_args: None)
    def run(command, **options):
        calls.append((command, options["input"]))
        assert "master-value" not in " ".join(command)
        if command[0] == "keepassxc-cli":
            assert "db-create" in command
            database.write_bytes(b"new-kdbx")
            return subprocess.CompletedProcess(command, 0)
        raise subprocess.CalledProcessError(1, command, stderr="master-value must stay hidden")
    def store(path, password, *args, **kwargs):
        assert auth["linux"]["mode"] == "command"
        protected.append((path, password))
    monkeypatch.setattr(MODULE.subprocess, "run", run)
    monkeypatch.setattr(MODULE, "store_protected_password", store)

    MODULE.create_vault_profile(config)

    assert len(passwords) == 2
    assert len(calls) == 2
    assert calls[0][0][0] == "keepassxc-cli"
    assert calls[0][1] == "master-value\nmaster-value\n"
    assert calls[1][0][0] == "secret-tool"
    assert calls[1][1] == "master-value\n"
    assert len(protected) == 1 and protected[0][1] == "master-value"
    config.save_credentials.assert_called_once()
    profile = config.save_credentials.call_args.args[0]["profiles"]["default"]
    assert profile["auth"]["linux"] == {"mode": "protected_file", "path": str(protected[0][0])}
    assert database.read_bytes() == b"new-kdbx"
    assert "master-value" not in capsys.readouterr().out


@pytest.mark.parametrize("saved, changed", [(True, True), (True, False), (False, False)])
def test_managing_master_password_persists_only_a_successful_provider_change(tmp_path, monkeypatch, linux, saved, changed):
    config = MagicMock(root=tmp_path)
    auth = command_auth()
    data = {"vaults": {"default": {"database": {"linux": str(tmp_path / "default.kdbx")}}}, "profiles": {"default": {"vault": "default", "auth": auth, "allowed_entry_roots": []}}}
    config.credentials.return_value = data
    actions = iter(["5", "x"])
    monkeypatch.setattr(MODULE, "prompt", lambda *_args: next(actions))
    def save(_name, selected_auth, _root):
        if changed:
            selected_auth["linux"] = {"mode": "protected_file", "path": str(tmp_path / "configs" / "mcp" / "secrets" / "default.secret")}
        return saved
    monkeypatch.setattr(MODULE, "save_password", save)

    MODULE.manage_vault_profile(config, "default")

    assert config.save_credentials.call_count == (1 if saved and changed else 0)
    if saved and changed:
        assert config.save_credentials.call_args.args[0]["profiles"]["default"]["auth"]["linux"]["mode"] == "protected_file"


def test_managing_provider_change_restores_authentication_when_configuration_cannot_be_saved(tmp_path, monkeypatch, capsys, linux):
    """O perfil em memória continua usando o backend vigente se sua publicação falhar."""
    config = MagicMock(root=tmp_path)
    original = command_auth()
    data = {"vaults": {"default": {}}, "profiles": {"default": {"vault": "default", "auth": copy.deepcopy(original), "allowed_entry_roots": []}}}
    config.credentials.return_value = data
    config.save_credentials.side_effect = MODULE.McpConfigurationError("Não foi possível publicar a configuração do cofre.")
    actions = iter(["5", "x"])
    monkeypatch.setattr(MODULE, "prompt", lambda *_args: next(actions))
    def save(_name, auth, _root):
        auth["linux"] = {"mode": "protected_file", "path": str(tmp_path / "configs" / "mcp" / "secrets" / "default.secret")}
        return True
    monkeypatch.setattr(MODULE, "save_password", save)

    MODULE.manage_vault_profile(config, "default")

    config.save_credentials.assert_called_once()
    assert data["profiles"]["default"]["auth"] == original
    assert "Não foi possível publicar a configuração do cofre." in capsys.readouterr().out
