import json
from pathlib import Path

import pytest

from onmyoji_daemon.state_layout import StateLayoutError, migrate


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
    assert json.loads((tmp_path / "state/onmyoji-state.json").read_text(encoding="utf-8"))["layout_version"] == 1
    assert migrate(tmp_path) == []


def test_migrate_refuses_active_supervisor_and_conflicting_destination(tmp_path: Path):
    lock = tmp_path / "configs/daemon/runtime/supervisor.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("{}", encoding="utf-8")
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
