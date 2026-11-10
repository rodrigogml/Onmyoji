"""Ponte privada para gravação OAuth quando o setup não possui pykeepass global."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

from .keepass import CredentialError, KeePassCredentialResolver


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        if importlib.util.find_spec("pykeepass") is None:
            raise CredentialError("dependency_missing", "Prepare o runtime MCP antes da gravação OAuth.")
        raw = sys.stdin.read(1048577)
        if len(raw) > 1048576:
            raise CredentialError("invalid_auth_state", "Estado OAuth excede o limite.")
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) != {"profile", "entry", "state"}:
            raise CredentialError("invalid_auth_state", "Requisição OAuth inválida.")
        KeePassCredentialResolver(args.onmyoji_root).save_oauth_state(request["profile"], request["entry"], request["state"])
        print(json.dumps({"ok": True}))
        return 0
    except (CredentialError, ValueError, OSError, TypeError) as error:
        print(json.dumps({"ok": False, "code": getattr(error, "code", "vault_write_failed")}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
