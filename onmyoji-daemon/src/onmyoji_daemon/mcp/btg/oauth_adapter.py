"""Adaptador fechado do BTG MCP para o callback do Gateway."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .client import Client, context, load_profile, storage_root
from .contracts import digest, require
from .exception import BtgError


def dispatch(root: Path, request: dict) -> dict:
    require(isinstance(request, dict) and request.get("version") == 1, "invalid_request", "Contrato OAuth v1 obrigatório.")
    require(set(request) <= {"version", "action", "profile", "state", "sessionId", "redirectUri", "code"}, "invalid_request", "Campos OAuth desconhecidos.")
    profile = load_profile(root / "configs" / "mcp" / "btg-empresas.toml", request["profile"])
    client = Client(profile)
    action = request.get("action")
    if action == "describe":
        state_root = storage_root(Path(profile["_config"]))
        return {"issuer": client.id_base, "authorizationEndpoint": client.id_base + "/oauth2/authorize", "redirectUri": profile["redirect_uri"], "storage_root": str(state_root), "binding": digest({"oauth": client.binding(), "account": context(profile), "vault": profile["credential_entry_path"], "vaultProfile": profile["credential_profile"], "scopes": profile["scopes"], "storage": str(state_root), "mode": profile["oauth_mode"]}), "requireIssuer": True}
    require(profile["oauth_mode"] == "remote", "remote_mode_required", "Use OAuth remoto para o BTG MCP.")
    if action in {"start", "complete"}:
        require(bool(profile["redirect_uri"]) and request.get("redirectUri") == profile["redirect_uri"], "redirect_mismatch", "Callback diverge do perfil MCP.")
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
    except (BtgError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        result, code = {"ok": False, "error": {"code": getattr(error, "code", "oauth_adapter_failed"), "message": "Confira perfil, consentimento e credenciais BTG MCP."}}, 2
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
