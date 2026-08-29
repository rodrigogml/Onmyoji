#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"): sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--onmyoji-root", type=Path); parser.add_argument("--action", choices=["describe", "status", "configure"], default="configure"); parser.add_argument("--json", action="store_true"); args = parser.parse_args()
    value = {"id": "onmyoji-control", "title": "Onmyōji Control", "description": "Administração limitada de skills pela API oficial do setup."}
    if args.action == "describe": print(json.dumps(value, ensure_ascii=False) if args.json else value["title"]); return 0
    if args.action == "status": print(json.dumps({"configured": True, "valid": True, "message": "A skill usa somente o setup central desta instância."}, ensure_ascii=False) if args.json else "Pronta."); return 0
    print("Esta skill não possui configuração própria; habilite-a somente quando o Shikigami puder administrar skills.")
    return 0


if __name__ == "__main__": raise SystemExit(main())
