from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

from .rpc import RpcError, call
from .supervisor import Supervisor, endpoint
from . import management
from .launcher import main as interactive_main


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _wait_publication_operation(host: str, port: int, token: str, response):
    """Acompanha operações longas sem manter uma conexão RPC bloqueada."""
    if isinstance(response, dict) and response.get("operation_id"):
        print("› Operação: " + str(response["operation_id"]), file=sys.stderr, flush=True)
    deadline, previous_progress = time.monotonic() + 900, None
    while isinstance(response, dict) and response.get("operation_id") and response.get("state") in {"queued", "running", "pending"}:
        if time.monotonic() >= deadline:
            raise TimeoutError("A operação continua em execução; consulte operation-status com seu identificador.")
        progress = response.get("progress")
        if progress and progress != previous_progress:
            print("› " + str(progress), file=sys.stderr, flush=True)
            previous_progress = progress
        time.sleep(1)
        response = call(host, port, token, "cloudflare-tunnel.operations.status", {"operation_id": response["operation_id"]}, timeout=5)
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onmyoji-daemon"); parser.add_argument("--onmyoji-root", required=True, type=Path)
    sub = parser.add_subparsers(dest="action", required=True); sub.add_parser("run"); sub.add_parser("interactive"); sub.add_parser("list-services")
    sub.add_parser("prepare-instance"); sub.add_parser("install-instance"); sub.add_parser("install-runtime"); sub.add_parser("remove-instance")
    sub.add_parser("process-status"); sub.add_parser("process-start")
    for action in ("process-stop", "process-force-stop"): sub.add_parser(action)
    install_service = sub.add_parser("install-service"); install_service.add_argument("--name", required=True); install_service.add_argument("--description", required=True); install_service.add_argument("--scope", choices=["user", "system"], default="system"); install_service.add_argument("--run-as-user", default=""); install_service.add_argument("--run-as-group", default=""); install_service.add_argument("--use-sudo", action="store_true"); install_service.add_argument("--windows-account", default=""); install_service.add_argument("--password-stdin", action="store_true")
    service_account = sub.add_parser("configure-windows-service-account"); service_account.add_argument("--account", required=True); service_account.add_argument("--password-stdin", action="store_true")
    remove_service = sub.add_parser("remove-service"); remove_service.add_argument("--use-sudo", action="store_true"); sub.add_parser("service-status")
    for action in ("service-start", "service-stop", "service-restart", "service-force-stop"): sub.add_parser(action).add_argument("--use-sudo", action="store_true")
    for action in ("start", "stop", "restart", "enable", "disable", "status"):
        command = sub.add_parser(action); command.add_argument("service", nargs="?", default="telegram")
    telegram = sub.add_parser("telegram"); telegram_sub = telegram.add_subparsers(dest="telegram_action", required=True)
    telegram_sub.add_parser("status"); telegram_sub.add_parser("owners"); telegram_sub.add_parser("sync-commands"); pairing = telegram_sub.add_parser("pair-request"); pairing.add_argument("--ttl", type=int, default=300)
    send = telegram_sub.add_parser("send"); send.add_argument("chat_id", type=int); send.add_argument("text")
    btg_monitor = sub.add_parser("btg-monitor"); btg_monitor.add_argument("btg_action", choices=["status"])
    miniapps = sub.add_parser("mini-apps"); miniapps_sub = miniapps.add_subparsers(dest="miniapps_action", required=True)
    miniapps_sub.add_parser("status"); miniapps_sub.add_parser("diagnose"); miniapps_sub.add_parser("list"); events = miniapps_sub.add_parser("events"); events.add_argument("--id"); events.add_argument("--limit", type=int, default=100)
    oauth = miniapps_sub.add_parser("oauth"); oauth_sub = oauth.add_subparsers(dest="oauth_action", required=True)
    for action in ("callback-url", "info", "active", "start", "status", "cancel", "cancel-active"):
        command = oauth_sub.add_parser(action)
        command.add_argument("--profile", required=action in {"info", "active", "start", "cancel-active"})
        command.add_argument("--session-id", required=action in {"status", "cancel"})
        command.add_argument("--confirm", action="store_true")
    create_app = miniapps_sub.add_parser("create"); create_app.add_argument("--request", required=True, help="JSON da publicação")
    inspect_app = miniapps_sub.add_parser("inspect"); inspect_app.add_argument("id")
    update_app = miniapps_sub.add_parser("update"); update_app.add_argument("id"); update_app.add_argument("--values", required=True, help="JSON parcial")
    for action in ("publish", "unpublish", "reload", "delete"):
        command = miniapps_sub.add_parser(action); command.add_argument("id")
    gateway = sub.add_parser("gateway"); gateway_sub = gateway.add_subparsers(dest="gateway_action", required=True)
    gateway_sub.add_parser("status")
    gateway_configure = gateway_sub.add_parser("configure"); gateway_configure.add_argument("--values", required=True); gateway_configure.add_argument("--confirm", action="store_true")
    listeners = gateway_sub.add_parser("listeners"); listeners_sub = listeners.add_subparsers(dest="listener_action", required=True)
    listeners_sub.add_parser("list")
    for action in ("inspect", "enable", "disable", "remove", "restore"):
        listener = listeners_sub.add_parser(action); listener.add_argument("path_prefix"); listener.add_argument("--owner"); listener.add_argument("--confirm", action="store_true")
    tunnel = sub.add_parser("cloudflare-tunnel"); tunnel_sub = tunnel.add_subparsers(dest="tunnel_action", required=True)
    tunnel_sub.add_parser("status")
    config_tunnel = tunnel_sub.add_parser("configure"); config_tunnel.add_argument("--values", required=True)
    for action in ("provision", "install-cloudflared", "update-cloudflared", "rollback-cloudflared"):
        operation = tunnel_sub.add_parser(action); operation.add_argument("--confirm", action="store_true"); operation.add_argument("--wait", action="store_true")
        if action in {"install-cloudflared", "update-cloudflared"}:
            operation.add_argument("--install-directory"); operation.add_argument("--version"); operation.add_argument("--adopt", action="store_true")
    operation = tunnel_sub.add_parser("operation-status"); operation.add_argument("operation_id")
    migration = sub.add_parser("publication-migrate"); migration.add_argument("migration_action", choices=["inspect", "apply", "rollback"]); migration.add_argument("--confirm", action="store_true")
    tls = miniapps_sub.add_parser("tls"); tls_sub = tls.add_subparsers(dest="tls_action", required=True); tls_sub.add_parser("status"); trust = tls_sub.add_parser("install-trust"); trust.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv); root = args.onmyoji_root.resolve()
    def install_service_action():
        return management.install_service(root, args.name, args.description, args.scope, args.run_as_user, args.run_as_group, args.use_sudo, args.windows_account, sys.stdin.read().rstrip("\r\n") if args.password_stdin else "", lambda message: print("› " + message, flush=True))
    if args.action == "run": Supervisor(root).run_forever(); return 0
    if args.action == "interactive": return interactive_main(["--onmyoji-root", str(root)])
    if args.action == "publication-migrate":
        from .publication_migration import PublicationMigration, MigrationError
        try:
            migration = PublicationMigration(root, progress=lambda message: print("› " + message, file=sys.stderr, flush=True))
            response = migration.inspect() if args.migration_action == "inspect" else getattr(migration, args.migration_action)(args.confirm)
        except (MigrationError, OSError, ValueError) as error:
            print(f"Erro: {error}", file=sys.stderr); return 2
        print(json.dumps(response, ensure_ascii=False)); return 0
    if args.action == "cloudflare-tunnel" and args.tunnel_action == "configure":
        from .cloudflare_tunnel import TunnelConfiguration, TunnelError
        try:
            values = json.loads(args.values)
            if not isinstance(values, dict): raise ValueError("--values deve conter um objeto JSON.")
            try: address = endpoint(root)
            except FileNotFoundError: address = None
            if address is not None and call(*address, "status", {"service": "cloudflare-tunnel"}, timeout=5).get("state") == "running":
                # A configuração de um serviço ativo participa da mesma fila de
                # provisionamento e instalação; o CLI não escreve por fora dela.
                response = call(*address, "cloudflare-tunnel.configure", {**values, "confirm": True}, timeout=5)
                response = _wait_publication_operation(*address, response)
            else:
                response = TunnelConfiguration(root).configure(values)
        except (TunnelError, RpcError, OSError, ValueError) as error:
            print(f"Erro: {error}", file=sys.stderr); return 2
        print(json.dumps(response, ensure_ascii=False))
        return 2 if isinstance(response, dict) and response.get("state") in {"failed", "interrupted", "cancelled"} else 0
    direct = {
        "prepare-instance": lambda: management.prepare_instance(root),
        "install-instance": lambda: management.install_instance(root, show_progress=True),
        "install-runtime": lambda: management.ensure_runtime(root, show_progress=True),
        "remove-instance": lambda: management.remove_instance(root),
        "enable": lambda: management.set_enabled(root, args.service, True),
        "disable": lambda: management.set_enabled(root, args.service, False),
        "process-start": lambda: management.start_background(root),
        "process-stop": lambda: management.stop_background(root),
        "process-force-stop": lambda: management.stop_background(root, True),
        "install-service": install_service_action,
        "configure-windows-service-account": lambda: management.configure_windows_service_account(root, args.account, sys.stdin.read().rstrip("\r\n") if args.password_stdin else ""),
        "remove-service": lambda: management.remove_service(root, args.use_sudo),
        "service-status": lambda: management.service_status(root),
        "service-start": lambda: management.service_action(root, "start", args.use_sudo),
        "service-stop": lambda: management.service_action(root, "stop", args.use_sudo),
        "service-restart": lambda: management.service_action(root, "restart", args.use_sudo),
        "service-force-stop": lambda: management.service_action(root, "force", args.use_sudo),
    }
    if args.action == "process-status":
        info = management.running_process(root); print(json.dumps(info or {"state": "stopped"}, ensure_ascii=False)); return 0
    if args.action in direct:
        ok, message = direct[args.action](); print(message, flush=True)
        code = 0 if ok else 2
        if args.action == "install-service" and sys.platform == "win32":
            # O registrador pywin32 pode manter threads nativas após concluir a
            # instalação. Este CLI é somente o processo administrativo; o
            # serviço real já foi delegado a pythonservice.exe.
            os._exit(code)
        return code
    try: host, port, token = endpoint(root)
    except (OSError, ValueError, KeyError) as error: parser.error(f"daemon não está em execução nesta instância: {error}")
    if args.action == "list-services": method, params = "list-services", {}
    elif args.action in {"start", "stop", "restart", "enable", "disable", "status"}: method, params = args.action, {"service": args.service}
    elif args.action == "btg-monitor": method, params = "btg-monitor.status", {}
    elif args.action == "gateway":
        method, params = "gateway." + args.gateway_action, {}
        if args.gateway_action == "configure":
            try: values = json.loads(args.values)
            except ValueError: parser.error("--values deve conter um objeto JSON válido.")
            if not isinstance(values, dict): parser.error("--values deve conter um objeto JSON.")
            params = {**values, "confirm": args.confirm}
        elif args.gateway_action == "listeners":
            method = "gateway.listeners." + args.listener_action
            if args.listener_action != "list":
                params = {"path_prefix": args.path_prefix, "confirm": args.confirm}
                if args.owner: params["owner"] = args.owner
    elif args.action == "cloudflare-tunnel":
        method, params = "cloudflare-tunnel." + args.tunnel_action, {}
        if args.tunnel_action in {"provision", "install-cloudflared", "update-cloudflared", "rollback-cloudflared"}:
            params = {"confirm": args.confirm}
            if args.tunnel_action in {"install-cloudflared", "update-cloudflared"}:
                for key in ("install_directory", "version"):
                    if getattr(args, key): params[key] = getattr(args, key)
                if args.adopt: params["adopt"] = True
        elif args.tunnel_action == "operation-status": method, params = "cloudflare-tunnel.operations.status", {"operation_id": args.operation_id}
    elif args.action == "telegram":
        methods = {"status": "telegram.status", "owners": "telegram.owners", "sync-commands": "telegram.sync-commands", "pair-request": "telegram.pair-request", "send": "telegram.send"}
        method = methods[args.telegram_action]; params = {"ttl_seconds": args.ttl} if args.telegram_action == "pair-request" else ({"chat_id": args.chat_id, "text": args.text} if args.telegram_action == "send" else {})
    else:
        action = args.miniapps_action
        if action == "oauth":
            method = "mini-apps.oauth." + args.oauth_action
            params = {"confirm": args.confirm}
            if args.profile: params["profile"] = args.profile
            if args.session_id: params["session_id"] = args.session_id
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
    timeout = 20 if args.action in {"start", "stop", "restart"} else (30 if args.action == "mini-apps" and args.miniapps_action == "tls" and args.tls_action == "install-trust" else 5)
    if args.action == "mini-apps" and args.miniapps_action == "oauth": timeout = 200 if args.oauth_action in {"start", "cancel", "info", "active", "cancel-active"} else 5
    try:
        response = call(host, port, token, method, params, timeout=timeout)
        if args.action == "cloudflare-tunnel" and getattr(args, "wait", False):
            response = _wait_publication_operation(host, port, token, response)
    except (OSError, RpcError, TimeoutError) as error:
        if args.action in {"gateway", "cloudflare-tunnel"} and isinstance(error, RpcError) and isinstance(error.code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", error.code):
            failure = {"code": error.code, "message": str(error)}
            prefix = error.details.get("path_prefix") if isinstance(error.details, dict) else None
            if isinstance(prefix, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", prefix): failure["details"] = {"path_prefix": prefix}
            print(json.dumps({"error": failure}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"Erro: {error}", file=sys.stderr)
        return 2
    if args.action in {"start", "restart"} and isinstance(response, dict) and response.get("state") == "failed":
        print(f"Erro: {response.get('last_error') or 'serviço não iniciou'}", file=sys.stderr)
        return 2
    if args.action == "cloudflare-tunnel" and isinstance(response, dict) and response.get("state") in {"failed", "interrupted", "cancelled"}:
        print(json.dumps(response, ensure_ascii=False)); return 2
    print(json.dumps(response, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
