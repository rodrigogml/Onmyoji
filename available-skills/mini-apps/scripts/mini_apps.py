#!/usr/bin/env python3
"""Wrapper da skill Mini Apps; conversa somente com o daemon local."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("action", choices=["status", "list", "events", "create", "inspect", "update", "publish", "unpublish", "reload", "delete", "tunnel", "tls"])
    parser.add_argument("--id"); parser.add_argument("--limit", type=int, default=100); parser.add_argument("--request"); parser.add_argument("--values"); parser.add_argument("--tunnel-action", choices=["status", "configure", "provision", "start", "stop", "restart", "install-cloudflared"]); parser.add_argument("--tls-action", choices=["status", "install-trust"]); parser.add_argument("--confirm", action="store_true"); args = parser.parse_args()
    command = [sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(ROOT), "mini-apps"] + ([] if args.action in {"tunnel", "tls"} else [args.action])
    if args.action in {"inspect", "publish", "unpublish", "reload", "delete"}:
        if not args.id: parser.error("--id é obrigatório")
        command.append(args.id)
    if args.action == "events":
        if args.id: command += ["--id", args.id]
        command += ["--limit", str(args.limit)]
    if args.action == "create":
        if not args.request: parser.error("--request é obrigatório")
        command += ["--request", args.request]
    if args.action == "update":
        if not args.id or not args.values: parser.error("--id e --values são obrigatórios")
        command += [args.id, "--values", args.values]
    if args.action == "tunnel":
        if not args.tunnel_action: parser.error("--tunnel-action é obrigatório")
        command += ["tunnel", args.tunnel_action]
        if args.tunnel_action == "configure":
            if not args.values: parser.error("--values é obrigatório para configurar tunnel")
            command += ["--values", args.values]
        if args.tunnel_action in {"provision", "install-cloudflared"} and args.confirm: command.append("--confirm")
    if args.action == "tls":
        if not args.tls_action: parser.error("--tls-action é obrigatório")
        command += ["tls", args.tls_action]
        if args.tls_action == "install-trust" and args.confirm: command.append("--confirm")
    # A skill deve funcionar tanto na instalacao do daemon quanto diretamente
    # a partir do checkout do Onmyoji.
    environment = os.environ.copy()
    source = str(ROOT / "onmyoji-daemon" / "src")
    environment["PYTHONPATH"] = source + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    result = subprocess.run(command, cwd=ROOT, text=True, check=False, env=environment)
    return result.returncode


if __name__ == "__main__": raise SystemExit(main())
