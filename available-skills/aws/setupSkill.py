#!/usr/bin/env python3
"""Configurador interativo de perfis AWS."""
from __future__ import annotations
import argparse,json,sys,tomllib
from pathlib import Path
SKILL=Path(__file__).resolve().parent
sys.path.insert(0,str(SKILL.parent))
from setup_ui import choose_keepass_profile,item,prompt,result,screen,suggested_vault_entry
from setup_profile_api import Field,handle as handle_profile,simple_save,wrapper_test
DEFAULTS={"timeout_seconds":30,"max_attempts":3}
FIELDS=(("cli_path","Executável AWS CLI","aws"),("region","Região","sa-east-1"),("expected_account_id","Conta AWS esperada (opcional)",""),("vault_profile","Perfil KeePass","example"),("vault_entry_path","Entrada KeePass","AWS/example"))
PROFILE_FIELDS=tuple(Field(key,label,default,key in {'vault_profile','vault_entry_path'}) for key,label,default in FIELDS)
def path(root):return root/'configs'/'aws.toml'
def load(file):return tomllib.loads(file.read_text(encoding='utf-8')) if file.exists() else {'schema_version':1,'defaults':dict(DEFAULTS),'profiles':{}}
def render(data):
 lines=['schema_version = 1','','[defaults]',*[f'{k} = {json.dumps(v)}' for k,v in data['defaults'].items()]]
 for name,profile in sorted(data['profiles'].items()):lines+=['',f'[profiles.{name}]',*[f'{k} = {json.dumps(v)}' for k,v in profile.items()]]
 return '\n'.join(lines)+'\n'
def save(file,data):
 old=file.read_text(encoding='utf-8') if file.exists() else None
 try:file.parent.mkdir(parents=True,exist_ok=True);file.write_text(render(data),encoding='utf-8',newline='\n');tomllib.loads(file.read_text(encoding='utf-8'))
 except Exception as e:
  if old is None:file.unlink(missing_ok=True)
  else:file.write_text(old,encoding='utf-8',newline='\n')
  result(False,f'Não salvo: {e}');return
 result(True,'Configuração salva e validada.')
def choose(profiles):
 names=sorted(profiles)
 screen('AWS','Selecionar perfil','Escolha um perfil ou pressione X para voltar')
 for i,name in enumerate(names,1):item(f'{i}.',name)
 item('X.','Voltar')
 raw=prompt('Perfil [X cancela]: ').strip().casefold()
 return names[int(raw)-1] if raw.isdigit() and 1<=int(raw)<=len(names) else None
def profile_test(name,file,data):return wrapper_test(SKILL/'scripts'/'aws.py',file,name,{'version':1,'operation':'identity.get'},'AWS')
def configure(root):
 file=path(root)
 while True:
  data=load(file);profiles=data['profiles'];screen('AWS','Configuração','Perfis de acesso AWS');item('1.','Criar perfil');item('2.','Editar perfil');item('3.','Excluir perfil');item('X.','Voltar');action=prompt('Opção: ').strip().casefold()
  if action in {'x','\x1b'}:return
  if action=='1':
   name=prompt('Nome [X cancela]: ').strip()
   if not name or name.casefold()=='x' or name in profiles or not name.replace('-','').replace('_','').isalnum():result(False,'Nome inválido ou existente.');continue
   profile={}
   for key,label,default in FIELDS:
    default=suggested_vault_entry('AWS',name) if key=='vault_entry_path' else default
    value=choose_keepass_profile(root) if key=='vault_profile' else (prompt(f'{label} [{default}]: ').strip() or default)
    if value is None:break
    profile[key]=value
   else:profiles[name]=profile;save(file,data)
  elif action in {'2','3'}:
   if not profiles:result(False,'Não há perfis configurados.');continue
   name=choose(profiles)
   if not name:result(False,'Seleção cancelada.');continue
   if action=='3':
    if prompt(f'Digite EXCLUIR para remover {name}: ').strip()=='EXCLUIR':del profiles[name];save(file,data)
    else:result(False,'Operação cancelada.')
   else:
    profile=profiles[name];screen('AWS','Editar perfil',name)
    for i,(key,label,_) in enumerate(FIELDS,1):item(f'{i}.',label,profile.get(key,''))
    item('X.','Voltar');field=prompt('Editar [X volta]: ').strip().casefold()
    if field.isdigit() and 1<=int(field)<=len(FIELDS):
     key,label,_=FIELDS[int(field)-1];value=choose_keepass_profile(root,profile.get(key,'')) if key=='vault_profile' else prompt(f'{label} [X cancela]: ').strip()
     if value and value.casefold() not in {'x','\x1b'}:profile[key]=value;save(file,data)
  else:result(False,'Opção inválida.')
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--onmyoji-root',type=Path);parser.add_argument('--action',default='configure');parser.add_argument('--json',action='store_true');parser.add_argument('--profile');parser.add_argument('--set',action='append');parser.add_argument('--confirm-delete');args=parser.parse_args();root=args.onmyoji_root.resolve() if args.onmyoji_root else SKILL.parents[1];info={'id':'aws','title':'AWS','description':'S3 e IAM com credenciais no KeePass Vault.'}
 if args.action=='describe':print(json.dumps(info,ensure_ascii=False) if args.json else info['title']);return
 if args.action=='status':print(json.dumps({'configured':bool(load(path(root))['profiles']),'valid':True}));return
 if args.action.startswith('profile-'):
  code,value=handle_profile(action=args.action,profile_name=args.profile,values=args.set,confirm_delete=args.confirm_delete,path=path(root),load=load,save=simple_save,fields=PROFILE_FIELDS,test=profile_test);print(json.dumps(value,ensure_ascii=False));return code
 configure(root)
if __name__=='__main__':main()
