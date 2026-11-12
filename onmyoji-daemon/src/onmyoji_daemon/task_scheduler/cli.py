"""CLI de consulta; definições são editadas como TOML no diretório observado."""
import json
from pathlib import Path
import sys

from ..rpc import RpcError


def register(subparsers):
    notification = subparsers.add_parser("notification")
    actions = notification.add_subparsers(dest="notification_action", required=True)
    for name in ("status", "diagnose"):
        actions.add_parser(name)
    submit = actions.add_parser("submit")
    submit.add_argument("--file", required=True, help="Arquivo JSON ou - para stdin")
    submit.add_argument("--wait-timeout", type=float, default=None)
    configure = actions.add_parser("configure")
    configure.add_argument("--file", required=True)

    task = subparsers.add_parser("task")
    actions = task.add_subparsers(dest="task_action", required=True)
    actions.add_parser("status")
    for name in ("get",):
        action = actions.add_parser(name); action.add_argument("id")
    listing = actions.add_parser("list")
    listing.add_argument("--sort", default="next_run_at")
    listing.add_argument("--descending", action="store_true")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--limit", type=int, default=20)
    history = actions.add_parser("history")
    history.add_argument("--id"); history.add_argument("--offset", type=int, default=0); history.add_argument("--limit", type=int, default=20)
    preview = actions.add_parser("preview")
    preview.add_argument("--id", required=True)
    preview.add_argument("--count", type=int, default=5)
    resolve = actions.add_parser("resolve")
    resolve.add_argument("id"); resolve.add_argument("--reason", required=True); resolve.add_argument("--confirm", action="store_true")


def read_json(filename):
    value = json.loads(sys.stdin.read() if filename == "-" else Path(filename).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RpcError("O JSON deve conter um objeto.", "invalid_argument")
    return value


def request(args):
    if args.action == "notification":
        action = args.notification_action
        timeout = args.wait_timeout if action == "submit" else 5
        if timeout is not None and timeout <= 0:
            raise RpcError("Prazo de acompanhamento deve ser positivo.", "invalid_argument")
        params = read_json(args.file) if action in {"submit", "configure"} else {}
        if action == "submit":
            from ..agent_notification.contract import validate_event
            params = validate_event(params)
        return "notification." + action, params, timeout
    action = args.task_action
    params = {}
    if hasattr(args, "id") and args.id:
        params["id"] = args.id
    if action == "list":
        params.update(sort=args.sort, descending=args.descending, offset=args.offset, limit=args.limit)
    if action == "history":
        params.update(offset=args.offset, limit=args.limit)
    if action == "preview":
        params["count"] = args.count
    if action == "resolve":
        params.update(reason=args.reason, confirm=args.confirm)
    return "task." + action, params, 5
