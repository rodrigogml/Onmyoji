#!/usr/bin/env python3
"""Configuração local da skill Omie."""
from __future__ import annotations
import argparse, json, subprocess, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_ui import choose_keepass_profile, item, prompt, result, screen, suggested_vault_entry
from setup_profile_api import Field, handle as handle_profile
if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8', errors='replace')
PROFILE_FIELDS=(Field('vault_profile','Perfil KeePass',required=True),Field('vault_entry_path','Entrada KeePass',required=True),Field('app_key_field','Campo KeePass para app_key','username'),Field('app_secret_field','Campo KeePass para app_secret','password'))
def path(root: Path) -> Path: return root / "configs" / "omie.toml"
def empty() -> dict: return {"schema_version": 1, "defaults": {"timeout_seconds": 30, "max_retries": 2}, "profiles": {}}
def render(data: dict) -> str:
    lines = ["schema_version = 1", "", "[defaults]", f"timeout_seconds = {data['defaults']['timeout_seconds']}", f"max_retries = {data['defaults']['max_retries']}"]
    for name, profile in sorted(data['profiles'].items()): lines += ["", f"[profiles.{name}]", *[f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in profile.items()]]
    return "\n".join(lines) + "\n"
def load(file: Path) -> dict:
    if not file.exists(): return empty()
    return tomllib.loads(file.read_text(encoding="utf-8"))
def valid(data: dict) -> tuple[bool, str]:
    for name, profile in data.get('profiles', {}).items():
        if not all(isinstance(profile.get(k), str) and profile[k] for k in ('vault_profile','vault_entry_path')): return False, f"Perfil {name}: KeePass incompleto."
        if profile.get('app_key_field') not in {'username','password','url','notes'} or profile.get('app_secret_field') not in {'username','password','url','notes'}: return False, f"Perfil {name}: campos KeePass inválidos."
    return True, "Configuração Omie válida."
def save(file: Path, data: dict) -> tuple[bool, str]:
    ok, message = valid(data)
    if not ok: return ok, message
    file.parent.mkdir(parents=True, exist_ok=True); file.write_text(render(data), encoding="utf-8", newline="\n"); tomllib.loads(file.read_text(encoding="utf-8")); return True, message

def choose_omie_profile(data: dict, action: str) -> str | None:
    names = sorted(data['profiles'])
    if not names:
        result(False, "Nenhum perfil Omie foi configurado.")
        return None
    screen("Omie", f"{action} perfil", "Escolha um perfil ou pressione X para voltar")
    for index, name in enumerate(names, 1): item(f"{index}.", name)
    item("X.", "Voltar")
    while True:
        selected = prompt("Opção: ").strip().casefold()
        if selected in {'x', '\x1b'}: return None
        if selected.isdigit() and 1 <= int(selected) <= len(names): return names[int(selected) - 1]
        result(False, "Opção inválida.")

def test_profile(root: Path, file: Path, data: dict) -> None:
    name = choose_omie_profile(data, "Testar")
    if name is None: return
    ok, message = valid(data)
    if not ok: result(False, f"Teste não iniciado: {message}"); return
    defaults = data['defaults']
    timeout = float(defaults['timeout_seconds']) * (int(defaults['max_retries']) + 1) + 5
    request = {"version": 1, "operation": "departments.list", "params": {"page": 1, "page_size": 1}}
    command = [sys.executable, str(Path(__file__).resolve().parent / "scripts" / "omie.py"), "--config", str(file), "--profile", name]
    try:
        process = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True, timeout=timeout, check=False)
        payload = json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        result(False, "Não foi possível concluir o teste de acesso à Omie.")
        return
    if process.returncode != 0 or payload.get("ok") is not True:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        detail = error.get("message") if isinstance(error, dict) else None
        result(False, f"Falha ao testar o perfil '{name}': {detail or 'a API Omie recusou a solicitação.'}")
        return
    result(True, f"Acesso à API Omie confirmado para o perfil '{name}'.")

def test_profile_json(name: str, file: Path, data: dict) -> tuple[bool, str]:
    defaults=data['defaults']; timeout=float(defaults['timeout_seconds'])*(int(defaults['max_retries'])+1)+5
    command=[sys.executable,str(Path(__file__).resolve().parent/'scripts'/'omie.py'),'--config',str(file),'--profile',name]
    try: process=subprocess.run(command,input=json.dumps({'version':1,'operation':'departments.list','params':{'page':1,'page_size':1}}),text=True,encoding='utf-8',errors='replace',capture_output=True,timeout=timeout); payload=json.loads(process.stdout)
    except (OSError,subprocess.TimeoutExpired,json.JSONDecodeError): return False, 'Não foi possível concluir o teste de acesso à Omie.'
    if process.returncode or payload.get('ok') is not True:
        error=payload.get('error',{}) if isinstance(payload,dict) else {}; return False, f"Falha ao testar a Omie: {error.get('message','a API recusou a solicitação.')}"
    return True, 'Acesso à API Omie confirmado.'

def configure(root: Path) -> None:
    file, data = path(root), load(path(root))
    while True:
        screen("Omie", "Configuração", "Perfis de acesso ao ERP")
        item("1.", "Criar perfil"); item("2.", "Remover perfil"); item("3.", "Testar perfil"); item("X.", "Voltar")
        choice=prompt("Opção: ").strip().casefold()
        if choice in {'x','\x1b'}: return
        if choice == '1':
            name=prompt("Nome do perfil [X cancela]: ").strip()
            if not name or name.casefold() in {'x','\x1b'} or name in data['profiles']: result(False, "Nome inválido ou existente."); continue
            vault_profile=choose_keepass_profile(root)
            if vault_profile is None: continue
            suggested_entry=suggested_vault_entry('Omie', name)
            profile={'vault_profile': vault_profile, 'vault_entry_path': prompt(f"Entrada KeePass [{suggested_entry}]: ").strip() or suggested_entry, 'app_key_field': prompt("Campo app_key [username]: ").strip() or 'username', 'app_secret_field': prompt("Campo app_secret [password]: ").strip() or 'password'}
            candidate={**data, 'profiles': {**data['profiles'], name: profile}}; ok,message=save(file,candidate); result(ok, message if ok else f"Não salvo: {message}"); data=candidate if ok else data
        elif choice == '2':
            name = choose_omie_profile(data, "Remover")
            if name and prompt(f"Digite REMOVER para excluir {name}: ").strip()=='REMOVER':
                candidate={**data, 'profiles': dict(data['profiles'])}; del candidate['profiles'][name]; ok,message=save(file,candidate); result(ok, message); data=candidate if ok else data
        elif choice == '3': test_profile(root, file, data)
        else: result(False, "Opção inválida.")
def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--onmyoji-root',type=Path,default=ROOT); parser.add_argument('--action',choices=['describe','status','configure','profile-schema','profile-list','profile-create','profile-update','profile-delete','profile-test'],default='configure'); parser.add_argument('--json',action='store_true'); parser.add_argument('--profile'); parser.add_argument('--set',action='append'); parser.add_argument('--confirm-delete'); args=parser.parse_args()
    if args.action=='describe':
        data={'id':'omie','title':'Omie','description':'ERP, financeiro e NF-e com credenciais no KeePass Vault.'}; print(json.dumps(data,ensure_ascii=False) if args.json else data['title']); return 0
    if args.action=='status':
        data=load(path(args.onmyoji_root)); ok,message=valid(data); print(json.dumps({'configured':bool(data['profiles']),'valid':ok,'message':message},ensure_ascii=False)); return 0 if ok else 2
    if args.action.startswith('profile-'):
        code,value=handle_profile(action=args.action,profile_name=args.profile,values=args.set,confirm_delete=args.confirm_delete,path=path(args.onmyoji_root),load=load,save=save,fields=PROFILE_FIELDS,test=test_profile_json)
        print(json.dumps(value,ensure_ascii=False)); return code
    configure(args.onmyoji_root); return 0
if __name__=='__main__': raise SystemExit(main())
