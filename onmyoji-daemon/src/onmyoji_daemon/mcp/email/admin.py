"""Ações interativas de setup executadas no runtime privado do MCP."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .oauth import EmailOAuth
from .service import EmailService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--action", choices=["test", "authorize-local"], required=True)
    parser.add_argument("--connection", choices=["incoming", "smtp"], default="incoming")
    args = parser.parse_args(argv)
    try:
        service = EmailService(args.onmyoji_root)
        if args.action == "test":
            print(json.dumps(service.test_account(args.account), ensure_ascii=False))
        else:
            account, limits = service.account(args.account, enabled=False)
            EmailOAuth(service.root, account, args.connection, limits["timeout_seconds"]).authorize_local()
            print("Consentimento concluído e salvo no vault MCP.")
        return 0
    except KeyboardInterrupt:
        print("Operação interrompida."); return 1
    except Exception:
        print("Não foi possível concluir a operação. Confira conta, acesso ao vault, conexão e consentimento OAuth."); return 1


if __name__ == "__main__": raise SystemExit(main())
