"""Identidade local de uma instância Shikigami."""
from __future__ import annotations

import tomllib
from pathlib import Path


def telegram_configuration_path(root: Path) -> Path:
    """Retorna a configuração local do gateway Telegram desta instância."""
    return root.resolve() / "configs" / "daemon" / "services" / "telegram" / "config.toml"


def instructions_path(root: Path) -> Path:
    """Retorna o overlay opcional de instruções da instância."""
    return root.resolve() / "instructions.md"


def shikigami_skills_path(root: Path) -> Path:
    """Retorna a fonte versionável de skills próprias da instância."""
    return root.resolve() / "shikigami-skills"


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
