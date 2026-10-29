"""Projeção reparável das skills canônicas do Shikigami no workspace."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from onmyoji_daemon.shikigami_skills import ShikigamiSkillMaterializationError, ShikigamiSkillMaterializer


def configure(root: Path, enabled: bool = True, source: str = "workflow") -> None:
    path = root / "configs" / "shikigami" / "skills.toml"; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'schema_version = 1\n\n[skills.workflow]\nenabled = {str(enabled).lower()}\nmode = "managed_copy"\nsource = "{source}"\n', encoding="utf-8")


def source(root: Path, name: str = "workflow") -> Path:
    directory = root / "shikigami" / "skills" / name; (directory / "references").mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: workflow\ndescription: Workflow de teste.\n---\n\nSiga o procedimento.\n", encoding="utf-8")
    (directory / "references" / "steps.md").write_text("Etapa 1.\n", encoding="utf-8")
    return directory


def test_materializer_copies_repairs_updates_and_removes_owned_skill(tmp_path: Path) -> None:
    root, workspace = tmp_path / "root", tmp_path / "work"; workspace.mkdir(); canonical = source(root); configure(root)
    materializer = ShikigamiSkillMaterializer(root)
    assert materializer.synchronize(workspace) is True
    target = materializer.target(workspace, "workflow")
    assert (target / "references" / "steps.md").read_text(encoding="utf-8") == "Etapa 1.\n"
    (target / "SKILL.md").write_text("alterada", encoding="utf-8")
    assert materializer.synchronize(workspace) is True
    assert "Siga o procedimento" in (target / "SKILL.md").read_text(encoding="utf-8")
    (canonical / "references" / "steps.md").write_text("Etapa 2.\n", encoding="utf-8")
    assert materializer.synchronize(workspace) is True
    assert (target / "references" / "steps.md").read_text(encoding="utf-8") == "Etapa 2.\n"
    configure(root, enabled=False)
    assert materializer.synchronize(workspace) is True
    assert not target.exists() and not materializer.runtime_state_path.exists()


def test_materializer_refuses_to_overwrite_unmanaged_workspace_directory(tmp_path: Path) -> None:
    root, workspace = tmp_path / "root", tmp_path / "work"; workspace.mkdir(); source(root); configure(root)
    target = ShikigamiSkillMaterializer.target(workspace, "workflow"); target.mkdir(parents=True)
    (target / "SKILL.md").write_text("conteúdo local", encoding="utf-8")
    with pytest.raises(ShikigamiSkillMaterializationError, match="não gerenciada"):
        ShikigamiSkillMaterializer(root).synchronize(workspace)


def test_materializer_removes_only_a_target_registered_for_the_workspace(tmp_path: Path) -> None:
    root, workspace = tmp_path / "root", tmp_path / "work"; workspace.mkdir(); source(root); configure(root)
    materializer = ShikigamiSkillMaterializer(root); materializer.synchronize(workspace)
    runtime = json.loads(materializer.runtime_state_path.read_text(encoding="utf-8"))
    runtime["workspaces"][str(workspace.resolve())]["skills"]["workflow"]["target"] = str(workspace / "other")
    materializer.runtime_state_path.write_text(json.dumps(runtime), encoding="utf-8")
    configure(root, enabled=False)
    with pytest.raises(ShikigamiSkillMaterializationError, match="outro destino"):
        materializer.synchronize(workspace)
    assert materializer.target(workspace, "workflow").is_dir()
