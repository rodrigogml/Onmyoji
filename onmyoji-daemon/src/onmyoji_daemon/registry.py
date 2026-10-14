from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Callable


@dataclass(frozen=True)
class ServiceSpec:
    identifier: str
    namespace: str
    command: Callable[[Path], list[str]]
    description: str


def telegram_command(data_dir: Path) -> list[str]:
    return [sys.executable, "-m", "onmyoji_daemon.telegram", "--data-dir", str(data_dir)]


def miniapps_command(data_dir: Path) -> list[str]:
    environment = data_dir.parent.parent / "venv"
    executable = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return [str(executable if executable.is_file() else Path(sys.executable)), "-m", "onmyoji_daemon.miniapps", "--data-dir", str(data_dir)]


def btg_monitor_command(data_dir: Path) -> list[str]:
    return [sys.executable, "-m", "onmyoji_daemon.btg_payments_monitor", "--data-dir", str(data_dir)]


SERVICES: dict[str, ServiceSpec] = {
    "telegram": ServiceSpec("telegram", "telegram.", telegram_command, "Gateway Telegram da instância"),
    "mini-apps": ServiceSpec("mini-apps", "mini-apps.", miniapps_command, "Gateway de Mini Apps da instância"),
    "btg-payments-monitor": ServiceSpec("btg-payments-monitor", "btg-monitor.", btg_monitor_command, "Acompanhamento de pagamentos BTG por consultas"),
}

# Namespace reservado para o futuro task-scheduler. Só um ServiceSpec poderá ativá-lo.
RESERVED_NAMESPACES = ("task.",)
