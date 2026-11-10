"""Política comum para artefatos locais compartilhados entre MCPs."""
from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path


class ArtifactPathError(ValueError):
    """Caminho local fora das permissões declaradas pelo serviço."""


def workspace(root: Path) -> Path:
    """Obtém o Work externo configurado para a instância."""
    try:
        data = tomllib.loads((Path(root) / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        value = data["codex"]["project_directory"]
        path = Path(str(value)).expanduser()
        if not path.is_absolute() or not path.is_dir(): raise ValueError
        return path.resolve()
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ArtifactPathError("Configure um diretório Work válido antes de usar artefatos locais.") from error


def reject_links(path: Path) -> None:
    """Recusa links e junctions no caminho e nos ancestrais existentes."""
    for candidate in (path, *path.parents):
        try:
            attributes = getattr(candidate.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        junction = os.name == "nt" and bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if junction or candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise ArtifactPathError("O caminho não pode conter links simbólicos ou junctions.")


def authorized_file(value: str, roots: list[str], maximum: int | None = None) -> Path:
    """Valida arquivo existente em uma raiz declarada, sem copiá-lo."""
    if not isinstance(value, str) or not value.strip(): raise ArtifactPathError("Informe um caminho absoluto de arquivo.")
    path = Path(value).expanduser()
    if not path.is_absolute(): raise ArtifactPathError("O caminho do arquivo deve ser absoluto.")
    reject_links(path)
    resolved = path.resolve()
    allowed = []
    for value in roots:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute(): continue
        reject_links(candidate); allowed.append(candidate.resolve())
    if not allowed or not any(resolved.is_relative_to(root) for root in allowed):
        raise ArtifactPathError("Arquivo fora das raízes autorizadas.")
    if not resolved.is_file(): raise ArtifactPathError("Arquivo de entrada não encontrado.")
    if maximum is not None and resolved.stat().st_size > maximum:
        raise ArtifactPathError("Arquivo excede o limite autorizado.")
    return resolved


def default_output(root: Path, service: str, name: str) -> Path:
    """Reserva o destino previsível de uma saída dentro do Work."""
    safe = Path(name).name
    if not safe or safe in {".", ".."}: raise ArtifactPathError("Nome de saída inválido.")
    destination = workspace(root) / ".onmyoji" / service / "output" / safe
    reject_links(destination)
    if destination.exists(): raise ArtifactPathError("A saída padrão já existe; informe outro destino.")
    return destination


def transfer_roots(root: Path) -> list[str]:
    """Reúne Work e raízes de arquivos explicitamente declaradas pelos MCPs."""
    values = [str(workspace(root))]
    directory = Path(root) / "configs" / "mcp"
    for path in directory.glob("*.toml") if directory.is_dir() else ():
        try: document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError): continue
        def visit(value: object) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"readable_roots", "writable_roots"} and isinstance(item, list):
                        values.extend(str(entry) for entry in item if isinstance(entry, str) and entry.strip())
                    else: visit(item)
            elif isinstance(value, list):
                for item in value: visit(item)
        visit(document)
    return list(dict.fromkeys(values))
