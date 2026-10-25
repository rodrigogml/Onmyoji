"""Controle local e multiplataforma da instância do daemon.

Este módulo nunca procura processos pelo nome: todo recurso administrado precisa
conter o identificador e a raiz da instância que o criou.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import tomllib
import uuid
import shutil

from .rpc import call
from .registry import SERVICES
from .instance import identity
from .supervisor import endpoint


def daemon_root(root: Path) -> Path: return root.resolve() / "configs" / "daemon"
def manifest_path(root: Path) -> Path: return daemon_root(root) / "daemon.toml"
def runtime_path(root: Path) -> Path: return daemon_root(root) / "runtime"
def process_path(root: Path) -> Path: return runtime_path(root) / "process.json"
def service_path(root: Path) -> Path: return daemon_root(root) / "service.json"
def runtime_configuration(root: Path) -> dict[str, str]:
    """Lê a escolha de runtime; instalações antigas continuam dedicadas."""
    try: data = tomllib.loads(manifest_path(root).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError): data = {}
    runtime = data.get("runtime", {}) if isinstance(data, dict) else {}
    runtime = runtime if isinstance(runtime, dict) else {}
    mode = str(runtime.get("mode") or "dedicated")
    if mode not in {"dedicated", "existing", "global"}: mode = "dedicated"
    return {"mode": mode, "base_python": str(runtime.get("base_python") or ""), "executable": str(runtime.get("executable") or "")}


def daemon_python(root: Path) -> Path:
    runtime = runtime_configuration(root)
    if runtime["mode"] == "dedicated":
        environment = daemon_root(root) / "venv"
        return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return Path(runtime["executable"]).expanduser()


def _runtime_ready(executable: Path) -> bool:
    if not executable.is_file(): return False
    probe = """import sys
from importlib.metadata import version
def parsed(name): return tuple(int(part) for part in version(name).split('.')[:2])
assert sys.version_info >= (3, 11)
assert (3, 10) <= parsed('aiohttp') < (4, 0)
assert (42, 0) <= parsed('cryptography') < (47, 0)
"""
    try:
        result = subprocess.run([str(executable), "-c", probe], text=True, capture_output=True, check=False, timeout=10)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def runtime_ready(root: Path) -> bool:
    """Informa se o ambiente privado possui as dependências do daemon."""
    return _runtime_ready(daemon_python(root))


def configuration_revision(root: Path) -> str:
    """Identifica o conteúdo efetivo da configuração da plataforma."""
    try: return hashlib.sha256(manifest_path(root).read_bytes()).hexdigest()
    except OSError: return ""


def applied_configuration_revision(root: Path) -> str:
    """Retorna a revisão registrada pelo supervisor ativo da instância."""
    info = running_process(root)
    return str(info.get("configuration_revision") or "") if info else ""


def runtime_requirements() -> list[str]:
    project = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = list(metadata["project"]["dependencies"])
    if os.name == "nt": requirements.extend(metadata["project"].get("optional-dependencies", {}).get("windows", []))
    if not requirements or any(not isinstance(value, str) for value in requirements): raise ValueError
    return requirements


def pip_invocation(executable: Path, arguments: list[str]) -> list[str]:
    """Monta a chamada do pip sem acionar a consulta WMI defeituosa do Windows."""
    if os.name != "nt": return [str(executable), "-m", "pip", *arguments]
    # pip importa Rich, que chama platform.system(). No Python 3.12 essa chamada
    # consulta WMI e pode bloquear. A plataforma já é conhecida neste subprocesso.
    wrapper = "import platform, runpy; platform.system = lambda: 'Windows'; runpy.run_module('pip', run_name='__main__')"
    return [str(executable), "-c", wrapper, *arguments]


def ensure_runtime(root: Path, show_progress: bool = False, allow_install: bool = True) -> tuple[bool, str]:
    """Prepara o runtime escolhido e instala as dependências do daemon."""
    runtime = runtime_configuration(root); executable = daemon_python(root)
    if _runtime_ready(executable): return True, "Ambiente Python do daemon está pronto."
    if not allow_install:
        return False, "O runtime escolhido não possui os componentes necessários. Use Instalar/validar daemon e confirme a instalação dos componentes."
    package_python = executable
    package_target: Path | None = None
    if runtime["mode"] == "dedicated":
        environment = executable.parent.parent
        environment.parent.mkdir(parents=True, exist_ok=True)
        creator = runtime["base_python"] or sys.executable
        if not executable.is_file():
            if show_progress: print(f"[1/3] Criando ambiente dedicado com {creator} (sem pacotes)...")
            try:
                created = subprocess.run([creator, "-m", "venv", "--without-pip", str(environment)], text=True, capture_output=not show_progress, check=False, timeout=120)
            except subprocess.TimeoutExpired:
                return False, "A criação do ambiente Python excedeu 2 minutos. O ambiente parcial foi preservado para diagnóstico."
            except OSError as error:
                return False, "Não foi possível iniciar o Python para criar o ambiente: " + str(error)
            if created.returncode != 0:
                detail = ((created.stderr or created.stdout) if not show_progress else "").strip()
                return False, "Não foi possível criar o ambiente Python do daemon: " + (detail or "venv falhou")
            if show_progress: print("✓ Ambiente Python dedicado criado.")
        # O pip do Python-base instala somente no site-packages deste venv. Isso
        # evita o ensurepip, que pode travar em algumas instalações do Windows.
        package_python = Path(creator)
        package_target = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    elif not executable.is_file():
        return False, "O executável Python escolhido não existe. Escolha outro runtime no instalador do daemon."
    if show_progress: print("[2/3] Verificando o instalador de pacotes (pip)...")
    try:
        pip_available = subprocess.run(pip_invocation(package_python, ["--version"]), text=True, capture_output=not show_progress, check=False, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "A verificação do pip excedeu 30 segundos. O runtime Python não respondeu."
    except OSError as error:
        return False, "Não foi possível executar o runtime Python escolhido: " + str(error)
    if pip_available.returncode != 0:
        detail = ((pip_available.stderr or pip_available.stdout) if not show_progress else "").strip().splitlines()
        return False, "O Python escolhido para preparar o ambiente não possui pip funcional: " + (detail[-1] if detail else str(package_python))
    try:
        requirements = runtime_requirements()
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError):
        return False, "Não foi possível ler as dependências do daemon em pyproject.toml."
    if show_progress:
        print("[3/3] Instalando os componentes do daemon...")
        print("Componentes que serão verificados/instalados:")
        for requirement in requirements: print("  • " + requirement)
        print("Instalando componentes; a saída do pip será exibida abaixo...")
    try:
        target_option = ["--target", str(package_target)] if package_target else []
        installed = subprocess.run(pip_invocation(package_python, ["install", "--disable-pip-version-check", "--no-input", "--upgrade", *target_option, *requirements]), text=True, capture_output=not show_progress, check=False, timeout=600)
    except subprocess.TimeoutExpired:
        return False, "A instalação dos componentes excedeu 10 minutos. Verifique a conexão e o índice de pacotes Python."
    except OSError as error:
        return False, "Não foi possível iniciar o pip: " + str(error)
    if installed.returncode != 0:
        detail = ((installed.stderr or installed.stdout) if not show_progress else "").strip().splitlines()
        return False, "Não foi possível instalar as dependências do daemon: " + (detail[-1] if detail else "pip falhou")
    if not _runtime_ready(executable): return False, "O ambiente Python foi criado, mas as dependências do daemon não puderam ser validadas."
    return True, "Ambiente Python do daemon instalado e validado."


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
    return identity(root)


def camel_name(value: str) -> str:
    parts = [part for part in "".join(char if char.isalnum() else " " for char in value).split() if part]
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Shikigami"


def default_service_name(root: Path) -> str: return f"Shikigami-{camel_name(instance_label(root))}"
def default_service_description(root: Path) -> str: return f"Shikigami {instance_label(root)} Daemon"


def is_installed(root: Path) -> bool:
    return manifest_path(root).is_file()


def prepare_instance(root: Path) -> tuple[bool, str]:
    path = manifest_path(root)
    created = not path.exists()
    if created:
        path.parent.mkdir(parents=True, exist_ok=True)
        instance_id = uuid.uuid4().hex
        path.write_text("\n".join([
            "schema_version = 1",
            f"instance_id = {json.dumps(instance_id)}",
            f"instance_name = {json.dumps(instance_label(root), ensure_ascii=False)}",
            "",
            "[app_server]",
            "enabled = false",
            "idle_timeout_seconds = 1800",
            "",
            "[instructions]",
            "enabled = true",
            'shikigami_file = "instructions.md"',
            "",
            "[runtime]",
            'mode = "dedicated"',
            'base_python = ""',
            'executable = ""',
            "",
        ]) , encoding="utf-8", newline="\n")
    return True, "Manifesto do daemon preparado." if created else "Manifesto do daemon já existe."


def install_instance(root: Path, show_progress: bool = False) -> tuple[bool, str]:
    created, message = prepare_instance(root)
    if not created: return False, message
    ready, message = ensure_runtime(root, show_progress=show_progress)
    if not ready: return False, message
    return True, "Daemon instalado. " + message


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
    if service not in SERVICES: return False, "Serviço não registrado."
    if enabled and service == "mini-apps":
        ready, message = ensure_runtime(root, allow_install=False)
        if not ready: return False, message
    path = daemon_root(root) / "services.json"; values = _read_json(path)
    values.setdefault(service, {})["enabled"] = enabled
    _write_json(path, values)
    if daemon_reachable(root):
        try:
            host, port, token = endpoint(root); call(host, port, token, "enable" if enabled else "disable", {"service": service})
        except Exception: pass
    label = SERVICES[service].description
    return True, f"{label} habilitado." if enabled else f"{label} desabilitado."


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
    ready, message = ensure_runtime(root, allow_install=False)
    if not ready: return False, message
    source = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    command = [str(daemon_python(root)), "-m", "onmyoji_daemon.cli", "--onmyoji-root", str(root.resolve()), "run"]
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


def _systemctl(scope: str, *arguments: str) -> list[str]:
    return ["systemctl", *( ["--user"] if scope == "user" else []), *arguments]


def _systemd_unit(root: Path, executable: str, source: Path, description: str, scope: str, run_as_user: str, run_as_group: str) -> str:
    """Gera uma unit sem depender do shell e preservando paths com espaços."""
    service = ["[Service]", "Type=simple", f"WorkingDirectory={root.resolve()}", f"Environment=PYTHONPATH={source}"]
    if scope == "system" and run_as_user: service.append(f"User={run_as_user}")
    if scope == "system" and run_as_group: service.append(f"Group={run_as_group}")
    service.extend([f"ExecStart={executable} -m onmyoji_daemon.cli --onmyoji-root {root.resolve()} run", "Restart=on-failure", "RestartSec=3"])
    return "\n".join(["[Unit]", f"Description={description}", "Wants=network-online.target", "After=network-online.target", "", *service, "", "[Install]", "WantedBy=default.target" if scope == "user" else "WantedBy=multi-user.target", ""])


def install_service(root: Path, name: str, description: str, scope: str = "system", run_as_user: str = "", run_as_group: str = "", use_sudo: bool = False) -> tuple[bool, str]:
    if not is_installed(root): return False, "Instale primeiro o daemon desta instância."
    if service_path(root).exists(): return False, "Já existe um serviço instalado para esta instância."
    if running_process(root): return False, "Finalize o processo local antes de instalar o serviço."
    ready, message = ensure_runtime(root, allow_install=False)
    if not ready: return False, message
    executable = str(daemon_python(root))
    if os.name == "nt":
        if scope != "system": return False, "O Windows Service é sempre instalado no escopo do sistema operacional."
        source = str(Path(__file__).resolve().parents[1]); environment = dict(os.environ)
        environment["PYTHONPATH"] = source + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        created = subprocess.run([executable, "-m", "onmyoji_daemon.windows_service", "--service-name", name, "--onmyoji-root", str(root.resolve()), "--startup", "auto", "install"], text=True, capture_output=True, env=environment, check=False)
        if created.returncode != 0: return False, (created.stderr or created.stdout).strip()
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Services\\{name}")
            try: registered_root, _kind = winreg.QueryValueEx(key, "OnmyojiRoot")
            finally: winreg.CloseKey(key)
            if Path(str(registered_root)).resolve() != root.resolve(): raise ValueError("a raiz registrada não corresponde à instância")
        except (OSError, ValueError) as error:
            subprocess.run([executable, "-m", "onmyoji_daemon.windows_service", "--service-name", name, "--onmyoji-root", str(root.resolve()), "remove"], text=True, capture_output=True, env=environment, check=False)
            return False, f"O registro do Serviço do Windows não pôde ser validado; a instalação foi desfeita: {error}"
        subprocess.run(["sc.exe", "description", name, description], text=True, capture_output=True, check=False)
    else:
        if not shutil.which("systemctl"): return False, "systemd não está disponível neste sistema."
        if scope not in {"user", "system"}: return False, "Escopo de serviço inválido."
        unit = (Path.home() / ".config" / "systemd" / "user" if scope == "user" else Path("/etc/systemd/system")) / f"{name}.service"
        source = Path(__file__).resolve().parents[1]
        content = _systemd_unit(root, executable, source, description, scope, run_as_user, run_as_group)
        elevated = scope == "system" and getattr(os, "geteuid", lambda: 0)() != 0
        if elevated and not use_sudo: return False, "A instalação de serviço do sistema exige confirmação de sudo."
        if elevated and not shutil.which("sudo"): return False, "sudo não está disponível. Execute o setup como root ou use su para obter uma sessão administrativa."
        try:
            if elevated:
                written = subprocess.run(["sudo", "tee", str(unit)], input=content, text=True, capture_output=True, check=False)
                if written.returncode != 0: return False, (written.stderr or written.stdout).strip() or "sudo não autorizou a gravação da unit."
            else:
                unit.parent.mkdir(parents=True, exist_ok=True); unit.write_text(content, encoding="utf-8")
        except OSError as error:
            privilege = "sudo" if scope == "system" else "permissão para o diretório do usuário"
            return False, f"Não foi possível gravar {unit}; execute o setup com {privilege}: {error}"
        command_prefix = ["sudo"] if elevated else []
        reloaded = subprocess.run([*command_prefix, *_systemctl(scope, "daemon-reload")], text=True, capture_output=True, check=False)
        if reloaded.returncode != 0: return False, (reloaded.stderr or reloaded.stdout).strip()
        enabled = subprocess.run([*command_prefix, *_systemctl(scope, "enable", name)], text=True, capture_output=True, check=False)
        if enabled.returncode != 0: return False, (enabled.stderr or enabled.stdout).strip()
    configured_user = run_as_user or ("LocalSystem" if os.name == "nt" else "")
    _write_json(service_path(root), {"schema_version": 2, "name": name, "description": description, "root": str(root.resolve()), "platform": platform.system(), "scope": scope, "run_as_user": configured_user, "run_as_group": run_as_group})
    return True, f"Serviço {name} instalado."


def service_status(root: Path) -> tuple[bool, str]:
    info = service_info(root)
    if not info: return False, "Serviço do sistema operacional não está instalado para esta instância."
    if os.name == "nt":
        value = subprocess.run(["sc.exe", "query", str(info["name"])], text=True, capture_output=True, check=False)
        return value.returncode == 0, (value.stdout or value.stderr).strip()
    value = subprocess.run(_systemctl(str(info.get("scope") or "system"), "status", str(info["name"]), "--no-pager"), text=True, capture_output=True, check=False)
    return value.returncode in {0, 3}, (value.stdout or value.stderr).strip()


def service_action(root: Path, action: str, use_sudo: bool = False) -> tuple[bool, str]:
    info = service_info(root)
    if not info: return False, "Serviço do sistema operacional não está instalado para esta instância."
    if os.name != "nt":
        command = {"start": "start", "stop": "stop", "restart": "restart", "force": "kill"}.get(action)
        if not command: return False, "Ação de serviço inválida."
        scope = str(info.get("scope") or "system"); elevated = scope == "system" and getattr(os, "geteuid", lambda: 0)() != 0
        if elevated and not use_sudo: return False, "A operação deste serviço de sistema exige confirmação de sudo."
        if elevated and not shutil.which("sudo"): return False, "sudo não está disponível. Execute a operação como root ou use su para obter uma sessão administrativa."
        value = subprocess.run([*( ["sudo"] if elevated else []), *_systemctl(scope, command, str(info["name"]))], text=True, capture_output=True, check=False)
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


def remove_service(root: Path, use_sudo: bool = False) -> tuple[bool, str]:
    info = service_info(root)
    if not info: return True, "Nenhum serviço do sistema operacional foi registrado nesta instância."
    if str(info.get("root")) != str(root.resolve()): return False, "Registro de serviço não pertence a esta instância."
    if os.name != "nt":
        name = str(info["name"]); scope = str(info.get("scope") or "system"); elevated = scope == "system" and getattr(os, "geteuid", lambda: 0)() != 0
        if elevated and not use_sudo: return False, "A remoção deste serviço de sistema exige confirmação de sudo."
        if elevated and not shutil.which("sudo"): return False, "sudo não está disponível. Execute a remoção como root ou use su para obter uma sessão administrativa."
        prefix = ["sudo"] if elevated else []; subprocess.run([*prefix, *_systemctl(scope, "disable", "--now", name)], text=True, capture_output=True, check=False)
        unit = (Path.home() / ".config" / "systemd" / "user" if scope == "user" else Path("/etc/systemd/system")) / f"{name}.service"
        try:
            if elevated: value = subprocess.run(["sudo", "rm", "-f", str(unit)], text=True, capture_output=True, check=False); value.check_returncode()
            else: unit.unlink(missing_ok=True)
        except OSError as error: return False, f"Não foi possível remover {unit}: {error}"
        except subprocess.CalledProcessError as error: return False, f"Não foi possível remover {unit}: {error}"
        value = subprocess.run([*prefix, *_systemctl(scope, "daemon-reload")], text=True, capture_output=True, check=False)
        if value.returncode != 0: return False, (value.stderr or value.stdout).strip()
        service_path(root).unlink(missing_ok=True); return True, "Serviço systemd removido."
    service_action(root, "stop")
    source = str(Path(__file__).resolve().parents[1]); environment = dict(os.environ)
    environment["PYTHONPATH"] = source + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    value = subprocess.run([str(daemon_python(root)), "-m", "onmyoji_daemon.windows_service", "--service-name", str(info["name"]), "--onmyoji-root", str(root.resolve()), "remove"], text=True, capture_output=True, env=environment, check=False)
    if value.returncode != 0: return False, (value.stderr or value.stdout).strip()
    service_path(root).unlink(missing_ok=True)
    return True, "Serviço removido."
