"""Substituição de configuração preserva bytes e limita retries de erros Windows."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from onmyoji_daemon import config_publication


def windows_error(code):
    error = PermissionError("bloqueio fictício")
    error.winerror = code
    return error


@pytest.mark.parametrize("code,failures", [(5, 1), (32, 1), (33, 1), (5, 4)])
def test_transient_windows_lock_retries_and_publishes_exact_utf8_bytes(tmp_path, monkeypatch, code, failures):
    source, destination = tmp_path / "candidate.toml", tmp_path / "config.toml"
    original, candidate = 'nome="antigo"\r\n'.encode("utf-8"), 'nome="configuração nova"\r\n'.encode("utf-8")
    source.write_bytes(candidate); destination.write_bytes(original)
    replace = Path.replace
    attempts, delays = [], []
    def publish(path, target):
        attempts.append((path, target))
        assert source.read_bytes() == candidate and destination.read_bytes() == original
        if len(attempts) <= failures: raise windows_error(code)
        return replace(path, target)
    monkeypatch.setattr(Path, "replace", publish)
    monkeypatch.setattr(config_publication.time, "sleep", delays.append)
    config_publication.publish_file(source, destination)
    assert attempts == [(source, destination)] * (failures + 1)
    assert delays == [attempt / 100 for attempt in range(1, failures + 1)]
    assert sum(delays) <= 0.1
    assert not source.exists() and destination.read_bytes() == candidate


def test_success_without_lock_needs_one_attempt_and_no_wait(tmp_path, monkeypatch):
    source, destination = tmp_path / "candidate.json", tmp_path / "config.json"
    source.write_bytes(b'{"fixture":true}')
    monkeypatch.setattr(config_publication.time, "sleep", lambda _delay: pytest.fail("sucesso não deve aguardar"))
    config_publication.publish_file(source, destination)
    assert destination.read_bytes() == b'{"fixture":true}' and not source.exists()


@pytest.mark.skipif(os.name == "nt", reason="Modos POSIX são validados no Linux.")
def test_private_publication_applies_owner_and_group_mode_before_replace(tmp_path):
    source, destination = tmp_path / "candidate.json", tmp_path / "config.json"
    source.write_bytes(b'{}')
    source.chmod(0o600)

    config_publication.publish_file(source, destination, mode=0o660)

    assert destination.stat().st_mode & 0o777 == 0o660


def test_fifth_windows_failure_propagates_same_error_and_preserves_both_files(tmp_path, monkeypatch):
    source, destination = tmp_path / "candidate.toml", tmp_path / "config.toml"
    source.write_bytes(b"candidate\r\n"); destination.write_bytes(b"original\r\n")
    errors, delays = [], []
    def publish(_source, _destination):
        error = windows_error(32); errors.append(error); raise error
    monkeypatch.setattr(Path, "replace", publish)
    monkeypatch.setattr(config_publication.time, "sleep", delays.append)
    with pytest.raises(PermissionError) as failure:
        config_publication.publish_file(source, destination)
    assert len(errors) == 5 and failure.value is errors[-1]
    assert delays == [0.01, 0.02, 0.03, 0.04] and sum(delays) == 0.1
    assert source.read_bytes() == b"candidate\r\n" and destination.read_bytes() == b"original\r\n"


@pytest.mark.parametrize("error", [PermissionError("sem winerror"), windows_error(13), OSError("falha diferente")])
def test_other_filesystem_errors_propagate_immediately(tmp_path, monkeypatch, error):
    source, destination = tmp_path / "candidate", tmp_path / "current"
    source.write_bytes(b"new"); destination.write_bytes(b"old")
    calls = []
    def publish(*args):
        calls.append(args); raise error
    monkeypatch.setattr(Path, "replace", publish)
    monkeypatch.setattr(config_publication.time, "sleep", lambda _delay: pytest.fail("erro diferente não deve aguardar"))
    with pytest.raises(type(error)) as failure:
        config_publication.publish_file(source, destination)
    assert failure.value is error and calls == [(source, destination)]
    assert source.read_bytes() == b"new" and destination.read_bytes() == b"old"


@pytest.mark.parametrize("publisher", ["json", "migration"])
def test_configuration_publishers_retry_then_clean_staging_without_changing_backup(tmp_path, monkeypatch, publisher):
    from onmyoji_daemon.telegram import json_file, write_json
    from onmyoji_daemon.telegram_persistence import protect_legacy_gateway

    destination = tmp_path / "configs" / ("contacts.json" if publisher == "json" else "config.toml")
    destination.parent.mkdir()
    original = ('{"nome":"configuração anterior"}\r\n' if publisher == "json" else 'schema_version=1\r\n[telegram]\r\nnome="configuração anterior"\r\n').encode("utf-8")
    destination.write_bytes(original)
    replace = Path.replace
    calls = []
    def publish(source, target):
        calls.append((source, target))
        assert source.parent == tmp_path / "tmp"
        if len(calls) == 1:
            assert destination.read_bytes() == original
            raise windows_error(5)
        return replace(source, target)
    monkeypatch.setattr(Path, "replace", publish)
    monkeypatch.setattr(config_publication.time, "sleep", lambda _delay: None)
    if publisher == "json":
        write_json(destination, {"nome": "configuração nova"})
        assert json_file(destination, {}) == {"nome": "configuração nova"}
    else:
        protect_legacy_gateway(tmp_path, destination)
        assert destination.read_bytes() == original.replace(b"schema_version=1", b"schema_version=2", 1)
        assert destination.with_name("config-before-delegation-v1.toml").read_bytes() == original
    assert len(calls) == 2 and list((tmp_path / "tmp").iterdir()) == []
    assert not destination.read_bytes().startswith(b"\xef\xbb\xbf")
