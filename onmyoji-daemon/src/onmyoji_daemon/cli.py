from __future__ import annotations

import argparse
import json
from pathlib import Path

from .rpc import call
from .supervisor import Supervisor, endpoint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onmyoji-daemon"); parser.add_argument("--onmyoji-root", required=True, type=Path)
    sub = parser.add_subparsers(dest="action", required=True); sub.add_parser("run"); sub.add_parser("list-services")
    for action in ("start", "stop", "restart", "enable", "disable", "status"):
        command = sub.add_parser(action); command.add_argument("service", nargs="?", default="telegram")
    telegram = sub.add_parser("telegram"); telegram_sub = telegram.add_subparsers(dest="telegram_action", required=True)
    telegram_sub.add_parser("status"); telegram_sub.add_parser("owners"); pairing = telegram_sub.add_parser("pair-request"); pairing.add_argument("--ttl", type=int, default=300)
    send = telegram_sub.add_parser("send"); send.add_argument("chat_id", type=int); send.add_argument("text")
    args = parser.parse_args(argv); root = args.onmyoji_root.resolve()
    if args.action == "run": Supervisor(root).run_forever(); return 0
    try: host, port, token = endpoint(root)
    except (OSError, ValueError, KeyError) as error: parser.error(f"daemon não está em execução nesta instância: {error}")
    if args.action == "list-services": method, params = "list-services", {}
    elif args.action in {"start", "stop", "restart", "enable", "disable", "status"}: method, params = args.action, {"service": args.service}
    else:
        methods = {"status": "telegram.status", "owners": "telegram.owners", "pair-request": "telegram.pair-request", "send": "telegram.send"}
        method = methods[args.telegram_action]; params = {"ttl_seconds": args.ttl} if args.telegram_action == "pair-request" else ({"chat_id": args.chat_id, "text": args.text} if args.telegram_action == "send" else {})
    print(json.dumps(call(host, port, token, method, params), ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
