#!/usr/bin/env python3
"""Inicializa e valida a configuração local do daemon Onmyōji."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = Path(__file__).resolve().parent / "configs" / "telegram.toml.model"


def target(root: Path) -> Path: return root / "configs" / "daemon" / "services" / "telegram" / "telegram.toml"


def bootstrap(root: Path) -> Path:
    path = target(root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(MODEL, path)
    return path


def validate(root: Path) -> tuple[bool, str]:
    path = target(root)
    try: data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError: return False, "Configuração Telegram ausente; execute bootstrap."
    except tomllib.TOMLDecodeError as error: return False, f"TOML inválido: {error}"
    if data.get("schema_version") != 1: return False, "schema_version deve ser 1."
    telegram = data.get("telegram", {})
    if not isinstance(telegram.get("keepass_profile"), str) or not telegram["keepass_profile"]: return False, "telegram.keepass_profile é obrigatório."
    if not isinstance(telegram.get("token_entry"), str) or not telegram["token_entry"]: return False, "telegram.token_entry é obrigatório."
    system = root / "configs" / "onmyoji-system.toml"
    if not system.exists(): return False, "Configure o Codex-CLI antes do gateway."
    return True, "Configuração do daemon válida."


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--onmyoji-root", default=str(ROOT)); parser.add_argument("--action", choices=["bootstrap", "validate", "run"], default="bootstrap"); args = parser.parse_args()
    root = Path(args.onmyoji_root).resolve()
    if args.action == "bootstrap": print(bootstrap(root)); return 0
    ok, message = validate(root); print(message)
    if not ok: return 2
    if args.action == "run":
        return subprocess.run([sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root), "run"], cwd=Path(__file__).resolve().parent).returncode
    return 0


if __name__ == "__main__": raise SystemExit(main())
