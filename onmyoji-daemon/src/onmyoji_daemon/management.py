"""Controle local e multiplataforma da instância do daemon.

Este módulo nunca procura processos pelo nome: todo recurso administrado precisa
conter o identificador e a raiz da instância que o criou.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid
import shutil

from .rpc import call
from .supervisor import endpoint


def daemon_root(root: Path) -> Path: return root.resolve() / "configs" / "daemon"
def manifest_path(root: Path) -> Path: return daemon_root(root) / "daemon.toml"
def runtime_path(root: Path) -> Path: return daemon_root(root) / "runtime"
def process_path(root: Path) -> Path: return runtime_path(root) / "process.json"
def service_path(root: Path) -> Path: return daemon_root(root) / "service.json"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError): return {}


def instance_label(root: Path) -> str:
    return root.resolve().name.removeprefix("Onmyoji-").removeprefix("Onmyōji-") or "Shikigami"


def camel_name(value: str) -> str:
    parts = [part for part in "".join(char if char.isalnum() else " " for char in value).split() if part]
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Shikigami"


def default_service_name(root: Path) -> str: return f"Shikigami-{camel_name(instance_label(root))}"
def default_service_description(root: Path) -> str: return f"Shikigami {instance_label(root)} Daemon"


def is_installed(root: Path) -> bool:
    return manifest_path(root).is_file()


def install_instance(root: Path) -> tuple[bool, str]:
    path = manifest_path(root)
    if path.exists(): return True, "Daemon já está instalado nesta instância."
    path.parent.mkdir(parents=True, exist_ok=True)
    instance_id = uuid.uuid4().hex
    path.write_text("\n".join(["schema_version = 1", f"instance_id = {json.dumps(instance_id)}", f"instance_name = {json.dumps(instance_label(root), ensure_ascii=False)}", ""]) , encoding="utf-8", newline="\n")
    return True, "Daemon instalado; nenhum processo foi iniciado."


def remove_instance(root: Path) -> tuple[bool, str]:
    if not is_installed(root): return True, "Daemon não está instalado nesta instância."
    if running_process(root): return False, "Finalize primeiro o processo local do daemon."
    if service_path(root).exists(): return False, "Remova primeiro o serviço do sistema operacional."
    target = daemon_root(root)
    # O diretório é fixo sob configs locais; nenhum outro alvo é aceito.
    if target.name != "daemon" or target.parent != root.resolve() / "configs": return False, "Alvo de remoção inválido."
    shutil.rmtree(target)
    return True, "Daemon, configurações e estado local removidos."


def installed_metadata(root: Path) -> dict:
    import tomllib
    try: return tomllib.loads(manifest_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError): return {}


def set_enabled(root: Path, service: str, enabled: bool) -> tuple[bool, str]:
    if not is_installed(root): return False, "Instale primeiro o daemon desta instância."
    if service != "telegram": return False, "Serviço não registrado."
    path = daemon_root(root) / "services.json"; values = _read_json(path)
    values.setdefault("telegram", {})["enabled"] = enabled
    _write_json(path, values)
    if not enabled and daemon_reachable(root):
        try:
            host, port, token = endpoint(root); call(host, port, token, "disable", {"service": service})
        except Exception: pass
    return True, "Telegram habilitado." if enabled else "Telegram desabilitado."


def _pid_alive(pid: int) -> bool:
    if pid <= 0: return False
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], text=True, capture_output=True, check=False)
        return str(pid) in result.stdout and "No tasks" not in result.stdout
    try: os.kill(pid, 0); return True
    except OSError: return False


def running_process(root: Path) -> dict | None:
    info = _read_json(process_path(root))
    if not info or str(info.get("root")) != str(root.resolve()): return None
    try: pid = int(info.get("pid", 0))
    except (TypeError, ValueError): return None
    return info if _pid_alive(pid) else None


def daemon_reachable(root: Path) -> bool:
    try:
        host, port, token = endpoint(root)
        return call(host, port, token, "ping", timeout=0.5).get("service") == "onmyoji-daemon"
    except Exception: return False


def start_background(root: Path) -> tuple[bool, str]:
    if not is_installed(root): return False, "Instale primeiro o daemon desta instância."
    if service_path(root).exists(): return False, "O serviço do sistema operacional está instalado; gerencie-o no submenu Serviço."
    if daemon_reachable(root) or running_process(root): return False, "Já existe um daemon em execução nesta instância."
    source = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    command = [sys.executable, "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root.resolve()), "run"]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
    with open(os.devnull, "w", encoding="utf-8") as sink:
        subprocess.Popen(command, cwd=str(root), env=environment, stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, creationflags=flags, start_new_session=os.name != "nt")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if daemon_reachable(root): return True, "Processo do daemon iniciado em segundo plano."
        time.sleep(0.1)
    return False, "O processo foi iniciado, mas não respondeu dentro do tempo esperado. Consulte os logs locais."


def _rpc(root: Path, method: str) -> tuple[bool, str]:
    try:
        host, port, token = endpoint(root); call(host, port, token, method, timeout=4)
        return True, "Solicitação entregue ao daemon."
    except Exception as error: return False, f"Daemon indisponível: {error}"


def stop_background(root: Path, force: bool = False) -> tuple[bool, str]:
    info = running_process(root)
    if not info: return True, "Nenhum processo local do daemon está em execução."
    pid = int(info["pid"])
    if not force:
        ok, message = _rpc(root, "shutdown")
        if ok:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if not _pid_alive(pid): return True, "Processo do daemon encerrado normalmente."
                time.sleep(0.1)
        return False, message
    if os.name == "nt": result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], text=True, capture_output=True, check=False)
    else: result = subprocess.run(["kill", "-KILL", str(pid)], text=True, capture_output=True, check=False)
    return (result.returncode == 0, "Processo finalizado à força." if result.returncode == 0 else (result.stderr or result.stdout).strip())


def service_info(root: Path) -> dict: return _read_json(service_path(root))


def install_service(root: Path, name: str, description: str) -> tuple[bool, str]:
    if not is_installed(root): return False, "Instale primeiro o daemon desta instância."
    if service_path(root).exists(): return False, "Já existe um serviço instalado para esta instância."
    if running_process(root): return False, "Finalize o processo local antes de instalar o serviço."
    if os.name == "nt":
        source = str(Path(__file__).resolve().parents[1]); environment = dict(os.environ)
        environment["PYTHONPATH"] = source + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        created = subprocess.run([sys.executable, "-m", "onmyoji_daemon.windows_service", "--service-name", name, "--onmyoji-root", str(root.resolve()), "--startup", "auto", "install"], text=True, capture_output=True, env=environment, check=False)
        if created.returncode != 0: return False, (created.stderr or created.stdout).strip()
        subprocess.run(["sc.exe", "description", name, description], text=True, capture_output=True, check=False)
    else:
        if not shutil.which("systemctl"): return False, "systemd não está disponível neste sistema."
        unit = Path("/etc/systemd/system") / f"{name}.service"
        source = Path(__file__).resolve().parents[1]
        content = "\n".join(["[Unit]", f"Description={description}", "After=network-online.target", "", "[Service]", "Type=simple", f"WorkingDirectory={root.resolve()}", f"Environment=PYTHONPATH={source}", f"ExecStart={sys.executable} -m onmyoji_daemon.cli --onmyoji-root {root.resolve()} run", "Restart=on-failure", "RestartSec=3", "", "[Install]", "WantedBy=multi-user.target", ""])
        try: unit.write_text(content, encoding="utf-8")
        except OSError as error: return False, f"Não foi possível gravar {unit}; execute o setup com privilégio administrativo: {error}"
        reloaded = subprocess.run(["systemctl", "daemon-reload"], text=True, capture_output=True, check=False)
        if reloaded.returncode != 0: return False, (reloaded.stderr or reloaded.stdout).strip()
        enabled = subprocess.run(["systemctl", "enable", name], text=True, capture_output=True, check=False)
        if enabled.returncode != 0: return False, (enabled.stderr or enabled.stdout).strip()
    _write_json(service_path(root), {"schema_version": 1, "name": name, "description": description, "root": str(root.resolve()), "platform": platform.system()})
    return True, f"Serviço {name} instalado."


def service_status(root: Path) -> tuple[bool, str]:
    info = service_info(root)
    if not info: return False, "Serviço do sistema operacional não está instalado para esta instância."
    if os.name == "nt":
        value = subprocess.run(["sc.exe", "query", str(info["name"])], text=True, capture_output=True, check=False)
        return value.returncode == 0, (value.stdout or value.stderr).strip()
    value = subprocess.run(["systemctl", "status", str(info["name"]), "--no-pager"], text=True, capture_output=True, check=False)
    return value.returncode in {0, 3}, (value.stdout or value.stderr).strip()


def service_action(root: Path, action: str) -> tuple[bool, str]:
    info = service_info(root)
    if not info: return False, "Serviço do sistema operacional não está instalado para esta instância."
    if os.name != "nt":
        command = {"start": "start", "stop": "stop", "restart": "restart", "force": "kill"}.get(action)
        if not command: return False, "Ação de serviço inválida."
        value = subprocess.run(["systemctl", command, str(info["name"])], text=True, capture_output=True, check=False)
        return value.returncode == 0, ("Serviço atualizado." if value.returncode == 0 else (value.stderr or value.stdout).strip())
    command = {"start": "start", "stop": "stop", "restart": "stop", "force": "stop"}.get(action)
    if not command: return False, "Ação de serviço inválida."
    name = str(info["name"])
    if action == "force":
        stopped = subprocess.run(["sc.exe", "stop", name], text=True, capture_output=True, check=False)
        time.sleep(2)
        status = service_status(root)[1]
        if "STOPPED" not in status: return False, "O Windows não confirmou a parada; não será finalizado um processo sem identificação segura."
        return True, "Solicitação de parada forçada concluída."
    if action == "restart":
        subprocess.run(["sc.exe", "stop", name], text=True, capture_output=True, check=False); time.sleep(1)
        command = "start"
    value = subprocess.run(["sc.exe", command, name], text=True, capture_output=True, check=False)
    return value.returncode == 0, ("Serviço atualizado." if value.returncode == 0 else (value.stderr or value.stdout).strip())


def remove_service(root: Path) -> tuple[bool, str]:
    info = service_info(root)
    if not info: return True, "Nenhum serviço do sistema operacional foi registrado nesta instância."
    if str(info.get("root")) != str(root.resolve()): return False, "Registro de serviço não pertence a esta instância."
    if os.name != "nt":
        name = str(info["name"]); subprocess.run(["systemctl", "disable", "--now", name], text=True, capture_output=True, check=False)
        unit = Path("/etc/systemd/system") / f"{name}.service"
        try: unit.unlink(missing_ok=True)
        except OSError as error: return False, f"Não foi possível remover {unit}: {error}"
        value = subprocess.run(["systemctl", "daemon-reload"], text=True, capture_output=True, check=False)
        if value.returncode != 0: return False, (value.stderr or value.stdout).strip()
        service_path(root).unlink(missing_ok=True); return True, "Serviço systemd removido."
    service_action(root, "stop")
    source = str(Path(__file__).resolve().parents[1]); environment = dict(os.environ)
    environment["PYTHONPATH"] = source + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    value = subprocess.run([sys.executable, "-m", "onmyoji_daemon.windows_service", "--service-name", str(info["name"]), "--onmyoji-root", str(root.resolve()), "remove"], text=True, capture_output=True, env=environment, check=False)
    if value.returncode != 0: return False, (value.stderr or value.stdout).strip()
    service_path(root).unlink(missing_ok=True)
    return True, "Serviço removido."
