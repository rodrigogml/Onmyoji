"""Acesso a fontes e publicação exclusiva de artefatos no workspace."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from uuid import uuid4

from .contracts import DocumentError, require


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_links(path: Path) -> None:
    """Rejeita links e junctions existentes no caminho, inclusive nos ancestrais."""
    for candidate in (path, *path.parents):
        try:
            reparse = bool(getattr(candidate.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)) if os.name == "nt" else False
        except FileNotFoundError:
            reparse = False
        if reparse or candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise DocumentError("path_not_allowed", "O caminho não pode conter links simbólicos ou junctions.")


def contained_path(value: str, roots: list[str], *, existing: bool = False) -> Path:
    require(isinstance(value, str) and bool(value.strip()), "invalid_path", "Informe um caminho absoluto válido.")
    raw = Path(value).expanduser()
    require(raw.is_absolute(), "invalid_path", "O caminho deve ser absoluto.")
    reject_links(raw)
    path = raw.resolve()
    allowed = []
    for configured in roots:
        candidate = Path(configured).expanduser()
        require(candidate.is_absolute(), "invalid_config", "As raízes autorizadas devem ser absolutas.")
        reject_links(candidate)
        allowed.append(candidate.resolve())
    require(any(path.is_relative_to(root) for root in allowed), "path_not_allowed", "O arquivo está fora das raízes autorizadas.")
    if existing:
        require(path.is_file(), "file_not_found", "O arquivo de entrada não foi encontrado.")
    return path


class DocumentContext:
    """Controla fontes, temporários e saídas de uma execução isolada.

    ``stage_output`` nunca abre o destino final. ``publish`` somente publica
    arquivos concluídos, recusa fontes alteradas e desfaz saídas publicadas por
    esta execução se uma publicação posterior falhar.
    """

    def __init__(self, root: Path, namespace: str, profile_name: str, profile: dict, defaults: dict, job_dir: Path):
        self.root = Path(root).resolve()
        self.namespace = namespace
        self.profile_name = profile_name
        self.profile = profile
        self.defaults = defaults
        self.job_dir = Path(job_dir).absolute()
        self._validate_locations()
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self._sources: dict[Path, str] = {}
        self._outputs: dict[Path, Path] = {}

    def _validate_locations(self) -> Path:
        from .configuration import validate_profile_directories
        try:
            work = validate_profile_directories(self.root, self.profile)
        except (ValueError, OSError) as error:
            raise DocumentError("path_not_allowed", "As permissões do perfil não pertencem ao workspace atual.") from error
        reject_links(self.job_dir)
        require(self.job_dir.resolve().is_relative_to(work) and self.job_dir.resolve() != work,
                "path_not_allowed", "O job deve ficar dentro do diretório de trabalho autorizado.")
        return work

    def input_path(self, value: str) -> Path:
        work = self._validate_locations()
        path = contained_path(value, self.profile.get("readable_roots", []), existing=True)
        require(not path.is_relative_to(work), "path_not_allowed", "Arquivos internos de trabalho não são documentos de entrada.")
        require(path.stat().st_size <= self.defaults.get("max_input_bytes", 262144000), "input_too_large", "O arquivo excede o limite de entrada configurado.")
        digest = file_digest(path)
        if path in self._sources:
            require(self._sources[path] == digest, "source_changed", "Um arquivo de entrada foi alterado durante a operação.")
        self._sources[path] = digest
        return path

    def temporary_path(self, suffix: str = "") -> Path:
        self._validate_locations()
        require(isinstance(suffix, str) and not any(c in suffix for c in "/\\\x00"), "invalid_argument", "Extensão temporária inválida.")
        return self.job_dir / (uuid4().hex + suffix)

    def stage_output(self, destination: str) -> Path:
        self._validate_locations()
        path = contained_path(destination, self.profile.get("writable_roots", []))
        work = Path(self.profile.get("work_directory", str(self.job_dir))).resolve()
        require(not path.is_relative_to(work), "path_not_allowed", "A saída final deve ficar fora do diretório temporário de trabalho.")
        require(not path.exists(), "output_exists", "O arquivo de saída já existe; escolha outro destino.")
        require(path not in self._outputs and path not in self._sources, "output_conflict", "O destino de saída foi utilizado mais de uma vez.")
        staged = self.temporary_path(path.suffix)
        self._outputs[path] = staged
        return staged

    def resolve_password(self, reference: dict | None) -> str | None:
        if reference is None:
            return None
        require(isinstance(reference, dict) and set(reference) <= {"credential_profile", "credential_entry_path", "field"}, "invalid_credential", "Informe uma referência de senha do vault.")
        require(all(isinstance(reference.get(key), str) and reference[key] for key in ("credential_profile", "credential_entry_path")), "invalid_credential", "A referência de senha do vault está incompleta.")
        field = reference.get("field", "password")
        require(field in {"username", "password", "url", "notes"}, "invalid_credential", "Campo de senha inválido.")
        from ..credentials import KeePassCredentialResolver
        try:
            return KeePassCredentialResolver(self.root).resolve(reference["credential_profile"], reference["credential_entry_path"], field)
        except Exception as error:
            raise DocumentError("credential_unavailable", "Não foi possível resolver a senha no vault configurado.") from error

    def progress(self, current: int, total: int, message: str = "") -> None:
        """Atualiza progresso operacional sem copiar conteúdo do documento."""
        target = self.job_dir / "progress.json"
        temporary = self.temporary_path(".json")
        temporary.write_text(json.dumps({"current": current, "total": total, "message": message[:240]}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    def publish(self) -> list[dict]:
        self._validate_locations()
        for source, digest in self._sources.items():
            contained_path(str(source), self.profile.get("readable_roots", []), existing=True)
            require(source.is_file() and file_digest(source) == digest, "source_changed", "Um arquivo de entrada foi alterado durante a operação.")
        for destination, staged in self._outputs.items():
            contained_path(str(destination), self.profile.get("writable_roots", []))
            require(not destination.exists(), "output_exists", "O arquivo de saída já existe; escolha outro destino.")
            require(staged.is_file() and not staged.is_symlink(), "output_missing", "A operação não produziu um arquivo de saída válido.")
        published: list[Path] = []
        temporary_files: list[Path] = []
        journal: list[dict] = []
        try:
            for destination, staged in self._outputs.items():
                self._validate_locations()
                destination.parent.mkdir(parents=True, exist_ok=True)
                reject_links(destination)
                temporary = destination.parent / (".onmyoji-" + uuid4().hex + ".tmp")
                temporary_files.append(temporary)
                with temporary.open("xb") as target, staged.open("rb") as source:
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
                identity = temporary.stat()
                journal.append({"path": str(destination), "sha256": file_digest(temporary), "bytes": identity.st_size,
                                "inode": identity.st_ino, "device": identity.st_dev, "temporary": str(temporary)})
                journal_file = self.temporary_path(".json")
                journal_file.write_text(json.dumps({"artifacts": journal}, ensure_ascii=False), encoding="utf-8")
                journal_file.replace(self.job_dir / "publication.json")
                # A ligação exclusiva publica somente bytes completos e não
                # sobrescreve um arquivo criado concorrentemente no destino.
                os.link(temporary, destination)
                published.append(destination)
            artifacts = [{"path": str(path), "sha256": file_digest(path), "bytes": path.stat().st_size} for path in published]
            return artifacts
        except (FileExistsError, DocumentError) as error:
            for path in published:
                path.unlink(missing_ok=True)
            if isinstance(error, DocumentError):
                raise
            raise DocumentError("output_exists", "Um destino foi criado por outra operação; nenhuma saída foi publicada.") from error
        except OSError as error:
            for path in published:
                path.unlink(missing_ok=True)
            raise DocumentError("output_write_failed", "Não foi possível publicar os arquivos de saída.") from error
        finally:
            for path in temporary_files:
                path.unlink(missing_ok=True)
