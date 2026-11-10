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
import re
import subprocess
import sys
import time
import tomllib
import unicodedata
import uuid
import shutil
from collections.abc import Callable


from .rpc import call
from .registry import SERVICES
from .instance import identity
from .supervisor import endpoint
from . import runtime as shared_runtime
from .linux_service import ServiceLimits, load_execution_profile, provision_account, recommended_limits, repair_permissions as repair_linux_permissions


def daemon_root(root: Path) -> Path: return root.resolve() / "configs" / "daemon"
def manifest_path(root: Path) -> Path: return daemon_root(root) / "daemon.toml"
def runtime_path(root: Path) -> Path: return daemon_root(root) / "runtime"
def process_path(root: Path) -> Path: return runtime_path(root) / "process.json"
def service_path(root: Path) -> Path: return daemon_root(root) / "service.json"
def runtime_configuration(root: Path) -> dict[str, str]:
    """Expõe a configuração central para compatibilidade dos chamadores."""
    return shared_runtime.configuration(root)


def daemon_python(root: Path) -> Path:
    return shared_runtime.python(root)


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
    """Prepara as dependências do daemon no runtime único da instância."""
    try:
        executable = daemon_python(root)
        if _runtime_ready(executable):
            return True, "Runtime Python do Shikigami está pronto."
        if not allow_install:
            return False, "O runtime Python do Shikigami não possui os componentes necessários. Use a manutenção para instalá-los."
        message = shared_runtime.ensure(root, runtime_requirements(), "from importlib.metadata import version; import sys; assert sys.version_info >= (3, 11); assert (3, 10) <= tuple(map(int, version('aiohttp').split('.')[:2])) < (4, 0); assert (42, 0) <= tuple(map(int, version('cryptography').split('.')[:2])) < (47, 0)", print if show_progress else None)
        return True, message
    except (OSError, ValueError, tomllib.TOMLDecodeError, shared_runtime.RuntimeError) as error:
        return False, str(error)


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


def normalized_service_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:128].rstrip("-") or "shikigami"


def default_service_name(root: Path) -> str: return normalized_service_name(instance_label(root))
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
    if enabled and service in {"mini-apps", "gateway", "cloudflare-tunnel"}:
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


def _systemd_quote(value: str | Path) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _systemd_path(value: str | Path) -> str:
    """Serializa um path para diretivas que não removem aspas externas."""
    rendered: list[str] = []
    for character in str(value):
        if character == "%":
            rendered.append("%%")
        elif character.isascii() and (character.isalnum() or character in "/._-+"):
            rendered.append(character)
        elif not character.isascii():
            rendered.append(character)
        else:
            rendered.append(f"\\x{ord(character):02x}")
    return "".join(rendered)


def _systemd_unit(root: Path, executable: str, source: Path, description: str, scope: str, run_as_user: str, run_as_group: str, *, workspace: Path | None = None, home: Path | None = None, isolated: bool = False, limits: ServiceLimits | None = None) -> str:
    """Gera uma unit sem depender do shell e preservando paths com espaços."""
    resolved_root = root.resolve()
    service = ["[Service]", "Type=simple", f"WorkingDirectory={_systemd_path(resolved_root)}", f"Environment={_systemd_quote('PYTHONPATH=' + str(source))}", f"Environment={_systemd_quote('CODEX_HOME=' + str(resolved_root))}"]
    if scope == "system" and run_as_user: service.append(f"User={run_as_user}")
    if scope == "system" and run_as_group: service.append(f"Group={run_as_group}")
    if home: service.append(f"Environment={_systemd_quote('HOME=' + str(home))}")
    if isolated:
        writable = [home, resolved_root, workspace.resolve() if workspace else None]
        service.extend([
            "UMask=0007", "ProtectSystem=strict", "ProtectHome=tmpfs", "PrivateTmp=yes", "NoNewPrivileges=yes",
            "CapabilityBoundingSet=", "AmbientCapabilities=", "RestrictSUIDSGID=yes", "LockPersonality=yes",
            "ProtectControlGroups=yes", "ProtectKernelModules=yes", "ProtectKernelTunables=yes", "ProtectKernelLogs=yes",
            "ProtectClock=yes", "ProtectHostname=yes",
            *(f"BindPaths={_systemd_path(path)}" for path in writable if path),
            *(f"ReadWritePaths={_systemd_path(path)}" for path in writable if path),
        ])
    service.extend((limits or ServiceLimits()).systemd_lines())
    service.extend([f"ExecStart={_systemd_quote(executable)} -m onmyoji_daemon.cli --onmyoji-root {_systemd_quote(resolved_root)} run", "Restart=on-failure", "RestartSec=3"])
    return "\n".join(["[Unit]", f"Description={description}", "Wants=network-online.target", "After=network-online.target", "", *service, "", "[Install]", "WantedBy=default.target" if scope == "user" else "WantedBy=multi-user.target", ""])


def _systemd_analyze_command(scope: str, *arguments: str | Path) -> list[str]:
    analyzer = shutil.which("systemd-analyze")
    return [str(analyzer), *(["--user"] if scope == "user" else []), *(str(argument) for argument in arguments)] if analyzer else []


def _systemd_verify_command(scope: str, unit: Path) -> list[str]:
    return _systemd_analyze_command(scope, "verify", unit)


def _verify_systemd_unit(root: Path, name: str, content: str, scope: str) -> tuple[bool, str]:
    """Valida a unit em tmp/ antes de tocar a configuração do sistema."""
    command_available = shutil.which("systemd-analyze")
    if not command_available:
        return False, "systemd-analyze não está disponível para validar a unit antes da instalação."
    directory = root.resolve() / "tmp" / "systemd-validation"
    candidate = directory / f"{name}.service"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        candidate.write_text(content, encoding="utf-8", newline="\n")
        environment = dict(os.environ); environment["LC_ALL"] = "C"
        checked = subprocess.run(_systemd_verify_command(scope, candidate), text=True, encoding="utf-8", errors="replace", capture_output=True, env=environment, check=False)
    except OSError as error:
        return False, f"Não foi possível validar a unit systemd: {error}"
    finally:
        candidate.unlink(missing_ok=True)
        try: directory.rmdir()
        except OSError: pass
    detail = (checked.stderr or checked.stdout).strip()
    return checked.returncode == 0, detail or ("Unit systemd validada." if checked.returncode == 0 else "systemd-analyze rejeitou a unit sem fornecer detalhes.")


def _rollback_systemd_unit(unit: Path, scope: str, name: str, prefix: list[str]) -> None:
    """Remove somente a unit recém-criada e atualiza o catálogo após uma falha."""
    subprocess.run([*prefix, *_systemctl(scope, "disable", name)], text=True, capture_output=True, check=False)
    if prefix:
        subprocess.run([*prefix, "rm", "-f", str(unit)], text=True, capture_output=True, check=False)
    else:
        unit.unlink(missing_ok=True)
    subprocess.run([*prefix, *_systemctl(scope, "daemon-reload")], text=True, capture_output=True, check=False)


def prepare_windows_service_host(root: Path) -> tuple[bool, str]:
    """Posiciona o host pywin32 onde o Python reconhece corretamente o venv."""
    executable = daemon_python(root); scripts = executable.parent; environment = scripts.parent; site_packages = environment / "Lib" / "site-packages"
    service_source = next((candidate for candidate in (site_packages / "win32" / "pythonservice.exe", environment / "pythonservice.exe") if candidate.is_file()), None)
    dlls = list((site_packages / "pywin32_system32").glob("pywintypes*.dll"))
    try:
        base_home = next(Path(line.split("=", 1)[1].strip()) for line in (environment / "pyvenv.cfg").read_text(encoding="utf-8").splitlines() if line.lower().startswith("home ="))
    except (OSError, StopIteration):
        return False, "Não foi possível identificar o Python-base do ambiente dedicado. Recrie o runtime do daemon."
    python_dlls = list(base_home.glob("python*.dll"))
    if not executable.is_file() or service_source is None or not dlls or not python_dlls:
        return False, "O runtime não possui os arquivos do pywin32 necessários ao Serviço do Windows. Reinstale/valide o runtime do daemon."
    try:
        shutil.copy2(service_source, scripts / "pythonservice.exe")
        for dll in [*dlls, *python_dlls]: shutil.copy2(dll, scripts / dll.name)
    except OSError as error:
        return False, f"Não foi possível preparar o host do Serviço do Windows: {error}"
    return True, "Host do Serviço do Windows preparado no ambiente dedicado."


def configure_windows_service_account(root: Path, account: str, password: str = "") -> tuple[bool, str]:
    """Atualiza a conta de logon sem expor a senha na linha de comando."""
    if os.name != "nt": return False, "A conta de serviço é configurável por este comando somente no Windows."
    info = service_info(root)
    if not info: return False, "Serviço do sistema operacional não está instalado para esta instância."
    selected = account.strip()
    if not selected: return False, "Informe a conta do Windows que executará o serviço."
    local_system = selected.casefold() == "localsystem"
    if not local_system and not password: return False, "Informe a senha da conta do Windows; ela não será armazenada pelo Onmyōji."
    try:
        import win32service
        manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        service = win32service.OpenService(manager, str(info["name"]), win32service.SERVICE_CHANGE_CONFIG)
        try:
            win32service.ChangeServiceConfig(
                service, win32service.SERVICE_NO_CHANGE, win32service.SERVICE_NO_CHANGE,
                win32service.SERVICE_NO_CHANGE, None, None, 0, None,
                None if local_system else selected, None if local_system else password, None,
            )
        finally:
            win32service.CloseServiceHandle(service); win32service.CloseServiceHandle(manager)
    except Exception as error:
        return False, f"Não foi possível alterar a conta do Serviço do Windows: {error}"
    info["run_as_user"] = "LocalSystem" if local_system else selected
    _write_json(service_path(root), info)
    return True, f"Conta do serviço atualizada para {info['run_as_user']}. Reinicie o serviço para aplicar a alteração."


def install_service(root: Path, name: str, description: str, scope: str = "system", run_as_user: str = "", run_as_group: str = "", use_sudo: bool = False, windows_account: str = "", windows_password: str = "", progress: Callable[[str], None] | None = None, *, isolate_linux: bool = False, installer_user: str = "", installer_access: bool = True, workspace: str = "", resource_limits: dict[str, object] | None = None) -> tuple[bool, str]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", name): return False, "O nome do serviço contém caracteres inválidos. Use letras, números, ponto, _, @ ou -."
    if not description.strip() or any(character in description for character in "\r\n"): return False, "A descrição do serviço deve ocupar uma única linha não vazia."
    if not is_installed(root): return False, "Instale primeiro o daemon desta instância."
    if service_path(root).exists(): return False, "Já existe um serviço instalado para esta instância."
    if running_process(root): return False, "Finalize o processo local antes de instalar o serviço."
    ready, message = ensure_runtime(root, allow_install=False)
    if not ready: return False, message
    executable = str(daemon_python(root))
    try: limits = ServiceLimits.from_mapping(resource_limits) if resource_limits is not None else recommended_limits()
    except (TypeError, ValueError) as error: return False, str(error)
    service_home: Path | None = None
    if os.name == "nt":
        if scope != "system": return False, "O Windows Service é sempre instalado no escopo do sistema operacional."
        if progress: progress("Preparando o host do Serviço do Windows.")
        host_ready, host_message = prepare_windows_service_host(root)
        if not host_ready: return False, host_message
        source = str(Path(__file__).resolve().parents[1]); environment = dict(os.environ)
        environment["PYTHONPATH"] = source + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        if progress: progress("Registrando o serviço no sistema operacional; esta etapa pode levar até 60 segundos.")
        try:
            created = subprocess.run([executable, "-m", "onmyoji_daemon.windows_service", "--service-name", name, "--onmyoji-root", str(root.resolve()), "--startup", "auto", "install"], text=True, capture_output=True, env=environment, check=False, timeout=60)
        except subprocess.TimeoutExpired:
            return False, "O registro do Serviço do Windows excedeu 60 segundos e foi interrompido. Verifique permissões administrativas e o estado do Gerenciador de Controle de Serviços."
        if created.returncode != 0: return False, (created.stderr or created.stdout).strip()
        if progress: progress("Validando o registro do serviço no Windows.")
        if progress: progress("Registrando a configuração local do serviço.")
        if windows_account and windows_account.casefold() != "localsystem":
            if progress: progress("Configurando a conta Windows selecionada; a senha não será exibida nem armazenada.")
            _write_json(service_path(root), {"name": name, "root": str(root.resolve())})
            configured, detail = configure_windows_service_account(root, windows_account, windows_password)
            if not configured:
                subprocess.run([executable, "-m", "onmyoji_daemon.windows_service", "--service-name", name, "--onmyoji-root", str(root.resolve()), "remove"], text=True, capture_output=True, env=environment, check=False)
                service_path(root).unlink(missing_ok=True)
                return False, f"A instalação foi desfeita porque a conta do serviço não pôde ser configurada: {detail}"
    else:
        if not shutil.which("systemctl"): return False, "systemd não está disponível neste sistema."
        if scope not in {"user", "system"}: return False, "Escopo de serviço inválido."
        workspace_path = Path(workspace).expanduser().resolve() if workspace else None
        if isolate_linux:
            if scope != "system": return False, "A identidade Linux dedicada exige um serviço de sistema."
            if not run_as_user or not installer_user or workspace_path is None:
                return False, "Informe conta dedicada, usuário instalador e workspace para habilitar o isolamento."
            execution_profile = load_execution_profile(root)
            if execution_profile.get("mode") != "dedicated" or execution_profile.get("validated") is not True or execution_profile.get("account") != run_as_user or Path(str(execution_profile.get("workspace") or "")).resolve() != workspace_path:
                return False, "A conta dedicada ainda não foi validada pelo setup para esta instância e workspace."
            prepared, detail, service_home = provision_account(run_as_user, installer_user, root, workspace_path, use_sudo, progress, share_with_installer=installer_access)
            if not prepared: return False, detail
            run_as_group = run_as_user
        elif scope == "user":
            service_home = Path.home()
        unit = (Path.home() / ".config" / "systemd" / "user" if scope == "user" else Path("/etc/systemd/system")) / f"{name}.service"
        source = Path(__file__).resolve().parents[1]
        content = _systemd_unit(root, executable, source, description, scope, run_as_user, run_as_group, workspace=workspace_path, home=service_home, isolated=isolate_linux, limits=limits)
        verified, verification_detail = _verify_systemd_unit(root, name, content, scope)
        if not verified: return False, "A unit systemd foi rejeitada antes da instalação:\n" + verification_detail
        elevated = scope == "system" and getattr(os, "geteuid", lambda: 0)() != 0
        if elevated and not use_sudo: return False, "A instalação de serviço do sistema exige confirmação de sudo."
        if elevated and not shutil.which("sudo"): return False, "sudo não está disponível. Execute o setup como root ou use su para obter uma sessão administrativa."
        if unit.exists(): return False, f"A unit {unit} já existe e não será sobrescrita sem remoção explícita."
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
        if reloaded.returncode != 0:
            _rollback_systemd_unit(unit, scope, name, command_prefix)
            return False, (reloaded.stderr or reloaded.stdout).strip()
        loaded = subprocess.run([*command_prefix, *_systemctl(scope, "show", name, "--property=LoadState", "--value")], text=True, capture_output=True, check=False)
        if loaded.returncode != 0 or loaded.stdout.strip() != "loaded":
            verification = subprocess.run(_systemd_verify_command(scope, unit), text=True, encoding="utf-8", errors="replace", capture_output=True, env={**os.environ, "LC_ALL": "C"}, check=False)
            detail = (verification.stderr or verification.stdout or loaded.stderr or loaded.stdout).strip() or "O systemd não carregou a unit recém-instalada."
            _rollback_systemd_unit(unit, scope, name, command_prefix)
            return False, detail
        enabled = subprocess.run([*command_prefix, *_systemctl(scope, "enable", name)], text=True, capture_output=True, check=False)
        if enabled.returncode != 0:
            _rollback_systemd_unit(unit, scope, name, command_prefix)
            return False, (enabled.stderr or enabled.stdout).strip()
    configured_user = windows_account or run_as_user or ("LocalSystem" if os.name == "nt" else "")
    service_state = {"schema_version": 3, "name": name, "description": description, "root": str(root.resolve()), "platform": platform.system(), "scope": scope, "run_as_user": configured_user, "run_as_group": run_as_group, "isolated": isolate_linux, "installer_user": installer_user if isolate_linux else "", "installer_access": installer_access if isolate_linux else False, "workspace": workspace if isolate_linux else "", "home": str(service_home) if service_home else "", "resource_limits": limits.mapping() if os.name != "nt" else {}}
    if os.name != "nt" and isolate_linux and not installer_access and getattr(os, "geteuid", lambda: 0)() != 0:
        written = subprocess.run(["sudo", "tee", str(service_path(root))], input=json.dumps(service_state, ensure_ascii=False, indent=2) + "\n", text=True, capture_output=True, check=False)
        if written.returncode != 0: return False, (written.stderr or written.stdout).strip() or "Não foi possível registrar o serviço na instância isolada."
        owned = subprocess.run(["sudo", "chown", f"{run_as_user}:{run_as_group}", str(service_path(root))], text=True, capture_output=True, check=False)
        if owned.returncode != 0: return False, (owned.stderr or owned.stdout).strip() or "Não foi possível atribuir o registro à conta isolada."
    else:
        _write_json(service_path(root), service_state)
    return True, f"Serviço {name} instalado."


def _service_elevation(info: dict, use_sudo: bool) -> tuple[bool, list[str], str]:
    elevated = str(info.get("scope") or "system") == "system" and getattr(os, "geteuid", lambda: 0)() != 0
    if elevated and not use_sudo: return False, [], "A operação deste serviço de sistema exige confirmação de sudo."
    if elevated and not shutil.which("sudo"): return False, [], "sudo não está disponível."
    return True, ["sudo"] if elevated else [], ""


def configure_service_resources(root: Path, values: dict[str, object], use_sudo: bool = False) -> tuple[bool, str]:
    """Grava limites em um drop-in, preservando a unit principal e a identidade."""
    if os.name == "nt": return False, "Limites systemd estão disponíveis somente no Linux."
    info = service_info(root)
    if not info: return False, "Serviço do sistema operacional não está instalado para esta instância."
    try: limits = ServiceLimits.from_mapping(values)
    except (TypeError, ValueError) as error: return False, str(error)
    allowed, prefix, detail = _service_elevation(info, use_sudo)
    if not allowed: return False, detail
    name, scope = str(info["name"]), str(info.get("scope") or "system")
    directory = (Path.home() / ".config" / "systemd" / "user" if scope == "user" else Path("/etc/systemd/system")) / f"{name}.service.d"
    path = directory / "20-onmyoji-resources.conf"
    content = "\n".join(["[Service]", "CPUQuota=", "MemoryHigh=", "MemoryMax=", "MemorySwapMax=", "TasksMax=", "IOWeight=", "PrivateDevices=no", *limits.systemd_lines(), ""])
    if prefix:
        created = subprocess.run([*prefix, "mkdir", "-p", str(directory)], text=True, capture_output=True, check=False)
        if created.returncode != 0: return False, (created.stderr or created.stdout).strip()
        written = subprocess.run([*prefix, "tee", str(path)], input=content, text=True, capture_output=True, check=False)
        if written.returncode != 0: return False, (written.stderr or written.stdout).strip()
    else:
        try: directory.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding="utf-8")
        except OSError as error: return False, f"Não foi possível gravar {path}: {error}"
    reloaded = subprocess.run([*prefix, *_systemctl(scope, "daemon-reload")], text=True, capture_output=True, check=False)
    if reloaded.returncode != 0: return False, (reloaded.stderr or reloaded.stdout).strip()
    info["resource_limits"] = limits.mapping(); _write_json(service_path(root), info)
    return True, "Limites atualizados; reinicie o serviço para aplicá-los aos processos em execução."


def repair_service_permissions(root: Path, use_sudo: bool = False, progress: Callable[[str], None] | None = None) -> tuple[bool, str]:
    if os.name == "nt": return False, "ACLs Linux não estão disponíveis no Windows."
    return repair_linux_permissions(root, service_info(root), use_sudo, progress)


def service_security(root: Path, use_sudo: bool = False) -> tuple[bool, str]:
    if os.name == "nt" or not shutil.which("systemd-analyze"): return False, "systemd-analyze não está disponível."
    info = service_info(root)
    if not info: return False, "Serviço do sistema operacional não está instalado para esta instância."
    allowed, prefix, detail = _service_elevation(info, use_sudo)
    if not allowed: return False, detail
    scope = str(info.get("scope") or "system")
    value = subprocess.run([*prefix, *_systemd_analyze_command(scope, "security", "--no-pager", str(info["name"]))], text=True, capture_output=True, check=False)
    return value.returncode == 0, (value.stdout or value.stderr).strip()


def service_status(root: Path) -> tuple[bool, str]:
    info = service_info(root)
    if not info: return False, "Serviço do sistema operacional não está instalado para esta instância."
    if os.name == "nt":
        value = subprocess.run(["sc.exe", "query", str(info["name"])], text=True, capture_output=True, check=False)
        return value.returncode == 0, (value.stdout or value.stderr).strip()
    scope, name = str(info.get("scope") or "system"), str(info["name"])
    value = subprocess.run(_systemctl(scope, "status", name, "--no-pager"), text=True, capture_output=True, check=False)
    detail = (value.stdout or value.stderr).strip()
    bad_setting = "bad-setting" in detail.casefold()
    if bad_setting:
        unit = (Path.home() / ".config" / "systemd" / "user" if scope == "user" else Path("/etc/systemd/system")) / f"{name}.service"
        command = _systemd_verify_command(scope, unit)
        if command:
            environment = dict(os.environ); environment["LC_ALL"] = "C"
            verified = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, env=environment, check=False)
            verification = (verified.stderr or verified.stdout).strip()
            if verification: detail += "\n\nDiagnóstico da unit:\n" + verification
    return value.returncode in {0, 3} and not bad_setting, detail


def _windows_service_state(status: str) -> str:
    """Extrai o estado do ``sc.exe`` nas localizações mais comuns do Windows."""
    matched = re.search(r"(?:STATE|ESTADO)\s*:\s*\d+\s+([A-Z_]+)", status, re.IGNORECASE)
    return matched.group(1).upper() if matched else ""


def _wait_for_windows_service_state(root: Path, expected: str, attempts: int = 10) -> tuple[bool, str]:
    """Aguarda o estado efetivo do serviço, além do aceite inicial do SCM."""
    last_status = ""
    for attempt in range(attempts):
        _ok, last_status = service_status(root)
        if _windows_service_state(last_status) == expected:
            return True, last_status
        if attempt < attempts - 1: time.sleep(0.5)
    observed = _windows_service_state(last_status) or "desconhecido"
    return False, f"O Windows aceitou a solicitação, mas o serviço não chegou ao estado {expected}; estado final: {observed}.\n{last_status}"


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
    if value.returncode != 0: return False, (value.stderr or value.stdout).strip()
    expected = "RUNNING" if command == "start" else "STOPPED"
    confirmed, status = _wait_for_windows_service_state(root, expected)
    if not confirmed: return False, status
    message = "Serviço em execução." if expected == "RUNNING" else "Serviço parado."
    return True, f"{message}\n{status}"


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
        dropin = unit.parent / f"{name}.service.d" / "20-onmyoji-resources.conf"
        try:
            if elevated:
                value = subprocess.run(["sudo", "rm", "-f", str(unit), str(dropin)], text=True, capture_output=True, check=False); value.check_returncode()
                subprocess.run(["sudo", "rmdir", str(dropin.parent)], text=True, capture_output=True, check=False)
            else:
                unit.unlink(missing_ok=True); dropin.unlink(missing_ok=True)
                try: dropin.parent.rmdir()
                except OSError: pass
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
