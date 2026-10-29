#!/usr/bin/env python3
"""Operações não interativas usadas pela central de configuração do Onmyōji."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import tomllib
from types import SimpleNamespace
from pathlib import Path
from urllib.error import HTTPError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "src"))
from onmyoji_daemon.management import applied_configuration_revision, configuration_revision, daemon_python, daemon_reachable, is_installed, runtime_ready, service_info
from onmyoji_daemon.instance import identity
from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.credentials import CredentialError, KeePassCredentialResolver

ROOT = PROJECT.parent
MODEL = PROJECT / "configs" / "telegram.toml.model"
INSTRUCTION_MODEL = PROJECT / "configs" / "shikigami.md.model"


def target(root: Path) -> Path: return root / "shikigami" / "daemon" / "telegram.toml"


def daemon_target(root: Path) -> Path: return root / "configs" / "daemon" / "daemon.toml"


def daemon_data(root: Path) -> dict:
    """Lê a configuração da plataforma sem criar um serviço específico."""
    try:
        data = tomllib.loads(daemon_target(root).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError("Instale primeiro o daemon desta instância.") from error
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"A configuração do daemon é inválida: {error}") from error
    if not isinstance(data, dict): raise ValueError("A configuração do daemon é inválida.")
    return data


def save_daemon_data(root: Path, data: dict) -> None:
    """Persiste somente as configurações compartilhadas pela plataforma do daemon."""
    app_server = data.get("app_server", {})
    instructions = data.get("instructions", {})
    if not isinstance(app_server, dict) or not isinstance(instructions, dict):
        raise ValueError("A configuração da plataforma do daemon é inválida.")
    idle = int(app_server.get("idle_timeout_seconds") or 1800)
    if idle < 60: raise ValueError("O tempo de inatividade do App Server deve ser de ao menos 60 segundos.")
    data["schema_version"] = int(data.get("schema_version") or 1)
    data["instance_id"] = str(data.get("instance_id") or "")
    data["instance_name"] = str(data.get("instance_name") or identity(root))
    app_server.update({"enabled": bool(app_server.get("enabled", False)), "idle_timeout_seconds": idle})
    instructions.update({"enabled": bool(instructions.get("enabled", True)), "shikigami_file": str(instructions.get("shikigami_file") or "instructions.md")})

    def value(raw: object) -> str:
        if isinstance(raw, bool): return str(raw).lower()
        if isinstance(raw, (int, float)): return str(raw)
        if isinstance(raw, str): return json.dumps(raw, ensure_ascii=False)
        if isinstance(raw, list): return "[" + ", ".join(value(item) for item in raw) + "]"
        raise ValueError(f"Valor TOML não suportado na configuração do daemon: {raw!r}")

    lines: list[str] = []
    def section(prefix: list[str], values: dict) -> None:
        scalars = [(key, item) for key, item in values.items() if not isinstance(item, dict)]
        if prefix: lines.extend(["", "[" + ".".join(prefix) + "]"])
        for key, item in scalars: lines.append(f"{key} = {value(item)}")
        for key, item in values.items():
            if isinstance(item, dict): section([*prefix, str(key)], item)
    section([], data)
    daemon_target(root).write_text("\n".join(lines).lstrip("\n") + "\n", encoding="utf-8", newline="\n")


def bootstrap_daemon(root: Path) -> Path:
    """Completa defaults do daemon sem criar nem configurar integrações."""
    data = daemon_data(root)
    legacy: dict = {}
    try: legacy = tomllib.loads(target(root).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError): pass
    app_server = data.setdefault("app_server", {})
    instructions = data.setdefault("instructions", {})
    runtime = data.setdefault("runtime", {})
    legacy_app_server = legacy.get("app_server", {}) if isinstance(legacy.get("app_server", {}), dict) else {}
    legacy_instructions = legacy.get("instructions", {}) if isinstance(legacy.get("instructions", {}), dict) else {}
    app_server.setdefault("enabled", bool(legacy_app_server.get("enabled", False)))
    app_server.setdefault("idle_timeout_seconds", int(legacy_app_server.get("idle_timeout_seconds") or 1800))
    instructions.setdefault("enabled", bool(legacy_instructions.get("enabled", True)))
    instructions.setdefault("shikigami_file", str(legacy_instructions.get("shikigami_file") or "instructions.md"))
    runtime.setdefault("mode", "dedicated")
    runtime.setdefault("base_python", "")
    runtime.setdefault("executable", "")
    save_daemon_data(root, data)
    instruction = root / "shikigami" / str(instructions["shikigami_file"])
    if not instruction.exists(): instruction.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(INSTRUCTION_MODEL, instruction)
    return daemon_target(root)


def bootstrap(root: Path) -> Path:
    # Compatibilidade: a configuração de um serviço antigo pode ser preparada
    # isoladamente. O wizard da plataforma usa daemon-bootstrap e exige a
    # instância instalada.
    if daemon_target(root).is_file(): bootstrap_daemon(root)
    path = target(root)
    if not path.exists(): path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(MODEL, path)
    instruction = root / "shikigami" / "instructions.md"
    if not instruction.exists(): instruction.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(INSTRUCTION_MODEL, instruction)
    return path


def profiles(root: Path) -> list[str]:
    try: return McpConfiguration(root).vault_profile_names()
    except Exception: return []


def keepass_validation(root: Path) -> list[dict[str, str]]:
    """Valida o cofre MCP compartilhado antes de qualquer uso do gateway."""
    checks: list[dict[str, str]] = []
    def add(state: str, label: str, detail: str) -> None: checks.append({"state": state, "label": label, "detail": detail})
    try:
        available = McpConfiguration(root).vault_profile_names()
        add("ok" if available else "error", "Perfis de vault MCP", f"{len(available)} perfil(is) disponível(is)." if available else "Crie um perfil de vault MCP antes de configurar o token do bot.")
    except Exception as error:
        add("error", "Perfis de vault MCP", f"Configuração MCP inválida: {error}")
    return checks


def telegram_data(root: Path) -> dict:
    return tomllib.loads(target(root).read_text(encoding="utf-8"))


def save_telegram(root: Path, profile: str, entry: str, data: dict | None = None) -> None:
    data = data or telegram_data(root)
    telegram = data.setdefault("telegram", {}); telegram["vault_profile"] = profile; telegram.pop("keepass_profile", None); telegram["token_entry"] = entry
    agent, app_server, limits, totp, voice = data.setdefault("agent", {}), data.setdefault("app_server", {}), data.setdefault("limits", {}), data.setdefault("totp", {}), data.setdefault("voice_reply", {})
    # A permissão de enviar mídia pertence ao gateway, não ao provedor EccoVox.
    telegram["agent_outbound_media"] = bool(telegram.get("agent_outbound_media", voice.pop("agent_outbound_media", False)))
    instructions = data.get('instructions', {})
    lines = ["schema_version = 1", f"enabled = {str(bool(data.get('enabled', False))).lower()}", "", "[telegram]", f"vault_profile = {json.dumps(profile, ensure_ascii=False)}", f"token_entry = {json.dumps(entry, ensure_ascii=False)}", f"poll_timeout_seconds = {int(telegram.get('poll_timeout_seconds', 30))}", f"agent_outbound_media = {str(bool(telegram['agent_outbound_media'])).lower()}", "", "[agent]", f"max_parallel_conversations = {int(agent.get('max_parallel_conversations', 1))}", f"turn_timeout_seconds = {int(agent.get('turn_timeout_seconds', 900))}", "", "[instructions]", f"enabled = {str(bool(instructions.get('enabled', True))).lower()}", "shikigami_file = \"instructions.md\"", "", "[app_server]", f"enabled = {str(bool(app_server.get('enabled', False))).lower()}", f"idle_timeout_seconds = {int(app_server.get('idle_timeout_seconds', 1800))}", "", "[limits]", f"max_attachment_bytes = {int(limits.get('max_attachment_bytes', 20971520))}", f"max_batch_attachment_bytes = {int(limits.get('max_batch_attachment_bytes', 52428800))}", f"max_pending_items = {int(limits.get('max_pending_items', 50))}", f"max_retained_attachment_bytes = {int(limits.get('max_retained_attachment_bytes', 262144000))}", f"max_outbound_media_bytes = {int(limits.get('max_outbound_media_bytes', 20971520))}", f"max_outbound_media_per_turn = {int(limits.get('max_outbound_media_per_turn', 3))}", "", "[voice_reply]", f"enabled = {str(bool(voice.get('enabled', False))).lower()}", f"eccovox_profile = {json.dumps(str(voice.get('eccovox_profile', '')), ensure_ascii=False)}", f"language = {json.dumps(str(voice.get('language', 'pt-BR')), ensure_ascii=False)}", f"voice = {json.dumps(str(voice.get('voice', '')), ensure_ascii=False)}", f"speed = {float(voice.get('speed', 1.0))}", f"response_format = {json.dumps(str(voice.get('response_format', 'opus')), ensure_ascii=False)}", f"max_text_characters = {int(voice.get('max_text_characters', 3500))}", f"auto_off_minutes = {int(voice.get('auto_off_minutes', 15))}", f"fallback_to_text = {str(bool(voice.get('fallback_to_text', True))).lower()}", "", "[totp]", f"enabled = {str(bool(totp.get('enabled', False))).lower()}", f"real_password_entry = {json.dumps(str(totp.get('real_password_entry', '')), ensure_ascii=False)}", f"fake_password_entry = {json.dumps(str(totp.get('fake_password_entry', '')), ensure_ascii=False)}", f"period_seconds = {int(totp.get('period_seconds', 30))}", ""]
    agent_end = lines.index("", lines.index("[agent]") + 1)
    lines[agent_end:agent_end] = [f"owner_execution_preferences = {str(bool(agent.get('owner_execution_preferences', True))).lower()}", f"owner_allowed_models = {json.dumps(agent.get('owner_allowed_models', []), ensure_ascii=False)}", f"owner_allowed_reasoning_efforts = {json.dumps(agent.get('owner_allowed_reasoning_efforts', []), ensure_ascii=False)}"]
    target(root).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    path = target(root)
    rendered = path.read_text(encoding="utf-8")
    marker = f"max_outbound_media_per_turn = {int(limits.get('max_outbound_media_per_turn', 3))}"
    context = f"context_max_messages = {int(limits.get('context_max_messages', 100))}\ncontext_max_age_seconds = {int(limits.get('context_max_age_seconds', 604800))}"
    path.write_text(rendered.replace(marker, marker + "\n" + context), encoding="utf-8", newline="\n")


def entry_exists(root: Path, profile: str, entry: str) -> tuple[bool, str]:
    try:
        resolver = KeePassCredentialResolver(root)
        if not resolver.entry_exists(profile, entry):
            return False, "A entrada ainda não existe no cofre MCP."
        found = resolver.password_exists(profile, entry)
        return (True, "A entrada e o token foram encontrados no cofre MCP.") if found else (False, "A entrada existe, mas não contém um token utilizável.")
    except CredentialError as error:
        return False, str(error)


def write_token(root: Path, profile: str, entry: str, token: str, exists: bool) -> tuple[bool, str]:
    try:
        created = KeePassCredentialResolver(root).save_password(profile, entry, token)
        return True, "Token gravado com segurança no cofre MCP." if created else "Token atualizado com segurança no cofre MCP."
    except CredentialError as error:
        return False, str(error)


def save_totp(root: Path, enabled: bool, real_entry: str = "", fake_entry: str = "") -> None:
    data = telegram_data(root); totp = data.setdefault("totp", {})
    totp["enabled"], totp["real_password_entry"], totp["fake_password_entry"] = enabled, real_entry or str(totp.get("real_password_entry") or ""), fake_entry or str(totp.get("fake_password_entry") or "")
    telegram = data.get("telegram", {}); save_telegram(root, str(telegram.get("vault_profile") or telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or ""), data)


def save_app_server(root: Path, enabled: bool | None, idle_timeout: int | None) -> None:
    data = daemon_data(root); app_server = data.setdefault("app_server", {})
    if enabled is not None: app_server["enabled"] = enabled
    if idle_timeout is not None:
        if idle_timeout < 60: raise ValueError("O tempo de inatividade deve ser de ao menos 60 segundos.")
        app_server["idle_timeout_seconds"] = idle_timeout
    save_daemon_data(root, data)


def save_runtime(root: Path, mode: str, executable: str, base_python: str = "") -> None:
    if mode not in {"dedicated", "existing", "global"}: raise ValueError("Modo de runtime inválido.")
    candidate = Path(executable).expanduser()
    if not candidate.is_file(): raise ValueError("O executável Python escolhido não existe.")
    data = daemon_data(root); runtime = data.setdefault("runtime", {})
    runtime.update({"mode": mode, "base_python": str(Path(base_python).expanduser()) if mode == "dedicated" else "", "executable": str(candidate) if mode != "dedicated" else ""})
    save_daemon_data(root, data)


def save_owner_execution_preferences(root: Path, enabled: bool, models: str, efforts: str) -> None:
    data = telegram_data(root); agent = data.setdefault("agent", {})
    allowed_models = list(dict.fromkeys(value.strip() for value in models.split(";") if value.strip()))
    allowed_efforts = list(dict.fromkeys(value.strip() for value in efforts.split(";") if value.strip()))
    if any(value not in {"none", "low", "medium", "high", "xhigh", "max"} for value in allowed_efforts): raise ValueError("Esforços permitidos: none, low, medium, high, xhigh ou max.")
    agent.update({"owner_execution_preferences": enabled, "owner_allowed_models": allowed_models, "owner_allowed_reasoning_efforts": allowed_efforts})
    telegram = data.get("telegram", {}); save_telegram(root, str(telegram.get("vault_profile") or telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or ""), data)


def workspace(root: Path) -> Path:
    try:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        candidate = Path(str(system.get("codex", {}).get("project_directory") or "")).resolve()
    except (OSError, tomllib.TOMLDecodeError, AttributeError) as error:
        raise ValueError("Configure primeiro a pasta de projeto do Shikigami no menu Codex-CLI.") from error
    if not candidate.is_dir():
        raise ValueError("A pasta de projeto do Shikigami não está configurada ou não existe.")
    return candidate


def telegram_staging(root: Path) -> Path:
    """A única área temporária compartilhada pelo gateway e integrações locais."""
    return workspace(root) / ".onmyoji" / "telegram" / "staging"


def save_system(root: Path, data: dict) -> None:
    """Atualiza a configuração local e o bloco gerado do Codex de forma atômica."""
    system_path, codex_path = root / "configs" / "onmyoji-system.toml", root / "config.toml"
    codex = data.get("codex", {})
    if not isinstance(codex, dict): raise ValueError("A configuração Codex-CLI está inválida.")
    shikigami = data.get("shikigami", {})
    if not isinstance(shikigami, dict): shikigami = {}
    identity = str(shikigami.get("identity") or "").strip()
    installed = bool(shikigami.get("installed", False))
    system_text = "\n".join(["schema_version = 1", "", "[shikigami]", f"identity = {json.dumps(identity, ensure_ascii=False)}", f"installed = {str(installed).lower()}", "", "[codex]", *[f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in codex.items()]]) + "\n"
    old_system = system_path.read_text(encoding="utf-8") if system_path.exists() else None
    old_codex = codex_path.read_text(encoding="utf-8") if codex_path.exists() else ""
    begin, end = "# BEGIN ONMYOJI MANAGED CODEX SETTINGS", "# END ONMYOJI MANAGED CODEX SETTINGS"
    managed = "\n".join([begin, "# Gerado por setupOnmyoji.py.", *[f"{key} = {json.dumps(codex.get(key), ensure_ascii=False)}" for key in ("model", "model_reasoning_effort", "approval_policy", "sandbox_mode")], "", "[sandbox_workspace_write]", f"writable_roots = {json.dumps(codex.get('additional_writable_directories', []), ensure_ascii=False)}", end])
    try:
        if begin in old_codex and end in old_codex:
            before, remainder = old_codex.split(begin, 1); _, after = remainder.split(end, 1)
            rendered_codex = before.rstrip() + "\n\n" + managed + after
        else: rendered_codex = old_codex.rstrip() + ("\n\n" if old_codex.strip() else "") + managed + "\n"
        system_path.parent.mkdir(parents=True, exist_ok=True)
        system_path.write_text(system_text, encoding="utf-8", newline="\n")
        codex_path.write_text(rendered_codex, encoding="utf-8", newline="\n")
        tomllib.loads(system_path.read_text(encoding="utf-8")); tomllib.loads(codex_path.read_text(encoding="utf-8"))
    except Exception:
        if old_system is None: system_path.unlink(missing_ok=True)
        else: system_path.write_text(old_system, encoding="utf-8", newline="\n")
        codex_path.write_text(old_codex, encoding="utf-8", newline="\n")
        raise


def ensure_eccovox_staging_access(root: Path, profile: str) -> Path:
    """Autoriza somente o perfil EccoVox a ler e escrever no staging Telegram."""
    if not profile.strip(): raise ValueError("Informe o perfil EccoVox que fará TTS e STT.")
    staging = telegram_staging(root).resolve(); staging.mkdir(parents=True, exist_ok=True)
    mcp = McpConfiguration(root); ecco = mcp.eccovox(); selected = ecco["profiles"].get(profile)
    if not isinstance(selected, dict) or selected.get("enabled") is not True: raise ValueError(f"O perfil MCP EccoVox {profile!r} não existe ou está desabilitado. Configure-o antes no menu MCP.")
    for key in ("readable_roots", "writable_roots"):
        roots = selected.setdefault(key, [])
        if not isinstance(roots, list): raise ValueError(f"O perfil EccoVox {profile!r} possui {key} inválido.")
        if all(Path(str(value)).resolve() != staging for value in roots): roots.append(str(staging))
    try:
        mcp.save_eccovox(ecco)
    except Exception:
        raise
    return staging


def save_voice_reply(root: Path, enabled: bool, profile: str, auto_off_minutes: int) -> Path | None:
    if not 1 <= auto_off_minutes <= 1440: raise ValueError("O desligamento automático deve estar entre 1 e 1440 minutos.")
    staging: Path | None = None
    if enabled: staging = ensure_eccovox_staging_access(root, profile)
    data = telegram_data(root); voice = data.setdefault("voice_reply", {})
    voice.update({"enabled": enabled, "eccovox_profile": profile, "auto_off_minutes": auto_off_minutes})
    telegram = data.get("telegram", {}); save_telegram(root, str(telegram.get("vault_profile") or telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or ""), data)
    return staging


def save_agent_outbound_media(root: Path, enabled: bool) -> Path | None:
    """Configura o envio de arquivos pelo agente sem depender do EccoVox."""
    staging: Path | None = None
    if enabled:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
        if str(system.get("sandbox_mode") or "workspace-write") == "read-only":
            raise ValueError("Escolha o sandbox workspace-write para permitir que o agente prepare arquivos do Telegram.")
        staging = telegram_staging(root).resolve(); staging.mkdir(parents=True, exist_ok=True)
    data = telegram_data(root); telegram = data.setdefault("telegram", {}); telegram["agent_outbound_media"] = enabled
    save_telegram(root, str(telegram.get("vault_profile") or telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or ""), data)
    return staging


def test_agent(root: Path) -> tuple[bool, str]:
    import os
    import subprocess
    marker = root / "configs" / "daemon" / "runtime" / "codex-test.json"
    def record(ok: bool, detail: str) -> None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        safe = __import__("re").sub(r"(?i)(token|password|secret|access[_-]?token)\s*[=:]\s*\S+", r"\1=[redacted]", detail)[-500:]
        marker.write_text(json.dumps({"tested_at": time.time(), "ok": ok, "detail": safe}), encoding="utf-8")
    def failed(detail: str) -> tuple[bool, str]:
        record(False, detail); return False, detail
    try:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
        executable, workspace = str(system.get("executable") or "codex"), Path(str(system.get("project_directory") or ""))
        if not workspace.is_dir(): return failed("Workspace do Shikigami não está configurado.")
        environment = dict(os.environ); environment["CODEX_HOME"] = str(root)
        login = subprocess.run([executable, "login", "status"], cwd=workspace, env=environment, text=True, capture_output=True, timeout=30)
        if login.returncode != 0: return failed("Codex-CLI não está autenticado nesta instância. Use Login no menu Codex-CLI.")
        platform = daemon_data(root)
        app_server_enabled = bool(platform.get("app_server", {}).get("enabled", False))
        if app_server_enabled:
            from onmyoji_daemon.telegram import CodexAppServer, Settings
            from onmyoji_daemon.instructions import InstructionComposer
            instructions = platform.get("instructions", {})
            settings = SimpleNamespace(root=root.resolve(), project=workspace, executable=executable)
            bundle = InstructionComposer(root, root / "configs" / "daemon", str(instructions.get("shikigami_file") or "instructions.md"), bool(instructions.get("enabled", True))).compose(identity=identity(root), telegram=False)
            client = CodexAppServer(settings)
            try:
                client.start()
                parameters = {"cwd": str(workspace), "approvalPolicy": str(system.get("approval_policy") or "never"), "sandbox": str(system.get("sandbox_mode") or "workspace-write"), "developerInstructions": bundle.text}
                if str(system.get("model") or ""): parameters["model"] = str(system["model"])
                thread = client.request("thread/start", parameters).get("thread", {})
                thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
                if not thread_id: return failed("O Codex App Server iniciou, mas não criou uma thread de teste.")
                completed, answer = __import__("threading").Event(), []
                def notification(method: str, params: dict) -> None:
                    if method == "item/completed":
                        item = params.get("item", {})
                        if isinstance(item, dict) and item.get("type") == "agentMessage" and isinstance(item.get("text"), str): answer.append(item["text"])
                    if method == "turn/completed": completed.set()
                client.notification_handler = notification
                sandbox = str(system.get("sandbox_mode") or "workspace-write")
                policy = {"type": "dangerFullAccess"} if sandbox == "danger-full-access" else ({"type": "readOnly", "networkAccess": False} if sandbox == "read-only" else {"type": "workspaceWrite", "writableRoots": [str(workspace)], "networkAccess": False})
                turn_parameters = {"threadId": thread_id, "input": [{"type": "text", "text": "Responda somente com OK."}], "cwd": str(workspace), "approvalPolicy": str(system.get("approval_policy") or "never"), "sandboxPolicy": policy, "effort": str(system.get("model_reasoning_effort") or "medium")}
                if str(system.get("model") or ""): turn_parameters["model"] = str(system["model"])
                client.request("turn/start", turn_parameters)
                if not completed.wait(120) or not answer: return failed("O Codex App Server não retornou a resposta do turno de teste.")
                try: client.request("thread/delete", {"threadId": thread_id})
                except Exception: pass
                record(True, "Codex App Server executou um turno controlado.")
                return True, "Codex App Server executou um turno controlado."
            finally: client.stop()
        return failed("Habilite o Codex App Server antes de testar o agente.")
    except Exception as error: return failed(f"Teste do Codex falhou: {error}")


def instruction_status(root: Path) -> dict[str, object]:
    """Valida a composição sem imprimir conteúdo potencialmente particular."""
    from onmyoji_daemon.instructions import InstructionComposer
    data = daemon_data(root); section = data.get("instructions", {})
    composer = InstructionComposer(root.resolve(), root / "configs" / "daemon", str(section.get("shikigami_file") or "instructions.md"), bool(section.get("enabled", True)))
    bundle = composer.compose(identity=identity(root), telegram=False)
    return {"ok": True, "sources": list(bundle.sources), "baseline_hash": bundle.baseline_hash[:12], "local_file": str(root / "shikigami" / "instructions.md")}


def validation(root: Path) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    def add(state: str, label: str, detail: str = "") -> None: checks.append({"state": state, "label": label, "detail": detail})
    add("ok" if is_installed(root) else "pending", "Daemon da instância", "Instalado." if is_installed(root) else "Use Instalar daemon da instância.")
    checks.extend(keepass_validation(root))
    path = target(root)
    try: data = tomllib.loads(path.read_text(encoding="utf-8")); add("ok", "Estrutura da configuração Telegram", "TOML válido.")
    except FileNotFoundError: add("pending", "Estrutura da configuração Telegram", "Abra Gateway Telegram para criar a configuração."); return checks
    except tomllib.TOMLDecodeError as error: add("error", "Estrutura da configuração Telegram", f"TOML inválido: {error}"); return checks
    telegram = data.get("telegram", {})
    profile, entry = str(telegram.get("vault_profile") or telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or "")
    known = profiles(root)
    add("ok" if profile and profile in known else "error", "Perfil de vault MCP", f"{profile!r} disponível." if profile in known else ("Selecione um perfil de vault MCP existente." if not profile else f"Perfil {profile!r} não encontrado."))
    legacy_entry = entry == "APIs/Telegram:Shikigami"
    add("ok" if entry and not legacy_entry else "pending", "Referência do token", entry if entry and not legacy_entry else "Defina a entrada KeePass do token do bot.")
    try:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
        executable = Path(str(system.get("executable") or ""))
        add("ok" if executable.is_file() else "error", "Executável Codex para serviço", str(executable) if executable.is_file() else "Informe no setup um caminho absoluto para o executável Codex; serviços systemd não herdam o PATH do terminal.")
    except (OSError, tomllib.TOMLDecodeError, AttributeError): add("error", "Executável Codex para serviço", "Não foi possível ler a configuração do Codex-CLI.")
    try:
        platform = daemon_data(root)
        app_server = platform.get("app_server", {})
    except ValueError:
        app_server = data.get("app_server", {})
    enabled, idle = bool(app_server.get("enabled", False)), int(app_server.get("idle_timeout_seconds") or 1800)
    add("ok" if enabled and idle >= 60 else "error", "Codex App Server", (f"Habilitado; encerra após {idle} segundos sem turnos ativos." if enabled else "O gateway exige App Server para aplicar developer instructions."))
    try:
        state = instruction_status(root)
        add("ok", "Developer instructions", f"Composição válida ({', '.join(str(value) for value in state['sources'])}).")
    except Exception as error:
        add("error", "Developer instructions", f"Corrija as instruções do Shikigami: {error}")
    limits = data.get("limits", {})
    try:
        per_file, batch, retained = int(limits.get("max_attachment_bytes", 20 * 1024 * 1024)), int(limits.get("max_batch_attachment_bytes", 50 * 1024 * 1024)), int(limits.get("max_retained_attachment_bytes", 250 * 1024 * 1024))
        add("ok" if 1 <= per_file <= batch <= retained else "error", "Limites de anexos", f"Arquivo: {per_file // 1024 // 1024} MiB; mensagem: {batch // 1024 // 1024} MiB; retenção: {retained // 1024 // 1024} MiB." if 1 <= per_file <= batch <= retained else "Os limites devem obedecer arquivo ≤ mensagem ≤ retenção.")
    except (TypeError, ValueError): add("error", "Limites de anexos", "Os limites precisam ser números inteiros válidos.")
    try:
        workspace = Path(str(tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {}).get("project_directory") or ""))
        activity = workspace / ".onmyoji" / "telegram"
        add("ok" if workspace.is_dir() else "error", "Área de anexos no workspace", str(activity) if workspace.is_dir() else "Configure primeiro o workspace do Shikigami; anexos nunca ficam no CODEX_HOME.")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        workspace = Path(); activity = workspace / ".onmyoji" / "telegram"
        add("error", "Área de anexos no workspace", "Configure primeiro o workspace do Shikigami; anexos nunca ficam no CODEX_HOME.")
    voice = data.get("voice_reply", {})
    if bool(voice.get("enabled", False)):
        profile_name = str(voice.get("eccovox_profile") or "")
        try:
            selected = McpConfiguration(root).eccovox()["profiles"].get(profile_name, {})
            readable, writable = selected.get("readable_roots", []), selected.get("writable_roots", [])
            staging = (activity / "staging").resolve()
            ready = profile_name and selected.get("enabled") is True and isinstance(readable, list) and isinstance(writable, list) and any(Path(str(value)).resolve() == staging for value in readable) and any(Path(str(value)).resolve() == staging for value in writable)
            add("ok" if ready else "error", "EccoVox para respostas em áudio", f"Perfil MCP {profile_name!r} pode ler e gravar no staging Telegram: {staging}" if ready else "Abra Configurar respostas em áudio: o setup autoriza automaticamente o staging no perfil MCP EccoVox e no sandbox do agente.")
        except (OSError, tomllib.TOMLDecodeError, AttributeError, KeyError): add("error", "EccoVox para respostas em áudio", "Configure um perfil MCP EccoVox antes de habilitar respostas em áudio.")
    contacts = root / "configs" / "daemon" / "services" / "telegram" / "contacts.json"
    try:
        values = json.loads(contacts.read_text(encoding="utf-8")); owners = [item for item in values.get("contacts", []) if isinstance(item, dict) and "owner" in item.get("roles", [])]
        add("ok" if owners else "pending", "Owners pareados", f"{len(owners)} owner(s) autorizado(s)." if owners else "Nenhum owner está pareado; inicie o gateway e escolha Parear owner.")
    except (OSError, ValueError, AttributeError): add("pending", "Owners pareados", "Nenhum owner está pareado; inicie o gateway e escolha Parear owner.")
    try:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
        workspace = Path(str(system.get("project_directory") or ""))
        add("ok" if workspace.is_dir() else "error", "Workspace do Shikigami", str(workspace) if workspace.is_dir() else "Configure uma pasta de projeto válida no Codex-CLI.")
    except (OSError, tomllib.TOMLDecodeError): add("error", "Codex-CLI", "Configure o Codex-CLI antes de habilitar o gateway.")
    try:
        marker = json.loads((root / "configs" / "daemon" / "runtime" / "codex-test.json").read_text(encoding="utf-8")); age = time.time() - float(marker.get("tested_at", 0)); passed = bool(marker.get("ok")) and age < 86_400
        add("ok" if passed else "error", "Execução do agente Codex", "Teste controlado aprovado nas últimas 24 horas." if passed else str(marker.get("detail") or "Teste do agente falhou; execute Testar agente Codex."))
    except (OSError, ValueError, TypeError): add("pending", "Execução do agente Codex", "Execute Testar agente Codex antes de habilitar o gateway.")
    try:
        gateway = json.loads((root / "configs" / "daemon" / "services" / "telegram" / "state" / "gateway-status.json").read_text(encoding="utf-8")); last_error = str(gateway.get("last_error") or "")
        if last_error: add("error", "Último erro do gateway", last_error)
        commands = gateway.get("commands", {})
        if isinstance(commands, dict):
            owners, verified, failed = int(commands.get("owners", 0)), int(commands.get("verified", 0)), int(commands.get("failed", 0))
            add("ok" if owners and verified == owners and not failed else "pending", "Comandos privados do owner", f"{verified}/{owners} owner(s) confirmados pelo Telegram." if owners else "Reinicie o gateway ou escolha Atualizar comandos privados.")
    except (OSError, ValueError, AttributeError): pass
    try:
        marker = json.loads((root / "configs" / "daemon" / "runtime" / "telegram-test.json").read_text(encoding="utf-8"))
        age = time.time() - float(marker.get("tested_at", 0))
        failed = bool(marker.get("failed"))
        add("error" if failed else ("ok" if age < 86_400 else "pending"), "Conexão Telegram", str(marker.get("detail") or "Teste de conexão falhou; revise o KeePass e o token.") if failed else ("Token validado nas últimas 24 horas." if age < 86_400 else "Repita o teste de conexão; a validação anterior expirou."))
    except (OSError, ValueError, TypeError): add("pending", "Conexão Telegram", "Execute Testar conexão; o token não é exibido.")
    return checks


def daemon_validation(root: Path) -> list[dict[str, str]]:
    """Diagnóstico da plataforma e de seu registro opcional no sistema operacional."""
    checks: list[dict[str, str]] = []
    def add(state: str, label: str, detail: str) -> None: checks.append({"state": state, "label": label, "detail": detail})
    if not is_installed(root):
        add("pending", "Instalação do daemon", "Use Utilitários e manutenção para instalar o daemon desta instância.")
        return checks
    add("ok", "Instalação do daemon", "Manifesto local encontrado.")
    python = daemon_python(root)
    ready = runtime_ready(root)
    add("ok" if ready else "error", "Runtime Python do daemon", str(python) if ready else "O runtime escolhido ou suas dependências não estão prontos; use Instalar/validar daemon ou Reparar ambiente Python.")
    service = service_info(root)
    if not service:
        add("pending", "Serviço do sistema operacional", "Não instalado; use Operação do daemon para registrar a inicialização persistente.")
    else:
        scope = str(service.get("scope") or "system")
        account = str(service.get("run_as_user") or "conta padrão")
        detail = f"{service.get('name')} · escopo {scope} · conta {account}"
        if os.name == "nt" and account == "LocalSystem": detail += ". Usa o CODEX_HOME e a autenticação persistida da instância."
        add("ok", "Serviço do sistema operacional", detail)
    try:
        platform = daemon_data(root)
        app_server = platform.get("app_server", {})
        enabled, idle = bool(app_server.get("enabled", False)), int(app_server.get("idle_timeout_seconds") or 0)
        add("ok" if enabled and idle >= 60 else "pending", "Codex App Server", f"Habilitado; encerra após {idle} segundos sem turnos ativos." if enabled and idle >= 60 else "Habilite o App Server no wizard do daemon.")
    except (ValueError, TypeError) as error:
        add("error", "Configuração do daemon", str(error))
    try:
        state = instruction_status(root)
        add("ok", "Developer instructions", f"Composição válida ({', '.join(str(value) for value in state['sources'])}).")
    except Exception as error:
        add("error", "Developer instructions", f"Corrija as instruções do Shikigami: {error}")
    try:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
        executable = Path(str(system.get("executable") or ""))
        workspace = Path(str(system.get("project_directory") or ""))
        add("ok" if executable.is_file() else "error", "Executável Codex do daemon", str(executable) if executable.is_file() else "Configure no menu Codex-CLI um caminho absoluto para o executável.")
        add("ok" if workspace.is_dir() else "error", "Workspace do Shikigami", str(workspace) if workspace.is_dir() else "Configure uma pasta de trabalho válida no menu Codex-CLI.")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        add("error", "Configuração Codex-CLI", "Não foi possível ler a configuração central do Shikigami.")
    current_revision, applied_revision = configuration_revision(root), applied_configuration_revision(root)
    if not daemon_reachable(root):
        add("pending", "Configuração aplicada", "O daemon ainda não está em execução.")
    elif current_revision and current_revision == applied_revision:
        add("ok", "Configuração aplicada", "O processo ativo usa a revisão atual da configuração.")
    else:
        add("pending", "Configuração aplicada", "A configuração foi alterada depois do início do processo; reinicie o daemon para aplicá-la.")
    try:
        marker = json.loads((root / "configs" / "daemon" / "runtime" / "codex-test.json").read_text(encoding="utf-8"))
        age = time.time() - float(marker.get("tested_at", 0)); passed = bool(marker.get("ok")) and age < 86_400
        add("ok" if passed else "pending", "Execução do agente Codex", "Teste controlado aprovado nas últimas 24 horas." if passed else str(marker.get("detail") or "Execute o teste pelo wizard do daemon."))
    except (OSError, ValueError, TypeError):
        add("pending", "Execução do agente Codex", "Execute o teste pelo wizard do daemon.")
    add("ok" if daemon_reachable(root) else "pending", "Processo do daemon", "Em execução e respondendo localmente." if daemon_reachable(root) else "Ainda não iniciado; use Operação do daemon após concluir a configuração.")
    return checks


def test_telegram(root: Path) -> tuple[bool, str]:
    from onmyoji_daemon.telegram import McpVault, Settings, TelegramApi
    marker = root / "configs" / "daemon" / "runtime" / "telegram-test.json"
    try:
        settings = Settings.load(root.resolve(), target(root).parent)
        account = TelegramApi(McpVault(settings).read(settings.token_entry)).call("getMe")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"tested_at": time.time(), "bot": str(account.get("username") or account.get("first_name") or "")}), encoding="utf-8")
        return True, f"Bot autenticado: @{account.get('username') or account.get('first_name') or 'sem username'}"
    except Exception as error:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"tested_at": time.time(), "failed": True, "detail": str(error)[-500:]}), encoding="utf-8")
        if isinstance(error, HTTPError) and error.code == 401:
            return False, "Telegram recusou o token do bot (HTTP 401). Confira o token criado no BotFather."
        return False, f"Não foi possível autenticar o bot: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--onmyoji-root", default=str(ROOT)); parser.add_argument("--action", choices=["bootstrap", "daemon-bootstrap", "validate", "validation-json", "keepass-validation-json", "daemon-validation-json", "instructions-status", "profiles", "set-runtime", "set-credential", "set-totp", "set-app-server", "set-owner-execution-preferences", "set-voice-reply", "set-agent-outbound-media", "check-entry", "write-token", "test-telegram", "test-agent"], default="bootstrap"); parser.add_argument("--profile"); parser.add_argument("--entry"); parser.add_argument("--real-entry"); parser.add_argument("--fake-entry"); parser.add_argument("--runtime-mode"); parser.add_argument("--python"); parser.add_argument("--base-python"); parser.add_argument("--enabled", action="store_true"); parser.add_argument("--disabled", action="store_true"); parser.add_argument("--idle-timeout", type=int); parser.add_argument("--auto-off-minutes", type=int); parser.add_argument("--models", default=""); parser.add_argument("--efforts", default=""); args = parser.parse_args()
    root = Path(args.onmyoji_root).resolve()
    if args.action == "bootstrap": print(bootstrap(root)); return 0
    if args.action == "daemon-bootstrap": print(bootstrap_daemon(root)); return 0
    if args.action == "instructions-status": print(json.dumps(instruction_status(root), ensure_ascii=False)); return 0
    if args.action == "profiles": print(json.dumps(profiles(root), ensure_ascii=False)); return 0
    if args.action == "keepass-validation-json": print(json.dumps(keepass_validation(root), ensure_ascii=False)); return 0
    if args.action == "set-runtime":
        if not args.runtime_mode or not args.python: parser.error("--runtime-mode e --python são obrigatórios")
        save_runtime(root, args.runtime_mode, args.python, args.base_python or args.python); print("Runtime do daemon salvo."); return 0
    if args.action == "set-credential":
        if not args.profile or not args.entry: parser.error("--profile e --entry são obrigatórios")
        bootstrap(root); save_telegram(root, args.profile, args.entry); print("Credencial configurada."); return 0
    if args.action == "set-totp":
        bootstrap(root); save_totp(root, args.enabled, args.real_entry or "", args.fake_entry or ""); print("TOTP configurado."); return 0
    if args.action == "set-app-server":
        if args.enabled and args.disabled: parser.error("--enabled e --disabled não podem ser usados juntos")
        bootstrap_daemon(root); save_app_server(root, True if args.enabled else (False if args.disabled else None), args.idle_timeout); print("Configuração do App Server salva."); return 0
    if args.action == "set-owner-execution-preferences":
        bootstrap(root); save_owner_execution_preferences(root, not args.disabled, args.models, args.efforts); print("Preferências de execução do owner salvas."); return 0
    if args.action == "set-voice-reply":
        bootstrap(root); staging = save_voice_reply(root, args.enabled, args.profile or "", args.auto_off_minutes or 15)
        print(f"Configuração de resposta por áudio salva. Staging autorizado: {staging}" if staging else "Configuração de resposta por áudio salva."); return 0
    if args.action == "set-agent-outbound-media":
        bootstrap(root); staging = save_agent_outbound_media(root, args.enabled)
        print(f"Envio de arquivos do agente habilitado. Staging preparado: {staging}" if staging else "Envio de arquivos do agente desabilitado."); return 0
    if args.action == "check-entry":
        if not args.profile or not args.entry: parser.error("--profile e --entry são obrigatórios")
        exists, message = entry_exists(root, args.profile, args.entry); print(json.dumps({"exists": exists, "message": message}, ensure_ascii=False)); return 0
    if args.action == "write-token":
        if not args.profile or not args.entry: parser.error("--profile e --entry são obrigatórios")
        token = sys.stdin.read().strip()
        if not token: print("Token não informado."); return 2
        exists, _message = entry_exists(root, args.profile, args.entry)
        ok, message = write_token(root, args.profile, args.entry, token, exists); print(message); return 0 if ok else 2
    if args.action == "daemon-validation-json": print(json.dumps(daemon_validation(root), ensure_ascii=False)); return 0
    if args.action in {"validate", "validation-json"}:
        values = validation(root); ok = not any(item["state"] == "error" for item in values)
        if args.action == "validation-json": print(json.dumps(values, ensure_ascii=False));
        else:
            for item in values: print(f"[{item['state']}] {item['label']}: {item['detail']}")
        return 0 if ok else 2
    ok, message = test_agent(root) if args.action == "test-agent" else test_telegram(root); print(message); return 0 if ok else 2


if __name__ == "__main__": raise SystemExit(main())
