"""Adaptador fechado de e-mail para o broker OAuth do MiniApps."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from ..configuration import McpConfiguration
from .contracts import require
from .oauth import EmailOAuth


def dispatch(root: Path, request: dict) -> dict:
    require(isinstance(request, dict) and request.get("version") == 1 and set(request) <= {"version", "action", "profile", "state", "sessionId", "redirectUri", "code"}, "invalid_request", "Contrato OAuth inválido.")
    name, _, connection = request["profile"].rpartition("--")
    require(connection in {"incoming", "smtp"}, "invalid_connection", "Conexão OAuth inválida.")
    account = McpConfiguration(root).email()["accounts"][name]
    oauth = EmailOAuth(root, account, connection)
    require(oauth.auth["callback_mode"] == "remote", "remote_mode_required", "Configure callback remoto.")
    action = request["action"]
    if action == "describe":
        meta = oauth.metadata(); meta.pop("scopes"); meta.pop("tokenEndpoint"); return meta
    if action == "start": return oauth.start(request["state"], request["sessionId"], request["redirectUri"])
    if action == "complete": return oauth.complete(request["code"], request["state"], request["sessionId"], request["redirectUri"])
    if action == "cancel": return oauth.cancel(request["sessionId"])
    require(False, "invalid_action", "Ação OAuth inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--onmyoji-root", type=Path, required=True); args = parser.parse_args()
    try:
        raw = sys.stdin.read(16385); require(len(raw) <= 16384, "invalid_request", "Requisição muito grande.")
        result = {"ok": True, "data": dispatch(args.onmyoji_root, json.loads(raw))}; code = 0
    except Exception as error:
        result = {"ok": False, "error": {"code": getattr(error, "code", "email_oauth_failed")}}; code = 2
    print(json.dumps(result)); return code


if __name__ == "__main__": raise SystemExit(main())
