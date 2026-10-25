"""Estado local que coordena a reconciliação do MCP com o Codex CLI."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
from typing import Iterator, Protocol


REGISTRATION_SCHEMA_VERSION = 2


class McpReconciliationError(RuntimeError):
    """Indica que o estado MCP não pôde ser reconciliado com segurança."""


class ReconciliableRegistration(Protocol):
    """Contrato mínimo do registro Codex usado pelo coordenador local."""

    def reconcile(self) -> str: ...


def mcp_reconciliation_path(root: Path) -> Path:
    """Retorna o estado local, não sensível, da próxima reconciliação MCP."""
    return root.resolve() / "configs" / "mcp" / "codex-registration.json"


def mcp_reconciliation_pending(root: Path) -> bool:
    """Informa se a próxima abertura do Codex deve reconciliar o registro.

    Instalações anteriores não possuem o arquivo de estado. Elas são tratadas
    como pendentes uma única vez para migrar naturalmente ao fluxo automático.
    """
    path = mcp_reconciliation_path(root)
    if not path.exists():
        return True
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return (
        not isinstance(value, dict)
        or value.get("schema_version") != REGISTRATION_SCHEMA_VERSION
        or value.get("pending") is not False
    )


class McpReconciliation:
    """Marca e executa uma reconciliação por vez entre setup e gateway."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = mcp_reconciliation_path(self.root)
        self.lock_path = self.path.with_suffix(".lock")

    def mark_pending(self) -> None:
        """Solicita que a próxima abertura segura do Codex atualize o MCP."""
        with self._lock():
            self._write(True)

    def mark_synchronized(self) -> None:
        """Registra que uma ação explícita já deixou o registro consistente."""
        with self._lock():
            self._write(False)

    def reconcile_if_pending(self, registration: ReconciliableRegistration) -> bool:
        """Reconcilia uma única vez e preserva a pendência em caso de falha."""
        with self._lock():
            if not mcp_reconciliation_pending(self.root):
                return False
            try:
                registration.reconcile()
            except Exception as error:
                raise McpReconciliationError("Não foi possível reconciliar o registro MCP do Codex CLI.") from error
            self._write(False)
            return True

    def _write(self, pending: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps({"schema_version": REGISTRATION_SCHEMA_VERSION, "pending": pending}, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            os.replace(temporary, self.path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise McpReconciliationError("Não foi possível atualizar o estado MCP local.") from error

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 15
        descriptor: int | None = None
        while descriptor is None:
            try:
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise McpReconciliationError("Outra operação MCP ainda está em andamento.")
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(descriptor)
            try:
                self.lock_path.unlink()
            except OSError:
                pass
