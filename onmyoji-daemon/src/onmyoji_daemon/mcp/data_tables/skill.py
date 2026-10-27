"""Materialização reparável da skill de tabelas no workspace do agente."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile


SKILL_NAME = "onmyoji-data-tables"
STATE_SCHEMA_VERSION = 1


class ManagedDataTablesSkillError(RuntimeError):
    """Impede sincronização quando o destino não pode ser alterado com segurança."""


class ManagedDataTablesSkill:
    """Copia a fonte canônica sem permitir que o workspace vire fonte de verdade."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.source = Path(__file__).resolve().parents[4] / "resources" / "agent-skills" / SKILL_NAME
        self.state_path = self.root / "configs" / "mcp" / "data-tables" / "managed-skill.json"

    @staticmethod
    def target(workspace: Path) -> Path:
        return workspace.resolve() / ".agents" / "skills" / SKILL_NAME

    def _files(self) -> list[Path]:
        if not (self.source / "SKILL.md").is_file():
            raise ManagedDataTablesSkillError("A skill canônica de tabelas de dados não está disponível ou ainda não foi aprovada.")
        files: list[Path] = []
        for path in sorted(self.source.rglob("*")):
            if path.is_symlink():
                raise ManagedDataTablesSkillError("A skill canônica não pode conter links simbólicos.")
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

    def _state(self) -> dict[str, object] | None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise ManagedDataTablesSkillError("O controle da skill gerenciada está inválido.") from error
        if not isinstance(data, dict) or data.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ManagedDataTablesSkillError("O controle da skill gerenciada não é suportado.")
        return data

    def _write_state(self, target: Path, digest: str) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps({"schema_version": STATE_SCHEMA_VERSION, "skill": SKILL_NAME, "target": str(target), "content_sha256": digest}, ensure_ascii=False, indent=2) + "\n"
        descriptor, temporary = tempfile.mkstemp(prefix="managed-skill.", suffix=".tmp", dir=self.state_path.parent, text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(content)
            os.replace(temporary, self.state_path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    @staticmethod
    def _safe_target(workspace: Path, target: Path) -> None:
        workspace = workspace.resolve(); agents = workspace / ".agents"; skills = agents / "skills"; expected = skills / SKILL_NAME
        if any(path.is_symlink() for path in (agents, skills, target)):
            raise ManagedDataTablesSkillError("A skill gerenciada não pode atravessar links simbólicos no workspace.")
        if target.absolute() != expected.absolute():
            raise ManagedDataTablesSkillError("O destino da skill gerenciada está fora do local reservado.")

    def synchronize(self, workspace: Path, enabled: bool) -> bool:
        """Cria, repara ou remove somente a cópia registrada como gerenciada."""
        workspace = workspace.resolve(); target = self.target(workspace); self._safe_target(workspace, target)
        state = self._state()
        if state is not None and Path(str(state.get("target") or "")).resolve() != target.resolve():
            raise ManagedDataTablesSkillError("O workspace mudou; desabilite as tabelas no workspace anterior antes de materializá-las em outro.")
        if not enabled:
            if state is not None and target.exists():
                shutil.rmtree(target)
            if state is not None:
                self.state_path.unlink(missing_ok=True)
            return state is not None
        files = self._files(); digest = self._digest(files, self.source)
        if target.exists() and state is None:
            raise ManagedDataTablesSkillError(f"Já existe uma pasta não gerenciada em {target}.")
        if state is not None and state.get("content_sha256") == digest and target.is_dir():
            current_files = [path for path in sorted(target.rglob("*")) if path.is_file()]
            if any(path.is_symlink() for path in current_files):
                raise ManagedDataTablesSkillError("A cópia gerenciada contém um link simbólico inesperado.")
            if self._digest(current_files, target) == digest:
                return False
        parent = target.parent; parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}.", dir=parent)); backup = parent / f".{SKILL_NAME}.previous"
        try:
            for source in files:
                destination = staging / source.relative_to(self.source)
                destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination)
            if backup.exists(): shutil.rmtree(backup)
            if target.exists(): os.replace(target, backup)
            os.replace(staging, target)
            if backup.exists(): shutil.rmtree(backup)
            self._write_state(target, digest)
        except Exception:
            if staging.exists(): shutil.rmtree(staging)
            if backup.exists() and not target.exists(): os.replace(backup, target)
            raise
        return True
