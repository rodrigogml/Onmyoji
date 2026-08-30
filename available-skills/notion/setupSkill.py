#!/usr/bin/env python3
"""Configurador interativo de perfis Notion."""
from __future__ import annotations
import argparse, json, sys, tomllib
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL.parent))
from setup_ui import choose_keepass_profile, item, prompt, result, screen, suggested_vault_entry
from setup_profile_api import Field,handle as handle_profile,wrapper_test
DEFAULTS = {"api_base":"https://api.notion.com","notion_version":"2026-03-11","timeout_seconds":30,"max_retries":2,"page_size":100}
PROFILE_FIELDS=(Field('vault_profile','Perfil KeePass',required=True),Field('vault_entry_path','Entrada KeePass',required=True),Field('vault_field','Campo do token','password'))
def path(root): return root / "configs" / "notion.toml"
def load(file): return tomllib.loads(file.read_text(encoding="utf-8")) if file.exists() else {"schema_version":1,"defaults":dict(DEFAULTS),"profiles":{}}
def render(data):
    lines=["schema_version = 1","","[defaults]"]
    for k,v in data["defaults"].items(): lines.append(f"{k} = {json.dumps(v,ensure_ascii=False)}")
    for n,p in sorted(data["profiles"].items()):
        lines += ["",f"[profiles.{n}]"]
        for k in ("vault_profile","vault_entry_path","vault_field"): lines.append(f"{k} = {json.dumps(p[k],ensure_ascii=False)}")
    return "\n".join(lines)+"\n"
def save(file,data):
    old=file.read_text(encoding="utf-8") if file.exists() else None
    try:
        file.parent.mkdir(parents=True,exist_ok=True);file.write_text(render(data),encoding="utf-8",newline="\n");tomllib.loads(file.read_text(encoding="utf-8"))
    except Exception as e:
        if old is None:file.unlink(missing_ok=True)
        else:file.write_text(old,encoding="utf-8",newline="\n")
        result(False, f"Não salvo: {e}");return
    result(True, "Configuração salva e validada.")
def profile_save(file,data):
    old=file.read_text(encoding="utf-8") if file.exists() else None
    try:file.parent.mkdir(parents=True,exist_ok=True);file.write_text(render(data),encoding="utf-8",newline="\n");tomllib.loads(file.read_text(encoding="utf-8"))
    except Exception as e:
        if old is None:file.unlink(missing_ok=True)
        else:file.write_text(old,encoding="utf-8",newline="\n")
        return False,f"Não salvo: {e}"
    return True,"Configuração salva e validada."
def profile_test(name,file,data):return wrapper_test(SKILL/'scripts'/'notion.py',file,name,{'version':1,'operation':'users.self'},'Notion')
def select(profiles):
    names=sorted(profiles)
    screen("Notion", "Selecionar perfil", "Escolha um perfil ou pressione X para voltar")
    for i,name in enumerate(names,1):item(f"{i}.", name)
    item("X.", "Voltar")
    c=prompt("Perfil [X cancela]: ").strip().casefold()
    return names[int(c)-1] if c.isdigit() and 1<=int(c)<=len(names) else None
def configure(root):
    file=path(root)
    while True:
        data=load(file);profiles=data["profiles"]
        screen("Notion", "Configuração", "Perfis de acesso à API")
        item("1.", "Criar perfil"); item("2.", "Editar perfil"); item("3.", "Excluir perfil"); item("X.", "Voltar")
        c=prompt("Opção: ").strip().casefold()
        if c in {"x","\x1b"}:return
        if c=="1":
            name=prompt("Nome do perfil [X cancela]: ").strip()
            if not name or name.casefold()=="x" or name in profiles or not name.replace("-","").replace("_","").isalnum():result(False, "Nome inválido ou existente.");continue
            p={}
            for key,label,default in (("vault_profile","Perfil KeePass","example"),("vault_entry_path","Entrada KeePass","APIs/Notion"),("vault_field","Campo do token","password")):
                default=suggested_vault_entry("Notion",name) if key=="vault_entry_path" else default
                value=choose_keepass_profile(root) if key=="vault_profile" else (prompt(f"{label} [{default}]: ").strip() or default)
                if value is None: break
                p[key]=value
            else: profiles[name]=p;save(file,data)
        elif c in {"2","3"}:
            if not profiles:result(False, "Não há perfis configurados.");continue
            name=select(profiles)
            if not name:result(False, "Seleção cancelada.");continue
            if c=="3":
                if prompt(f"Digite EXCLUIR para remover {name}: ").strip()=="EXCLUIR":del profiles[name];save(file,data)
                else:result(False, "Operação cancelada.")
            else:
                p=profiles[name];screen("Notion", "Editar perfil", name);item("1.", "Perfil KeePass", p['vault_profile']);item("2.", "Entrada KeePass", p['vault_entry_path']);item("3.", "Campo", p['vault_field']);item("X.", "Voltar")
                key={"1":"vault_profile","2":"vault_entry_path","3":"vault_field"}.get(prompt("Editar: ").strip().casefold())
                if key:
                    value=choose_keepass_profile(root,p.get(key,"")) if key=="vault_profile" else prompt("Novo valor [X cancela]: ").strip()
                    if value and value.casefold() not in {"x","\x1b"}:p[key]=value;save(file,data)
        else:result(False, "Opção inválida.")
def main():
    parser=argparse.ArgumentParser();parser.add_argument("--action",default="configure");parser.add_argument("--json",action="store_true");parser.add_argument("--onmyoji-root",type=Path);parser.add_argument('--profile');parser.add_argument('--set',action='append');parser.add_argument('--confirm-delete');args=parser.parse_args();root=args.onmyoji_root.resolve() if args.onmyoji_root else SKILL.parents[1];info={"id":"notion","title":"Notion","description":"Páginas, bases e arquivos com token no KeePass Vault."}
    if args.action=="describe":print(json.dumps(info,ensure_ascii=False) if args.json else info["title"]);return
    if args.action=="status":print(json.dumps({"configured":path(root).exists(),"valid":True}));return
    if args.action.startswith('profile-'):
        code,value=handle_profile(action=args.action,profile_name=args.profile,values=args.set,confirm_delete=args.confirm_delete,path=path(root),load=load,save=profile_save,fields=PROFILE_FIELDS,test=profile_test);print(json.dumps(value,ensure_ascii=False));return code
    configure(root)
if __name__=="__main__":main()
