"""Contratos de posse, compatibilidade e recuperação dos perfis da instância."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil

import pytest

from onmyoji_daemon import shikigami_agents as agents
from onmyoji_daemon.shikigami_agents import ShikigamiAgentMaterializationError as Error, ShikigamiAgentMaterializer as Materializer


@pytest.fixture
def instance(tmp_path: Path) -> tuple[Path, Path]:
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir(); workspace.mkdir()
    return root, workspace


@pytest.fixture
def capability() -> dict[str, object]:
    return {"supported": True, "discovery": "workspace_codex_agents", "snapshot": "startup", "clients": ["native", "console", "app_server"]}


def source(root: Path, identifier: str = "reviewer", instructions: str = "Confira os contratos.", extra: str = "") -> Path:
    path = root / "shikigami-agents" / (identifier + ".toml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'name = "{identifier}"\ndescription = "Revisão específica."\ndeveloper_instructions = "{instructions}"\n{extra}', encoding="utf-8")
    return path


def configure(root: Path, enabled: bool = True, identifier: str = "reviewer") -> None:
    path = root / "configs" / "shikigami" / "agents.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'schema_version = 1\n[agents.{identifier}]\nenabled = {str(enabled).lower()}\nmode = "managed_copy"\nsource = "{identifier}.toml"\n', encoding="utf-8")


def test_absent_and_disabled_profiles_do_not_create_runtime_files(instance) -> None:
    root, workspace = instance
    materializer = Materializer(root)
    assert materializer.discover() == {} and materializer.configuration() == {}
    assert materializer.synchronize(workspace) is False
    configure(root, False)
    assert materializer.synchronize(workspace) is False
    assert not (workspace / ".codex").exists() and not (workspace / ".onmyoji").exists()
    assert not materializer.runtime_state_path.exists()


def test_copy_repair_update_disable_preserves_local_profiles(instance, capability) -> None:
    root, workspace = instance
    canonical = source(root); configure(root)
    materializer = Materializer(root)
    assert materializer.synchronize(workspace, capability=capability) is True
    target = materializer.target(workspace, "reviewer")
    assert target.read_bytes() == canonical.read_bytes()
    local = target.with_name("local.toml"); local.write_text("local", encoding="utf-8")
    assert materializer.synchronize(workspace, capability=capability) is False
    target.write_text("edição da cópia", encoding="utf-8")
    assert materializer.synchronize(workspace, capability=capability) is True
    source(root, instructions="Verifique o contexto novo.")
    assert materializer.synchronize(workspace, capability=capability) is True
    assert target.read_bytes() == canonical.read_bytes()
    configure(root, False)
    assert materializer.synchronize(workspace, capability=capability) is True
    assert not target.exists() and local.read_text(encoding="utf-8") == "local"
    assert materializer.synchronize(workspace) is False


@pytest.mark.parametrize("capability", [None, {}, {"supported": False}, {"supported": True, "clients": None}, {"supported": True, "discovery": "workspace_codex_agents", "snapshot": "managed_closed", "clients": ["native", "console", "app_server"]}, {"supported": True, "discovery": "workspace_codex_agents", "snapshot": "startup", "clients": ["app_server"]}])
def test_enabled_profile_requires_runtime_proof(instance, capability) -> None:
    root, workspace = instance
    source(root); configure(root)
    with pytest.raises(Error, match="G4"):
        Materializer(root).synchronize(workspace, capability=capability)
    assert not (workspace / ".codex").exists()


def test_setup_enabling_is_separate_from_runtime_publication(instance) -> None:
    root, workspace = instance
    source(root)
    materializer = Materializer(root)
    materializer.set_enabled("reviewer", True)
    assert materializer.configuration()["reviewer"]["enabled"] is True
    materializer.set_enabled("reviewer", False)
    assert materializer.configuration()["reviewer"]["enabled"] is False
    assert not (workspace / ".codex").exists()
    assert not materializer.agents_state_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not list(materializer.agents_state_path.parent.glob("*.tmp"))
    with pytest.raises(Error, match="booleana"):
        materializer.set_enabled("reviewer", None)
    with pytest.raises(Error, match="não existe"):
        materializer.set_enabled("missing", True)


@pytest.mark.parametrize("identifier", ["default", "worker", "explorer", "con", "com1", "lpt9", "../reviewer", "C:/reviewer", "Reviewer", "_reviewer", "a" * 65, "", None])
def test_invalid_reserved_or_ambiguous_identifiers_are_rejected(instance, identifier) -> None:
    root, workspace = instance
    with pytest.raises(Error, match="Identificador"):
        Materializer(root).set_enabled(identifier, False)
    with pytest.raises(Error, match="Identificador"):
        Materializer.target(workspace, identifier)


@pytest.mark.parametrize("content", [b"", b"[", b"\xff", b"\xef\xbb\xbf", b"x" * (agents.MAX_PROFILE_BYTES + 1), b'name="other"\ndescription="x"\ndeveloper_instructions="x"', b'name="reviewer"\ndescription=""\ndeveloper_instructions="x"', b'name="reviewer"\ndescription="x"', b'name="reviewer"\ndescription=1\ndeveloper_instructions="x"', b'name="reviewer"\ndescription="x"\ndeveloper_instructions="x"\nsandbox_mode="read-only"', b'name="reviewer"\ndescription="x"\ndeveloper_instructions="x"\nmodel=4'], ids=lambda value: f"bytes-{len(value)}")
def test_profile_content_contract_is_validated(instance, content) -> None:
    root, _workspace = instance
    path = source(root); path.write_bytes(content)
    with pytest.raises(Error):
        Materializer(root).discover()


@pytest.mark.parametrize("extra", ['[features]\nshell_tool=true', '[features]\nunknown=false', 'features=false', '[skills]\ninclude_instructions=true', '[skills]\nunknown=false', '[skills.bundled]\nenabled=true', '[skills.bundled]\nunknown=false', 'skills=[]', '[[skills.config]]\npath="x"\nenabled=true', '[[skills.config]]\npath=""\nenabled=false', '[skills]\nconfig="x"'])
def test_profiles_can_only_reduce_supported_capabilities(instance, extra) -> None:
    root, _workspace = instance
    source(root, extra=extra)
    with pytest.raises(Error):
        Materializer(root).discover()


def test_optional_fields_need_certificate_and_model_availability(instance, capability) -> None:
    root, workspace = instance
    source(root, extra='model="gpt-fixture"\nmodel_reasoning_effort="high"\n[features]\nshell_tool=false\n[skills]\ninclude_instructions=false\n[skills.bundled]\nenabled=false\n[[skills.config]]\npath="/fixture/skill"\nenabled=false\n')
    configure(root)
    materializer = Materializer(root)
    with pytest.raises(Error, match="opções ainda"):
        materializer.synchronize(workspace, capability=capability)
    capability["profile_fields"] = ["name", "description", "developer_instructions", "model", "model_reasoning_effort", "features", "skills"]
    with pytest.raises(Error, match="modelo"):
        materializer.synchronize(workspace, capability=capability)
    capability["available_models"] = ["gpt-fixture"]
    with pytest.raises(Error, match="esforço"):
        materializer.synchronize(workspace, capability=capability)
    capability["profile_reasoning_efforts"] = ["high"]
    assert materializer.synchronize(workspace, capability=capability)


@pytest.mark.parametrize("field,values", [("model_reasoning_summary", "profile_reasoning_summaries"), ("model_verbosity", "profile_verbosities")])
def test_summary_and_verbosity_values_require_separate_runtime_evidence(instance, capability, field, values) -> None:
    root, workspace = instance
    source(root, extra=f'{field}="fixture-value"\n'); configure(root)
    capability["profile_fields"] = ["name", "description", "developer_instructions", field]
    materializer = Materializer(root)
    with pytest.raises(Error, match="resumo ou verbosidade"):
        materializer.synchronize(workspace, capability=capability)
    capability[values] = ["fixture-value"]
    assert materializer.synchronize(workspace, capability=capability)


@pytest.mark.parametrize("contents", ["[", "schema_version=2", 'schema_version=1\nagents=[]', 'schema_version=1\n[agents.reviewer]\nenabled="true"', 'schema_version=1\n[agents.reviewer]\nenabled=true\nmode="symlink"', 'schema_version=1\n[agents.reviewer]\nenabled=true\nsource="../reviewer.toml"', 'schema_version=1\n[agents.reviewer]\nenabled=true\nunknown=1'])
def test_invalid_configuration_blocks_distribution(instance, contents) -> None:
    root, workspace = instance
    configure(root); path = Materializer(root).agents_state_path
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(Error):
        Materializer(root).synchronize(workspace)


def test_proposals_and_other_files_are_not_sources(instance) -> None:
    root, _workspace = instance
    path = source(root)
    (path.parent / "reviewer-2026-11-05.proposed.md").write_text("inválido", encoding="utf-8")
    (path.parent / "notes.md").write_text("inválido", encoding="utf-8")
    assert list(Materializer(root).discover()) == ["reviewer"]


def test_missing_enabled_source_does_not_silently_drop_profile(instance, capability) -> None:
    root, workspace = instance
    configure(root)
    with pytest.raises(Error, match="não existe"):
        Materializer(root).synchronize(workspace, capability=capability)


def test_local_and_other_instances_files_are_never_claimed(instance, capability) -> None:
    root, workspace = instance
    source(root); configure(root)
    materializer = Materializer(root)
    target = materializer.target(workspace, "reviewer"); target.parent.mkdir(parents=True)
    target.write_text("conteúdo local", encoding="utf-8")
    with pytest.raises(Error, match="não gerenciado"):
        materializer.synchronize(workspace, capability=capability)
    assert target.read_text(encoding="utf-8") == "conteúdo local"


def test_workspace_cannot_be_inside_instance(instance, capability) -> None:
    root, _workspace = instance
    internal = root / "work"; internal.mkdir()
    source(root); configure(root)
    with pytest.raises(Error, match="externo"):
        Materializer(root).synchronize(internal, capability=capability)


@pytest.mark.parametrize("part", ["source", "target", "parent", "root", "workspace"])
def test_reparse_points_are_rejected_before_read_or_replace(instance, capability, monkeypatch, part) -> None:
    root, workspace = instance
    canonical = source(root); configure(root)
    target = Materializer.target(workspace, "reviewer")
    forbidden = {"source": canonical, "target": target, "parent": workspace / ".codex", "root": root, "workspace": workspace}[part]
    original = agents._reparse
    monkeypatch.setattr(agents, "_reparse", lambda path: path == forbidden or original(path))
    with pytest.raises(Error, match="link|reparse|Links"):
        Materializer(root).synchronize(workspace, capability=capability)


def test_manifest_semantic_tampering_cannot_remove_another_target(instance, capability) -> None:
    root, workspace = instance
    source(root); configure(root)
    materializer = Materializer(root); materializer.synchronize(workspace, capability=capability)
    state = materializer._read_state()
    key = next(iter(state["workspaces"]))
    state["workspaces"][key]["agents"]["reviewer"]["target"] = str(workspace / "unrelated.toml")
    materializer._write_state(state)
    configure(root, False)
    with pytest.raises(Error, match="outro destino"):
        materializer.synchronize(workspace, capability=capability)
    assert materializer.target(workspace, "reviewer").exists()


def test_manifest_redundancy_recovers_one_torn_copy_but_not_two(instance, capability) -> None:
    root, workspace = instance
    source(root); configure(root)
    materializer = Materializer(root); materializer.synchronize(workspace, capability=capability)
    materializer.runtime_state_path.write_text("{", encoding="utf-8")
    assert materializer.synchronize(workspace, capability=capability) is False
    materializer.runtime_backup_path.write_text("{", encoding="utf-8")
    with pytest.raises(Error, match="posse"):
        materializer.synchronize(workspace, capability=capability)


def test_pending_inventory_is_durable_before_target_mutation(instance, capability, monkeypatch) -> None:
    root, workspace = instance
    canonical = source(root); configure(root)
    materializer = Materializer(root)
    original = materializer._apply
    def inspect(workspace, pending, **kwargs):
        for manifest in (materializer.runtime_state_path, materializer.runtime_backup_path):
            state = json.loads(manifest.read_text(encoding="utf-8"))
            assert state["pending"]["candidate"]["reviewer"]["content_sha256"] == agents._digest(canonical)
        original(workspace, pending, **kwargs)
    monkeypatch.setattr(materializer, "_apply", inspect)
    assert materializer.synchronize(workspace, capability=capability)


def test_crash_after_first_replacement_recovers_without_tmp_journal(instance, capability, monkeypatch) -> None:
    root, workspace = instance
    source(root); configure(root)
    materializer = Materializer(root)
    original = materializer._apply
    def crash(workspace, pending, **kwargs):
        original(workspace, pending, **kwargs)
        raise RuntimeError("crash depois de publicar")
    monkeypatch.setattr(materializer, "_apply", crash)
    with pytest.raises(RuntimeError, match="crash"):
        materializer.synchronize(workspace, capability=capability)
    assert materializer._read_state()["pending"] is not None
    shutil.rmtree(root / "tmp")
    monkeypatch.setattr(materializer, "_apply", original)
    assert materializer.synchronize(workspace, capability=capability)
    assert materializer._read_state()["pending"] is None
    assert materializer.target(workspace, "reviewer").read_bytes() == source(root).read_bytes()


def test_recovery_rolls_back_if_candidate_and_source_are_lost(instance, capability, monkeypatch) -> None:
    root, workspace = instance
    canonical = source(root); configure(root)
    materializer = Materializer(root); materializer.synchronize(workspace, capability=capability)
    previous = canonical.read_bytes()
    source(root, instructions="Revisão nova.")
    original = materializer._apply
    def crash(*_args, **_kwargs):
        raise RuntimeError("crash antes de substituir")
    monkeypatch.setattr(materializer, "_apply", crash)
    with pytest.raises(RuntimeError):
        materializer.synchronize(workspace, capability=capability)
    pending = materializer._read_state()["pending"]
    (materializer._stage(workspace, pending) / "reviewer.candidate").unlink()
    canonical.unlink()
    monkeypatch.setattr(materializer, "_apply", original)
    with pytest.raises(Error, match="não existe"):
        materializer.synchronize(workspace, capability=capability)
    assert materializer._read_state()["pending"] is None
    assert materializer.target(workspace, "reviewer").read_bytes() == previous


def test_incomplete_workspace_blocks_an_unrelated_opening(instance, capability, monkeypatch, tmp_path) -> None:
    root, workspace = instance
    source(root); configure(root)
    materializer = Materializer(root)
    monkeypatch.setattr(materializer, "_apply", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        materializer.synchronize(workspace, capability=capability)
    other = tmp_path / "other"; other.mkdir()
    with pytest.raises(Error, match="Outra workspace"):
        Materializer(root).synchronize(other, capability=capability)


def test_simultaneous_openings_publish_one_consistent_revision(instance, capability) -> None:
    root, workspace = instance
    source(root); configure(root)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: Materializer(root).synchronize(workspace, capability=capability), range(4)))
    assert results.count(True) == 1
    materializer = Materializer(root)
    assert materializer._read_state()["revision"] == 1
    assert materializer.target(workspace, "reviewer").read_bytes() == source(root).read_bytes()


def test_workspace_change_keeps_ownership_separate(instance, capability, tmp_path) -> None:
    root, first = instance
    second = tmp_path / "second"; second.mkdir()
    source(root); configure(root)
    materializer = Materializer(root)
    materializer.synchronize(first, capability=capability)
    materializer.synchronize(second, capability=capability)
    configure(root, False)
    materializer.synchronize(second, capability=capability)
    assert materializer.target(first, "reviewer").exists()
    assert not materializer.target(second, "reviewer").exists()
    materializer.synchronize(first, capability=capability)
    assert materializer._read_state()["workspaces"] == {}


def test_runtime_does_not_modify_native_configuration(instance, capability) -> None:
    root, workspace = instance
    native = root / "config.toml"; native.write_text('[agents.personal]\nconfig_file="mine.toml"\n', encoding="utf-8")
    original = native.read_bytes()
    source(root); configure(root)
    assert Materializer(root).synchronize(workspace, capability=capability)
    assert native.read_bytes() == original


def test_io_failure_is_sanitized_and_pending_can_be_recovered(instance, capability, monkeypatch) -> None:
    root, workspace = instance
    source(root); configure(root)
    original = agents.os.replace
    monkeypatch.setattr(agents.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("private/path secret=value")))
    materializer = Materializer(root)
    with pytest.raises(Error, match="preserve o manifesto") as failure:
        materializer.synchronize(workspace, capability=capability)
    assert "secret" not in str(failure.value)
    assert materializer._read_state()["pending"] is not None
    monkeypatch.setattr(agents.os, "replace", original)
    assert materializer.synchronize(workspace, capability=capability)


def test_unknown_local_change_after_crash_blocks_recovery_without_overwriting(instance, capability, monkeypatch) -> None:
    root, workspace = instance
    source(root); configure(root)
    materializer = Materializer(root)
    original = materializer._apply
    monkeypatch.setattr(materializer, "_apply", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        materializer.synchronize(workspace, capability=capability)
    target = materializer.target(workspace, "reviewer")
    target.write_text("arquivo novo do usuário", encoding="utf-8")
    monkeypatch.setattr(materializer, "_apply", original)
    with pytest.raises(Error, match="destino mudou"):
        materializer.synchronize(workspace, capability=capability)
    assert target.read_text(encoding="utf-8") == "arquivo novo do usuário"
    assert materializer._read_state()["pending"] is not None


def test_tampered_pending_cannot_claim_an_unmanaged_file(instance, capability, monkeypatch) -> None:
    root, workspace = instance
    source(root); configure(root)
    materializer = Materializer(root)
    monkeypatch.setattr(materializer, "_apply", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        materializer.synchronize(workspace, capability=capability)
    state = materializer._read_state()
    state["pending"]["changes"].append({"identifier": "other", "before": "a" * 64, "after": None})
    materializer._write_state(state)
    with pytest.raises(Error, match="inventário"):
        Materializer(root).synchronize(workspace, capability=capability)


def test_profile_file_and_runtime_file_must_be_regular(instance, capability) -> None:
    root, workspace = instance
    configure(root)
    directory = root / "shikigami-agents" / "reviewer.toml"; directory.mkdir(parents=True)
    with pytest.raises(Error, match="arquivo regular"):
        Materializer(root).discover()
    directory.rmdir(); source(root)
    target = Materializer.target(workspace, "reviewer"); target.mkdir(parents=True)
    with pytest.raises(Error, match="arquivo regular"):
        Materializer(root).synchronize(workspace, capability=capability)


def test_lock_reparse_is_rejected(instance, capability, monkeypatch) -> None:
    root, workspace = instance
    source(root); configure(root)
    original = agents._reparse
    monkeypatch.setattr(agents, "_reparse", lambda path: path.name == "agents-runtime.lock" or original(path))
    with pytest.raises(Error, match="lock"):
        Materializer(root).synchronize(workspace, capability=capability)
