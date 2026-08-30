#!/usr/bin/env python3
"""Operações não interativas usadas pela central de configuração do Onmyōji."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
import time
import tomllib
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "src"))
from onmyoji_daemon.management import is_installed
from onmyoji_daemon.instance import identity

ROOT = PROJECT.parent
MODEL = PROJECT / "configs" / "telegram.toml.model"
INSTRUCTION_MODEL = PROJECT / "configs" / "shikigami.md.model"


def target(root: Path) -> Path: return root / "shikigami" / "daemon" / "telegram.toml"


def bootstrap(root: Path) -> Path:
    path = target(root)
    if not path.exists(): path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(MODEL, path)
    instruction = root / "shikigami" / "instructions.md"
    if not instruction.exists(): instruction.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(INSTRUCTION_MODEL, instruction)
    return path


def profiles(root: Path) -> list[str]:
    try: values = tomllib.loads((root / "configs" / "keepass.toml").read_text(encoding="utf-8")).get("profiles", {})
    except (OSError, tomllib.TOMLDecodeError): return []
    return sorted(name for name, value in values.items() if name != "example" and isinstance(value, dict)) if isinstance(values, dict) else []


def telegram_data(root: Path) -> dict:
    return tomllib.loads(target(root).read_text(encoding="utf-8"))


def save_telegram(root: Path, profile: str, entry: str, data: dict | None = None) -> None:
    data = data or telegram_data(root)
    telegram = data.setdefault("telegram", {}); telegram["keepass_profile"] = profile; telegram["token_entry"] = entry
    agent, app_server, limits, totp, voice = data.setdefault("agent", {}), data.setdefault("app_server", {}), data.setdefault("limits", {}), data.setdefault("totp", {}), data.setdefault("voice_reply", {})
    instructions = data.get('instructions', {})
    lines = ["schema_version = 1", f"enabled = {str(bool(data.get('enabled', False))).lower()}", "", "[telegram]", f"keepass_profile = {json.dumps(profile, ensure_ascii=False)}", f"token_entry = {json.dumps(entry, ensure_ascii=False)}", f"poll_timeout_seconds = {int(telegram.get('poll_timeout_seconds', 30))}", "", "[agent]", f"max_parallel_conversations = {int(agent.get('max_parallel_conversations', 1))}", f"turn_timeout_seconds = {int(agent.get('turn_timeout_seconds', 900))}", "", "[instructions]", f"enabled = {str(bool(instructions.get('enabled', True))).lower()}", "shikigami_file = \"instructions.md\"", "", "[app_server]", f"enabled = {str(bool(app_server.get('enabled', False))).lower()}", f"idle_timeout_seconds = {int(app_server.get('idle_timeout_seconds', 1800))}", "", "[limits]", f"max_attachment_bytes = {int(limits.get('max_attachment_bytes', 20971520))}", f"max_batch_attachment_bytes = {int(limits.get('max_batch_attachment_bytes', 52428800))}", f"max_pending_items = {int(limits.get('max_pending_items', 50))}", f"max_retained_attachment_bytes = {int(limits.get('max_retained_attachment_bytes', 262144000))}", f"max_outbound_media_bytes = {int(limits.get('max_outbound_media_bytes', 20971520))}", f"max_outbound_media_per_turn = {int(limits.get('max_outbound_media_per_turn', 3))}", "", "[voice_reply]", f"enabled = {str(bool(voice.get('enabled', False))).lower()}", f"eccovox_profile = {json.dumps(str(voice.get('eccovox_profile', '')), ensure_ascii=False)}", f"language = {json.dumps(str(voice.get('language', 'pt-BR')), ensure_ascii=False)}", f"voice = {json.dumps(str(voice.get('voice', '')), ensure_ascii=False)}", f"speed = {float(voice.get('speed', 1.0))}", f"response_format = {json.dumps(str(voice.get('response_format', 'opus')), ensure_ascii=False)}", f"max_text_characters = {int(voice.get('max_text_characters', 3500))}", f"auto_off_minutes = {int(voice.get('auto_off_minutes', 15))}", f"fallback_to_text = {str(bool(voice.get('fallback_to_text', True))).lower()}", f"agent_outbound_media = {str(bool(voice.get('agent_outbound_media', False))).lower()}", "", "[totp]", f"enabled = {str(bool(totp.get('enabled', False))).lower()}", f"real_password_entry = {json.dumps(str(totp.get('real_password_entry', '')), ensure_ascii=False)}", f"fake_password_entry = {json.dumps(str(totp.get('fake_password_entry', '')), ensure_ascii=False)}", f"period_seconds = {int(totp.get('period_seconds', 30))}", ""]
    agent_end = lines.index("", lines.index("[agent]") + 1)
    lines[agent_end:agent_end] = [f"owner_execution_preferences = {str(bool(agent.get('owner_execution_preferences', True))).lower()}", f"owner_allowed_models = {json.dumps(agent.get('owner_allowed_models', []), ensure_ascii=False)}", f"owner_allowed_reasoning_efforts = {json.dumps(agent.get('owner_allowed_reasoning_efforts', []), ensure_ascii=False)}"]
    target(root).write_text("\n".join(lines), encoding="utf-8", newline="\n")


def render_keepass_config(data: dict) -> str:
    """Renderiza somente o contrato local v1 para uma autorização administrativa efêmera."""
    def quote(value: str) -> str: return json.dumps(value, ensure_ascii=False)
    def values(items: list[str]) -> str: return "[" + ", ".join(quote(str(item)) for item in items) + "]"
    lines = ["schema_version = 1", "", "[defaults]", f"timeout_seconds = {int(data.get('defaults', {}).get('timeout_seconds', 30))}"]
    for name, vault in sorted(data.get("vaults", {}).items()):
        database = vault.get("database", {})
        lines.extend(["", f"[vaults.{name}]", f"cli_command = {values(vault.get('cli_command', []))}", "", f"[vaults.{name}.database]", f"windows = {quote(str(database.get('windows', '')))}", f"linux = {quote(str(database.get('linux', '')))}"])
    for name, profile in sorted(data.get("profiles", {}).items()):
        auth = profile.get("auth", {}); windows, linux = auth.get("windows", {}), auth.get("linux", {})
        lines.extend(["", f"[profiles.{name}]", f"vault = {quote(str(profile.get('vault', '')))}", f"access = {quote(str(profile.get('access', 'read_only')))}", f"allowed_operations = {values(profile.get('allowed_operations', []))}", f"allowed_entry_roots = {values(profile.get('allowed_entry_roots', []))}", f"allowed_attachment_roots = {values(profile.get('allowed_attachment_roots', []))}", "", f"[profiles.{name}.auth]", f"allowed_modes = {values(auth.get('allowed_modes', []))}", "", f"[profiles.{name}.auth.windows]", f"mode = {quote(str(windows.get('mode', '')))}", f"target = {quote(str(windows.get('target', '')))}", "", f"[profiles.{name}.auth.linux]", f"mode = {quote(str(linux.get('mode', '')))}", f"command = {values(linux.get('command', []))}"])
    return "\n".join(lines) + "\n"


def administrative_config(root: Path, profile: str) -> tuple[Path, str]:
    """Cria um perfil write-only-to-setup que nunca é persistido no CODEX_HOME."""
    source = root / "configs" / "keepass.toml"
    data = tomllib.loads(source.read_text(encoding="utf-8")); profiles = data.get("profiles", {})
    if not isinstance(profiles.get(profile), dict): raise ValueError("Perfil KeePass não encontrado.")
    temporary_profile = "onmyoji_setup_admin"
    clone = copy.deepcopy(profiles[profile]); clone["access"] = "read_write"; clone["allowed_operations"] = ["list", "list.totp", "read", "attachment.export", "add", "edit", "delete", "copy", "attachment.import", "attachment.delete"]
    data["profiles"] = dict(profiles); data["profiles"][temporary_profile] = clone
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".toml", prefix="onmyoji-keepass-setup-", delete=False)
    try:
        handle.write(render_keepass_config(data)); return Path(handle.name), temporary_profile
    finally: handle.close()


def vault_request(root: Path, profile: str, request: dict, administrative: bool = False) -> tuple[bool, dict | str]:
    """Chama o wrapper pelo stdin; segredos nunca são colocados na linha de comando."""
    import subprocess
    wrapper = root / "available-skills" / "keepass-vault" / "scripts" / "keepass_vault.py"; config, selected = root / "configs" / "keepass.toml", profile
    temporary: Path | None = None
    try:
        if administrative: temporary, selected = administrative_config(root, profile); config = temporary
        process = subprocess.run([sys.executable, str(wrapper), "--config", str(config), "--profile", selected], input=json.dumps(request), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=45)
    finally:
        if temporary: temporary.unlink(missing_ok=True)
    try: value = json.loads(process.stdout)
    except ValueError: return False, "O wrapper KeePass retornou uma resposta inválida."
    if not value.get("ok"):
        error = value.get("error", {}); message = error.get("message") if isinstance(error, dict) else str(error)
        return False, str(message or "O KeePass recusou a operação.")
    return True, value.get("result") or {}


def entry_exists(root: Path, profile: str, entry: str) -> tuple[bool, str]:
    ok, value = vault_request(root, profile, {"operation": "read", "entry": {"path": entry}, "field": "password"})
    if ok and isinstance(value, dict) and str(value.get("value") or ""):
        return True, "A entrada e o token foram encontrados no KeePass."
    if ok: return False, "A entrada existe, mas não contém um token utilizável."
    # O wrapper deliberadamente não diferencia uma entrada ausente de outras falhas sem expor detalhes do cofre.
    return False, str(value)


def write_token(root: Path, profile: str, entry: str, token: str, exists: bool) -> tuple[bool, str]:
    operation = "edit" if exists else "add"
    # O token integra exclusivamente o JSON enviado pelo stdin ao wrapper.
    ok, value = vault_request(root, profile, {"operation": operation, "path": entry, "values": {"password": token}}, administrative=True)
    return (True, "Token gravado com segurança no KeePass.") if ok else (False, str(value))


def save_totp(root: Path, enabled: bool, real_entry: str = "", fake_entry: str = "") -> None:
    data = telegram_data(root); totp = data.setdefault("totp", {})
    totp["enabled"], totp["real_password_entry"], totp["fake_password_entry"] = enabled, real_entry or str(totp.get("real_password_entry") or ""), fake_entry or str(totp.get("fake_password_entry") or "")
    telegram = data.get("telegram", {}); save_telegram(root, str(telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or ""), data)


def save_app_server(root: Path, enabled: bool | None, idle_timeout: int | None) -> None:
    data = telegram_data(root); app_server = data.setdefault("app_server", {})
    if enabled is not None: app_server["enabled"] = enabled
    if idle_timeout is not None:
        if idle_timeout < 60: raise ValueError("O tempo de inatividade deve ser de ao menos 60 segundos.")
        app_server["idle_timeout_seconds"] = idle_timeout
    telegram = data.get("telegram", {}); save_telegram(root, str(telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or ""), data)


def save_owner_execution_preferences(root: Path, enabled: bool, models: str, efforts: str) -> None:
    data = telegram_data(root); agent = data.setdefault("agent", {})
    allowed_models = list(dict.fromkeys(value.strip() for value in models.split(";") if value.strip()))
    allowed_efforts = list(dict.fromkeys(value.strip() for value in efforts.split(";") if value.strip()))
    if any(value not in {"none", "low", "medium", "high", "xhigh", "max"} for value in allowed_efforts): raise ValueError("Esforços permitidos: none, low, medium, high, xhigh ou max.")
    agent.update({"owner_execution_preferences": enabled, "owner_allowed_models": allowed_models, "owner_allowed_reasoning_efforts": allowed_efforts})
    telegram = data.get("telegram", {}); save_telegram(root, str(telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or ""), data)


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


def render_eccovox(data: dict) -> str:
    defaults = data.get("defaults", {})
    lines = ["schema_version = 1", "", "[defaults]"]
    for key, default in (("request_timeout_seconds", 120), ("max_audio_bytes", 10485760), ("max_text_characters", 4000)):
        lines.append(f"{key} = {int(defaults.get(key, default))}")
    for name, profile in sorted(data.get("profiles", {}).items()):
        lines.extend(["", f"[profiles.{name}]"])
        lines.append(f"base_url = {json.dumps(str(profile.get('base_url') or ''), ensure_ascii=False)}")
        for key in ("readable_roots", "writable_roots"):
            values = profile.get(key, [])
            if not isinstance(values, list): raise ValueError(f"Perfil EccoVox {name!r} possui {key} inválido.")
            lines.append(f"{key} = {json.dumps([str(value) for value in values], ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def save_system(root: Path, data: dict) -> None:
    """Atualiza a configuração local e o bloco gerado do Codex de forma atômica."""
    system_path, codex_path = root / "configs" / "onmyoji-system.toml", root / "config.toml"
    codex = data.get("codex", {})
    if not isinstance(codex, dict): raise ValueError("A configuração Codex-CLI está inválida.")
    system_text = "\n".join(["schema_version = 1", "", "[codex]", *[f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in codex.items()]]) + "\n"
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


def ensure_gateway_activity_permissions(root: Path, profile: str) -> Path:
    """Autoriza apenas o staging do Telegram para EccoVox e para o sandbox do agente."""
    if not profile.strip(): raise ValueError("Informe o perfil EccoVox que fará TTS e STT.")
    staging = telegram_staging(root).resolve(); staging.mkdir(parents=True, exist_ok=True)
    config = root / "configs" / "eccovox.toml"
    try: ecco = tomllib.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError as error: raise ValueError(f"Não existe configuração EccoVox. Configure antes o perfil {profile!r} na skill EccoVox.") from error
    except tomllib.TOMLDecodeError as error: raise ValueError(f"A configuração EccoVox é inválida: {error}") from error
    profiles_data = ecco.get("profiles", {})
    selected = profiles_data.get(profile) if isinstance(profiles_data, dict) else None
    if not isinstance(selected, dict): raise ValueError(f"O perfil EccoVox {profile!r} não existe. Configure-o antes na skill EccoVox.")
    for key in ("readable_roots", "writable_roots"):
        roots = selected.setdefault(key, [])
        if not isinstance(roots, list): raise ValueError(f"O perfil EccoVox {profile!r} possui {key} inválido.")
        if all(Path(str(value)).resolve() != staging for value in roots): roots.append(str(staging))
    old_ecco = config.read_text(encoding="utf-8")
    try:
        config.write_text(render_eccovox(ecco), encoding="utf-8", newline="\n")
        tomllib.loads(config.read_text(encoding="utf-8"))
        system_path = root / "configs" / "onmyoji-system.toml"
        system = tomllib.loads(system_path.read_text(encoding="utf-8")); codex = system.setdefault("codex", {})
        roots = codex.setdefault("additional_writable_directories", [])
        if not isinstance(roots, list): raise ValueError("A lista de diretórios adicionais do Codex-CLI está inválida.")
        if all(Path(str(value)).resolve() != staging for value in roots): roots.append(str(staging))
        save_system(root, system)
    except Exception:
        config.write_text(old_ecco, encoding="utf-8", newline="\n")
        raise
    return staging


def save_voice_reply(root: Path, enabled: bool, profile: str, auto_off_minutes: int, outbound_media: bool) -> Path | None:
    if not 1 <= auto_off_minutes <= 1440: raise ValueError("O desligamento automático deve estar entre 1 e 1440 minutos.")
    staging: Path | None = None
    if enabled or outbound_media: staging = ensure_gateway_activity_permissions(root, profile)
    data = telegram_data(root); voice = data.setdefault("voice_reply", {})
    voice.update({"enabled": enabled, "eccovox_profile": profile, "auto_off_minutes": auto_off_minutes, "agent_outbound_media": outbound_media})
    telegram = data.get("telegram", {}); save_telegram(root, str(telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or ""), data)
    return staging


def test_agent(root: Path) -> tuple[bool, str]:
    import os
    import subprocess
    try:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
        executable, workspace = str(system.get("executable") or "codex"), Path(str(system.get("project_directory") or ""))
        if not workspace.is_dir(): return False, "Workspace do Shikigami não está configurado."
        environment = dict(os.environ); environment["CODEX_HOME"] = str(root)
        login = subprocess.run([executable, "login", "status"], cwd=workspace, env=environment, text=True, capture_output=True, timeout=30)
        if login.returncode != 0: return False, "Codex-CLI não está autenticado nesta instância. Use Login no menu Codex-CLI."
        data = telegram_data(root)
        app_server_enabled = bool(data.get("app_server", {}).get("enabled", False))
        if app_server_enabled:
            from onmyoji_daemon.telegram import CodexAppServer, Settings
            from onmyoji_daemon.instructions import InstructionComposer
            settings = Settings.load(root.resolve(), target(root).parent)
            bundle = InstructionComposer(root, target(root).parent, settings.instructions_file, settings.instructions_enabled).compose(identity=identity(root), telegram=True)
            client = CodexAppServer(settings)
            try:
                client.start()
                parameters = {"cwd": str(workspace), "approvalPolicy": str(system.get("approval_policy") or "never"), "sandbox": str(system.get("sandbox_mode") or "workspace-write"), "developerInstructions": bundle.text}
                if str(system.get("model") or ""): parameters["model"] = str(system["model"])
                thread = client.request("thread/start", parameters).get("thread", {})
                thread_id = str(thread.get("id") or "") if isinstance(thread, dict) else ""
                if not thread_id: return False, "O Codex App Server iniciou, mas não criou uma thread de teste."
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
                if not completed.wait(120) or not answer: return False, "O Codex App Server não retornou a resposta do turno de teste."
                try: client.request("thread/delete", {"threadId": thread_id})
                except Exception: pass
                marker = root / "configs" / "daemon" / "runtime" / "codex-test.json"; marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(json.dumps({"tested_at": time.time(), "ok": True, "detail": "Codex App Server executou um turno controlado."}), encoding="utf-8")
                return True, "Codex App Server executou um turno controlado."
            finally: client.stop()
        return False, "Habilite o Codex App Server antes de testar o agente; o gateway e o teste usam as mesmas developer instructions."
    except Exception as error: return False, f"Teste do Codex falhou: {error}"


def instruction_status(root: Path) -> dict[str, object]:
    """Valida a composição sem imprimir conteúdo potencialmente particular."""
    bootstrap(root)
    from onmyoji_daemon.instructions import InstructionComposer
    data = telegram_data(root); section = data.get("instructions", {})
    composer = InstructionComposer(root.resolve(), target(root).parent, str(section.get("shikigami_file") or "instructions.md"), bool(section.get("enabled", True)))
    bundle = composer.compose(identity=identity(root), telegram=True)
    return {"ok": True, "sources": list(bundle.sources), "baseline_hash": bundle.baseline_hash[:12], "local_file": str(root / "shikigami" / "instructions.md")}


def validation(root: Path) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    def add(state: str, label: str, detail: str = "") -> None: checks.append({"state": state, "label": label, "detail": detail})
    add("ok" if is_installed(root) else "pending", "Daemon da instância", "Instalado." if is_installed(root) else "Use Instalar daemon da instância.")
    path = target(root)
    try: data = tomllib.loads(path.read_text(encoding="utf-8")); add("ok", "Estrutura da configuração Telegram", "TOML válido.")
    except FileNotFoundError: add("pending", "Estrutura da configuração Telegram", "Abra Gateway Telegram para criar a configuração."); return checks
    except tomllib.TOMLDecodeError as error: add("error", "Estrutura da configuração Telegram", f"TOML inválido: {error}"); return checks
    telegram = data.get("telegram", {})
    profile, entry = str(telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or "")
    known = profiles(root)
    add("ok" if profile and profile in known else "error", "Perfil KeePass", f"{profile!r} disponível." if profile in known else ("Selecione um perfil KeePass existente." if not profile else f"Perfil {profile!r} não encontrado."))
    add("ok" if entry and not entry.endswith(":Shikigami") else "pending", "Referência do token", entry if entry and not entry.endswith(":Shikigami") else "Defina a entrada KeePass do token do bot.")
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
    workspace = Path(str(tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {}).get("project_directory") or ""))
    activity = workspace / ".onmyoji" / "telegram"
    add("ok" if workspace.is_dir() else "error", "Área de anexos no workspace", str(activity) if workspace.is_dir() else "Configure primeiro o workspace do Shikigami; anexos nunca ficam no CODEX_HOME.")
    voice = data.get("voice_reply", {})
    if bool(voice.get("enabled", False)):
        profile_name, config = str(voice.get("eccovox_profile") or ""), root / "configs" / "eccovox.toml"
        try:
            profiles_data = tomllib.loads(config.read_text(encoding="utf-8")).get("profiles", {}); selected = profiles_data.get(profile_name, {}) if isinstance(profiles_data, dict) else {}
            readable, writable = selected.get("readable_roots", []), selected.get("writable_roots", [])
            staging = (activity / "staging").resolve()
            ready = profile_name and isinstance(readable, list) and isinstance(writable, list) and any(Path(str(value)).resolve() == staging for value in readable) and any(Path(str(value)).resolve() == staging for value in writable)
            add("ok" if ready else "error", "EccoVox para respostas em áudio", f"Perfil {profile_name!r} pode ler e gravar somente no staging Telegram: {staging}" if ready else "Abra Configurar respostas em áudio: o setup autoriza automaticamente o staging no perfil EccoVox e no sandbox do agente.")
        except (OSError, tomllib.TOMLDecodeError, AttributeError): add("error", "EccoVox para respostas em áudio", "Configure a skill EccoVox antes de habilitar respostas em áudio.")
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


def test_telegram(root: Path) -> tuple[bool, str]:
    from onmyoji_daemon.telegram import Settings, TelegramApi, Vault
    marker = root / "configs" / "daemon" / "runtime" / "telegram-test.json"
    try:
        settings = Settings.load(root.resolve(), target(root).parent)
        account = TelegramApi(Vault(settings).read(settings.token_entry)).call("getMe")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"tested_at": time.time(), "bot": str(account.get("username") or account.get("first_name") or "")}), encoding="utf-8")
        return True, f"Bot autenticado: @{account.get('username') or account.get('first_name') or 'sem username'}"
    except Exception as error:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"tested_at": time.time(), "failed": True, "detail": str(error)[-500:]}), encoding="utf-8")
        return False, f"Não foi possível autenticar o bot: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--onmyoji-root", default=str(ROOT)); parser.add_argument("--action", choices=["bootstrap", "validate", "validation-json", "instructions-status", "profiles", "set-credential", "set-totp", "set-app-server", "set-owner-execution-preferences", "set-voice-reply", "check-entry", "write-token", "test-telegram", "test-agent"], default="bootstrap"); parser.add_argument("--profile"); parser.add_argument("--entry"); parser.add_argument("--real-entry"); parser.add_argument("--fake-entry"); parser.add_argument("--enabled", action="store_true"); parser.add_argument("--disabled", action="store_true"); parser.add_argument("--idle-timeout", type=int); parser.add_argument("--auto-off-minutes", type=int); parser.add_argument("--agent-outbound-media", action="store_true"); parser.add_argument("--models", default=""); parser.add_argument("--efforts", default=""); args = parser.parse_args()
    root = Path(args.onmyoji_root).resolve()
    if args.action == "bootstrap": print(bootstrap(root)); return 0
    if args.action == "instructions-status": print(json.dumps(instruction_status(root), ensure_ascii=False)); return 0
    if args.action == "profiles": print(json.dumps(profiles(root), ensure_ascii=False)); return 0
    if args.action == "set-credential":
        if not args.profile or not args.entry: parser.error("--profile e --entry são obrigatórios")
        bootstrap(root); save_telegram(root, args.profile, args.entry); print("Credencial configurada."); return 0
    if args.action == "set-totp":
        bootstrap(root); save_totp(root, args.enabled, args.real_entry or "", args.fake_entry or ""); print("TOTP configurado."); return 0
    if args.action == "set-app-server":
        if args.enabled and args.disabled: parser.error("--enabled e --disabled não podem ser usados juntos")
        bootstrap(root); save_app_server(root, True if args.enabled else (False if args.disabled else None), args.idle_timeout); print("Configuração do App Server salva."); return 0
    if args.action == "set-owner-execution-preferences":
        bootstrap(root); save_owner_execution_preferences(root, not args.disabled, args.models, args.efforts); print("Preferências de execução do owner salvas."); return 0
    if args.action == "set-voice-reply":
        bootstrap(root); staging = save_voice_reply(root, args.enabled, args.profile or "", args.auto_off_minutes or 15, args.agent_outbound_media)
        print(f"Configuração de resposta por áudio salva. Staging autorizado: {staging}" if staging else "Configuração de resposta por áudio salva."); return 0
    if args.action == "check-entry":
        if not args.profile or not args.entry: parser.error("--profile e --entry são obrigatórios")
        exists, message = entry_exists(root, args.profile, args.entry); print(json.dumps({"exists": exists, "message": message}, ensure_ascii=False)); return 0
    if args.action == "write-token":
        if not args.profile or not args.entry: parser.error("--profile e --entry são obrigatórios")
        token = sys.stdin.read().rstrip("\r\n")
        if not token: print("Token não informado."); return 2
        exists, _message = entry_exists(root, args.profile, args.entry)
        ok, message = write_token(root, args.profile, args.entry, token, exists); print(message); return 0 if ok else 2
    if args.action in {"validate", "validation-json"}:
        values = validation(root); ok = not any(item["state"] == "error" for item in values)
        if args.action == "validation-json": print(json.dumps(values, ensure_ascii=False));
        else:
            for item in values: print(f"[{item['state']}] {item['label']}: {item['detail']}")
        return 0 if ok else 2
    ok, message = test_agent(root) if args.action == "test-agent" else test_telegram(root); print(message); return 0 if ok else 2


if __name__ == "__main__": raise SystemExit(main())
