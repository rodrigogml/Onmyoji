"""Contrato fechado dos arquivos TOML observados pelo agendador."""
from copy import deepcopy
import json
from pathlib import Path

from .calendar import validate_rule
from ..agent_notification.contract import validate_event, uuid7
from ..rpc import RpcError


def validate_definition(value, workspace: Path, now, script_timeout_seconds: int = 900):
    if not isinstance(value, dict) or set(value) != {"schema_version", "enabled", "description", "created_at", "time", "action"} or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise RpcError("Definição inválida ou versão não suportada.", "invalid_schedule")
    if not isinstance(value.get("description"), str) or not value["description"].strip() or len(value["description"]) > 500 or type(value.get("enabled")) is not bool:
        raise RpcError("Descrição ou estado inválido.", "invalid_schedule")
    result = deepcopy(value)
    from .calendar import instant, iso
    result["created_at"] = iso(instant(value.get("created_at")))
    result["time"] = validate_rule(result.get("time"), now)
    action = result.get("action")
    if not isinstance(action, dict):
        raise RpcError("Ação inválida.", "invalid_action")
    if action.get("type") == "event":
        if set(action) != {"type", "event"} or not isinstance(action["event"], dict) or "event_id" in action["event"]:
            raise RpcError("Ação de evento exige envelope sem event_id.", "invalid_action")
        validate_event({**action["event"], "event_id": uuid7()})
    elif action.get("type") == "script":
        if set(action) - {"type", "path", "args", "timeout_seconds"}:
            raise RpcError("Campos de script inválidos.", "invalid_action")
        path = action.get("path")
        if not isinstance(path, str) or not path or Path(path).is_absolute() or "\x00" in path:
            raise RpcError("O caminho do script deve ser relativo ao workspace.", "invalid_action")
        resolved = (workspace / path).resolve()
        if not resolved.is_relative_to(workspace.resolve()) or not resolved.is_file() or resolved.suffix.casefold() != ".py":
            raise RpcError("O script Python deve existir dentro do workspace.", "invalid_action")
        if not isinstance(action.get("args", []), list) or any(not isinstance(arg, str) or "\x00" in arg for arg in action.get("args", [])):
            raise RpcError("Argumentos devem ser uma lista de textos.", "invalid_action")
        timeout = action.get("timeout_seconds", script_timeout_seconds)
        if type(timeout) is not int or not 1 <= timeout <= 604800:
            raise RpcError("Timeout deve ser de 1 a 604800 segundos.", "invalid_action")
        action.setdefault("args", [])
        if len(json.dumps([path, *action["args"]]).encode("utf-8")) + 1 > 1_048_576:
            raise RpcError("Comando excede o limite do executor de scripts.", "invalid_action")
        action["timeout_seconds"] = timeout
    else:
        raise RpcError("Tipo de ação inválido.", "invalid_action")
    return result
