"""Isolamento de arquivos e publicação transacional de documentos."""
from pathlib import Path
from unittest.mock import patch

import pytest

from onmyoji_daemon.mcp.document_processing.contracts import DocumentError
from onmyoji_daemon.mcp.document_processing.paths import DocumentContext


def context(tmp_path):
    instance = tmp_path / "instance"; instance.mkdir()
    workspace = tmp_path / "workspace"; workspace.mkdir()
    profile = {"readable_roots": [str(workspace)], "writable_roots": [str(workspace)]}
    return DocumentContext(instance, "pdf", "default", profile, {"max_input_bytes": 100}, workspace / "jobs"), workspace


def test_publication_preserves_sources_and_reports_hashes(tmp_path):
    execution, workspace = context(tmp_path)
    source = workspace / "source.pdf"; source.write_bytes(b"source")
    assert execution.input_path(str(source)) == source
    destination = workspace / "nested" / "result.pdf"
    staged = execution.stage_output(str(destination)); staged.write_bytes(b"result")
    assert not destination.exists()

    artifacts = execution.publish()

    assert source.read_bytes() == b"source" and destination.read_bytes() == b"result"
    assert artifacts[0]["path"] == str(destination) and artifacts[0]["bytes"] == 6
    assert len(artifacts[0]["sha256"]) == 64


def test_changed_source_prevents_any_publication(tmp_path):
    execution, workspace = context(tmp_path)
    source = workspace / "source.pdf"; source.write_bytes(b"source")
    execution.input_path(str(source))
    destination = workspace / "result.pdf"
    execution.stage_output(str(destination)).write_bytes(b"result")
    source.write_bytes(b"changed")
    with pytest.raises(DocumentError, match="alterado"):
        execution.publish()
    assert not destination.exists()


def test_input_limits_paths_and_existing_outputs_are_enforced(tmp_path):
    execution, workspace = context(tmp_path)
    source = workspace / "source.pdf"; source.write_bytes(b"x" * 101)
    for path in (str(tmp_path / "outside.pdf"), "relative.pdf", str(source)):
        with pytest.raises(DocumentError):
            execution.input_path(path)
    with pytest.raises(DocumentError, match="já existe"):
        execution.stage_output(str(source))


def test_linked_paths_are_rejected(tmp_path):
    execution, workspace = context(tmp_path)
    source = workspace / "source.pdf"; source.write_bytes(b"source")
    linked = workspace / "linked.pdf"
    try:
        linked.symlink_to(source)
    except OSError:
        pytest.skip("Links indisponíveis nesta plataforma.")
    with pytest.raises(DocumentError, match="links"):
        execution.input_path(str(linked))


def test_concurrent_destination_rolls_back_only_this_execution(tmp_path):
    execution, workspace = context(tmp_path)
    first, second = workspace / "first.pdf", workspace / "second.pdf"
    execution.stage_output(str(first)).write_bytes(b"first")
    execution.stage_output(str(second)).write_bytes(b"second")
    import os
    original = os.link
    def link(source, destination):
        if destination == second:
            second.write_bytes(b"other execution")
        original(source, destination)
    with patch("onmyoji_daemon.mcp.document_processing.paths.os.link", side_effect=link):
        with pytest.raises(DocumentError, match="outra operação"):
            execution.publish()
    assert not first.exists() and second.read_bytes() == b"other execution"
    assert not list(workspace.glob(".onmyoji-*.tmp"))


def test_raw_password_is_not_accepted_as_a_vault_reference(tmp_path):
    execution, _ = context(tmp_path)
    with pytest.raises(DocumentError, match="referência"):
        execution.resolve_password("secret")
