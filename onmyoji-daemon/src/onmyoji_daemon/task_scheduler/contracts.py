"""Definições fechadas e snapshots imutáveis de ações."""
from copy import deepcopy
import json
from pathlib import Path

from .calendar import validate_rule
from ..agent_notification.contract import validate_event, uuid7
from ..rpc import RpcError


def validate_definition(value, workspace: Path, now):
    if not isinstance(value, dict) or set(value) - {"schema_version", "name", "enabled", "rule", "action"} or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise RpcError("Definição inválida ou versão não suportada.", "invalid_schedule")
    if not isinstance(value.get("name"), str) or not value["name"].strip() or len(value["name"]) > 200 or type(value.get("enabled", False)) is not bool:
        raise RpcError("Nome ou estado inválido.", "invalid_schedule")
    result = deepcopy(value)
    result.setdefault("enabled", False)
    result["rule"] = validate_rule(result.get("rule"), now)
    action = result.get("action")
    if not isinstance(action, dict):
        raise RpcError("Ação inválida.", "invalid_action")
    if action.get("type") == "notification":
        if set(action) != {"type", "event"} or not isinstance(action["event"], dict) or "event_id" in action["event"]:
            raise RpcError("Ação de notificação exige envelope sem event_id.", "invalid_action")
        validate_event({**action["event"], "event_id": uuid7()})
    elif action.get("type") == "process":
        if set(action) - {"type", "executable", "arguments", "cwd", "timeout_seconds"}:
            raise RpcError("Campos de processo inválidos.", "invalid_action")
        executable, cwd = action.get("executable"), action.get("cwd")
        if not isinstance(executable, str) or not Path(executable).is_absolute() or not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise RpcError("Executável e cwd devem ser absolutos.", "invalid_action")
        if not Path(cwd).resolve().is_relative_to(workspace.resolve()) or not Path(cwd).is_dir():
            raise RpcError("cwd deve existir dentro do workspace.", "invalid_action")
        if not isinstance(action.get("arguments", []), list) or any(not isinstance(arg, str) or "\x00" in arg for arg in action.get("arguments", [])):
            raise RpcError("Argumentos devem ser uma lista de textos.", "invalid_action")
        timeout = action.get("timeout_seconds", 900)
        if type(timeout) is not int or not 1 <= timeout <= 604800:
            raise RpcError("Timeout deve ser de 1 a 604800 segundos.", "invalid_action")
        action.setdefault("arguments", [])
        if len(json.dumps([executable, *action["arguments"]]).encode("utf-8")) + 1 > 1_048_576:
            raise RpcError("Comando excede o limite de transporte do processo.", "invalid_action")
        action["timeout_seconds"] = timeout
    else:
        raise RpcError("Tipo de ação inválido.", "invalid_action")
    return result
