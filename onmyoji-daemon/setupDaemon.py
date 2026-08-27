#!/usr/bin/env python3
"""Operações não interativas usadas pela central de configuração do Onmyōji."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "src"))
from onmyoji_daemon.management import is_installed

ROOT = PROJECT.parent
MODEL = PROJECT / "configs" / "telegram.toml.model"


def target(root: Path) -> Path: return root / "configs" / "daemon" / "services" / "telegram" / "telegram.toml"


def bootstrap(root: Path) -> Path:
    path = target(root)
    if not path.exists(): path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(MODEL, path)
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
    agent, limits, totp = data.setdefault("agent", {}), data.setdefault("limits", {}), data.setdefault("totp", {})
    lines = ["schema_version = 1", f"enabled = {str(bool(data.get('enabled', False))).lower()}", "", "[telegram]", f"keepass_profile = {json.dumps(profile, ensure_ascii=False)}", f"token_entry = {json.dumps(entry, ensure_ascii=False)}", f"poll_timeout_seconds = {int(telegram.get('poll_timeout_seconds', 30))}", "", "[agent]", f"max_parallel_conversations = {int(agent.get('max_parallel_conversations', 1))}", f"turn_timeout_seconds = {int(agent.get('turn_timeout_seconds', 900))}", f"developer_file = {json.dumps(str(agent.get('developer_file', '')), ensure_ascii=False)}", "", "[limits]", f"max_attachment_bytes = {int(limits.get('max_attachment_bytes', 20971520))}", f"max_pending_items = {int(limits.get('max_pending_items', 50))}", "", "[totp]", f"enabled = {str(bool(totp.get('enabled', False))).lower()}", f"real_password_entry = {json.dumps(str(totp.get('real_password_entry', '')), ensure_ascii=False)}", f"fake_password_entry = {json.dumps(str(totp.get('fake_password_entry', '')), ensure_ascii=False)}", f"period_seconds = {int(totp.get('period_seconds', 30))}", ""]
    target(root).write_text("\n".join(lines), encoding="utf-8", newline="\n")


def vault_request(root: Path, profile: str, request: dict) -> tuple[bool, dict | str]:
    """Chama o wrapper pelo stdin; segredos nunca são colocados na linha de comando."""
    import subprocess
    wrapper = root / "available-skills" / "keepass-vault" / "scripts" / "keepass_vault.py"
    process = subprocess.run([sys.executable, str(wrapper), "--config", str(root / "configs" / "keepass.toml"), "--profile", profile], input=json.dumps(request), text=True, capture_output=True, timeout=45)
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
    ok, value = vault_request(root, profile, {"operation": operation, "path": entry, "values": {"password": token}})
    return (True, "Token gravado com segurança no KeePass.") if ok else (False, str(value))


def save_totp(root: Path, enabled: bool, real_entry: str = "", fake_entry: str = "") -> None:
    data = telegram_data(root); totp = data.setdefault("totp", {})
    totp["enabled"], totp["real_password_entry"], totp["fake_password_entry"] = enabled, real_entry or str(totp.get("real_password_entry") or ""), fake_entry or str(totp.get("fake_password_entry") or "")
    telegram = data.get("telegram", {}); save_telegram(root, str(telegram.get("keepass_profile") or ""), str(telegram.get("token_entry") or ""), data)


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
        command = [executable, "exec", "-C", str(workspace), "--skip-git-repo-check", "-m", str(system.get("model") or ""), "-c", f"model_reasoning_effort={json.dumps(str(system.get('model_reasoning_effort') or 'medium'))}", "-s", str(system.get("sandbox_mode") or "workspace-write"), "Responda somente com OK."]
        run = subprocess.run(command, cwd=workspace, env=environment, text=True, capture_output=True, timeout=120)
        message = (run.stderr or run.stdout or "sem saída").strip().replace("\n", " ")[-500:]
        marker = root / "configs" / "daemon" / "runtime" / "codex-test.json"; marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"tested_at": time.time(), "ok": run.returncode == 0, "detail": "Codex respondeu ao teste controlado." if run.returncode == 0 else message}), encoding="utf-8")
        return (True, "Codex respondeu ao teste controlado.") if run.returncode == 0 else (False, f"Codex exec falhou: {message}")
    except Exception as error: return False, f"Teste do Codex falhou: {error}"


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
    except (OSError, ValueError, AttributeError): pass
    try:
        marker = json.loads((root / "configs" / "daemon" / "runtime" / "telegram-test.json").read_text(encoding="utf-8"))
        age = time.time() - float(marker.get("tested_at", 0))
        add("ok" if age < 86_400 else "pending", "Conexão Telegram", "Token validado nas últimas 24 horas." if age < 86_400 else "Repita o teste de conexão; a validação anterior expirou.")
    except (OSError, ValueError, TypeError): add("pending", "Conexão Telegram", "Execute Testar conexão; o token não é exibido.")
    return checks


def test_telegram(root: Path) -> tuple[bool, str]:
    from onmyoji_daemon.telegram import Settings, TelegramApi, Vault
    try:
        settings = Settings.load(root.resolve(), target(root).parent)
        account = TelegramApi(Vault(settings).read(settings.token_entry)).call("getMe")
        marker = root / "configs" / "daemon" / "runtime" / "telegram-test.json"; marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"tested_at": time.time(), "bot": str(account.get("username") or account.get("first_name") or "")}), encoding="utf-8")
        return True, f"Bot autenticado: @{account.get('username') or account.get('first_name') or 'sem username'}"
    except Exception as error: return False, f"Não foi possível autenticar o bot: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--onmyoji-root", default=str(ROOT)); parser.add_argument("--action", choices=["bootstrap", "validate", "validation-json", "profiles", "set-credential", "set-totp", "check-entry", "write-token", "test-telegram", "test-agent"], default="bootstrap"); parser.add_argument("--profile"); parser.add_argument("--entry"); parser.add_argument("--real-entry"); parser.add_argument("--fake-entry"); parser.add_argument("--enabled", action="store_true"); args = parser.parse_args()
    root = Path(args.onmyoji_root).resolve()
    if args.action == "bootstrap": print(bootstrap(root)); return 0
    if args.action == "profiles": print(json.dumps(profiles(root), ensure_ascii=False)); return 0
    if args.action == "set-credential":
        if not args.profile or not args.entry: parser.error("--profile e --entry são obrigatórios")
        bootstrap(root); save_telegram(root, args.profile, args.entry); print("Credencial configurada."); return 0
    if args.action == "set-totp":
        bootstrap(root); save_totp(root, args.enabled, args.real_entry or "", args.fake_entry or ""); print("TOTP configurado."); return 0
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
