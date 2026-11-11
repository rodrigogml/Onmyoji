"""Configuração local atômica; limites de transporte aplicados no startup."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import secrets
import threading

from ..config_publication import publish_file
from ..rpc import RpcError

# ``smtp_account`` é aceito somente para abrir configurações v1 anteriores;
# entregas novas sempre capturam conta e assunto no próprio destino.
DEFAULTS = {"schema_version": 1, "enabled": False, "telegram_receiver_enabled": True, "port": 39392, "max_request_bytes": 1_048_576, "long_limit": 32, "smtp_account": "", "incident": None}


class NotificationConfiguration:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "configs" / "daemon" / "services" / "telegram" / "notification.json"
        self.lock = threading.RLock()

    def load(self) -> dict:
        with self.lock:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return deepcopy(DEFAULTS)
            self.validate(value)
            return {**deepcopy(DEFAULTS), **value}

    @staticmethod
    def validate(value: dict) -> None:
        valid = isinstance(value, dict) and not set(value) - set(DEFAULTS) and type(value.get("schema_version")) is int and value["schema_version"] == 1
        if not valid:
            raise RpcError("Configuração de notificações inválida.", "invalid_config")
        for field in ("enabled", "telegram_receiver_enabled"):
            if type(value.get(field, DEFAULTS[field])) is not bool:
                raise RpcError("Estado inválido.", "invalid_config")
        for field, low, high in (("port", 1, 65535), ("max_request_bytes", 1_048_576, 1_048_576), ("long_limit", 1, 256)):
            number = value.get(field, DEFAULTS[field])
            if type(number) is not int or not low <= number <= high:
                raise RpcError("Limite inválido.", "invalid_config")
        if not isinstance(value.get("smtp_account", ""), str):
            raise RpcError("Conta SMTP inválida.", "invalid_config")
        incident = value.get("incident")
        channel_fields = {"type", "value"} if isinstance(incident, dict) and incident.get("type") == "telegram" else {"type", "value", "account", "subject"}
        if incident is not None and (not isinstance(incident, dict) or set(incident) != channel_fields or incident.get("type") not in {"telegram", "email"} or any(not isinstance(incident.get(field), str) or not incident[field].strip() for field in channel_fields - {"type"})):
            raise RpcError("Canal de incidentes inválido.", "invalid_config")

    def configure(self, changes: dict) -> dict:
        with self.lock:
            value = {**self.load(), **changes}
            self.validate(value)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_root = self.root / "tmp"
            temporary_root.mkdir(parents=True, exist_ok=True)
            temporary = temporary_root / ("notification-" + secrets.token_hex(12) + ".json")
            try:
                temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
                publish_file(temporary, self.path)
                execution = self.path.parent / "config.toml"
                if not execution.exists():
                    temporary.write_text('schema_version = 2\n[telegram]\n[agent]\nmax_parallel_conversations = 1\nturn_timeout_seconds = 900\ninterrupt_grace_seconds = 15\n', encoding="utf-8")
                    publish_file(temporary, execution)
            finally:
                temporary.unlink(missing_ok=True)
            return {**value, "restart_required": bool(set(changes) & {"port", "long_limit", "max_request_bytes", "telegram_receiver_enabled"})}
