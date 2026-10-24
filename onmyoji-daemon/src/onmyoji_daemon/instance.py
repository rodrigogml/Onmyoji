"""Identidade local de uma instância Shikigami."""
from __future__ import annotations

import tomllib
from pathlib import Path


def identity(root: Path) -> str:
    """Retorna a identidade configurada localmente ou um fallback seguro."""
    try:
        data = tomllib.loads((root.resolve() / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
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
