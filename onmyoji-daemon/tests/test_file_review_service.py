"""Contratos de revisão humana, preservação dos arquivos e recuperação de aplicação."""
from __future__ import annotations

import codecs
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from onmyoji_daemon.file_review import FileReviewError, FileReviewService, build_chunks
from onmyoji_daemon.file_review.service import MAX_FILE_BYTES


@pytest.fixture
def service(tmp_path):
    root, workspace = tmp_path / "base", tmp_path / "workspace"
    root.mkdir()
    workspace.mkdir()
    return FileReviewService(root, workspace, root / "configs" / "reviews")


def pair(service, original="first\nkeep\nlast\n", proposed="FIRST\nkeep\nLAST\n"):
    project = service.root / "project"
    project.mkdir(exist_ok=True)
    first, second = project / "guide.md", project / "guide-2026-11-08.proposed.md"
    first.write_bytes(original.encode("utf-8"))
    second.write_bytes(proposed.encode("utf-8"))
    return first, second


def accept_all(service, view):
    for chunk in view["chunks"]:
        view = service.decide(view["review_id"], view["revision"], chunk["id"], "accepted")
    return view


def test_snapshots_and_index_separate_operational_state_from_document_content(service):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    assert view["result"] == view["original"] == "first\nkeep\nlast\n"
    assert view["chunks"][0]["before"] == ["first\n"]
    assert view["chunks"][0]["original_start"] == 1
    assert view["chunks"][1]["original_start"] == 3
    assert view["revision"] == 0
    assert view["csrf_token"]
    status = service.status(view["review_id"])
    assert not {"original", "proposed", "result", "csrf_token"} & status.keys()
    registry = json.loads((service.state_dir / (view["review_id"] + ".json")).read_text())
    assert set(registry) == {"directory"}
    assert Path(registry["directory"]).is_relative_to(service.workspace)
    service.bind_publication(view["review_id"], "app-1")
    assert service.status(view["review_id"])["app_id"] == "app-1"
    assert original.read_bytes() == b"first\nkeep\nlast\n"
    assert proposed.is_file()


@pytest.mark.parametrize(("original_text", "proposed_text"), [
    ("", "insert\n"), ("remove\n", ""),
    ("first\nend\n", "first\ninsert\nend\n"),
    ("first\nend", "first\nend\ninsert"),
    ("a\nkeep\nb\n", "A\nkeep\nB\n"),
])
def test_full_approval_merges_insert_delete_replace_and_end_of_file(service, original_text, proposed_text):
    original, proposed = pair(service, original_text, proposed_text)
    view = accept_all(service, service.create(str(original), str(proposed)))
    assert view["result"] == proposed_text
    receipt = service.apply(view["review_id"], view["revision"], view["result_hash"])
    assert receipt["approval"] == "full"
    assert receipt["proposed_removed"]
    assert original.read_bytes() == proposed_text.encode("utf-8")
    assert not proposed.exists()
    assert Path(receipt["backup_path"]).read_bytes() == original_text.encode("utf-8")
    assert service.apply(view["review_id"], view["revision"], view["result_hash"]) == receipt
    assert service.status(view["review_id"])["receipt"] == receipt


def test_partial_approval_preserves_remaining_proposal_and_manual_edits(service):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    view = service.save(view["review_id"], view["revision"], view["result"].replace("keep", "my edit"))
    view = service.decide(view["review_id"], view["revision"], "chunk-1", "accepted")
    view = service.decide(view["review_id"], view["revision"], "chunk-0", "rejected")
    receipt = service.apply(view["review_id"], view["revision"], view["result_hash"])
    assert receipt["approval"] == "partial"
    assert not receipt["proposed_removed"]
    assert proposed.exists()
    assert original.read_bytes() == b"first\nmy edit\nLAST\n"


def test_explicit_discard_removes_remaining_proposal(service):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    for chunk in view["chunks"]:
        view = service.decide(view["review_id"], view["revision"], chunk["id"], "rejected")
    receipt = service.apply(view["review_id"], view["revision"], view["result_hash"], True)
    assert receipt["approval"] == "partial"
    assert receipt["proposed_removed"]
    assert not proposed.exists()


@pytest.mark.parametrize(("encoding", "bom", "newline"), [
    ("utf-8", b"", "\n"), ("utf-8", codecs.BOM_UTF8, "\r\n"),
    ("utf-16-le", codecs.BOM_UTF16_LE, "\r\n"),
    ("utf-16-be", codecs.BOM_UTF16_BE, "\r"),
    ("utf-32-le", codecs.BOM_UTF32_LE, "\n"),
])
def test_applied_result_preserves_original_encoding_bom_and_line_endings(service, encoding, bom, newline):
    original, proposed = pair(service, "ação\n", "nova ação\n")
    original.write_bytes(bom + ("ação" + newline).encode(encoding))
    view = accept_all(service, service.create(str(original), str(proposed)))
    service.apply(view["review_id"], view["revision"], view["result_hash"])
    assert original.read_bytes() == bom + ("nova ação" + newline).encode(encoding)


@pytest.mark.parametrize(("content", "code"), [
    (b"first\r\nlast\n", "mixed_newlines"), (b"a\x00b", "binary_file"),
    (b"caf\xe9", "unsupported_encoding"), (b"a" * (MAX_FILE_BYTES + 1), "file_too_large"),
], ids=["mixed-newlines", "binary", "legacy-encoding", "too-large"])
def test_invalid_documents_are_rejected_without_creating_review(service, content, code):
    original, proposed = pair(service)
    original.write_bytes(content)
    with pytest.raises(FileReviewError) as caught:
        service.create(str(original), str(proposed))
    assert caught.value.code == code
    assert not list(service.state_dir.glob("*.json"))


@pytest.mark.parametrize(("original_path", "proposed_path"), [(None, None), ("guide.md", "guide.proposed.md"), ("", "")])
def test_missing_and_relative_paths_are_rejected(service, original_path, proposed_path):
    with pytest.raises(FileReviewError) as caught:
        service.create(original_path, proposed_path)
    assert caught.value.code == "invalid_path"


def test_proposal_must_be_beside_original_and_have_proposed_suffix(service):
    original, proposed = pair(service)
    other = service.workspace / "copy.proposed.md"
    other.write_bytes(b"other")
    for invalid in (other, original):
        with pytest.raises(FileReviewError) as caught:
            service.create(str(original), str(invalid))
        assert caught.value.code == "invalid_path"


def test_symlink_and_hardlink_cannot_disguise_same_file(service):
    original, proposed = pair(service)
    proposed.unlink()
    try:
        proposed.symlink_to(original)
    except OSError:
        pytest.skip("Criação de links simbólicos indisponível nesta máquina.")
    with pytest.raises(FileReviewError) as caught:
        service.create(str(original), str(proposed))
    assert caught.value.code == "unsafe_path"


def test_hardlink_cannot_disguise_same_file(service):
    original, proposed = pair(service)
    proposed.unlink()
    os.link(original, proposed)
    with pytest.raises(FileReviewError) as caught:
        service.create(str(original), str(proposed))
    assert caught.value.code == "invalid_path"


def test_revision_guard_prevents_stale_tabs_and_save_is_restored_between_instances(service):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    replacement = FileReviewService(service.root, service.workspace, service.state_dir)
    changed = service.save(view["review_id"], 0, "manual\n")
    assert replacement.view(view["review_id"])["result"] == "manual\n"
    assert replacement.lock is service.lock
    for revision in (0, True, None):
        with pytest.raises(FileReviewError) as caught:
            replacement.save(view["review_id"], revision, "stale\n")
        assert caught.value.code == "revision_conflict"
    assert service.save(view["review_id"], changed["revision"], "manual\n")["revision"] == changed["revision"]


def test_decision_does_not_overwrite_manual_edit_in_same_block(service):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    view = service.save(view["review_id"], 0, view["result"].replace("first", "custom"))
    with pytest.raises(FileReviewError) as caught:
        service.decide(view["review_id"], view["revision"], "chunk-0", "accepted")
    assert caught.value.code == "chunk_conflict"
    view = service.decide(view["review_id"], view["revision"], "chunk-0", "rejected")
    assert view["result"].startswith("custom")
    assert view["chunks"][0]["decision"] == "rejected"


def test_optimistic_guard_pending_decisions_and_exact_preview_hash(service):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    with pytest.raises(FileReviewError) as caught:
        service.apply(view["review_id"], 0, view["result_hash"])
    assert caught.value.code == "pending_decisions"
    view = accept_all(service, view)
    with pytest.raises(FileReviewError) as caught:
        service.apply(view["review_id"], view["revision"], "wrong-hash")
    assert caught.value.code == "result_conflict"
    with pytest.raises(FileReviewError) as caught:
        service.apply(view["review_id"], view["revision"], view["result_hash"], "yes")
    assert caught.value.code == "invalid_discard"
    assert original.read_bytes() == b"first\nkeep\nlast\n"


@pytest.mark.parametrize("changed_file", ["original", "proposed"])
def test_external_source_changes_close_review_without_overwriting(service, changed_file):
    original, proposed = pair(service)
    view = accept_all(service, service.create(str(original), str(proposed)))
    changed = original if changed_file == "original" else proposed
    changed.write_bytes(b"external\n")
    with pytest.raises(FileReviewError) as caught:
        service.apply(view["review_id"], view["revision"], view["result_hash"])
    assert caught.value.code == "source_conflict"
    assert service.status(view["review_id"])["status"] == "conflict"
    assert changed.read_bytes() == b"external\n"


def test_replaced_source_identity_conflicts_even_when_contents_are_equal(service):
    original, proposed = pair(service)
    view = accept_all(service, service.create(str(original), str(proposed)))
    previous = original.with_suffix(".saved")
    original.rename(previous)
    original.write_bytes(previous.read_bytes())
    with pytest.raises(FileReviewError) as caught:
        service.apply(view["review_id"], view["revision"], view["result_hash"])
    assert caught.value.code == "source_conflict"


def test_cancel_keeps_sources_and_disallows_later_edits(service):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    assert service.cancel(view["review_id"])["status"] == "cancelled"
    assert service.cancel(view["review_id"])["status"] == "cancelled"
    with pytest.raises(FileReviewError) as caught:
        service.save(view["review_id"], 0, "changed")
    assert caught.value.code == "review_closed"
    assert original.read_bytes() == b"first\nkeep\nlast\n"
    assert proposed.is_file()


def test_snapshot_tampering_and_invalid_identifiers_are_rejected(service):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    for invalid in (None, "../escape", "a" * 32):
        with pytest.raises(FileReviewError) as caught:
            service.view(invalid)
        assert caught.value.code == "review_not_found"
    (service.reviews_dir / view["review_id"] / "original.bin").write_bytes(b"tampered")
    with pytest.raises(FileReviewError) as caught:
        service.view(view["review_id"])
    assert caught.value.code == "review_corrupt"


def test_cleanup_failure_reports_success_and_preserves_proposal(service):
    original, proposed = pair(service)
    view = accept_all(service, service.create(str(original), str(proposed)))
    actual_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == proposed:
            raise PermissionError("busy")
        return actual_unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", unlink):
        receipt = service.apply(view["review_id"], view["revision"], view["result_hash"])
    assert receipt["status"] == "applied"
    assert receipt["cleanup_warning"]
    assert not receipt["proposed_removed"]
    assert original.read_bytes() == b"FIRST\nkeep\nLAST\n"
    assert proposed.exists()


def test_failed_target_write_preserves_original_and_reopens_draft(service):
    original, proposed = pair(service)
    view = accept_all(service, service.create(str(original), str(proposed)))
    actual_write = service._atomic_write

    def write(target, content, mode=None):
        if target == original:
            raise PermissionError("busy")
        actual_write(target, content, mode)

    with patch.object(service, "_atomic_write", write):
        with pytest.raises(FileReviewError) as caught:
            service.apply(view["review_id"], view["revision"], view["result_hash"])
    assert caught.value.code == "apply_failed"
    assert original.read_bytes() == b"first\nkeep\nlast\n"
    assert service.status(view["review_id"])["status"] == "editing"


def test_journal_recovers_successful_replace_when_receipt_write_was_interrupted(service):
    original, proposed = pair(service)
    view = accept_all(service, service.create(str(original), str(proposed)))
    actual_persist = service._persist

    def persist(directory, data):
        if data["status"] == "applied":
            raise OSError("simulated crash")
        actual_persist(directory, data)

    with patch.object(service, "_persist", persist):
        with pytest.raises(OSError):
            service.apply(view["review_id"], view["revision"], view["result_hash"])
    assert original.read_bytes() == b"FIRST\nkeep\nLAST\n"
    replacement = FileReviewService(service.root, service.workspace, service.state_dir)
    status = replacement.status(view["review_id"])
    assert status["status"] == "applied"
    assert status["receipt"]["proposed_removed"]
    assert replacement.apply(view["review_id"], view["revision"], view["result_hash"])["status"] == "applied"


def test_no_diff_has_no_pending_decisions(service):
    original, proposed = pair(service, "same", "same")
    view = service.create(str(original), str(proposed))
    assert view["chunks"] == []
    assert view["pending"] == 0
    assert build_chunks("same", "same") == []


@pytest.mark.parametrize("decision", [None, [], {}, True, "pending"])
def test_invalid_decision_returns_domain_error(service, decision):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    with pytest.raises(FileReviewError) as caught:
        service.decide(view["review_id"], 0, "chunk-0", decision)
    assert caught.value.code == "invalid_decision"


@pytest.mark.parametrize("result_hash", [None, {}, [], "ação"])
def test_invalid_result_hash_returns_domain_error(service, result_hash):
    original, proposed = pair(service)
    view = accept_all(service, service.create(str(original), str(proposed)))
    with pytest.raises(FileReviewError) as caught:
        service.apply(view["review_id"], view["revision"], result_hash)
    assert caught.value.code == "result_conflict"


def test_resolved_decision_is_idempotent_and_cannot_silently_change_meaning(service):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    view = service.decide(view["review_id"], 0, "chunk-0", "accepted")
    assert service.decide(view["review_id"], view["revision"], "chunk-0", "accepted")["revision"] == view["revision"]
    with pytest.raises(FileReviewError) as caught:
        service.decide(view["review_id"], view["revision"], "chunk-0", "rejected")
    assert caught.value.code == "decision_closed"


def test_failed_write_of_unchanged_result_does_not_report_success(service):
    original, proposed = pair(service)
    view = service.create(str(original), str(proposed))
    for chunk in view["chunks"]:
        view = service.decide(view["review_id"], view["revision"], chunk["id"], "rejected")
    actual_write = service._atomic_write

    def write(target, content, mode=None):
        if target == original:
            raise PermissionError("busy")
        actual_write(target, content, mode)

    with patch.object(service, "_atomic_write", write):
        with pytest.raises(FileReviewError) as caught:
            service.apply(view["review_id"], view["revision"], view["result_hash"], True)
    assert caught.value.code == "apply_failed"
    assert service.status(view["review_id"])["status"] == "editing"
    assert proposed.exists()
