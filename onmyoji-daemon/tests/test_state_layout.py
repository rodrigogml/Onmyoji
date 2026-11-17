import json
import os
from pathlib import Path

import pytest

from onmyoji_daemon.state_layout import StateLayoutError, cloudflare_tunnel_state_path, migrate


def test_migrate_moves_legacy_operational_state_without_leaving_legacy_files(tmp_path: Path):
    legacy = tmp_path / "configs/daemon/services/telegram/state/gateway.sqlite3"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"sqlite")
    runtime = tmp_path / "configs/daemon/runtime/endpoint.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("{}", encoding="utf-8")
    skills = tmp_path / "configs/shikigami/skills-runtime.json"
    skills.parent.mkdir(parents=True)
    skills.write_text("{}", encoding="utf-8")

    moved = migrate(tmp_path)

    assert str(Path("configs/daemon/services/telegram/state")) in moved
    assert not legacy.exists() and not runtime.exists() and not skills.exists()
    assert (tmp_path / "state/daemon/services/telegram/gateway.sqlite3").read_bytes() == b"sqlite"
    assert (tmp_path / "state/daemon/runtime/endpoint.json").read_text(encoding="utf-8") == "{}"
    assert json.loads((tmp_path / "state/onmyoji-state.json").read_text(encoding="utf-8"))["layout_version"] == 2
    assert migrate(tmp_path) == []


def test_migrate_refuses_active_supervisor_and_conflicting_destination(tmp_path: Path):
    lock = tmp_path / "configs/daemon/runtime/supervisor.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "root": str(tmp_path.resolve())}), encoding="utf-8")
    with pytest.raises(StateLayoutError, match="Pare o daemon"):
        migrate(tmp_path)

    lock.unlink()
    source = tmp_path / "configs/shikigami/skills-runtime.json"
    source.parent.mkdir(parents=True)
    source.write_text("old", encoding="utf-8")
    destination = tmp_path / "state/shikigami/skills-runtime.json"
    destination.parent.mkdir(parents=True)
    destination.write_text("new", encoding="utf-8")
    with pytest.raises(StateLayoutError, match="não pode escolher"):
        migrate(tmp_path)


def test_migrate_accepts_orphaned_lock(tmp_path: Path):
    lock = tmp_path / "configs/daemon/runtime/supervisor.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("incompleto", encoding="utf-8")

    migrate(tmp_path)

    assert (tmp_path / "state/daemon/runtime/supervisor.lock").exists()


@pytest.mark.parametrize("previous_version", [0, 1])
def test_migrate_preserves_tunnel_operations_and_configuration(tmp_path: Path, previous_version: int):
    source = tmp_path / "configs/daemon/services/cloudflare-tunnel/operations.json"
    source.parent.mkdir(parents=True)
    history = b'{"schema_version":1,"operations":{"old":{"state":"running"}}}\r\n'
    source.write_bytes(history)
    configuration = source.with_name("tunnel.toml")
    settings = b'schema_version = 1\r\nhostname = "apps.example.com"\r\n'
    configuration.write_bytes(settings)
    marker = tmp_path / "state/onmyoji-state.json"
    if previous_version:
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"layout_version": previous_version}), encoding="utf-8")

    moved = migrate(tmp_path)

    destination = cloudflare_tunnel_state_path(tmp_path) / "operations.json"
    assert moved == [str(source.relative_to(tmp_path))]
    assert destination == tmp_path / "state/daemon/services/cloudflare-tunnel/operations.json"
    assert destination.read_bytes() == history
    assert not source.exists()
    assert configuration.read_bytes() == settings
    assert json.loads(marker.read_text(encoding="utf-8"))["layout_version"] == 2
    assert migrate(tmp_path) == []
    assert destination.read_bytes() == history


def test_migrate_version_one_does_not_repeat_legacy_moves(tmp_path: Path):
    marker = tmp_path / "state/onmyoji-state.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"layout_version": 1}', encoding="utf-8")
    legacy = tmp_path / "configs/shikigami/skills-runtime.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    current = tmp_path / "state/shikigami/skills-runtime.json"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"current")

    assert migrate(tmp_path) == []

    assert legacy.read_bytes() == b"legacy"
    assert current.read_bytes() == b"current"
    assert json.loads(marker.read_text(encoding="utf-8"))["layout_version"] == 2


def test_migrate_tunnel_operations_refuses_conflicting_destination(tmp_path: Path):
    marker = tmp_path / "state/onmyoji-state.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"layout_version": 1}', encoding="utf-8")
    source = tmp_path / "configs/daemon/services/cloudflare-tunnel/operations.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"old history")
    destination = cloudflare_tunnel_state_path(tmp_path) / "operations.json"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"current history")

    with pytest.raises(StateLayoutError, match="não pode escolher"):
        migrate(tmp_path)

    assert source.read_bytes() == b"old history"
    assert destination.read_bytes() == b"current history"
    assert json.loads(marker.read_text(encoding="utf-8"))["layout_version"] == 1


def test_migrate_tunnel_operations_refuses_active_version_one_supervisor(tmp_path: Path):
    marker = tmp_path / "state/onmyoji-state.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"layout_version": 1}', encoding="utf-8")
    source = tmp_path / "configs/daemon/services/cloudflare-tunnel/operations.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"history")
    lock = tmp_path / "state/daemon/runtime/supervisor.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "root": str(tmp_path.resolve())}), encoding="utf-8")

    with pytest.raises(StateLayoutError, match="Pare o daemon"):
        migrate(tmp_path)

    assert source.read_bytes() == b"history"
    assert not (cloudflare_tunnel_state_path(tmp_path) / "operations.json").exists()
    assert json.loads(marker.read_text(encoding="utf-8"))["layout_version"] == 1
