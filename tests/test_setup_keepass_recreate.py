from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "onmyoji-daemon" / "setupMcp.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_keepass_recreate_setup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ORIGINAL = b"existing-kdbx-with-entries"
REPLACEMENT = b"new-empty-kdbx"
RECREATE = "Excluir e criar um novo cofre"


@pytest.fixture
def existing_vault(tmp_path, monkeypatch):
    config = MODULE.McpConfiguration(tmp_path)
    database = tmp_path / "configs" / "mcp" / "vaults" / "default.kdbx"
    database.parent.mkdir(parents=True)
    database.write_bytes(ORIGINAL)
    def provider(name, *_args):
        return {
            "windows": {"mode": "windows_credential_manager", "target": f"Turing/MCP/KeePass/{name}"},
            "linux": {"mode": "protected_file", "path": str(tmp_path / "configs" / "mcp" / "secrets" / f"{name}.secret")},
        }
    calls = []
    choices = []

    def choose(title, options, default):
        choices.append((title, options, default))
        return "Criar cofre KDBX local" if title == "Cofre KeePassXC" else RECREATE

    def create(command, **options):
        assert database.read_bytes() == ORIGINAL
        assert "db-create" in command
        assert "new-master-value" not in " ".join(command)
        calls.append((command, options))
        candidate = Path(command[-1])
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(REPLACEMENT)
        return subprocess.CompletedProcess(command, 0)

    def discard(auth):
        secret = Path(auth["linux"]["path"])
        assert secret.is_relative_to(tmp_path / "configs" / "mcp" / "secrets")
        assert secret.name != "default.secret"
        secret.unlink(missing_ok=True)

    monkeypatch.setattr(MODULE, "ask_new_profile_name", lambda *_args: "default")
    monkeypatch.setattr(MODULE, "flow_choice", choose)
    monkeypatch.setattr(MODULE, "select_keepassxc_command", lambda: ["keepassxc-cli"])
    monkeypatch.setattr(MODULE, "profile_auth", provider)
    monkeypatch.setattr(MODULE, "ask", lambda *_args: "")
    monkeypatch.setattr(MODULE, "prompt", lambda *_args: "s")
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: "new-master-value")
    monkeypatch.setattr(MODULE, "apply_shared_file_mode", lambda *_args: None)
    monkeypatch.setattr(MODULE.KeePassCredentialResolver, "validate_vault_access", lambda *_args: None)
    monkeypatch.setattr(MODULE, "save_password", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(MODULE, "discard_recreated_password", discard)
    monkeypatch.setattr(MODULE.subprocess, "run", create)
    return config, database, calls, choices


@pytest.mark.parametrize("answer", ["", "n", "x", "\x1b"])
def test_recreation_requires_explicit_confirmation_before_password_or_file_changes(existing_vault, monkeypatch, answer):
    """Enter, recusa e cancelamento preservam o cofre e não pedem a nova senha."""
    config, database, calls, _choices = existing_vault
    prompts = []
    monkeypatch.setattr(MODULE, "prompt", lambda text: prompts.append(text) or answer)
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: pytest.fail("A exclusão ainda não foi confirmada."))
    monkeypatch.setattr(MODULE, "save_password", lambda *_args, **_kwargs: pytest.fail("O cancelamento não grava credenciais."))

    MODULE.create_vault_profile(config)

    assert prompts == ["Excluir o cofre existente e criar um novo? [s/N]: "]
    assert database.read_bytes() == ORIGINAL
    assert calls == []
    assert config.credentials()["profiles"] == {}


def test_existing_vault_menu_keeps_association_first_and_recreation_second(existing_vault, monkeypatch):
    config, database, _calls, choices = existing_vault
    monkeypatch.setattr(MODULE, "prompt", lambda *_args: "n")

    MODULE.create_vault_profile(config)

    assert choices[1] == ("Cofre local já existente", ["Associar o cofre existente", RECREATE], "Associar o cofre existente")
    assert database.read_bytes() == ORIGINAL


@pytest.mark.parametrize("answer", ["s", "sim"])
def test_confirmed_recreation_publishes_new_database_and_profile(existing_vault, monkeypatch, capsys, answer):
    """A confirmação publica um novo cofre com senha enviada somente pelo stdin."""
    config, database, calls, _choices = existing_vault
    monkeypatch.setattr(MODULE, "prompt", lambda *_args: answer)
    saved = []

    def save(name, auth, root, password):
        assert database.read_bytes() == ORIGINAL
        saved.append((name, auth, root, password))
        return True

    monkeypatch.setattr(MODULE, "save_password", save)

    MODULE.create_vault_profile(config)

    assert database.read_bytes() == REPLACEMENT
    assert len(calls) == len(saved) == 1
    command, options = calls[0]
    assert Path(command[-1]).is_relative_to(config.root / "tmp")
    assert options["input"] == "new-master-value\nnew-master-value\n"
    assert saved[0][0].startswith("default-recreated-")
    assert saved[0][2:] == (config.root, "new-master-value")
    credentials = config.credentials()
    assert credentials["profiles"]["default"]["vault"] == "default"
    assert credentials["profiles"]["default"]["auth"] == saved[0][1]
    assert str(database) in credentials["vaults"]["default"]["database"].values()
    assert "new-master-value" not in capsys.readouterr().out


def test_mismatched_passwords_preserve_existing_database(existing_vault, monkeypatch):
    config, database, calls, _choices = existing_vault
    passwords = iter(["new-master-value", "different-password"])
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: next(passwords))
    monkeypatch.setattr(MODULE, "save_password", lambda *_args, **_kwargs: pytest.fail("As senhas não conferem."))

    MODULE.create_vault_profile(config)

    assert database.read_bytes() == ORIGINAL
    assert calls == []
    assert config.credentials()["profiles"] == {}


def test_failed_candidate_creation_preserves_existing_database(existing_vault, monkeypatch):
    config, database, _calls, _choices = existing_vault

    def fail(command, **_options):
        Path(command[-1]).write_bytes(b"incomplete-candidate")
        return subprocess.CompletedProcess(command, 1, "", "database creation failed")

    monkeypatch.setattr(MODULE.subprocess, "run", fail)
    monkeypatch.setattr(MODULE, "save_password", lambda *_args, **_kwargs: pytest.fail("O candidato não foi criado com sucesso."))

    MODULE.create_vault_profile(config)

    assert database.read_bytes() == ORIGINAL
    assert config.credentials()["profiles"] == {}


def test_failed_password_storage_preserves_existing_database(existing_vault, monkeypatch):
    config, database, calls, _choices = existing_vault

    def refused(*_args, **_kwargs):
        assert database.read_bytes() == ORIGINAL
        return False

    monkeypatch.setattr(MODULE, "save_password", refused)

    MODULE.create_vault_profile(config)

    assert len(calls) == 1
    assert database.read_bytes() == ORIGINAL
    assert config.credentials()["profiles"] == {}


def test_failed_database_publication_preserves_existing_database_and_profile(existing_vault, monkeypatch):
    """Uma recusa na troca atômica não deixa o cofre anterior removido ou um perfil novo."""
    config, database, calls, _choices = existing_vault
    original_replace = MODULE.os.replace

    def denied(source, destination, *args, **kwargs):
        if Path(destination) == database:
            raise PermissionError("Não foi possível substituir o cofre.")
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(MODULE.os, "replace", denied)

    MODULE.create_vault_profile(config)

    assert len(calls) == 1
    assert database.read_bytes() == ORIGINAL
    assert config.credentials()["profiles"] == {}


def test_failed_profile_publication_restores_existing_database(existing_vault, monkeypatch):
    """Falha ao registrar o perfil não deixa o cofre anterior substituído."""
    config, database, calls, _choices = existing_vault

    def refused(_data):
        raise MODULE.McpConfigurationError("Não foi possível publicar o perfil.")

    monkeypatch.setattr(config, "save_credentials", refused)

    try:
        MODULE.create_vault_profile(config)
    except MODULE.McpConfigurationError:
        pass

    assert len(calls) == 1
    assert database.read_bytes() == ORIGINAL
    assert config.credentials()["profiles"] == {}


def test_failed_profile_publication_preserves_previous_master_password(existing_vault, monkeypatch):
    """A recriação usa outra credencial e mantém a senha anterior quando não conclui."""
    config, database, calls, _choices = existing_vault
    previous_secret = config.root / "configs" / "mcp" / "secrets" / "default.secret"
    previous_secret.parent.mkdir(parents=True)
    previous_secret.write_bytes(b"previous-master-value")
    saved = []

    def save(name, auth, root, password):
        assert name.startswith("default-recreated-")
        candidate = Path(auth["linux"]["path"])
        assert candidate != previous_secret
        candidate.write_text(password, encoding="utf-8")
        saved.append(candidate)
        return True

    def refused(_data):
        raise MODULE.McpConfigurationError("Não foi possível publicar o perfil.")

    monkeypatch.setattr(MODULE, "save_password", save)
    monkeypatch.setattr(config, "save_credentials", refused)

    try:
        MODULE.create_vault_profile(config)
    except MODULE.McpConfigurationError:
        pass

    assert len(calls) == len(saved) == 1
    assert database.read_bytes() == ORIGINAL
    assert previous_secret.read_bytes() == b"previous-master-value"
    assert config.credentials()["profiles"] == {}


@pytest.mark.parametrize("failure", [OSError("Gravação interrompida."), MODULE.McpConfigurationError("Publicação recusada.")])
def test_partial_profile_publication_restores_existing_configuration(existing_vault, monkeypatch, failure):
    """O rollback repõe a configuração anterior mesmo depois de uma escrita parcial."""
    config, database, calls, _choices = existing_vault
    credentials = config.credentials()
    credentials["vaults"]["other"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": str(config.root / "other.kdbx"), "linux": str(config.root / "other.kdbx")}}
    credentials["profiles"]["other"] = {"vault": "other", "allowed_entry_roots": [], "auth": MODULE.profile_auth("other", config.root)}
    config.save_credentials(credentials)
    original_configuration = config.credentials_path.read_bytes()

    def refused(_data):
        config.credentials_path.write_bytes(b"partial-configuration")
        raise failure

    monkeypatch.setattr(config, "save_credentials", refused)

    MODULE.create_vault_profile(config)

    assert len(calls) == 1
    assert database.read_bytes() == ORIGINAL
    assert config.credentials_path.read_bytes() == original_configuration
    assert set(config.credentials()["profiles"]) == {"other"}


def test_concurrent_database_change_is_not_overwritten_by_recreation(existing_vault, monkeypatch):
    """Um cofre alterado depois da confirmação não recebe o candidato preparado antes."""
    config, database, calls, _choices = existing_vault
    create = MODULE.subprocess.run

    def changed(command, **options):
        completed = create(command, **options)
        database.write_bytes(b"updated-by-another-process")
        return completed

    monkeypatch.setattr(MODULE.subprocess, "run", changed)

    MODULE.create_vault_profile(config)

    assert len(calls) == 1
    assert database.read_bytes() == b"updated-by-another-process"
    assert config.credentials()["profiles"] == {}


def test_cancelled_windows_credential_target_preserves_existing_vault(existing_vault, monkeypatch):
    """Cancelar o destino da credencial Windows encerra o roteiro antes da nova senha."""
    config, database, calls, _choices = existing_vault
    monkeypatch.setattr(MODULE, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(MODULE, "profile_auth", lambda *_args: {"windows": {"target": ""}, "linux": {"mode": "command", "command": ["secret-tool", "lookup"]}})
    monkeypatch.setattr(MODULE, "ask", lambda *_args: pytest.fail("O destino da credencial foi cancelado antes das raízes."))
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: pytest.fail("O cancelamento precede a nova senha."))

    MODULE.create_vault_profile(config)

    assert calls == []
    assert database.read_bytes() == ORIGINAL
    assert config.credentials()["profiles"] == {}


def test_database_referenced_by_another_profile_cannot_be_recreated(existing_vault, monkeypatch):
    """A opção destrutiva recusa um KDBX que já atende a outro perfil da instância."""
    config, database, calls, _choices = existing_vault
    credentials = config.credentials()
    credentials["vaults"]["other"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": str(database), "linux": str(database)}}
    credentials["profiles"]["other"] = {"vault": "other", "allowed_entry_roots": [], "auth": MODULE.profile_auth("other", config.root)}
    config.save_credentials(credentials)
    original_configuration = config.credentials_path.read_bytes()
    monkeypatch.setattr(MODULE, "prompt", lambda *_args: pytest.fail("Um cofre usado por outro perfil não pode chegar à confirmação."))
    monkeypatch.setattr(MODULE.getpass, "getpass", lambda *_args: pytest.fail("A recriação não é permitida."))

    MODULE.create_vault_profile(config)

    assert calls == []
    assert database.read_bytes() == ORIGINAL
    assert config.credentials_path.read_bytes() == original_configuration
    assert set(config.credentials()["profiles"]) == {"other"}


def test_master_password_store_uses_recreated_provider_attributes(tmp_path, monkeypatch, capsys):
    """Salvar novamente a senha respeita o namespace exclusivo declarado no lookup."""
    lookup = ["secret-tool", "lookup", "service", "shikigami-mcp", "shikigami", "Turing", "profile", "default-recreated-abc"]
    auth = {"linux": {"mode": "command", "command": lookup}}
    calls = []
    monkeypatch.setattr(MODULE, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(MODULE, "instance_identity", lambda _root: "Turing")
    monkeypatch.setattr(MODULE.subprocess, "run", lambda command, **options: calls.append((command, options)) or subprocess.CompletedProcess(command, 0))

    assert MODULE.save_password("default", auth, tmp_path, "new-master-value") is True

    assert len(calls) == 1
    command, options = calls[0]
    assert command[:2] == ["secret-tool", "store"]
    assert command[3:] == lookup[2:]
    assert options["input"] == "new-master-value\n"
    assert "new-master-value" not in " ".join(command)
    assert "new-master-value" not in capsys.readouterr().out
