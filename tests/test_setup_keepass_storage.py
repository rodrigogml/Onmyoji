from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "onmyoji-daemon" / "setupMcp.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_keepass_storage_setup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class OperatingSystem:
    name = "posix"

    def __getattr__(self, name):
        return getattr(os, name)


def destination(root: Path, *, previous: str | None = None) -> Path:
    path = root / "configs" / "mcp" / "secrets" / "profile.secret"
    if previous is not None:
        path.parent.mkdir(parents=True)
        path.write_text(previous, encoding="utf-8")
    return path


def test_protected_password_is_published_in_utf8_with_private_temporary_files(tmp_path, monkeypatch):
    """A publicação nova não deixa staging ou senha parcial na pasta de configurações."""
    root = tmp_path / "instance"
    path = destination(root)
    monkeypatch.setattr(MODULE, "operational_file_mode", lambda _root: None)

    MODULE.store_protected_password(path, "senha-çã🔐", root)

    assert path.read_text(encoding="utf-8") == "senha-çã🔐"
    assert list((root / "tmp").iterdir()) == []
    assert list(path.parent.iterdir()) == [path]
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("shared, expected", [(False, 0o600), (True, 0o660)])
def test_password_policy_is_applied_only_to_new_staging_file(tmp_path, monkeypatch, shared, expected):
    """O modo final pertence ao staging novo; o arquivo anterior nunca recebe chmod."""
    root = tmp_path / "instance"
    path = destination(root, previous="senha anterior")
    previous_inode = path.stat().st_ino
    operating_system = OperatingSystem()
    modes = []
    def chmod(descriptor, mode):
        assert os.fstat(descriptor).st_ino != previous_inode
        modes.append(mode)
    operating_system.fchmod = chmod
    monkeypatch.setattr(MODULE, "os", operating_system)
    monkeypatch.setattr(MODULE, "operational_file_mode", lambda _root: 0o660 if shared else None)

    MODULE.store_protected_password(path, "senha nova", root)

    assert modes == [expected]
    assert path.read_text(encoding="utf-8") == "senha nova"


def test_shared_directory_with_compatible_permissions_is_not_chmodded(tmp_path, monkeypatch):
    """A segunda conta pode substituir o segredo sem ser dona do diretório existente."""
    root = tmp_path / "instance"
    path = destination(root, previous="senha anterior")
    operating_system = OperatingSystem()
    operating_system.fchmod = lambda *_args: None
    monkeypatch.setattr(MODULE, "os", operating_system)
    monkeypatch.setattr(MODULE, "operational_file_mode", lambda _root: 0o660)
    monkeypatch.setattr(MODULE, "stat", SimpleNamespace(S_ISREG=stat.S_ISREG, S_IMODE=lambda _mode: 0o2770))
    monkeypatch.setattr(Path, "chmod", lambda *_args, **_kwargs: pytest.fail("Uma pasta já compatível não exige chmod do proprietário."))

    MODULE.store_protected_password(path, "senha nova", root)

    assert path.read_text(encoding="utf-8") == "senha nova"


def test_staging_inherits_target_group_only_when_necessary(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    path = destination(root, previous="senha anterior")
    group = path.parent.stat().st_gid
    operating_system = OperatingSystem()
    calls = []
    operating_system.fstat = lambda _descriptor: SimpleNamespace(st_gid=group + 1)
    operating_system.fchown = lambda descriptor, owner, target_group: calls.append((owner, target_group))
    operating_system.fchmod = lambda *_args: None
    monkeypatch.setattr(MODULE, "os", operating_system)
    monkeypatch.setattr(MODULE, "operational_file_mode", lambda _root: 0o660)

    MODULE.store_protected_password(path, "senha nova", root)

    assert calls == [(-1, group)]
    assert path.read_text(encoding="utf-8") == "senha nova"


def test_matching_staging_group_does_not_require_chown(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    path = destination(root, previous="senha anterior")
    operating_system = OperatingSystem()
    operating_system.fchmod = lambda *_args: None
    operating_system.fchown = lambda *_args: pytest.fail("O grupo já corresponde ao destino.")
    monkeypatch.setattr(MODULE, "os", operating_system)
    monkeypatch.setattr(MODULE, "operational_file_mode", lambda _root: 0o660)

    MODULE.store_protected_password(path, "senha nova", root)

    assert path.read_text(encoding="utf-8") == "senha nova"


@pytest.mark.parametrize("failure", ["chown", "chmod", "fsync", "replace", "fdopen"])
def test_publication_failure_preserves_previous_password_and_cleans_staging(tmp_path, monkeypatch, failure):
    """Falhas de permissão, persistência e troca não truncam o segredo válido anterior."""
    root = tmp_path / "instance"
    path = destination(root, previous="senha anterior")
    operating_system = OperatingSystem()
    operating_system.fchmod = lambda *_args: None
    operating_system.fchown = lambda *_args: None
    def fail(*_args, **_kwargs):
        raise PermissionError("gravação indisponível")
    if failure == "chown":
        operating_system.fstat = lambda _descriptor: SimpleNamespace(st_gid=path.parent.stat().st_gid + 1)
        operating_system.fchown = fail
    elif failure == "chmod":
        operating_system.fchmod = fail
    else:
        setattr(operating_system, failure, fail)
    monkeypatch.setattr(MODULE, "os", operating_system)
    monkeypatch.setattr(MODULE, "operational_file_mode", lambda _root: 0o660)

    with pytest.raises(PermissionError, match="gravação indisponível"):
        MODULE.store_protected_password(path, "senha nova", root)

    assert path.read_text(encoding="utf-8") == "senha anterior"
    assert list((root / "tmp").iterdir()) == []


@pytest.mark.parametrize("kind", ["outside", "directory", "hardlink", "secret-folder", "temporary-folder", "destination-changed"])
def test_unsafe_password_destinations_are_rejected_without_overwriting(tmp_path, monkeypatch, kind):
    root = tmp_path / "instance"
    path = destination(root, previous="senha anterior")
    original = path.read_bytes()
    monkeypatch.setattr(MODULE, "operational_file_mode", lambda _root: None)
    if kind == "outside":
        path = tmp_path / "outside.secret"
        path.write_text("externo", encoding="utf-8")
        original = path.read_bytes()
    elif kind == "directory":
        path = path.parent
    elif kind == "hardlink":
        try:
            os.link(path, tmp_path / "linked.secret")
        except OSError as error:
            pytest.skip(f"Hard links indisponíveis: {type(error).__name__}")
    elif kind in {"secret-folder", "temporary-folder"}:
        linked = path.parent if kind == "secret-folder" else root / "tmp"
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda selected: selected == linked or original_is_symlink(selected))
    else:
        original_lstat = Path.lstat
        reads = []
        def lstat(selected):
            if selected == path:
                reads.append(selected)
                if len(reads) > 1:
                    return SimpleNamespace(st_mode=stat.S_IFLNK, st_nlink=1)
            return original_lstat(selected)
        monkeypatch.setattr(Path, "lstat", lstat)

    with pytest.raises(OSError):
        MODULE.store_protected_password(path, "senha nova", root)

    if kind != "directory":
        assert path.read_bytes() == original
    if (root / "tmp").exists():
        assert list((root / "tmp").iterdir()) == []


def test_nested_relative_secret_path_is_scoped_to_the_instance(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    monkeypatch.setattr(MODULE, "operational_file_mode", lambda _root: None)

    MODULE.store_protected_password(Path("configs/mcp/secrets/nested/profile.secret"), "senha nova", root)

    assert (root / "configs" / "mcp" / "secrets" / "nested" / "profile.secret").read_text(encoding="utf-8") == "senha nova"
