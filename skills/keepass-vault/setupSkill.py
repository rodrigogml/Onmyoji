#!/usr/bin/env python3
"""Setup local da skill KeePass Vault."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
MODEL = SKILL_DIR / "configs" / "keepass.toml.model"


def root_for(value: str | None) -> Path:
    return Path(value).resolve() if value else SKILL_DIR.parents[1]


def config_for(root: Path) -> Path: return root / "configs" / "keepass.toml"


def describe() -> dict[str, object]:
    return {"id": "keepass-vault", "title": "KeePass Vault", "description": "Cofres KeePassXC, TOTPs e anexos", "actions": ["status", "init", "configure", "validate", "migrate"]}


def validate(path: Path) -> tuple[bool, str]:
    if not path.exists(): return False, "Configuração ainda não foi criada."
    try:
        sys.path.insert(0, str(SKILL_DIR / "scripts")); import keepass_vault
        keepass_vault.load_settings(path, next(iter(__import__('tomllib').loads(path.read_text(encoding='utf-8')).get('profiles', {})), ""))
    except Exception: return False, "Configuração inválida ou sem perfis."
    return True, "Configuração válida."


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--onmyoji-root"); parser.add_argument("--action", choices=["describe", "status", "init", "configure", "validate", "migrate"], default="status"); parser.add_argument("--json", action="store_true"); parser.add_argument("--force", action="store_true"); args = parser.parse_args()
    root = root_for(args.onmyoji_root)
    target = config_for(root)
    if args.action == "describe":
        print(json.dumps(describe(), ensure_ascii=False) if args.json else describe()["title"]); return 0
    if args.action == "init":
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not args.force: print(f"Preservado: {target}"); return 0
        if target.exists(): shutil.copy2(target, target.with_suffix(".toml.bak"))
        shutil.copy2(MODEL, target); print(f"Criado: {target}"); return 0
    if args.action == "configure":
        if not target.exists(): print("Inicialize primeiro com --action init.", file=sys.stderr); return 2
        print(f"Edite os perfis em: {target}"); return 0
    if args.action == "migrate": print("Nenhuma migração é necessária para schema_version = 1."); return 0
    ok, message = validate(target); print(message); return 0 if ok else 2


if __name__ == "__main__": raise SystemExit(main())
