from __future__ import annotations

from pathlib import Path

import pytest

from onmyoji_daemon.mcp.artifacts import ArtifactPathError, authorized_file, default_output
from onmyoji_daemon.mcp.document_processing.configuration import profile_defaults


def system(root: Path, workspace: Path) -> None:
    configs = root / "configs"; configs.mkdir()
    (configs / "onmyoji-system.toml").write_text('[codex]\nproject_directory = "' + workspace.as_posix() + '"\n', encoding="utf-8")


def test_new_document_profile_authorizes_the_work_root(tmp_path):
    workspace = tmp_path.parent / (tmp_path.name + "-work"); workspace.mkdir(); system(tmp_path, workspace)

    profile = profile_defaults(tmp_path, "pdf")

    assert profile["readable_roots"] == [str(workspace.resolve())]
    assert profile["writable_roots"] == [str(workspace.resolve())]
    assert profile["work_directory"] == str(workspace / ".onmyoji" / "pdf" / "work")


def test_authorized_file_reuses_source_without_copying(tmp_path):
    source = tmp_path / "mail" / "attachment.pdf"; source.parent.mkdir(); source.write_bytes(b"pdf")

    resolved = authorized_file(str(source), [str(source.parent)], 10)

    assert resolved == source.resolve() and source.read_bytes() == b"pdf"


def test_artifact_paths_reject_files_outside_declared_root_and_default_outputs_use_work(tmp_path):
    workspace = tmp_path / "work"; workspace.mkdir(); system(tmp_path, workspace)
    source = tmp_path / "outside.pdf"; source.write_bytes(b"pdf")

    with pytest.raises(ArtifactPathError, match="fora das raízes"):
        authorized_file(str(source), [str(workspace)])
    assert default_output(tmp_path, "email", "message.eml") == workspace / ".onmyoji" / "email" / "output" / "message.eml"
