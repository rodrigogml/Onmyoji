"""Posse nativa, PID reutilizado e recuperação de processo descartável."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon import telegram_process as lifecycle
from onmyoji_daemon.telegram_persistence import GatewayStore


@pytest.fixture
def process_store(tmp_path):
    database = sqlite3.connect(tmp_path / "gateway.sqlite3")
    database.execute("CREATE TABLE inbox_items(id TEXT PRIMARY KEY,chat_id TEXT,generation INTEGER,text TEXT,attachment_ids TEXT,turn_context TEXT,state TEXT,created_at REAL)")
    database.commit()
    store = GatewayStore(database, threading.RLock())
    yield store
    database.close()


@pytest.fixture
def native_fixture(tmp_path, process_store, monkeypatch):
    binary = tmp_path / "fixture.exe"
    binary.write_bytes(b"synthetic executable")
    identity = {"pid": 424242, "created": "native-creation-123", "native_executable_path_sha256": "a" * 64, "native_executable_sha256": sha256(binary.read_bytes()).hexdigest()}
    terminated, closed = [], []
    native = SimpleNamespace(identity=lambda: dict(identity), terminate=lambda: terminated.append(identity["pid"]), close=lambda: closed.append(True))
    monkeypatch.setattr(lifecycle, "_open_native", lambda *_args, **_kwargs: native)
    client = SimpleNamespace(process=SimpleNamespace(pid=identity["pid"], poll=lambda: None), process_epoch="original-epoch")
    return SimpleNamespace(root=tmp_path, binary=binary, identity=identity, native=native, terminated=terminated, closed=closed, client=client, store=process_store)


def checkpoint(store):
    row = store.db.execute("SELECT value FROM gateway_checkpoints WHERE key=?", (lifecycle.CHECKPOINT_KEY,)).fetchone()
    return json.loads(row[0]) if row else None


def test_no_checkpoint_does_not_inspect_or_terminate_process(process_store, tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "_open_native", lambda *_args, **_kwargs: pytest.fail("Não há processo registrado"))
    assert lifecycle.recover_process(process_store, tmp_path, "fixture").action == "absent"
    assert not lifecycle.mark_stopped(process_store, "epoch")


def test_fake_client_without_pid_preserves_compatibility_without_checkpoint(process_store, tmp_path):
    assert lifecycle.record_process(process_store, tmp_path, "fixture", SimpleNamespace()) is False
    assert checkpoint(process_store) is None


def test_record_is_durable_private_and_idempotent_before_model(native_fixture):
    fixture = native_fixture
    assert lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    assert lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    recorded = checkpoint(fixture.store)
    assert recorded["state"] == "active" and recorded["process_epoch"] == "original-epoch"
    assert recorded["pid"] == fixture.identity["pid"] and recorded["created"] == fixture.identity["created"]
    assert str(fixture.root) not in json.dumps(recorded)
    assert fixture.terminated == [] and fixture.closed == [True, True]


def test_node_launcher_recovery_requires_both_script_and_native_identity(native_fixture):
    fixture = native_fixture
    fixture.identity["native_executable_sha256"] = "b" * 64
    fixture.native.matches_node_launcher = lambda executable: executable == str(fixture.binary)
    assert lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    assert checkpoint(fixture.store)["launcher"] == "node_script"
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_launcher_recovery_unverified"):
        lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary))
    assert not fixture.terminated and checkpoint(fixture.store)["state"] == "active"


def test_node_launcher_changed_arguments_block_recovery(native_fixture):
    fixture = native_fixture
    fixture.identity["native_executable_sha256"] = "b" * 64
    fixture.native.matches_node_launcher = lambda _executable: True
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    fixture.native.matches_node_launcher = lambda _executable: False
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_executable_unverified"):
        lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary))
    assert not fixture.terminated and checkpoint(fixture.store)["state"] == "active"


def test_node_launcher_changed_script_blocks_recovery(native_fixture):
    fixture = native_fixture
    fixture.identity["native_executable_sha256"] = "b" * 64
    fixture.native.matches_node_launcher = lambda _executable: True
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    fixture.binary.write_bytes(b"changed launcher")
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_executable_changed"):
        lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary))
    assert not fixture.terminated


def test_node_launcher_rejects_unverified_process_on_registration(native_fixture):
    fixture = native_fixture
    fixture.identity["native_executable_sha256"] = "b" * 64
    fixture.native.matches_node_launcher = lambda _executable: False
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_identity_unavailable"):
        lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    assert checkpoint(fixture.store) is None


@pytest.mark.parametrize("native_name,arguments,expected", [
    ("node", ["node", "script", "app-server", "--stdio"], True),
    ("nodejs", ["nodejs", "script", "app-server", "--stdio"], True),
    ("python", ["python", "script", "app-server", "--stdio"], False),
    ("node", ["node", "script", "other", "--stdio"], False),
    ("node", ["node", "other", "app-server", "--stdio"], False),
])
def test_linux_node_launcher_matches_exact_script_and_arguments(tmp_path, monkeypatch, native_name, arguments, expected):
    script = tmp_path / "codex.js"
    script.write_text("fixture", encoding="utf-8")
    other = tmp_path / "other.js"
    other.write_text("other", encoding="utf-8")
    arguments = [str(script) if value == "script" else str(other) if value == "other" else value for value in arguments]
    original = Path.read_bytes
    def read_bytes(path):
        return b"\0".join(os.fsencode(value) for value in arguments) + b"\0" if path.name == "cmdline" else original(path)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(lifecycle.os, "readlink", lambda _path: "/usr/bin/" + native_name)
    process = object.__new__(lifecycle._LinuxProcess)
    process.pid, process.fd = 424242, None
    assert process.matches_node_launcher(str(script)) is expected


@pytest.mark.skipif(sys.platform != "linux", reason="Exige identidade real em /proc Linux")
def test_real_linux_node_script_can_be_registered_without_terminating_another_process(tmp_path, process_store):
    node = shutil.which("node")
    if not node or not hasattr(os, "pidfd_open"):
        pytest.skip("Node e pidfd são necessários")
    script = tmp_path / "codex.js"
    script.write_text("setInterval(() => {}, 1000);", encoding="utf-8")
    process = subprocess.Popen([node, str(script), "app-server", "--stdio"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = SimpleNamespace(process=process, process_epoch="node-real-test")
    try:
        assert lifecycle.record_process(process_store, tmp_path, str(script), client)
        with pytest.raises(lifecycle.ProcessRecoveryError, match="process_launcher_recovery_unverified"):
            lifecycle.recover_process(process_store, tmp_path, str(script))
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_matching_orphan_is_terminated_once_and_checkpoint_becomes_stopped(native_fixture):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    result = lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary))
    assert (result.action, result.process_epoch, result.pid) == ("terminated", "original-epoch", fixture.identity["pid"])
    assert checkpoint(fixture.store)["state"] == "stopped"
    assert lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary)).action == "already_stopped"
    assert fixture.terminated == [fixture.identity["pid"]]


def test_changed_creation_proves_pid_reuse_without_terminating_replacement(native_fixture):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    fixture.identity["created"] = "new-creation"
    fixture.identity["native_executable_path_sha256"] = "c" * 64
    assert lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary)).action == "identity_mismatch"
    assert fixture.terminated == []
    assert checkpoint(fixture.store)["state"] == "replaced"


def test_changed_path_with_same_creation_does_not_prove_process_replacement(native_fixture):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    fixture.identity["native_executable_path_sha256"] = "c" * 64
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_executable_unverified"):
        lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary))
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_executable_unverified"):
        lifecycle.mark_stopped(fixture.store, "original-epoch")
    assert fixture.terminated == [] and checkpoint(fixture.store)["state"] == "active"


def test_changed_file_hash_alone_does_not_prove_original_process_is_gone(native_fixture):
    """Arquivo mutable no disco não revela quais bytes o processo já carregou."""
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    fixture.identity["native_executable_sha256"] = "b" * 64
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_executable_unverified"):
        lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary))
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_executable_unverified"):
        lifecycle.mark_stopped(fixture.store, "original-epoch")
    assert fixture.terminated == [] and checkpoint(fixture.store)["state"] == "active"


def test_other_instance_cannot_terminate_even_matching_native_process(native_fixture, tmp_path):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    other = tmp_path / "other-instance"
    other.mkdir()
    with pytest.raises(lifecycle.ProcessRecoveryError) as failure:
        lifecycle.recover_process(fixture.store, other, str(fixture.binary))
    assert failure.value.reason == "process_instance_mismatch"
    assert fixture.terminated == [] and checkpoint(fixture.store)["state"] == "active"


def test_changed_configured_executable_blocks_without_terminating(native_fixture):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    other = fixture.root / "new.exe"
    other.write_bytes(b"other synthetic executable")
    with pytest.raises(lifecycle.ProcessRecoveryError) as failure:
        lifecycle.recover_process(fixture.store, fixture.root, str(other))
    assert failure.value.reason == "process_executable_changed"
    assert fixture.terminated == [] and checkpoint(fixture.store)["state"] == "active"


def test_inaccessible_native_identity_blocks_and_retains_checkpoint(native_fixture, monkeypatch):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    def inaccessible(*_args, **_kwargs):
        raise lifecycle.ProcessRecoveryError("process_identity_unavailable")
    monkeypatch.setattr(lifecycle, "_open_native", inaccessible)
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_identity_unavailable"):
        lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary))
    assert fixture.terminated == [] and checkpoint(fixture.store)["state"] == "active"


def test_confirmed_gone_can_be_marked_without_terminating_any_pid(native_fixture, monkeypatch):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    monkeypatch.setattr(lifecycle, "_open_native", lambda *_args, **_kwargs: None)
    assert lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary)).action == "gone"
    assert checkpoint(fixture.store)["state"] == "stopped" and fixture.terminated == []


def test_mark_stopped_requires_death_and_never_overwrites_another_epoch(native_fixture):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    assert lifecycle.mark_stopped(fixture.store, "different-epoch") is False
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_still_running"):
        lifecycle.mark_stopped(fixture.store, "original-epoch")
    assert checkpoint(fixture.store)["state"] == "active"
    fixture.native.identity = lambda: None
    assert lifecycle.mark_stopped(fixture.store, "original-epoch")
    assert lifecycle.mark_stopped(fixture.store, "original-epoch")


def test_alive_new_client_cannot_overwrite_an_unresolved_process(native_fixture):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    fixture.client.process_epoch = "new-epoch"
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_checkpoint_active"):
        lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    assert checkpoint(fixture.store)["process_epoch"] == "original-epoch"


@pytest.mark.parametrize("document", ["not json", "[]", '{"schema_version":99}', '{"schema_version":1,"state":"active","pid":true}'])
def test_invalid_checkpoint_blocks_without_inspecting_or_terminating(process_store, tmp_path, monkeypatch, document):
    process_store.db.execute("INSERT INTO gateway_checkpoints VALUES (?,?)", (lifecycle.CHECKPOINT_KEY, document))
    process_store.db.commit()
    monkeypatch.setattr(lifecycle, "_open_native", lambda *_args, **_kwargs: pytest.fail("Checkpoint inválido não autoriza inspeção"))
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_checkpoint_invalid"):
        lifecycle.recover_process(process_store, tmp_path, "fixture")


@pytest.mark.parametrize("field,value", [("pid", 2 ** 32 + 424242), ("pid", 0), ("process_epoch", "untrusted\nvalue"), ("created", "unknown/path"), ("expected_executable_sha256", "x" * 64)])
def test_malformed_native_identity_never_reaches_pid_lookup(native_fixture, monkeypatch, field, value):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    malformed = {**checkpoint(fixture.store), field: value}
    fixture.store.db.execute("UPDATE gateway_checkpoints SET value=? WHERE key=?", (json.dumps(malformed), lifecycle.CHECKPOINT_KEY))
    fixture.store.db.commit()
    monkeypatch.setattr(lifecycle, "_open_native", lambda *_args, **_kwargs: pytest.fail("PID/campos inválidos não autorizam lookup"))
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_checkpoint_invalid"):
        lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary))


def test_unconfirmed_termination_remains_active_and_cannot_be_reopened(native_fixture):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    def unconfirmed():
        raise lifecycle.ProcessRecoveryError("process_termination_unconfirmed")
    fixture.native.terminate = unconfirmed
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_termination_unconfirmed"):
        lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary))
    assert checkpoint(fixture.store)["state"] == "active"


def test_process_exited_during_record_is_not_registered(native_fixture):
    fixture = native_fixture
    fixture.client.process.poll = lambda: 0
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_identity_unavailable"):
        lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    assert checkpoint(fixture.store) is None


def test_checkpoint_write_failure_blocks_before_any_model_operation(native_fixture):
    fixture = native_fixture
    fixture.store.db.set_authorizer(lambda action, table, *_args: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "gateway_checkpoints" else sqlite3.SQLITE_OK)
    try:
        with pytest.raises(lifecycle.ProcessRecoveryError, match="process_checkpoint_unavailable"):
            lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
        assert checkpoint(fixture.store) is None and fixture.terminated == []
    finally:
        fixture.store.db.set_authorizer(None)


def test_unreadable_checkpoint_blocks_without_process_inspection(process_store, tmp_path, monkeypatch):
    process_store.db.close()
    monkeypatch.setattr(lifecycle, "_open_native", lambda *_args, **_kwargs: pytest.fail("Sem estado legível não há posse"))
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_checkpoint_unavailable"):
        lifecycle.recover_process(process_store, tmp_path, "fixture")


def test_late_recovery_does_not_mark_a_replacement_epoch_stopped(native_fixture):
    fixture = native_fixture
    lifecycle.record_process(fixture.store, fixture.root, str(fixture.binary), fixture.client)
    def concurrent_replacement():
        replacement = {**checkpoint(fixture.store), "process_epoch": "replacement-epoch"}
        fixture.store.db.execute("UPDATE gateway_checkpoints SET value=? WHERE key=?", (json.dumps(replacement), lifecycle.CHECKPOINT_KEY))
        fixture.store.db.commit()
    fixture.native.terminate = concurrent_replacement
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_checkpoint_changed"):
        lifecycle.recover_process(fixture.store, fixture.root, str(fixture.binary))
    assert checkpoint(fixture.store)["process_epoch"] == "replacement-epoch"
    assert checkpoint(fixture.store)["state"] == "active"


def test_native_disposable_process_is_recorded_and_reaped_safely(process_store, tmp_path):
    """Integração real mata somente o Popen descartável criado por este teste."""
    if os.name not in {"nt", "posix"} or os.name == "posix" and not Path("/proc").is_dir():
        pytest.skip("Identidade nativa não implementada nesta plataforma")
    process = subprocess.Popen([sys.executable, "-B", "-c", "import threading; threading.Event().wait(60)"], cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    client = SimpleNamespace(process=process, process_epoch="disposable-real-process")
    try:
        assert lifecycle.record_process(process_store, tmp_path, sys.executable, client)
        assert process.poll() is None
        assert lifecycle.recover_process(process_store, tmp_path, sys.executable).action == "terminated"
        assert process.wait(timeout=5) is not None
        assert checkpoint(process_store)["state"] == "stopped"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.fixture
def linux_identity_probe(monkeypatch):
    """Modela apenas metadados /proc; prontidão do pidfd continua explícita."""
    native = lifecycle._LinuxProcess.__new__(lifecycle._LinuxProcess)
    native.pid, native.fd = 424242, 41
    ready = []
    monkeypatch.setattr(lifecycle.select, "select", lambda *_args: (list(ready), [], []))
    original_read_text = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == Path("/proc/424242/stat"):
            return "424242 (fixture) S " + " ".join(["0"] * 18 + ["123"])
        if path == Path("/proc/sys/kernel/random/boot_id"):
            return "00000000-0000-0000-0000-000000000000\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(lifecycle.os, "readlink", lambda _path: "/fixture/executable")
    monkeypatch.setattr(lifecycle, "_identity", lambda pid, created, executable: {"pid": pid, "created": created, "executable": executable})
    return SimpleNamespace(native=native, ready=ready, read_text=read_text)


@pytest.mark.parametrize("component,error", [("stat", FileNotFoundError), ("boot_id", FileNotFoundError), ("boot_id", PermissionError), ("exe", FileNotFoundError), ("executable_path", FileNotFoundError)])
def test_linux_missing_metadata_never_means_gone_while_pidfd_is_live(linux_identity_probe, monkeypatch, component, error):
    probe = linux_identity_probe

    def unavailable(*_args, **_kwargs):
        raise error("Metadata unavailable")

    if component in {"stat", "boot_id"}:
        def read_text(path, *args, **kwargs):
            if path.name == component:
                return unavailable()
            return probe.read_text(path, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", read_text)
    elif component == "exe":
        monkeypatch.setattr(lifecycle.os, "readlink", unavailable)
    else:
        monkeypatch.setattr(lifecycle, "_identity", unavailable)
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_identity_unavailable"):
        probe.native.identity()


def test_linux_zombie_leader_does_not_prove_entire_thread_group_is_gone(linux_identity_probe, monkeypatch):
    probe = linux_identity_probe

    def read_text(path, *args, **kwargs):
        value = probe.read_text(path, *args, **kwargs)
        return value.replace("(fixture) S", "(fixture) Z") if path.name == "stat" else value

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(lifecycle.os, "readlink", lambda _path: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_identity_unavailable"):
        probe.native.identity()


def test_linux_ready_pidfd_confirms_death_without_requiring_proc_metadata(linux_identity_probe, monkeypatch):
    probe = linux_identity_probe
    probe.ready.append(probe.native.fd)
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: pytest.fail("O pidfd já confirmou o término"))
    assert probe.native.identity() is None


def test_linux_metadata_loss_is_gone_only_after_pidfd_confirms_concurrent_exit(linux_identity_probe, monkeypatch):
    probe = linux_identity_probe

    def read_text(*_args, **_kwargs):
        probe.ready.append(probe.native.fd)
        raise FileNotFoundError("Process exited during /proc read")

    monkeypatch.setattr(Path, "read_text", read_text)
    assert probe.native.identity() is None


def test_linux_termination_requires_pidfd_instead_of_numeric_pid(monkeypatch):
    monkeypatch.delattr(lifecycle.os, "pidfd_open", raising=False)
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_pin_unavailable"):
        lifecycle._LinuxProcess(424242, terminate=True)


@pytest.fixture
def linux_disposable_process(tmp_path):
    """Cria ELF próprio; nunca renomeia nem encerra o executável do host."""
    if not sys.platform.startswith("linux"):
        pytest.skip("Exige Linux nativo com /proc e pidfd")
    if not hasattr(os, "pidfd_open") or not hasattr(lifecycle.signal, "pidfd_send_signal"):
        pytest.skip("Kernel/Python sem operações pidfd; recuperação deve bloquear")
    source = Path(shutil.which("sleep") or sys.executable).resolve(strict=True)
    with source.open("rb") as stream:
        assert stream.read(4) == b"\x7fELF", "O laboratório exige ELF real"
    binary = tmp_path / ("sleep" if shutil.which("sleep") else "python")
    shutil.copy2(source, binary)
    arguments = [str(binary), "60"] if binary.name == "sleep" else [str(binary), "-B", "-c", "import threading; threading.Event().wait(60)"]
    process = subprocess.Popen(arguments, cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield SimpleNamespace(binary=binary, arguments=arguments, process=process, client=SimpleNamespace(process=process, process_epoch="linux-disposable"))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_linux_native_executable_rename_keeps_live_identity_blocked(process_store, tmp_path, linux_disposable_process):
    fixture = linux_disposable_process
    assert lifecycle.record_process(process_store, tmp_path, str(fixture.binary), fixture.client)
    fixture.binary.rename(tmp_path / "renamed-executable")
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_executable_unverified"):
        lifecycle.recover_process(process_store, tmp_path, str(fixture.binary))
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_executable_unverified"):
        lifecycle.mark_stopped(process_store, "linux-disposable")
    assert fixture.process.poll() is None and checkpoint(process_store)["state"] == "active"


def test_linux_native_unlinked_executable_blocks_without_assuming_death(process_store, tmp_path, linux_disposable_process):
    fixture = linux_disposable_process
    assert lifecycle.record_process(process_store, tmp_path, str(fixture.binary), fixture.client)
    fixture.binary.unlink()
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_identity_unavailable"):
        lifecycle.recover_process(process_store, tmp_path, str(fixture.binary))
    assert fixture.process.poll() is None and checkpoint(process_store)["state"] == "active"


def test_linux_native_pidfd_confirms_gone_and_preserves_another_process(process_store, tmp_path, linux_disposable_process):
    fixture = linux_disposable_process
    assert lifecycle.record_process(process_store, tmp_path, str(fixture.binary), fixture.client)
    native = lifecycle._open_native(fixture.process.pid, terminate=True)
    other = subprocess.Popen(fixture.arguments, cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        native.terminate()
        fixture.process.wait(timeout=5)
        assert native.identity() is None
        assert other.poll() is None
        assert lifecycle.recover_process(process_store, tmp_path, str(fixture.binary)).action == "gone"
        assert checkpoint(process_store)["state"] == "stopped"
    finally:
        native.close()
        if other.poll() is None:
            other.kill()
        other.wait(timeout=5)


def test_linux_native_missing_boot_identity_blocks_live_pidfd(process_store, tmp_path, linux_disposable_process, monkeypatch):
    fixture = linux_disposable_process
    assert lifecycle.record_process(process_store, tmp_path, str(fixture.binary), fixture.client)
    original_read_text = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == Path("/proc/sys/kernel/random/boot_id"):
            raise PermissionError("Boot identity inaccessible")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(lifecycle.ProcessRecoveryError, match="process_identity_unavailable"):
        lifecycle.recover_process(process_store, tmp_path, str(fixture.binary))
    assert fixture.process.poll() is None and checkpoint(process_store)["state"] == "active"
