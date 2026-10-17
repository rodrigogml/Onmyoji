#!/usr/bin/env python3
"""CLI unidirecional da skill Telegram via RPC local do daemon."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "onmyoji-daemon" / "src"))

from onmyoji_daemon.rpc import call
from onmyoji_daemon.supervisor import endpoint


def invoke(action: str, args: argparse.Namespace, rpc=call) -> object:
    host, port, token = endpoint(ROOT)
    if action == "list":
        result = rpc(host, port, token, "telegram.channels.list", timeout=10)
        query = args.query.casefold().strip()
        if query:
            result = [channel for channel in result if query in str(channel.get("name", "")).casefold()]
        return {"ok": True, "channels": result}
    if action == "remove":
        result = rpc(host, port, token, "telegram.channels.remove", {"channel": args.channel}, timeout=10)
        return {"ok": True, **result}
    if action == "send":
        if not args.text and not args.file: raise ValueError("Informe --text e/ou --file.")
        params = {"channel": args.channel, "text": args.text or ""}
        if args.file:
            params.update({"path": str(Path(args.file).expanduser().resolve(strict=True)), "kind": args.kind})
        if args.new_topic: params["new_topic"] = args.new_topic
        result = rpc(host, port, token, "telegram.outbound.send", params, timeout=120)
        return {"ok": True, **result}
    raise ValueError("Operação desconhecida.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    listing = commands.add_parser("list"); listing.add_argument("--query", default="")
    remove = commands.add_parser("remove"); remove.add_argument("--channel", required=True)
    send = commands.add_parser("send")
    send.add_argument("--channel", required=True)
    send.add_argument("--text", default="")
    send.add_argument("--file")
    send.add_argument("--kind", choices=["document", "photo", "audio", "voice"], default="document")
    send.add_argument("--new-topic", default="")
    args = parser.parse_args()
    try:
        value = invoke(args.action, args)
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(json.dumps({"ok": False, "error": {"code": "telegram_error", "message": str(error)}}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
