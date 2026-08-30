"""Definição versionada e não secreta de uma instância Shikigami."""
from __future__ import annotations

import tomllib
from pathlib import Path


def definition_dir(root: Path) -> Path:
    return root.resolve() / "shikigami"


def definition_path(root: Path) -> Path:
    return definition_dir(root) / "instance.toml"


def identity(root: Path) -> str:
    """Retorna a identidade declarada; conserva compatibilidade com instâncias antigas."""
    try:
        data = tomllib.loads(definition_path(root).read_text(encoding="utf-8"))
        value = data.get("shikigami", {}).get("identity", "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    name = root.name
    for prefix in ("Shikigami-", "Onmyoji-", "Onmyōji-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.strip() or "Shikigami"
