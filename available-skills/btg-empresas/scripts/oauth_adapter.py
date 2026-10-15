#!/usr/bin/env python3
"""Adaptador BTG para o callback compartilhado do Gateway Mini Apps."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from client import Client, context, load_profile
from contracts import BtgError, digest, require


def dispatch(root: Path, request: dict) -> dict:
    require(isinstance(request, dict) and request.get("version") == 1, "invalid_request", "Contrato OAuth v1 obrigatório.")
    require(set(request) <= {"version", "action", "profile", "state", "sessionId", "redirectUri", "code"}, "invalid_request", "Campos OAuth desconhecidos.")
    profile = load_profile(root / "configs" / "btg-empresas.toml", request["profile"])
    client = Client(profile)
    action = request.get("action")
    if action == "describe":
        return {"issuer": client.id_base, "authorizationEndpoint": client.id_base + "/oauth2/authorize", "redirectUri": profile["redirect_uri"], "workspace": profile["workspace"], "binding": digest({"oauth": client.binding(), "account": context(profile), "vault": profile["vault_entry_path"], "vaultProfile": profile["vault_profile"], "scopes": profile["scopes"], "workspace": str(Path(profile["workspace"]).resolve()), "mode": profile["oauth_mode"]}), "requireIssuer": True}
    require(profile["oauth_mode"] == "remote", "remote_mode_required", "Selecione oauth_mode=remote.")
    if action in {"start", "complete"}:
        require(request.get("redirectUri") == profile["redirect_uri"], "redirect_mismatch", "Callback diverge do perfil.")
    if action == "start":
        return client.auth_start(state=request["state"], session_id=request["sessionId"])
    if action == "complete":
        return client.auth_complete(request["code"], request["state"], session_id=request["sessionId"], verify_account=True)
    if action == "cancel":
        return client.auth_cancel(request["sessionId"])
    raise BtgError("unsupported_operation", "Ação OAuth não suportada.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        raw = sys.stdin.read(16385)
        require(len(raw) <= 16384, "invalid_request", "Requisição OAuth excede limite.")
        result, code = {"ok": True, "data": dispatch(args.onmyoji_root, json.loads(raw))}, 0
    except (BtgError, OSError, ValueError, KeyError, TypeError, AttributeError):
        result, code = {"ok": False, "error": {"code": "oauth_adapter_failed", "message": "Confira perfil, consentimento e credenciais BTG."}}, 2
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
