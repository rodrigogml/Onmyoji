"""Contratos de habilitação conservadora dos gates do runtime Codex."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import stat
from types import SimpleNamespace

import pytest

from onmyoji_daemon.codex_capabilities import ADAPTER_REVISION, CapabilitySet, REPORT_RELATIVE, executable_digest, load_profile_capability


def certified_report(tmp_path: Path) -> tuple[Path, Path, dict]:
    binary = tmp_path / "fake-codex.exe"
    binary.write_bytes(b"codex runtime fixture")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    report = {
        "schema_version": 1,
        "runtime": {"executable_sha256": executable_digest(str(binary)), "schema_sha256": "a" * 64, "transport": "stdio", "platform": platform.system(), "adapter_revision": ADAPTER_REVISION},
        "gates": {f"G{i}": {"status": "supported", "evidence_kind": "runtime", "checks": ["local_runtime_probe"], "reason": "supported"} for i in range(8)},
    }
    report["gates"]["G1"]["input_kinds"] = ["text", "photo", "document"]
    report["gates"]["G4"].update(discovery="workspace_codex_agents", snapshot="startup", clients=["native", "console", "app_server"], workspace_kinds=["git", "plain"], profile_fields=["name", "description", "developer_instructions"], available_models=["validated-model"], profile_reasoning_efforts=["low"])
    return binary, workspace, report


def save_report(root: Path, report: dict | list) -> None:
    target = root / REPORT_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report), encoding="utf-8")


def test_absent_certification_is_not_inferred_from_binary_or_version(tmp_path):
    binary, workspace, _ = certified_report(tmp_path)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), workspace)
    assert capabilities.reason("steering") == "report_missing"
    assert not capabilities.supports("steering")
    assert not capabilities.input_supported("text")
    assert capabilities.reason("invented") == "unknown_capability"
    assert capabilities.evidence("invented") == {}
    assert not load_profile_capability(tmp_path, str(binary), workspace)["supported"]


def test_certified_runtime_enables_only_evidenced_matrix(tmp_path):
    binary, workspace, report = certified_report(tmp_path)
    save_report(tmp_path, report)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), workspace)
    for feature in ("base", "steering", "input_reconciliation", "subagents", "agent_profiles", "post_turn", "process_resume", "tool_results"):
        assert capabilities.supports(feature), feature
    assert capabilities.input_supported("photo")
    assert not capabilities.input_supported("voice")
    assert capabilities.evidence("steering")["G1"]["evidence_kind"] == "runtime"
    profile = load_profile_capability(tmp_path, str(binary), workspace)
    assert profile["snapshot"] == "startup"
    assert profile["clients"] == ["native", "console", "app_server"]
    assert profile["available_models"] == ["validated-model"]


@pytest.mark.parametrize(("field", "value", "reason"), [("executable_sha256", "b" * 64, "executable_changed"), ("schema_sha256", "invalid", "schema_identity_missing"), ("transport", "websocket", "transport_changed"), ("platform", "unknown", "platform_changed"), ("adapter_revision", -1, "adapter_changed")])
def test_fingerprint_change_invalidates_all_capabilities(tmp_path, field, value, reason):
    binary, workspace, report = certified_report(tmp_path)
    report["runtime"][field] = value
    save_report(tmp_path, report)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), workspace)
    assert not capabilities.supports("base")
    assert capabilities.reason("base") == reason


@pytest.mark.parametrize("document", [None, [], {}, {"schema_version": 99}, {"schema_version": 1, "runtime": {}, "gates": []}])
def test_invalid_report_never_enables_runtime(tmp_path, document):
    binary, workspace, _ = certified_report(tmp_path)
    save_report(tmp_path, document)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), workspace)
    assert capabilities.reason("base") == "report_invalid"


def test_private_revocation_overrides_packaged_certification(tmp_path, monkeypatch):
    binary, workspace, report = certified_report(tmp_path)
    module = __import__("onmyoji_daemon.codex_capabilities", fromlist=["__file__"])
    fake_module = tmp_path / "component" / "src" / "package" / "codex_capabilities.py"
    fake_module.parent.mkdir(parents=True)
    monkeypatch.setattr(module, "__file__", str(fake_module))
    packaged = fake_module.parents[2] / "resources" / "codex-capabilities" / (executable_digest(str(binary)) + ".json")
    packaged.parent.mkdir(parents=True)
    packaged.write_text(json.dumps(report), encoding="utf-8")
    assert CapabilitySet.from_instance(tmp_path, str(binary), workspace).supports("steering")
    report["gates"]["G1"] = {"status": "unsupported", "reason": "operator_revoked"}
    save_report(tmp_path, report)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), workspace)
    assert not capabilities.supports("steering")
    assert capabilities.reason("steering") == "operator_revoked"


@pytest.mark.parametrize("evidence", [{"status": "supported", "evidence_kind": "schema", "checks": ["field_present"]}, {"status": "supported", "evidence_kind": "runtime", "checks": []}, {"status": "supported", "evidence_kind": "runtime", "checks": ["secret content with spaces"]}, {"status": "supported", "evidence_kind": "runtime", "checks": None}])
def test_schema_or_incomplete_evidence_is_not_runtime_proof(tmp_path, evidence):
    binary, workspace, report = certified_report(tmp_path)
    report["gates"]["G1"] = evidence
    save_report(tmp_path, report)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), workspace)
    assert capabilities.reason("steering") == "g1_runtime_evidence_missing"


def test_post_turn_and_process_survival_are_separate_capabilities(tmp_path):
    binary, workspace, report = certified_report(tmp_path)
    report["gates"]["G6"] = {"status": "unsupported", "reason": "process_loss_interrupts_work"}
    save_report(tmp_path, report)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), workspace)
    assert capabilities.supports("post_turn")
    assert not capabilities.supports("process_resume")
    report["gates"]["G7"] = {"status": "unverified"}
    save_report(tmp_path, report)
    assert not CapabilitySet.from_instance(tmp_path, str(binary), workspace).supports("post_turn")


@pytest.mark.parametrize(("field", "value", "reason"), [("discovery", "agents_agents", "profile_snapshot_unverified"), ("snapshot", "dynamic", "profile_snapshot_unverified"), ("clients", ["app_server"], "profile_clients_unverified"), ("clients", None, "profile_clients_unverified"), ("workspace_kinds", ["git"], "profile_workspace_unverified"), ("workspace_kinds", None, "profile_workspace_unverified")])
def test_profile_discovery_requires_clients_workspace_and_snapshot(tmp_path, field, value, reason):
    binary, workspace, report = certified_report(tmp_path)
    report["gates"]["G4"][field] = value
    save_report(tmp_path, report)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), workspace)
    assert capabilities.reason("agent_profiles") == reason
    assert not load_profile_capability(tmp_path, str(binary), workspace)["supported"]


def test_configured_executable_and_git_workspace_are_resolved_from_instance(tmp_path):
    binary, workspace, report = certified_report(tmp_path)
    (workspace / ".git").write_text("gitdir: fixture", encoding="utf-8")
    save_report(tmp_path, report)
    (tmp_path / "configs" / "onmyoji-system.toml").write_text("[codex]\nexecutable=" + json.dumps(str(binary)) + "\nproject_directory=\"workspace\"\n", encoding="utf-8")
    capabilities = CapabilitySet.from_instance(tmp_path)
    assert capabilities.workspace_kind == "git"
    assert capabilities.supports("agent_profiles")


def test_missing_bad_json_oversized_and_bad_config_are_safe(tmp_path):
    assert not CapabilitySet.from_instance(tmp_path).supports("base")
    binary, workspace, _ = certified_report(tmp_path)
    path = tmp_path / REPORT_RELATIVE
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not json")
    assert CapabilitySet.from_instance(tmp_path, str(binary), workspace).reason("base") == "report_invalid"
    path.write_bytes(b" " * (128 * 1024 + 1))
    assert CapabilitySet.from_instance(tmp_path, str(binary), workspace).reason("base") == "report_invalid"
    (tmp_path / "configs" / "onmyoji-system.toml").write_text("[broken", encoding="utf-8")
    assert not CapabilitySet.from_instance(tmp_path).supports("base")


def test_unknown_status_and_reason_do_not_expose_report_content(tmp_path):
    binary, workspace, report = certified_report(tmp_path)
    report["gates"]["G1"] = {"status": "other", "reason": "password=secret value", "evidence_kind": "schema"}
    save_report(tmp_path, report)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), workspace)
    assert capabilities.reason("steering") == "g1_unverified"
    assert capabilities.evidence("steering")["G1"] == {"status": "unverified", "evidence_kind": "unverified", "reason": "not_recorded"}


@pytest.mark.parametrize("executable", [None, "", "   ", "missing-codex-fixture"])
def test_unresolvable_binary_has_no_identity(executable):
    assert executable_digest(executable) == ""


def test_directory_is_not_an_executable_fingerprint(tmp_path):
    assert executable_digest(str(tmp_path)) == ""


def test_report_symlink_is_rejected_without_following_it(tmp_path, monkeypatch):
    binary, workspace, report = certified_report(tmp_path)
    save_report(tmp_path, report)
    report_path = tmp_path / REPORT_RELATIVE
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: True if path == report_path else original(path))
    assert CapabilitySet.from_instance(tmp_path, str(binary), workspace).reason("base") == "report_invalid"


def test_report_reparse_point_is_rejected(tmp_path, monkeypatch):
    binary, workspace, report = certified_report(tmp_path)
    save_report(tmp_path, report)
    report_path = tmp_path / REPORT_RELATIVE
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        metadata = original(path, *args, **kwargs)
        if path == report_path:
            return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        return metadata
    monkeypatch.setattr(Path, "lstat", reparse)
    assert CapabilitySet.from_instance(tmp_path, str(binary), workspace).reason("base") == "report_invalid"


def test_report_metadata_permission_failure_is_a_safe_fallback(tmp_path, monkeypatch):
    binary, workspace, report = certified_report(tmp_path)
    save_report(tmp_path, report)
    report_path = tmp_path / REPORT_RELATIVE
    original = Path.lstat
    def inaccessible(path, *args, **kwargs):
        if path == report_path:
            raise PermissionError("private fixture")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", inaccessible)
    assert CapabilitySet.from_instance(tmp_path, str(binary), workspace).reason("base") == "report_invalid"


def test_non_table_codex_configuration_is_not_executed(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "onmyoji-system.toml").write_text("codex=[]\n", encoding="utf-8")
    assert not CapabilitySet.from_instance(tmp_path).supports("base")


def test_malformed_status_is_sanitized_when_read_as_evidence(tmp_path):
    binary, workspace, report = certified_report(tmp_path)
    report["gates"]["G1"] = {"status": ["secret"], "reason": {"secret": "value"}}
    save_report(tmp_path, report)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), workspace)
    assert capabilities.evidence("steering")["G1"]["status"] == "unverified"
    assert capabilities.reason("steering") == "g1_unverified"
