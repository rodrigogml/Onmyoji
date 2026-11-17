"""Regressões da exclusão mútua Omie entre processos e das falhas locais."""
from __future__ import annotations

import errno
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread

import pytest

from onmyoji_daemon.mcp.omie.exception import OmieError
from onmyoji_daemon.mcp.omie import traffic
from onmyoji_daemon.mcp.omie.traffic import OmieTrafficGate


@pytest.mark.parametrize("legacy_byte", [False, True])
def test_method_lock_serializes_independent_processes_without_changing_file(tmp_path, legacy_byte):
    """Um concorrente espera tanto pela trava vazia quanto pelo arquivo legado de um byte."""
    gate = OmieTrafficGate()
    with gate.serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
        pass
    lock = next((tmp_path / "state" / "mcp" / "omie" / "method-locks").glob("*.lock"))
    if legacy_byte:
        lock.write_bytes(b"0")
    original_size = lock.stat().st_size
    source = Path(traffic.__file__).resolve().parents[3]
    script = """
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from onmyoji_daemon.mcp.omie.traffic import OmieTrafficGate
print("ready", flush=True)
with OmieTrafficGate().serialize(Path(sys.argv[2]), "synthetic-key", "ConsultarProjeto"):
    print("entered", flush=True)
"""
    lines = Queue()
    process = None
    reader = None

    def collect_output():
        for line in process.stdout:
            lines.put(line.strip())

    try:
        with gate.serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", script, str(source), str(tmp_path)],
                cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            reader = Thread(target=collect_output, daemon=True)
            reader.start()
            assert lines.get(timeout=10) == "ready"
            with pytest.raises(Empty):
                lines.get(timeout=0.2)
        assert process.wait(timeout=10) == 0, process.stderr.read()
        assert lines.get(timeout=2) == "entered"
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if reader is not None:
                reader.join(timeout=2)
            process.stdout.close()
            process.stderr.close()
    for _ in range(3):
        with gate.serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
            pass
    assert lock.stat().st_size == original_size


def test_method_lock_is_released_when_operation_raises(tmp_path):
    """A falha da operação preserva a exceção original e libera o método para outra instância."""
    with pytest.raises(ValueError, match="synthetic failure"):
        with OmieTrafficGate().serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
            raise ValueError("synthetic failure")
    with OmieTrafficGate().serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
        pass


def test_method_lock_preparation_error_is_sanitized(tmp_path):
    (tmp_path / "state").write_text("synthetic obstruction", encoding="utf-8")
    with pytest.raises(OmieError) as error:
        with OmieTrafficGate().serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
            pytest.fail("A operação não pode executar sem a trava.")
    assert error.value.code == "omie_local_lock_error"
    assert error.value.details == {"retryable": False}
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("contention_errno", [errno.EACCES, errno.EAGAIN])
def test_method_lock_contention_has_bounded_wait(tmp_path, monkeypatch, contention_errno):
    """Disputa repetida termina em omie_local_busy e não executa a operação."""
    def unavailable(*_args):
        raise OSError(contention_errno, "synthetic sensitive detail")

    if os.name == "nt":
        monkeypatch.setattr("msvcrt.locking", unavailable)
    else:
        monkeypatch.setattr("fcntl.flock", unavailable)
    clock = iter([0.0, 0.0, 60.0])
    delays = []
    monkeypatch.setattr(traffic, "monotonic", lambda: next(clock))
    monkeypatch.setattr(traffic, "sleep", delays.append)
    with pytest.raises(OmieError) as error:
        with OmieTrafficGate().serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
            pytest.fail("A operação não pode executar sem a trava.")
    assert error.value.code == "omie_local_busy"
    assert error.value.details == {"retryable": False, "retry_after_seconds": 3}
    assert delays == [0.1]
    assert "sensitive" not in str(error.value.as_dict())


def test_method_lock_unexpected_acquisition_error_is_sanitized(tmp_path, monkeypatch):
    def unavailable(*_args):
        raise OSError(errno.EIO, "synthetic sensitive detail")

    if os.name == "nt":
        monkeypatch.setattr("msvcrt.locking", unavailable)
    else:
        monkeypatch.setattr("fcntl.flock", unavailable)
    with pytest.raises(OmieError) as error:
        with OmieTrafficGate().serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
            pytest.fail("A operação não pode executar sem a trava.")
    assert error.value.code == "omie_local_lock_error"
    assert error.value.details == {"retryable": False}
    assert "sensitive" not in str(error.value.as_dict())


@pytest.mark.parametrize("operation_error", [
    None,
    ValueError("synthetic operation failure"),
    OmieError("network_error", "synthetic network failure"),
    KeyboardInterrupt("synthetic interruption"),
], ids=["success", "value_error", "domain_error", "interruption"])
def test_method_lock_release_preserves_operation_error_and_closes_handle(tmp_path, monkeypatch, operation_error):
    """A liberação informa sua fase após sucesso ou preserva a falha original e sempre fecha a trava."""
    if os.name == "nt":
        import msvcrt
        original = msvcrt.locking
        release = msvcrt.LK_UNLCK
        target = "msvcrt.locking"
    else:
        import fcntl
        original = fcntl.flock
        release = fcntl.LOCK_UN
        target = "fcntl.flock"

    def fail_release(handle, mode, *args):
        if mode == release:
            raise OSError(errno.EIO, "synthetic sensitive detail")
        return original(handle, mode, *args)

    with monkeypatch.context() as patch:
        patch.setattr(target, fail_release)
        with pytest.raises(OmieError if operation_error is None else type(operation_error)) as error:
            with OmieTrafficGate().serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
                if operation_error is not None:
                    raise operation_error
    if operation_error is None:
        assert error.value.code == "omie_local_lock_error"
        assert error.value.details == {"retryable": False, "phase": "release"}
        assert "sensitive" not in str(error.value.as_dict())
    else:
        assert error.value is operation_error
    with OmieTrafficGate().serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
        pass
