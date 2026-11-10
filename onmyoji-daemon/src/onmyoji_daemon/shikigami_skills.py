"""Materialização segura de skills canônicas do Shikigami no workspace."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import tomllib
from typing import Any

from .instance import shikigami_skills_path


STATE_SCHEMA_VERSION = 1
RUNTIME_SCHEMA_VERSION = 1
SKILLS_STATE_PATH = Path("configs/shikigami/skills.toml")
RUNTIME_STATE_PATH = Path("configs/shikigami/skills-runtime.json")


class ShikigamiSkillMaterializationError(RuntimeError):
    """Indica que a projeção de skills no workspace não pode ser feita com segurança."""


class ShikigamiSkillMaterializer:
    """Projeta cópias reparáveis das skills versionadas, sem tornar o workspace canônico."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.source_root = shikigami_skills_path(self.root)
        self.skills_state_path = self.root / SKILLS_STATE_PATH
        self.runtime_state_path = self.root / RUNTIME_STATE_PATH

    @staticmethod
    def target(workspace: Path, identifier: str) -> Path:
        return workspace.resolve() / ".agents" / "skills" / identifier

    def _configuration(self) -> dict[str, dict[str, object]]:
        try:
            data = tomllib.loads(self.skills_state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ShikigamiSkillMaterializationError("O estado das skills do Shikigami é inválido.") from error
        records = data.get("skills", {})
        if data.get("schema_version") != STATE_SCHEMA_VERSION or not isinstance(records, dict):
            raise ShikigamiSkillMaterializationError("O estado das skills do Shikigami não é suportado.")
        values: dict[str, dict[str, object]] = {}
        for identifier, record in records.items():
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", identifier) or not isinstance(record, dict):
                raise ShikigamiSkillMaterializationError("O estado das skills contém um identificador inválido.")
            if type(record.get("enabled")) is not bool or record.get("mode") != "managed_copy":
                raise ShikigamiSkillMaterializationError(f"O estado da skill {identifier} é inválido.")
            values[identifier] = record
        return values

    def _source(self, identifier: str, record: dict[str, object]) -> Path:
        source_name = record.get("source", identifier)
        if not isinstance(source_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", source_name):
            raise ShikigamiSkillMaterializationError(f"A origem da skill {identifier} é inválida.")
        source = self.source_root / source_name
        try:
            resolved = source.resolve(strict=True)
        except OSError as error:
            raise ShikigamiSkillMaterializationError(f"A fonte canônica da skill {identifier} não existe.") from error
        if source.is_symlink() or not resolved.is_relative_to(self.source_root.resolve()) or not (resolved / "SKILL.md").is_file():
            raise ShikigamiSkillMaterializationError(f"A fonte canônica da skill {identifier} não é válida.")
        return resolved

    @staticmethod
    def _files(source: Path, identifier: str) -> list[Path]:
        files: list[Path] = []
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ShikigamiSkillMaterializationError(f"A fonte canônica da skill {identifier} contém um link simbólico.")
            if path.is_file() and not path.name.endswith(".proposed.md"):
                files.append(path)
        return files

    @staticmethod
    def _digest(files: list[Path], base: Path) -> str:
        digest = sha256()
        for path in files:
            relative = path.relative_to(base).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big")); digest.update(relative)
            content = path.read_bytes(); digest.update(len(content).to_bytes(8, "big")); digest.update(content)
        return digest.hexdigest()

    @staticmethod
    def _safe_target(workspace: Path, identifier: str) -> Path:
        workspace = workspace.resolve()
        target = ShikigamiSkillMaterializer.target(workspace, identifier)
        if any(path.is_symlink() for path in (workspace / ".agents", workspace / ".agents" / "skills", target)):
            raise ShikigamiSkillMaterializationError("A projeção de skills não pode atravessar links simbólicos no workspace.")
        if target.absolute() != (workspace / ".agents" / "skills" / identifier).absolute():
            raise ShikigamiSkillMaterializationError("O destino da skill está fora do local reservado do workspace.")
        return target

    def _runtime_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.runtime_state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema_version": RUNTIME_SCHEMA_VERSION, "workspaces": {}}
        except (OSError, ValueError) as error:
            raise ShikigamiSkillMaterializationError("O manifesto de runtime das skills está inválido.") from error
        if not isinstance(data, dict) or data.get("schema_version") != RUNTIME_SCHEMA_VERSION or not isinstance(data.get("workspaces"), dict):
            raise ShikigamiSkillMaterializationError("O manifesto de runtime das skills não é suportado.")
        return data

    def _write_runtime_state(self, state: dict[str, Any]) -> None:
        self.runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        descriptor, temporary = tempfile.mkstemp(prefix="skills-runtime.", suffix=".tmp", dir=self.runtime_state_path.parent, text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(content)
            os.replace(temporary, self.runtime_state_path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    @staticmethod
    def _target_matches(target: Path, digest: str) -> bool:
        if not target.is_dir() or target.is_symlink():
            return False
        files = [path for path in sorted(target.rglob("*")) if path.is_file()]
        if any(path.is_symlink() for path in target.rglob("*")):
            return False
        return ShikigamiSkillMaterializer._digest(files, target) == digest

    @staticmethod
    def _remove_owned(target: Path) -> bool:
        if not target.exists() and not target.is_symlink():
            return False
        if target.is_symlink():
            raise ShikigamiSkillMaterializationError("A projeção gerenciada foi substituída por um link simbólico.")
        shutil.rmtree(target)
        return True

    def _materialize(self, workspace: Path, identifier: str, source: Path, files: list[Path], digest: str, record: dict[str, Any] | None) -> bool:
        target = self._safe_target(workspace, identifier)
        if target.exists() and record is None:
            raise ShikigamiSkillMaterializationError(f"Já existe uma pasta não gerenciada em {target}.")
        if record is not None and record.get("target") != str(target):
            raise ShikigamiSkillMaterializationError("O manifesto de runtime aponta a skill para outro destino.")
        if record is not None and record.get("content_sha256") == digest and self._target_matches(target, digest):
            return False
        parent = target.parent; parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{identifier}.", dir=parent))
        backup = parent / f".{identifier}.previous"
        try:
            for source_file in files:
                destination = staging / source_file.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, destination)
            if backup.exists(): shutil.rmtree(backup)
            if target.exists(): os.replace(target, backup)
            os.replace(staging, target)
            if backup.exists(): shutil.rmtree(backup)
        except Exception:
            if staging.exists(): shutil.rmtree(staging)
            if backup.exists() and not target.exists(): os.replace(backup, target)
            raise
        return True

    def synchronize(self, workspace: Path) -> bool:
        """Cria, repara e remove apenas projeções registradas como gerenciadas."""
        workspace = workspace.resolve()
        configuration = self._configuration()
        state = self._runtime_state()
        workspaces = state["workspaces"]
        key = str(workspace)
        previous = workspaces.get(key, {})
        if not isinstance(previous, dict) or not isinstance(previous.get("skills", {}), dict):
            raise ShikigamiSkillMaterializationError("O manifesto de runtime da workspace está inválido.")
        previous_skills: dict[str, dict[str, Any]] = previous.get("skills", {})
        changed = False

        for identifier, record in list(previous_skills.items()):
            if configuration.get(identifier, {}).get("enabled") is True:
                continue
            target = self._safe_target(workspace, identifier)
            if not isinstance(record, dict) or record.get("target") != str(target):
                raise ShikigamiSkillMaterializationError("O manifesto de runtime aponta a skill para outro destino.")
            changed = self._remove_owned(target) or changed
            previous_skills.pop(identifier, None)

        for identifier, configured in configuration.items():
            if configured["enabled"] is not True:
                continue
            source = self._source(identifier, configured)
            files = self._files(source, identifier)
            digest = self._digest(files, source)
            old = previous_skills.get(identifier)
            changed = self._materialize(workspace, identifier, source, files, digest, old) or changed
            target = self._safe_target(workspace, identifier)
            previous_skills[identifier] = {"source": str(source), "target": str(target), "content_sha256": digest}

        if previous_skills:
            workspaces[key] = {"skills": previous_skills}
        else:
            workspaces.pop(key, None)
        if workspaces:
            self._write_runtime_state(state)
        elif self.runtime_state_path.exists():
            self.runtime_state_path.unlink()
        return changed
