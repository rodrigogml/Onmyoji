"""Entrada restrita compartilhada pelos adaptadores OAuth Mercado."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .oauth import MercadoOAuth


def main_for(provider: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        request = json.load(sys.stdin)
        oauth = MercadoOAuth(args.onmyoji_root, provider, request["profile"])
        action = request["action"]
        if action == "describe":
            data = oauth.describe()
        elif action == "start":
            data = oauth.start(request["state"], request["sessionId"], request["redirectUri"])
        elif action == "complete":
            data = oauth.complete(request["code"], request["state"], request["sessionId"], request["redirectUri"])
        elif action == "cancel":
            data = oauth.cancel(request["sessionId"])
        else:
            raise ValueError("Ação OAuth inválida.")
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": {"code": getattr(error, "code", "oauth_adapter_failed")}}, ensure_ascii=False))
        return 2
