"""Reservas duráveis de namespaces; destinos e gerações são efêmeros."""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .exception import GatewayError

PREFIX = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
OWNER = re.compile(r"[a-z][a-z0-9_-]{0,127}\Z", re.ASCII)


def validate_prefix(value: Any) -> str:
    """Aceita um segmento ASCII minúsculo e impede publicar a API interna."""
    if not isinstance(value, str) or not PREFIX.fullmatch(value) or value == "internal":
        raise GatewayError("invalid_prefix", "Namespace público inválido.")
    return value


def validate_target(value: Any) -> str:
    """Restringe destinos a origens HTTP(S) em loopback, sem URLs arbitrárias."""
    try:
        parsed = urlsplit(value) if isinstance(value, str) else None
        valid = parsed and not any(ord(char) <= 32 or ord(char) == 127 for char in value) and parsed.scheme in {"http", "https"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.port and not parsed.username and not parsed.password and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
    except ValueError:
        valid = False
    if not valid:
        raise GatewayError("invalid_target", "O destino deve ser uma origem HTTP(S) local com porta explícita.")
    return value.rstrip("/")


class ListenerRegistry:
    """Garante unicidade atômica e conserva reservas desabilitadas após reinício.

    Remoções deixam uma supressão por proprietário, evitando que a próxima
    declaração recrie automaticamente uma publicação removida pelo operador.
    A remoção libera o prefixo para outros proprietários.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.runtime: dict[str, dict[str, Any]] = {}
        with self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS listeners (
                    owner TEXT NOT NULL, path_prefix TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, removed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(owner,path_prefix)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS listener_reservation
                    ON listeners(path_prefix) WHERE removed=0;
            """)

    @staticmethod
    def _owner(value: Any) -> str:
        if not isinstance(value, str) or not OWNER.fullmatch(value):
            raise GatewayError("invalid_owner", "Serviço proprietário inválido.")
        return value

    def sync(self, owner: Any, generation: Any, listeners: Any, available: bool = True) -> list[dict]:
        """Aplica uma declaração inteira ou rejeita sem alterar as reservas.

        Lança ``prefix_conflict`` quando outro proprietário reserva um prefixo;
        ``invalid_declaration`` indica uma declaração incompleta ou duplicada.
        Prefixos omitidos conservam a reserva e ficam indisponíveis.
        """
        owner = self._owner(owner)
        if isinstance(generation, bool) or not isinstance(generation, (str, int)) or not str(generation) or len(str(generation)) > 128 or not isinstance(listeners, list) or type(available) is not bool:
            raise GatewayError("invalid_declaration", "Declaração de listeners inválida.")
        desired: dict[str, dict] = {}
        for item in listeners:
            if not isinstance(item, dict):
                raise GatewayError("invalid_declaration", "Declaração de listener inválida.")
            prefix = validate_prefix(item.get("path_prefix"))
            if prefix in desired or type(item.get("ready", False)) is not bool:
                raise GatewayError("invalid_declaration", "Listener duplicado ou prontidão inválida.", prefix)
            desired[prefix] = {"target": validate_target(item.get("target")), "ready": item.get("ready", False), "ca_file": item.get("ca_file")}
        with self.lock:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                for prefix in desired:
                    own = self.db.execute("SELECT removed FROM listeners WHERE owner=? AND path_prefix=?", (owner, prefix)).fetchone()
                    if own and own["removed"]:
                        continue
                    conflict = self.db.execute("SELECT owner FROM listeners WHERE path_prefix=? AND removed=0 AND owner<>?", (prefix, owner)).fetchone()
                    if conflict:
                        raise GatewayError("prefix_conflict", "Namespace reservado por outro serviço.", prefix)
                for prefix in desired:
                    self.db.execute("INSERT OR IGNORE INTO listeners(owner,path_prefix) VALUES(?,?)", (owner, prefix))
            self.runtime[owner] = {"generation": str(generation), "available": available, "listeners": desired}
            return self.list(owner=owner)

    def unavailable(self, owner: Any, generation: Any = None) -> list[dict]:
        """Invalida somente a geração indicada; reservas continuam ocupadas."""
        owner = self._owner(owner)
        with self.lock:
            current = self.runtime.get(owner)
            if current and (generation is None or current["generation"] == str(generation)):
                current["available"] = False
            return self.list(owner=owner)

    def list(self, owner: str | None = None) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT owner,path_prefix,enabled,removed FROM listeners ORDER BY path_prefix,owner").fetchall()
            result = []
            for row in rows:
                if owner is not None and row["owner"] != owner:
                    continue
                runtime = self.runtime.get(row["owner"], {})
                endpoint = runtime.get("listeners", {}).get(row["path_prefix"], {})
                item = {**dict(row), "enabled": bool(row["enabled"]), "removed": bool(row["removed"]), "target": endpoint.get("target"), "ca_file": endpoint.get("ca_file"), "generation": runtime.get("generation")}
                item["ready"] = bool(item["enabled"] and not item["removed"] and runtime.get("available") and endpoint.get("ready"))
                item["state"] = "removed" if item["removed"] else "disabled" if not item["enabled"] else "ready" if item["ready"] else "unavailable"
                result.append(item)
            return result

    def inspect(self, path_prefix: Any, owner: str | None = None, include_removed: bool = True) -> dict:
        prefix = validate_prefix(path_prefix)
        rows = [item for item in self.list(owner) if item["path_prefix"] == prefix and (include_removed or not item["removed"])]
        active = [item for item in rows if not item["removed"]]
        if active:
            return active[0]
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            raise GatewayError("owner_required", "Informe o proprietário da reserva removida.", prefix)
        raise GatewayError("listener_not_found", "Listener não encontrado.", prefix)

    def change(self, action: str, path_prefix: Any, owner: str | None = None, confirm: bool = False) -> dict:
        """Altera uma reserva; remover/restaurar exige confirmação explícita."""
        if action not in {"enable", "disable", "remove", "restore"}:
            raise GatewayError("unsupported_operation", "Operação de listener inválida.")
        if action in {"remove", "restore"} and confirm is not True:
            raise GatewayError("confirmation_required", "A operação exige confirm=true.")
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            item = self.inspect(path_prefix, owner)
            if action == "restore":
                conflict = self.db.execute("SELECT owner FROM listeners WHERE path_prefix=? AND removed=0 AND owner<>?", (item["path_prefix"], item["owner"])).fetchone()
                if conflict:
                    raise GatewayError("prefix_conflict", "Namespace reservado por outro serviço.", item["path_prefix"])
                self.db.execute("UPDATE listeners SET removed=0 WHERE owner=? AND path_prefix=?", (item["owner"], item["path_prefix"]))
            elif action == "remove":
                self.db.execute("UPDATE listeners SET removed=1 WHERE owner=? AND path_prefix=?", (item["owner"], item["path_prefix"]))
            elif item["removed"]:
                raise GatewayError("listener_removed", "Restaure a reserva antes de habilitar ou desabilitar.", item["path_prefix"])
            else:
                self.db.execute("UPDATE listeners SET enabled=? WHERE owner=? AND path_prefix=?", (int(action == "enable"), item["owner"], item["path_prefix"]))
            return self.inspect(item["path_prefix"], item["owner"])

    def close(self) -> None:
        with self.lock:
            self.db.close()
