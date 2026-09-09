from __future__ import annotations

import argparse
import json
from pathlib import Path

from .rpc import call
from .supervisor import Supervisor, endpoint
from . import management
from .launcher import main as interactive_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onmyoji-daemon"); parser.add_argument("--onmyoji-root", required=True, type=Path)
    sub = parser.add_subparsers(dest="action", required=True); sub.add_parser("run"); sub.add_parser("interactive"); sub.add_parser("list-services")
    sub.add_parser("install-instance"); sub.add_parser("remove-instance")
    sub.add_parser("process-status"); sub.add_parser("process-start")
    for action in ("process-stop", "process-force-stop"): sub.add_parser(action)
    install_service = sub.add_parser("install-service"); install_service.add_argument("--name", required=True); install_service.add_argument("--description", required=True)
    sub.add_parser("remove-service"); sub.add_parser("service-status")
    for action in ("service-start", "service-stop", "service-restart", "service-force-stop"): sub.add_parser(action)
    for action in ("start", "stop", "restart", "enable", "disable", "status"):
        command = sub.add_parser(action); command.add_argument("service", nargs="?", default="telegram")
    telegram = sub.add_parser("telegram"); telegram_sub = telegram.add_subparsers(dest="telegram_action", required=True)
    telegram_sub.add_parser("status"); telegram_sub.add_parser("owners"); telegram_sub.add_parser("sync-commands"); pairing = telegram_sub.add_parser("pair-request"); pairing.add_argument("--ttl", type=int, default=300)
    send = telegram_sub.add_parser("send"); send.add_argument("chat_id", type=int); send.add_argument("text")
    miniapps = sub.add_parser("mini-apps"); miniapps_sub = miniapps.add_subparsers(dest="miniapps_action", required=True)
    miniapps_sub.add_parser("status"); miniapps_sub.add_parser("list"); events = miniapps_sub.add_parser("events"); events.add_argument("--id"); events.add_argument("--limit", type=int, default=100)
    create_app = miniapps_sub.add_parser("create"); create_app.add_argument("--request", required=True, help="JSON da publicação")
    inspect_app = miniapps_sub.add_parser("inspect"); inspect_app.add_argument("id")
    update_app = miniapps_sub.add_parser("update"); update_app.add_argument("id"); update_app.add_argument("--values", required=True, help="JSON parcial")
    for action in ("publish", "unpublish", "reload", "delete"):
        command = miniapps_sub.add_parser(action); command.add_argument("id")
    tunnel = miniapps_sub.add_parser("tunnel"); tunnel_sub = tunnel.add_subparsers(dest="tunnel_action", required=True)
    tunnel_sub.add_parser("status")
    config_tunnel = tunnel_sub.add_parser("configure"); config_tunnel.add_argument("--values", required=True)
    provision_tunnel = tunnel_sub.add_parser("provision"); provision_tunnel.add_argument("--confirm", action="store_true")
    for action in ("start", "stop", "restart"): tunnel_sub.add_parser(action)
    install_cloudflared = tunnel_sub.add_parser("install-cloudflared"); install_cloudflared.add_argument("--confirm", action="store_true")
    tls = miniapps_sub.add_parser("tls"); tls_sub = tls.add_subparsers(dest="tls_action", required=True); tls_sub.add_parser("status"); trust = tls_sub.add_parser("install-trust"); trust.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv); root = args.onmyoji_root.resolve()
    if args.action == "run": Supervisor(root).run_forever(); return 0
    if args.action == "interactive": return interactive_main(["--onmyoji-root", str(root)])
    direct = {
        "install-instance": lambda: management.install_instance(root),
        "remove-instance": lambda: management.remove_instance(root),
        "enable": lambda: management.set_enabled(root, args.service, True),
        "disable": lambda: management.set_enabled(root, args.service, False),
        "process-start": lambda: management.start_background(root),
        "process-stop": lambda: management.stop_background(root),
        "process-force-stop": lambda: management.stop_background(root, True),
        "install-service": lambda: management.install_service(root, args.name, args.description),
        "remove-service": lambda: management.remove_service(root),
        "service-status": lambda: management.service_status(root),
        "service-start": lambda: management.service_action(root, "start"),
        "service-stop": lambda: management.service_action(root, "stop"),
        "service-restart": lambda: management.service_action(root, "restart"),
        "service-force-stop": lambda: management.service_action(root, "force"),
    }
    if args.action == "process-status":
        info = management.running_process(root); print(json.dumps(info or {"state": "stopped"}, ensure_ascii=False)); return 0
    if args.action in direct:
        ok, message = direct[args.action](); print(message); return 0 if ok else 2
    try: host, port, token = endpoint(root)
    except (OSError, ValueError, KeyError) as error: parser.error(f"daemon não está em execução nesta instância: {error}")
    if args.action == "list-services": method, params = "list-services", {}
    elif args.action in {"start", "stop", "restart", "enable", "disable", "status"}: method, params = args.action, {"service": args.service}
    elif args.action == "telegram":
        methods = {"status": "telegram.status", "owners": "telegram.owners", "sync-commands": "telegram.sync-commands", "pair-request": "telegram.pair-request", "send": "telegram.send"}
        method = methods[args.telegram_action]; params = {"ttl_seconds": args.ttl} if args.telegram_action == "pair-request" else ({"chat_id": args.chat_id, "text": args.text} if args.telegram_action == "send" else {})
    else:
        action = args.miniapps_action
        if action == "tunnel":
            method = f"mini-apps.tunnel.{args.tunnel_action}"
            params = json.loads(args.values) if args.tunnel_action == "configure" else ({"confirm": True} if args.tunnel_action in {"provision", "install-cloudflared"} and args.confirm else {})
        elif action == "tls":
            method = f"mini-apps.tls.{args.tls_action}"; params = {"confirm": True} if args.tls_action == "install-trust" and args.confirm else {}
        else:
            method = f"mini-apps.{action}"
            if action == "events": params = {"id": args.id, "limit": args.limit}
            elif action == "create": params = json.loads(args.request)
            elif action == "inspect": params = {"id": args.id}
            elif action == "update": params = {"id": args.id, "values": json.loads(args.values)}
            elif action in {"publish", "unpublish", "reload", "delete"}: params = {"id": args.id}
            else: params = {}
    print(json.dumps(call(host, port, token, method, params), ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
