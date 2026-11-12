"""Migra o estado operacional privado para a árvore ``state`` da instância.

A configuração declarativa continua em ``configs``. Esta migração é deliberadamente
conservadora: um destino existente nunca é sobrescrito e uma instância com o
supervisor ativo não é alterada por baixo do processo residente.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


LAYOUT_VERSION = 1
SETUP_VERSION = "2026.11.12.13"


class StateLayoutError(RuntimeError):
    """Indica que não foi possível preservar o estado durante a migração."""


def version_path(root: Path) -> Path:
    return root.resolve() / "state" / "onmyoji-state.json"


def state_path(root: Path, *parts: str) -> Path:
    return root.resolve() / "state" / Path(*parts)


def daemon_runtime_path(root: Path) -> Path:
    return state_path(root, "daemon", "runtime")


def telegram_state_path(root: Path) -> Path:
    return state_path(root, "daemon", "services", "telegram")


def _read_version(root: Path) -> dict:
    path = version_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"layout_version": 0}
    except (OSError, ValueError) as error:
        raise StateLayoutError("O registro de versão do estado está ilegível; ele não foi substituído.") from error
    if not isinstance(value, dict) or not isinstance(value.get("layout_version"), int):
        raise StateLayoutError("O registro de versão do estado é inválido; ele não foi substituído.")
    return value


def _active(root: Path) -> bool:
    for path in (root / "configs" / "daemon" / "runtime" / "supervisor.lock", daemon_runtime_path(root) / "supervisor.lock"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            pid = int(value.get("pid", 0))
        except (OSError, ValueError, TypeError):
            continue
        if pid <= 0 or str(value.get("root")) != str(root):
            continue
        if os.name == "nt":
            probe = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], text=True, capture_output=True, check=False)
            if probe.returncode or str(pid) not in probe.stdout or "No tasks" in probe.stdout:
                continue
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                continue
        return True
    return False


def _move(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    if destination.exists():
        raise StateLayoutError(f"A migração preservou os dois caminhos e não pode escolher qual estado manter: {source} e {destination}.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
    except OSError:
        try:
            shutil.move(str(source), str(destination))
        except OSError as error:
            raise StateLayoutError(f"Não foi possível mover o estado privado {source}.") from error
    return True


def migrate(root: Path) -> list[str]:
    """Aplica a versão atual do layout sem descartar estado legado.

    O retorno lista os caminhos de origem movidos. Chamadas posteriores são
    idempotentes depois que o marcador de versão é gravado.
    """
    root = root.resolve()
    saved = _read_version(root)
    version = saved["layout_version"]
    if version > LAYOUT_VERSION:
        raise StateLayoutError("O estado pertence a uma versão mais nova do Onmyōji; não é seguro continuar.")
    if version == LAYOUT_VERSION:
        return []
    if _active(root):
        raise StateLayoutError("Pare o daemon antes da primeira migração de estado; nenhum arquivo foi movido.")
    moves = (
        (root / "configs" / "daemon" / "runtime", daemon_runtime_path(root)),
        (root / "configs" / "daemon" / "services" / "telegram" / "state", telegram_state_path(root)),
        (root / "configs" / "daemon" / "services" / "task-scheduler" / "state", state_path(root, "daemon", "services", "task-scheduler")),
        (root / "configs" / "shikigami" / "skills-runtime.json", state_path(root, "shikigami", "skills-runtime.json")),
        (root / "configs" / "shikigami" / "agents-runtime.json", state_path(root, "shikigami", "agents-runtime.json")),
        (root / "configs" / "shikigami" / "agents-runtime.backup.json", state_path(root, "shikigami", "agents-runtime.backup.json")),
        (root / "configs" / "mcp" / "state" / "email-monitor.sqlite3", state_path(root, "mcp", "email-monitor.sqlite3")),
        (root / "configs" / "mcp" / "state" / "email-monitor.sqlite3-wal", state_path(root, "mcp", "email-monitor.sqlite3-wal")),
        (root / "configs" / "mcp" / "state" / "email-monitor.sqlite3-shm", state_path(root, "mcp", "email-monitor.sqlite3-shm")),
        (root / "configs" / "mcp" / "email" / "submissions.sqlite3", state_path(root, "mcp", "email", "submissions.sqlite3")),
        (root / "configs" / "mcp" / "email" / "submissions.sqlite3-wal", state_path(root, "mcp", "email", "submissions.sqlite3-wal")),
        (root / "configs" / "mcp" / "email" / "submissions.sqlite3-shm", state_path(root, "mcp", "email", "submissions.sqlite3-shm")),
    )
    moved = [str(source.relative_to(root)) for source, destination in moves if _move(source, destination)]
    marker = {"layout_version": LAYOUT_VERSION, "setup_version": SETUP_VERSION}
    path = version_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return moved
