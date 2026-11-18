from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon import supervisor as supervisor_module
from onmyoji_daemon.rpc import call
from onmyoji_daemon.supervisor import Supervisor, endpoint


def test_rpc_identity_matches_the_lock_and_changes_even_when_the_pid_is_reused(tmp_path, monkeypatch):
    identities = []
    for _attempt in range(2):
        supervisor = Supervisor(tmp_path)
        published = threading.Event()
        failures = []
        publish = supervisor._endpoint

        def ready():
            publish()
            published.set()

        def run():
            try:
                supervisor.run_forever()
            except Exception as error:
                failures.append(error)

        monkeypatch.setattr(supervisor, "_endpoint", ready)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            assert published.wait(2)
            ping = call(*endpoint(tmp_path), "ping", timeout=2)
            identity = json.loads(supervisor.process_file.read_text(encoding="utf-8"))
            assert json.loads(supervisor.lock_file.read_text(encoding="utf-8")) == identity
            assert identity == supervisor.process_identity
            assert ping == {"service": "onmyoji-daemon", "state": "running", **identity}
            assert identity["root"] == str(tmp_path.resolve())
            assert identity["pid"] == supervisor_module.os.getpid()
            assert identity["setup_version"] == supervisor_module.SETUP_VERSION
            assert isinstance(identity["started_at"], float)
            assert len(identity["instance"]) == 32
            assert supervisor.token not in json.dumps(ping)
            assert "token" not in ping
            identities.append(identity)
        finally:
            supervisor.stop_event.set()
            thread.join(3)
        assert not thread.is_alive()
        assert failures == []
        assert not supervisor.lock_file.exists()
        assert not supervisor.process_file.exists()
    assert identities[0]["pid"] == identities[1]["pid"]
    assert identities[0]["instance"] != identities[1]["instance"]


def test_ping_keeps_the_configuration_and_version_captured_at_startup(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path)
    manifest = supervisor.root / "daemon.toml"
    manifest.write_text("initial configuration", encoding="utf-8")
    try:
        supervisor._acquire_lock()
        identity = supervisor.process_identity.copy()
        manifest.write_text("configuration changed after startup", encoding="utf-8")
        monkeypatch.setattr(supervisor_module, "SETUP_VERSION", "future-version")

        assert supervisor.handle("ping", {}) == {"service": "onmyoji-daemon", "state": "running", **identity}
        assert identity["configuration_revision"]
        assert identity["setup_version"] != "future-version"
    finally:
        supervisor.rpc.close()


def test_a_supervisor_without_an_acquired_lock_does_not_publish_a_process_identity(tmp_path):
    supervisor = Supervisor(tmp_path)
    try:
        assert supervisor.process_identity == {}
        assert supervisor.handle("ping", {}) == {"service": "onmyoji-daemon", "state": "running"}
    finally:
        supervisor.rpc.close()


@pytest.mark.parametrize("error", [PermissionError("different account"), None])
def test_a_live_lock_is_preserved_when_the_process_cannot_be_signalled(tmp_path, monkeypatch, error):
    supervisor = Supervisor(tmp_path)
    previous = b'{"pid":42,"instance":"existing"}'
    supervisor.lock_file.write_bytes(previous)
    platform = SimpleNamespace(**vars(supervisor_module.os))
    platform.name = "posix"
    probes = []

    def probe(pid, signal):
        probes.append((pid, signal))
        if error is not None:
            raise error

    platform.kill = probe
    monkeypatch.setattr(supervisor_module, "os", platform)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            supervisor._acquire_lock()
        assert probes == [(42, 0)]
        assert supervisor.lock_file.read_bytes() == previous
        assert not supervisor.process_file.exists()
        assert supervisor.process_identity == {}
    finally:
        supervisor.rpc.close()


@pytest.mark.parametrize("previous", [b'{"pid":42,"instance":"old"}', b'{"pid":"invalid"}', b"invalid json"])
def test_an_orphan_or_invalid_lock_is_replaced_with_a_new_identity(tmp_path, monkeypatch, previous):
    supervisor = Supervisor(tmp_path)
    supervisor.lock_file.write_bytes(previous)
    platform = SimpleNamespace(**vars(supervisor_module.os))
    platform.name = "posix"

    def missing(_pid, _signal):
        raise ProcessLookupError("process exited")

    platform.kill = missing
    monkeypatch.setattr(supervisor_module, "os", platform)
    try:
        supervisor._acquire_lock()
        identity = json.loads(supervisor.lock_file.read_text(encoding="utf-8"))
        assert identity == supervisor.process_identity
        assert identity["instance"] != "old"
        assert identity["pid"] == platform.getpid()
    finally:
        supervisor.rpc.close()
