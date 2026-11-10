"""Provisionamento e limites para uma identidade Linux dedicada ao Shikigami."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping


ACCOUNT_PATTERN = re.compile(r"[a-z_][a-z0-9_-]{0,30}\Z")
MIB = 1024 * 1024


@dataclass(frozen=True)
class ServiceLimits:
    """Limites persistidos e renderizados diretamente na unit systemd."""

    cpu_quota_percent: int = 0
    memory_high_bytes: int = 0
    memory_max_bytes: int = 0
    memory_swap_max_bytes: int = 0
    tasks_max: int = 0
    io_weight: int = 0
    private_devices: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, object] | None) -> "ServiceLimits":
        if not values:
            return cls()
        integers: dict[str, int] = {}
        for field in ("cpu_quota_percent", "memory_high_bytes", "memory_max_bytes", "memory_swap_max_bytes", "tasks_max", "io_weight"):
            value = values.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"Limite inválido: {field}.")
            integers[field] = value
            if integers[field] < 0:
                raise ValueError(f"Limite inválido: {field}.")
        private_devices = values.get("private_devices", False)
        if not isinstance(private_devices, bool):
            raise ValueError("Limite inválido: private_devices.")
        if integers["io_weight"] not in range(0, 10001):
            raise ValueError("IOWeight deve ficar entre 1 e 10000, ou zero para omitir.")
        if integers["memory_high_bytes"] and integers["memory_max_bytes"] and integers["memory_high_bytes"] > integers["memory_max_bytes"]:
            raise ValueError("MemoryHigh não pode superar MemoryMax.")
        return cls(**integers, private_devices=private_devices)

    def mapping(self) -> dict[str, object]:
        return asdict(self)

    def systemd_lines(self) -> list[str]:
        lines: list[str] = []
        if self.cpu_quota_percent: lines.append(f"CPUQuota={self.cpu_quota_percent}%")
        if self.memory_high_bytes: lines.append(f"MemoryHigh={self.memory_high_bytes}")
        if self.memory_max_bytes: lines.append(f"MemoryMax={self.memory_max_bytes}")
        if self.memory_swap_max_bytes: lines.append(f"MemorySwapMax={self.memory_swap_max_bytes}")
        if self.tasks_max: lines.extend(["TasksAccounting=yes", f"TasksMax={self.tasks_max}"])
        if self.io_weight: lines.append(f"IOWeight={self.io_weight}")
        if self.private_devices: lines.append("PrivateDevices=yes")
        return lines


def _memory_information() -> tuple[int, int]:
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
        return values.get("MemTotal", 0), values.get("SwapTotal", 0)
    except (OSError, ValueError, IndexError):
        try: total = int(getattr(os, "sysconf", lambda _name: 0)("SC_PHYS_PAGES") or 0) * int(getattr(os, "sysconf", lambda _name: 0)("SC_PAGE_SIZE") or 0)
        except (OSError, ValueError): total = 0
        return total, 0


def recommended_limits(cpu_count: int | None = None, memory_bytes: int | None = None, swap_bytes: int | None = None) -> ServiceLimits:
    """Calcula uma base conservadora uma única vez para a máquina atual."""
    cpus = max(1, int(cpu_count or os.cpu_count() or 1))
    detected_memory, detected_swap = _memory_information()
    memory = max(0, int(detected_memory if memory_bytes is None else memory_bytes))
    swap = max(0, int(detected_swap if swap_bytes is None else swap_bytes))
    high = memory * 60 // 100 // MIB * MIB if memory else 0
    maximum = memory * 75 // 100 // MIB * MIB if memory else 0
    swap_maximum = min(swap, memory * 25 // 100) // MIB * MIB if memory else 0
    return ServiceLimits(
        cpu_quota_percent=max(100, (cpus - 1) * 100),
        memory_high_bytes=high,
        memory_max_bytes=maximum,
        memory_swap_max_bytes=swap_maximum,
        tasks_max=min(4096, max(512, cpus * 128)),
        io_weight=80,
    )


def account_name(identity: str) -> str:
    normalized = unicodedata.normalize("NFKD", identity).encode("ascii", "ignore").decode("ascii").lower()
    suffix = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "local"
    return ("shikigami-" + suffix)[:31].rstrip("-")


def validate_account(value: str) -> str:
    candidate = value.strip()
    if not ACCOUNT_PATTERN.fullmatch(candidate):
        raise ValueError("A conta Linux deve começar por letra minúscula ou _, e conter somente a-z, 0-9, _ e -.")
    return candidate


def _run(command: list[str], *, elevated: bool, use_sudo: bool, input_value: str | None = None) -> subprocess.CompletedProcess[str]:
    if elevated and not use_sudo:
        return subprocess.CompletedProcess(command, 126, "", "A operação exige confirmação de sudo.")
    if elevated and not shutil.which("sudo"):
        return subprocess.CompletedProcess(command, 127, "", "sudo não está disponível.")
    prefix = ["sudo", "--"] if elevated else []
    return subprocess.run([*prefix, *command], input=input_value, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)


def _checked(command: list[str], *, elevated: bool, use_sudo: bool) -> tuple[bool, str]:
    completed = _run(command, elevated=elevated, use_sudo=use_sudo)
    return completed.returncode == 0, (completed.stderr or completed.stdout).strip()


def provision_account(account: str, installer: str, root: Path, workspace: Path, use_sudo: bool, progress: Callable[[str], None] | None = None, *, share_with_installer: bool = True) -> tuple[bool, str, Path]:
    """Cria uma conta sem login e aplica grupo, setgid e ACL padrão às duas árvores."""
    try:
        account = validate_account(account)
        installer = installer.strip()
        if share_with_installer and not installer:
            raise ValueError("Não foi possível identificar o usuário instalador.")
    except ValueError as error:
        return False, str(error), Path("/")
    root, workspace = root.resolve(), workspace.resolve()
    if not root.is_dir() or not workspace.is_dir():
        return False, "A instância e o workspace precisam existir antes do isolamento.", Path("/")
    required = ["getent", "useradd", "chown", "chmod", "find"]
    if share_with_installer: required.extend(["usermod", "chgrp", "setfacl", "getfacl"])
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        guidance = "Instale o pacote de ACL e os utilitários de contas." if share_with_installer else "Instale os utilitários de contas do sistema."
        return False, "Ferramentas Linux ausentes: " + ", ".join(missing) + ". " + guidance, Path("/")
    elevated = getattr(os, "geteuid", lambda: 0)() != 0
    existing = subprocess.run(["getent", "passwd", account], text=True, capture_output=True, check=False)
    home = Path("/home") / account
    if existing.returncode == 0:
        fields = existing.stdout.strip().split(":")
        group = subprocess.run(["getent", "group", account], text=True, capture_output=True, check=False)
        group_fields = group.stdout.strip().split(":") if group.returncode == 0 else []
        if len(fields) < 7 or Path(fields[5]) != home or not fields[6].endswith(("nologin", "false")) or len(group_fields) < 3 or fields[3] != group_fields[2]:
            return False, f"A conta {account} já existe, mas não corresponde à identidade isolada esperada.", home
    else:
        if progress: progress(f"Criando a conta Linux dedicada {account}.")
        occupied_group = subprocess.run(["getent", "group", account], text=True, capture_output=True, check=False)
        if occupied_group.returncode == 0:
            return False, f"O grupo {account} já existe sem a conta dedicada correspondente.", home
        nologin = shutil.which("nologin") or "/usr/sbin/nologin"
        ok, detail = _checked(["useradd", "--system", "--create-home", "--home-dir", str(home), "--shell", nologin, "--user-group", account], elevated=elevated, use_sudo=use_sudo)
        if not ok: return False, detail or f"Não foi possível criar {account}.", home
    for command in (["chown", f"{account}:{account}", str(home)], ["chmod", "0700", str(home)]):
        ok, detail = _checked(command, elevated=elevated, use_sudo=use_sudo)
        if not ok: return False, detail or f"Não foi possível proteger o home {home}.", home
    if share_with_installer:
        installer_entry = subprocess.run(["getent", "passwd", installer], text=True, capture_output=True, check=False)
        if installer_entry.returncode != 0:
            return False, f"O usuário instalador {installer} não existe no cadastro Linux.", home
        if progress: progress(f"Associando {installer} ao grupo privado {account}.")
        ok, detail = _checked(["usermod", "--append", "--groups", account, installer], elevated=elevated, use_sudo=use_sudo)
        if not ok: return False, detail or "Não foi possível configurar o grupo compartilhado.", home
    for directory in (root, workspace):
        if progress: progress(f"Aplicando permissões herdáveis em {directory}.")
        if share_with_installer:
            commands = (
                ["chgrp", "-R", account, str(directory)],
                ["chmod", "-R", "g+rwX,o-rwx", str(directory)],
                ["find", str(directory), "-type", "d", "-exec", "chmod", "g+s", "{}", "+"],
                ["setfacl", "-R", "-m", f"u:{installer}:rwX,m::rwX", str(directory)],
                ["find", str(directory), "-type", "d", "-exec", "setfacl", "-m", f"d:u:{installer}:rwx,d:u::rwx,d:g::rwx,d:m::rwx,d:o::---", "{}", "+"],
            )
        else:
            commands = (["chown", "-R", f"{account}:{account}", str(directory)], ["chmod", "-R", "u+rwX,go-rwx", str(directory)])
        for command in commands:
            ok, detail = _checked(command, elevated=elevated, use_sudo=use_sudo)
            if not ok: return False, detail or f"Não foi possível proteger {directory}.", home
    access = f"acesso compartilhado com {installer}" if share_with_installer else "acesso exclusivo"
    return True, f"Conta {account} e {access} configurados.", home


def repair_permissions(root: Path, info: Mapping[str, object], use_sudo: bool, progress: Callable[[str], None] | None = None) -> tuple[bool, str]:
    account = str(info.get("run_as_user") or "")
    installer = str(info.get("installer_user") or "")
    workspace = Path(str(info.get("workspace") or ""))
    if not info.get("isolated") or not account or not installer or not workspace.is_absolute():
        return False, "Este serviço não possui uma identidade Linux isolada registrada."
    ok, message, _home = provision_account(account, installer, root, workspace, use_sudo, progress, share_with_installer=bool(info.get("installer_access", True)))
    return ok, message


def limits_json(limits: ServiceLimits) -> str:
    return json.dumps(limits.mapping(), ensure_ascii=False, separators=(",", ":"))


def format_bytes(value: int) -> str:
    if not value: return "sem limite adicional"
    if value % (1024 ** 3) == 0: return f"{value // (1024 ** 3)} GiB"
    return f"{value // MIB} MiB"


def limits_summary(limits: ServiceLimits) -> list[str]:
    return [
        f"CPUQuota: {limits.cpu_quota_percent}%" if limits.cpu_quota_percent else "CPUQuota: sem limite adicional",
        f"MemoryHigh: {format_bytes(limits.memory_high_bytes)}",
        f"MemoryMax: {format_bytes(limits.memory_max_bytes)}",
        f"MemorySwapMax: {format_bytes(limits.memory_swap_max_bytes)}",
        f"TasksMax: {limits.tasks_max or 'sem limite adicional'}",
        f"IOWeight: {limits.io_weight or 'padrão do sistema'}",
        "PrivateDevices: habilitado" if limits.private_devices else "PrivateDevices: desabilitado",
    ]
