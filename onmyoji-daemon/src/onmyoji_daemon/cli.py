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


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _wait_publication_operation(host: str, port: int, token: str, response):
    """Acompanha operações longas sem manter uma conexão RPC bloqueada."""
    if isinstance(response, dict) and response.get("operation_id"):
        print("› Operação: " + str(response["operation_id"]), file=sys.stderr, flush=True)
    started = time.monotonic()
    deadline, previous_progress, last_notice = started + 900, None, started
    while isinstance(response, dict) and response.get("operation_id") and response.get("state") in {"queued", "running", "pending"}:
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError("A operação continua em execução; consulte operation-status com seu identificador.")
        progress = response.get("progress")
        if progress and progress != previous_progress:
            print("› " + str(progress), file=sys.stderr, flush=True)
            previous_progress = progress
            last_notice = now
        elif now - last_notice >= 15:
            print("› Ainda em andamento: " + str(progress or "aguardando resposta da operação") + ".", file=sys.stderr, flush=True)
            last_notice = now
        time.sleep(1)
        response = call(host, port, token, "cloudflare-tunnel.operations.status", {"operation_id": response["operation_id"]}, timeout=5)
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onmyoji-daemon"); parser.add_argument("--onmyoji-root", required=True, type=Path)
    sub = parser.add_subparsers(dest="action", required=True); sub.add_parser("run"); sub.add_parser("interactive"); sub.add_parser("list-services")
    from .task_scheduler.cli import register as register_task_cli
    register_task_cli(sub)
    sub.add_parser("prepare-instance"); sub.add_parser("install-instance"); install_runtime = sub.add_parser("install-runtime"); install_runtime.add_argument("--repair", action="store_true"); sub.add_parser("remove-instance")
    sub.add_parser("publication-diagnose", help="Diagnostica Gateway/Tunnel sem iniciar serviços ou alterar arquivos")
    sub.add_parser("process-status"); sub.add_parser("process-start")
    for action in ("process-stop", "process-force-stop"): sub.add_parser(action)
    install_service = sub.add_parser("install-service"); install_service.add_argument("--name", required=True); install_service.add_argument("--description", required=True); install_service.add_argument("--scope", choices=["user", "system"], default="system"); install_service.add_argument("--run-as-user", default=""); install_service.add_argument("--run-as-group", default=""); install_service.add_argument("--use-sudo", action="store_true"); install_service.add_argument("--windows-account", default=""); install_service.add_argument("--password-stdin", action="store_true"); install_service.add_argument("--isolate-linux", action="store_true"); install_service.add_argument("--installer-user", default=""); install_service.add_argument("--installer-access", action="store_true"); install_service.add_argument("--workspace", default=""); install_service.add_argument("--resource-limits", type=json.loads, default=None)
    service_account = sub.add_parser("configure-windows-service-account"); service_account.add_argument("--account", required=True); service_account.add_argument("--password-stdin", action="store_true")
    remove_service = sub.add_parser("remove-service"); remove_service.add_argument("--use-sudo", action="store_true"); sub.add_parser("service-status")
    for action in ("service-start", "service-stop", "service-restart", "service-force-stop"): sub.add_parser(action).add_argument("--use-sudo", action="store_true")
    resources = sub.add_parser("configure-service-resources"); resources.add_argument("--values", type=json.loads, required=True); resources.add_argument("--use-sudo", action="store_true")
    repair_permissions = sub.add_parser("repair-service-permissions"); repair_permissions.add_argument("--use-sudo", action="store_true")
    service_security = sub.add_parser("service-security"); service_security.add_argument("--use-sudo", action="store_true")
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
    publication_test = gateway_sub.add_parser("publication-test", help="Confirma a chegada ao Gateway pelo link público temporário")
    publication_test_sub = publication_test.add_subparsers(dest="publication_test_action", required=True)
    publication_test_sub.add_parser("start").add_argument("--confirm", action="store_true")
    for action in ("status", "cancel"):
        publication_test_sub.add_parser(action).add_argument("test_id")
    listeners = gateway_sub.add_parser("listeners"); listeners_sub = listeners.add_subparsers(dest="listener_action", required=True)
    listeners_sub.add_parser("list")
    for action in ("inspect", "enable", "disable", "remove", "restore"):
        listener = listeners_sub.add_parser(action); listener.add_argument("path_prefix"); listener.add_argument("--owner"); listener.add_argument("--confirm", action="store_true")
    tunnel = sub.add_parser("cloudflare-tunnel"); tunnel_sub = tunnel.add_subparsers(dest="tunnel_action", required=True)
    tunnel_sub.add_parser("status")
    inspect_publication = tunnel_sub.add_parser("inspect-publication", help="Consulta DNS e Tunnel remoto e prepara uma prévia sem alterar a Cloudflare")
    inspect_publication.add_argument("--wait", action="store_true")
    for action in ("adopt-publication", "rollback-publication"):
        recovery = tunnel_sub.add_parser(action)
        recovery.add_argument("--preview-id", required=True)
        recovery.add_argument("--confirm", action="store_true")
        recovery.add_argument("--wait", action="store_true")
    config_tunnel = tunnel_sub.add_parser("configure"); config_tunnel.add_argument("--values", required=True)
    for action in ("provision", "install-cloudflared", "update-cloudflared", "rollback-cloudflared"):
        operation = tunnel_sub.add_parser(action); operation.add_argument("--confirm", action="store_true"); operation.add_argument("--wait", action="store_true")
        if action in {"install-cloudflared", "update-cloudflared"}:
            operation.add_argument("--install-directory"); operation.add_argument("--version"); operation.add_argument("--adopt", action="store_true")
    operation = tunnel_sub.add_parser("operation-status"); operation.add_argument("operation_id")
    reuse = tunnel_sub.add_parser("use-cloudflared"); reuse.add_argument("--executable", required=True); reuse.add_argument("--confirm", action="store_true"); reuse.add_argument("--wait", action="store_true")
    migration = sub.add_parser("publication-migrate"); migration.add_argument("migration_action", choices=["inspect", "apply", "rollback"]); migration.add_argument("--confirm", action="store_true")
    tls = miniapps_sub.add_parser("tls"); tls_sub = tls.add_subparsers(dest="tls_action", required=True); tls_sub.add_parser("status"); trust = tls_sub.add_parser("install-trust"); trust.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv); root = args.onmyoji_root.resolve()
    def install_service_action():
        if args.resource_limits is not None and not isinstance(args.resource_limits, dict): return False, "--resource-limits deve conter um objeto JSON."
        return management.install_service(root, args.name, args.description, args.scope, args.run_as_user, args.run_as_group, args.use_sudo, args.windows_account, sys.stdin.read().rstrip("\r\n") if args.password_stdin else "", lambda message: print("› " + message, flush=True), isolate_linux=args.isolate_linux, installer_user=args.installer_user, installer_access=args.installer_access, workspace=args.workspace, resource_limits=args.resource_limits)
    if args.action == "run": Supervisor(root).run_forever(); return 0
    if args.action == "interactive":
        from .launcher import main as interactive_main
        return interactive_main(["--onmyoji-root", str(root)])
    if args.action == "publication-diagnose":
        from .publication_diagnostics import diagnose
        response = diagnose(root, progress=lambda message: print("› " + message, file=sys.stderr, flush=True))
        print(json.dumps(response, ensure_ascii=False)); return 0
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
        "install-runtime": lambda: management.ensure_runtime(root, show_progress=True, repair=args.repair),
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
        "configure-service-resources": lambda: management.configure_service_resources(root, args.values, args.use_sudo) if isinstance(args.values, dict) else (False, "--values deve conter um objeto JSON."),
        "repair-service-permissions": lambda: management.repair_service_permissions(root, args.use_sudo, lambda message: print("› " + message, flush=True)),
        "service-security": lambda: management.service_security(root, args.use_sudo),
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
    new_timeout = None
    if args.action in {"task", "notification"}:
        from .task_scheduler.cli import request as task_request
        try:
            method, params, new_timeout = task_request(args)
        except (OSError, ValueError, RpcError) as error:
            print(f"Erro: {error}", file=sys.stderr)
            return 2
    elif args.action == "list-services": method, params = "list-services", {}
    elif args.action in {"start", "stop", "restart", "enable", "disable", "status"}: method, params = args.action, {"service": args.service}
    elif args.action == "btg-monitor": method, params = "btg-monitor.status", {}
    elif args.action == "gateway":
        method, params = "gateway." + args.gateway_action, {}
        if args.gateway_action == "configure":
            try: values = json.loads(args.values)
            except ValueError: parser.error("--values deve conter um objeto JSON válido.")
            if not isinstance(values, dict): parser.error("--values deve conter um objeto JSON.")
            params = {**values, "confirm": args.confirm}
        elif args.gateway_action == "publication-test":
            method = "gateway.publication-test." + args.publication_test_action
            params = {"confirm": args.confirm} if args.publication_test_action == "start" else {"test_id": args.test_id}
        elif args.gateway_action == "listeners":
            method = "gateway.listeners." + args.listener_action
            if args.listener_action != "list":
                params = {"path_prefix": args.path_prefix, "confirm": args.confirm}
                if args.owner: params["owner"] = args.owner
    elif args.action == "cloudflare-tunnel":
        method, params = "cloudflare-tunnel." + args.tunnel_action, {}
        if args.tunnel_action in {"provision", "install-cloudflared", "update-cloudflared", "rollback-cloudflared", "use-cloudflared", "adopt-publication", "rollback-publication"}:
            params = {"confirm": args.confirm}
            if args.tunnel_action in {"adopt-publication", "rollback-publication"}: params["preview_id"] = args.preview_id
            if args.tunnel_action == "use-cloudflared": params["executable"] = args.executable
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
    if args.action in {"task", "notification"}:
        timeout = new_timeout
    if args.action == "mini-apps" and args.miniapps_action == "oauth": timeout = 200 if args.oauth_action in {"start", "cancel", "info", "active", "cancel-active"} else 5
    try:
        response = call(host, port, token, method, params, timeout=timeout)
        if args.action == "cloudflare-tunnel" and getattr(args, "wait", False):
            response = _wait_publication_operation(host, port, token, response)
    except (OSError, RpcError, TimeoutError) as error:
        if args.action == "notification" and args.notification_action == "submit" and (not isinstance(error, RpcError) or error.code == "acceptance_unknown"):
            print("Erro: acompanhamento perdido; o evento pode ter sido aceito e continua registrado. Não reenvie automaticamente.", file=sys.stderr)
            return 2
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
    if args.action == "notification" and args.notification_action == "submit":
        print(json.dumps(response, ensure_ascii=False))
        return 0 if response.get("execution") == "completed" and response.get("delivery") == "confirmed" else 2
    print(json.dumps(response, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
