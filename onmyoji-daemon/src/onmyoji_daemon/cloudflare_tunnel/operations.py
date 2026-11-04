"""Operações administrativas serializadas, com estado persistente sem segredos."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from .configuration import write_local_json
from .exception import TunnelError


class TunnelOperations:
    """Executa uma mutação por vez e nunca repete pedidos após reinício.

    Somente callbacks internos podem produzir resultados e mensagens. Exceções
    inesperadas são convertidas em ``operation_failed`` sem incluir seus dados.
    """

    def __init__(self, root: Path, data_dir: Path):
        self.root, self.path = root, data_dir / "operations.json"
        self.lock = threading.RLock()
        self.closed = False
        self.active: str | None = None
        self.worker: threading.Thread | None = None
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            saved = {"schema_version": 1, "operations": {}}
        except (OSError, ValueError) as error:
            raise TunnelError("invalid_operation_state", "O estado das operações do Tunnel é inválido.") from error
        if not isinstance(saved, dict) or saved.get("schema_version") != 1 or not isinstance(saved.get("operations"), dict):
            raise TunnelError("invalid_operation_state", "O estado das operações do Tunnel é inválido.")
        self.records = saved["operations"]
        interrupted = False
        for identifier, record in self.records.items():
            if not isinstance(identifier, str) or not isinstance(record, dict):
                raise TunnelError("invalid_operation_state", "O estado das operações do Tunnel é inválido.")
            if record.get("state") in {"queued", "running"}:
                record.update(state="interrupted", finished_at=time.time(), error_code="operation_interrupted",
                              error="A execução anterior foi interrompida; nenhuma operação foi repetida.")
                interrupted = True
        if interrupted:
            self._save()

    def _save(self) -> None:
        write_local_json(self.root, self.path, {"schema_version": 1, "operations": self.records})

    @property
    def busy(self) -> bool:
        with self.lock:
            return self.active is not None

    def start(self, name: str, action: Callable[[Callable[[str], None]], Any]) -> dict[str, Any]:
        """Agenda trabalho ou lança operation_in_progress sem iniciar outra mutação."""
        with self.lock:
            if self.closed:
                raise TunnelError("service_stopping", "O serviço Tunnel está encerrando.")
            if self.active is not None:
                raise TunnelError("operation_in_progress", "Já existe uma operação administrativa em execução.")
            identifier = uuid4().hex
            record = {"operation_id": identifier, "operation": name, "state": "queued", "created_at": time.time(), "progress": "aguardando execução"}
            self.records[identifier] = record
            # Retém um histórico limitado de resultados; o pedido ativo nunca é removido.
            for old in list(self.records)[:-100]:
                self.records.pop(old)
            self.active = identifier
            self._save()
            self.worker = threading.Thread(target=self._run, args=(identifier, action), daemon=True, name="onmyoji-tunnel-operation")
            self.worker.start()
            return copy.deepcopy(record)

    def _run(self, identifier: str, action: Callable[[Callable[[str], None]], Any]) -> None:
        def progress(message: str) -> None:
            with self.lock:
                if self.records[identifier]["state"] == "interrupted":
                    raise TunnelError("operation_interrupted", "A operação foi interrompida pelo encerramento do serviço.")
                self.records[identifier]["progress"] = message
                self._save()

        with self.lock:
            if self.closed:
                if self.active == identifier:
                    self.active = None
                return
            self.records[identifier].update(state="running", started_at=time.time())
            self._save()
        terminal: dict[str, Any]
        try:
            result = action(progress)
        except Exception as error:
            terminal = {"state": "failed", "error_code": error.code if isinstance(error, TunnelError) else "operation_failed",
                        "error": str(error) if isinstance(error, TunnelError) else "Não foi possível concluir a operação administrativa.", "finished_at": time.time()}
        else:
            terminal = {"state": "succeeded", "result": result, "progress": "concluída", "finished_at": time.time()}
        with self.lock:
            if self.records[identifier]["state"] != "interrupted":
                self.records[identifier].update(terminal)
            if self.active == identifier:
                self.active = None
            self._save()

    def status(self, identifier: str) -> dict[str, Any]:
        with self.lock:
            if identifier not in self.records:
                raise TunnelError("operation_not_found", "Operação administrativa não encontrada.")
            return copy.deepcopy(self.records[identifier])

    def close(self) -> None:
        """Marca trabalho incompleto como interrompido sem repetir efeitos remotos."""
        with self.lock:
            self.closed = True
            if self.active is not None:
                self.records[self.active].update(state="interrupted", error_code="operation_interrupted",
                                                  error="O serviço foi encerrado antes da confirmação do resultado.", finished_at=time.time())
                self._save()
