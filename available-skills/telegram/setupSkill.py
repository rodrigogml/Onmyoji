#!/usr/bin/env python3
"""A skill Telegram depende exclusivamente do gateway local."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path)
    parser.add_argument("--action", choices=["describe", "status", "configure"], default="configure")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.onmyoji_root.resolve() if args.onmyoji_root else Path(__file__).resolve().parents[2]
    if args.action == "describe":
        payload = {"id": "telegram", "title": "Telegram", "description": "Envio unilateral pelo gateway local."}
    elif args.action == "status":
        payload = {"configured": True, "valid": True, "message": "A skill requer serviço Telegram habilitado no daemon."}
    else:
        payload = {"ok": True, "message": "Não há perfil/credencial da skill. Configure o Gateway Telegram no setup do daemon."}
    print(json.dumps(payload, ensure_ascii=False) if args.json else payload.get("title") or payload.get("message"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
