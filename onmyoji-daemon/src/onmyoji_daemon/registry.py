from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Callable


@dataclass(frozen=True)
class ServiceSpec:
    identifier: str
    namespace: str
    command: Callable[[Path], list[str]]
    description: str
    publishes_http: bool = False
    publication_port: int | None = None
    publication_prefixes: tuple[str, ...] = ()
    contain_process_tree: bool = False
    additional_namespaces: tuple[str, ...] = ()

    @property
    def namespaces(self) -> tuple[str, ...]:
        return (self.namespace, *self.additional_namespaces)


def runtime_python(data_dir: Path) -> str:
    """Retorna o Python configurado para o daemon, inclusive em um Windows Service."""
    configured = Path(os.environ.get("ONMYOJI_DAEMON_PYTHON") or "")
    if configured.is_file():
        return str(configured)
    # O supervisor já executa no runtime central; nunca recupere venvs legados.
    return str(Path(sys.executable))


def telegram_command(data_dir: Path) -> list[str]:
    return [runtime_python(data_dir), "-m", "onmyoji_daemon.telegram", "--data-dir", str(data_dir)]


def miniapps_command(data_dir: Path) -> list[str]:
    return [runtime_python(data_dir), "-m", "onmyoji_daemon.miniapps", "--data-dir", str(data_dir)]


def btg_monitor_command(data_dir: Path) -> list[str]:
    return [runtime_python(data_dir), "-m", "onmyoji_daemon.btg_payments_monitor", "--data-dir", str(data_dir)]


def gateway_command(data_dir: Path) -> list[str]:
    return [runtime_python(data_dir), "-m", "onmyoji_daemon.gateway", "--data-dir", str(data_dir)]


def cloudflare_tunnel_command(data_dir: Path) -> list[str]:
    return [runtime_python(data_dir), "-m", "onmyoji_daemon.cloudflare_tunnel", "--data-dir", str(data_dir)]


def task_scheduler_command(data_dir: Path) -> list[str]:
    return [runtime_python(data_dir), "-m", "onmyoji_daemon.task_scheduler.service", "--data-dir", str(data_dir)]


SERVICES: dict[str, ServiceSpec] = {
    "telegram": ServiceSpec("telegram", "telegram.", telegram_command, "Gateway de execução da instância", additional_namespaces=("notification.",)),
    "mini-apps": ServiceSpec("mini-apps", "mini-apps.", miniapps_command, "Gateway de Mini Apps da instância", True, 39391, ("a", "oauth")),
    "btg-payments-monitor": ServiceSpec("btg-payments-monitor", "btg-monitor.", btg_monitor_command, "Acompanhamento de pagamentos BTG por consultas"),
    "gateway": ServiceSpec("gateway", "gateway.", gateway_command, "Gateway HTTP compartilhado da instância"),
    "cloudflare-tunnel": ServiceSpec("cloudflare-tunnel", "cloudflare-tunnel.", cloudflare_tunnel_command, "Publicação Cloudflare Tunnel da instância", contain_process_tree=True),
    "task-scheduler": ServiceSpec("task-scheduler", "task.", task_scheduler_command, "Agendador local de ações", contain_process_tree=True),
}

# Namespace de posse exclusiva do task-scheduler.
RESERVED_NAMESPACES = ("task.",)
