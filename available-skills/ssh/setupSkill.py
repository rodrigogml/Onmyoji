#!/usr/bin/env python3
"""Configurador interativo da skill SSH."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_profile_api import Field, handle as handle_profile, simple_load, simple_render
from setup_ui import item, note, prompt, result, screen

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL))
DEFAULTS = {"timeout_seconds": 30, "temp_dir": ""}
PROFILE_FIELDS = (
    Field("host", "Host", required=True),
    Field("port", "Porta", 22, True),
    Field("username", "Usuário", required=True),
    Field("auth_mode", "Autenticação", "key", True),
    Field("vault_profile", "Perfil KeePass", required=True),
    Field("vault_entry_path", "Entrada KeePass", required=True),
    Field("keepass_password_field", "Campo da senha", "password"),
    Field("keepass_key_attachment", "Anexo da chave", "id_ed25519"),
    Field("keepass_key_passphrase_field", "Campo da frase secreta", "password"),
    Field("known_hosts", "Arquivo known_hosts", ""),
    Field("temp_dir", "Diretório temporário de chave", required=True),
)


def root_from(args: argparse.Namespace) -> Path:
    return args.onmyoji_root.resolve() if args.onmyoji_root else SKILL.parents[1]


def config_path(root: Path) -> Path:
    return root / "configs" / "ssh.toml"


def load(path: Path) -> dict:
    return simple_load(path, DEFAULTS)


def workspace(root: Path) -> Path | None:
    try:
        data = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        candidate = Path(str(data.get("codex", {}).get("project_directory") or "")).resolve()
        return candidate if candidate.is_dir() else None
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return None


def activity_directory(root: Path) -> Path | None:
    current = workspace(root)
    return current / ".onmyoji" / "ssh" / "temporary-keys" if current else None


def keepass_profiles(root: Path) -> list[str]:
    try:
        data = tomllib.loads((root / "configs" / "keepass.toml").read_text(encoding="utf-8"))
        profiles = data.get("profiles", {})
        return sorted(name for name, value in profiles.items() if name != "example" and isinstance(value, dict)) if isinstance(profiles, dict) else []
    except (OSError, tomllib.TOMLDecodeError):
        return []


def valid(path: Path, data: dict) -> tuple[bool, str]:
    root = path.parent.parent; current_workspace = workspace(root)
    try:
        from scripts.ssh import SshError, load_profile
        profiles = data.get("profiles", {})
        if not isinstance(profiles, dict): raise ValueError("A seção profiles é inválida.")
        known_profiles = keepass_profiles(root)
        for name, profile in profiles.items():
            if not isinstance(profile, dict): raise ValueError(f"Perfil SSH {name!r} é inválido.")
            load_profile(str(path), name)
            selected = str(profile.get("vault_profile") or "")
            if selected not in known_profiles: raise ValueError(f"O perfil KeePass {selected!r} de {name!r} não existe nesta instância.")
            temporary = Path(str(profile.get("temp_dir") or "")).resolve()
            if not str(profile.get("temp_dir") or "").strip(): raise ValueError(f"Defina o diretório temporário de chave do perfil {name!r}.")
            if not current_workspace or not temporary.is_relative_to(current_workspace): raise ValueError(f"O diretório temporário de {name!r} deve ficar dentro do workspace do Shikigami.")
            known_hosts = str(profile.get("known_hosts") or "").strip()
            if known_hosts and not Path(known_hosts).expanduser().is_file(): raise ValueError(f"O arquivo known_hosts de {name!r} não existe.")
    except (ValueError, SshError) as error:
        return False, str(error)
    except Exception as error:
        return False, f"Configuração SSH inválida: {error}"
    return True, "Configuração salva e validada."


def save(path: Path, data: dict) -> tuple[bool, str]:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(simple_render(data), encoding="utf-8", newline="\n")
        tomllib.loads(path.read_text(encoding="utf-8"))
        accepted, message = valid(path, data)
        if not accepted: raise ValueError(message)
    except Exception as error:
        if old is None: path.unlink(missing_ok=True)
        else: path.write_text(old, encoding="utf-8", newline="\n")
        return False, f"Não salvo: {error}"
    return True, "Configuração salva e validada."


def ask(label: str, current: str = "") -> str | None:
    value = prompt(f"{label}" + (f" [{current}]" if current else "") + " (X cancela): ").strip()
    return None if value.casefold() in {"x", "\x1b"} else (value or current)


def select_profile(profiles: dict, action: str) -> str | None:
    names = sorted(profiles)
    if not names:
        result(False, "Não há perfis SSH configurados.")
        return None
    screen("SSH", "Selecionar perfil", action)
    for index, name in enumerate(names, 1): item(f"{index}.", name)
    item("X.", "Voltar")
    selected = ask("Número do perfil")
    if selected is None: return None
    if not selected.isdigit() or not 1 <= int(selected) <= len(names):
        result(False, "Seleção inválida.")
        return None
    return names[int(selected) - 1]


def select_keepass(root: Path, current: str = "") -> str | None:
    names = keepass_profiles(root)
    if not names:
        result(False, "Nenhum perfil KeePass disponível. Configure primeiro a skill KeePass Vault.")
        return None
    screen("SSH", "Perfil KeePass", "Escolha a credencial que o wrapper poderá consultar")
    for index, name in enumerate(names, 1): item(f"{index}.", name, "atual" if name == current else "")
    item("X.", "Voltar")
    selected = ask("Número do perfil")
    if selected is None: return None
    if not selected.isdigit() or not 1 <= int(selected) <= len(names):
        result(False, "Seleção inválida.")
        return None
    return names[int(selected) - 1]


def choose_auth(current: str) -> str | None:
    screen("SSH", "Autenticação", "A senha ou chave permanece exclusivamente no KeePass")
    item("1.", "Senha", "Campo de uma entrada KeePass")
    item("2.", "Chave privada", "Anexo temporário do KeePass")
    item("X.", "Voltar")
    value = prompt(f"Opção [{'2' if current == 'key' else '1'}]: ").strip().casefold()
    if value in {"x", "\x1b"}: return None
    if value in {"", "1"}: return "password"
    if value == "2": return "key"
    result(False, "Opção inválida.")
    return None


def camel(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.replace("-", "_").split("_") if part) or "Perfil"


def create_profile(root: Path, path: Path, data: dict) -> None:
    profiles = data["profiles"]
    name = ask("Nome do perfil")
    if not name: return
    if name in profiles or not name.replace("-", "").replace("_", "").isalnum(): result(False, "Nome inválido ou já existente."); return
    selected_vault = select_keepass(root)
    if not selected_vault: return
    current_workspace = workspace(root)
    temporary = activity_directory(root)
    if not temporary:
        result(False, "Configure primeiro a pasta de projeto no menu Codex-CLI; as chaves temporárias precisam ficar no workspace.")
        return
    authentication = choose_auth("key")
    if not authentication: return
    profile = {"host": "", "port": 22, "username": "", "auth_mode": authentication, "vault_profile": selected_vault, "vault_entry_path": f"APIs/SSH:{camel(name)}", "keepass_password_field": "password", "keepass_key_attachment": "id_ed25519", "keepass_key_passphrase_field": "password", "known_hosts": "", "temp_dir": str(temporary)}
    for key, label in (("host", "Host"), ("port", "Porta"), ("username", "Usuário"), ("vault_entry_path", "Entrada KeePass"), ("known_hosts", "Arquivo known_hosts")):
        value = ask(label, str(profile[key]))
        if value is None: return
        profile[key] = int(value) if key == "port" and value.isdigit() else value
    if authentication == "password":
        value = ask("Campo da senha", profile["keepass_password_field"])
        if value is None: return
        profile["keepass_password_field"] = value
    else:
        value = ask("Nome do anexo da chave privada", profile["keepass_key_attachment"])
        if value is None: return
        profile["keepass_key_attachment"] = value
        value = ask("Campo da frase secreta (vazio = sem frase)", profile["keepass_key_passphrase_field"])
        if value is None: return
        profile["keepass_key_passphrase_field"] = value
    temporary.mkdir(parents=True, exist_ok=True)
    profiles[name] = profile
    ok, message = save(path, data); result(ok, message)


def edit_profile(root: Path, path: Path, data: dict, name: str) -> None:
    profile = data["profiles"][name]
    while True:
        screen("SSH", "Editar perfil", name)
        item("1.", "Host", str(profile.get("host", ""))); item("2.", "Porta", str(profile.get("port", 22))); item("3.", "Usuário", str(profile.get("username", "")))
        item("4.", "Autenticação", "Chave privada" if profile.get("auth_mode") == "key" else "Senha")
        item("5.", "Perfil KeePass", str(profile.get("vault_profile", ""))); item("6.", "Entrada KeePass", str(profile.get("vault_entry_path", "")))
        item("7.", "Campo da senha", str(profile.get("keepass_password_field", "password"))); item("8.", "Anexo da chave", str(profile.get("keepass_key_attachment", "id_ed25519")))
        item("9.", "Campo da frase secreta", str(profile.get("keepass_key_passphrase_field", "password"))); item("10.", "Arquivo known_hosts", str(profile.get("known_hosts", "") or "(não definido)"))
        item("11.", "Diretório temporário de chave", str(profile.get("temp_dir", ""))); item("X.", "Voltar")
        selected = prompt("Editar: ").strip().casefold()
        if selected in {"x", "\x1b"}: return
        mapping = {"1": ("host", "Host"), "2": ("port", "Porta"), "3": ("username", "Usuário"), "6": ("vault_entry_path", "Entrada KeePass"), "7": ("keepass_password_field", "Campo da senha"), "8": ("keepass_key_attachment", "Anexo da chave"), "9": ("keepass_key_passphrase_field", "Campo da frase secreta"), "10": ("known_hosts", "Arquivo known_hosts"), "11": ("temp_dir", "Diretório temporário de chave")}
        if selected == "4":
            value = choose_auth(str(profile.get("auth_mode") or "key"))
            if value: profile["auth_mode"] = value
        elif selected == "5":
            value = select_keepass(root, str(profile.get("vault_profile") or ""))
            if value: profile["vault_profile"] = value
        elif selected in mapping:
            key, label = mapping[selected]; value = ask(label, str(profile.get(key) or ""))
            if value is None: continue
            if key == "port" and not value.isdigit(): result(False, "A porta deve ser um número inteiro."); continue
            profile[key] = int(value) if key == "port" else value
        else:
            result(False, "Opção inválida."); continue
        ok, message = save(path, data); result(ok, message)


def test_profile_json(name: str, path: Path, data: dict) -> tuple[bool, str]:
    """Autentica e encerra: não executa nenhum comando remoto no teste."""
    accepted, message = valid(path, data)
    if not accepted: return False, message
    from scripts.ssh import connect, export_key, load_profile
    temporary_directory: str | None = None; client = None
    try:
        profile = load_profile(str(path), name)
        key_path = None
        if profile["auth_mode"] == "key":
            temporary_directory = tempfile.mkdtemp(prefix="ssh-key-test-", dir=profile["temp_dir"])
            key_path = export_key(profile, temporary_directory)
        client = connect(profile, key_path)
        return True, "Conexão SSH e autenticação KeePass confirmadas; nenhum comando remoto foi executado."
    except Exception as error:
        return False, f"Teste SSH falhou: {getattr(error, 'message', str(error))}"
    finally:
        if client: client.close()
        if temporary_directory: shutil.rmtree(temporary_directory, ignore_errors=True)


def configure(root: Path) -> None:
    path = config_path(root)
    while True:
        data = load(path); profiles = data.setdefault("profiles", {})
        screen("SSH", "Configuração", "Perfis de acesso remoto com credenciais no KeePass")
        if profiles:
            print("  PERFIS CONFIGURADOS")
            for name in sorted(profiles): print(f"    • {name}")
            print()
        print("  AÇÕES")
        item("1.", "Criar novo perfil"); item("2.", "Editar perfil existente"); item("3.", "Excluir perfil existente"); item("4.", "Testar perfil", "Conexão e autenticação, sem comando remoto"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1": create_profile(root, path, data)
        elif choice in {"2", "3", "4"}:
            name = select_profile(profiles, {"2": "Escolha o perfil para editar", "3": "Escolha o perfil para excluir", "4": "Escolha o perfil para testar"}[choice])
            if not name: continue
            if choice == "2": edit_profile(root, path, data, name)
            elif choice == "3":
                if prompt(f"Digite EXCLUIR para remover {name}: ").strip() == "EXCLUIR":
                    del profiles[name]; ok, message = save(path, data); result(ok, message)
                else: result(False, "Operação cancelada.")
            else:
                ok, message = test_profile_json(name, path, data); result(ok, message)
        else: result(False, "Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--action", default="configure"); parser.add_argument("--json", action="store_true"); parser.add_argument("--onmyoji-root", type=Path); parser.add_argument("--profile"); parser.add_argument("--set", action="append"); parser.add_argument("--confirm-delete"); args = parser.parse_args(); root = root_from(args)
    description = {"id": "ssh", "title": "SSH", "description": "Execução e transferência SSH com credenciais e chaves no KeePass Vault."}
    if args.action == "describe": print(json.dumps(description, ensure_ascii=False) if args.json else description["title"]); return 0
    if args.action == "status":
        path = config_path(root)
        try: accepted, message = valid(path, load(path)) if path.exists() else (True, "Nenhum perfil configurado.")
        except Exception as error: accepted, message = False, str(error)
        print(json.dumps({"configured": path.exists(), "valid": accepted, "message": message}, ensure_ascii=False)); return 0 if accepted else 2
    if args.action.startswith("profile-"):
        code, value = handle_profile(action=args.action, profile_name=args.profile, values=args.set, confirm_delete=args.confirm_delete, path=config_path(root), load=load, save=save, fields=PROFILE_FIELDS, test=test_profile_json)
        print(json.dumps(value, ensure_ascii=False)); return code
    configure(root); return 0


if __name__ == "__main__": raise SystemExit(main())
