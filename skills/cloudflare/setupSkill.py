#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys,tomllib
from pathlib import Path
SKILL=Path(__file__).resolve().parent
sys.path.insert(0,str(SKILL.parent))
from setup_ui import item,prompt,result,screen
FIELDS=(('vault_profile','Perfil KeePass','example'),('vault_entry_path','Entrada KeePass','APIs/Cloudflare'),('vault_field','Campo do token','password'),('zone_id','Zone ID (opcional)',''))
def file(root):return root/'configs'/'cloudflare.toml'
def load(p):return tomllib.loads(p.read_text(encoding='utf-8')) if p.exists() else {'schema_version':1,'defaults':{'api_base':'https://api.cloudflare.com/client/v4','timeout_seconds':30,'max_retries':2},'profiles':{}}
def render(d):
 lines=['schema_version = 1','','[defaults]',*[f'{k} = {json.dumps(v)}' for k,v in d['defaults'].items()]]
 for n,p in sorted(d['profiles'].items()):lines+=['',f'[profiles.{n}]',*[f'{k} = {json.dumps(v)}' for k,v in p.items()]]
 return '\n'.join(lines)+'\n'
def save(p,d):
 old=p.read_text(encoding='utf-8') if p.exists() else None
 try:p.parent.mkdir(parents=True,exist_ok=True);p.write_text(render(d),encoding='utf-8',newline='\n');tomllib.loads(p.read_text(encoding='utf-8'))
 except Exception as e:
  if old is None:p.unlink(missing_ok=True)
  else:p.write_text(old,encoding='utf-8',newline='\n')
  result(False,f'Não salvo: {e}');return
 result(True,'Configuração salva e validada.')
def select(profiles):
 names=sorted(profiles)
 screen('Cloudflare','Selecionar perfil','Escolha um perfil ou pressione X para voltar')
 for i,n in enumerate(names,1):item(f'{i}.',n)
 item('X.','Voltar')
 v=prompt('Perfil [X cancela]: ').strip().casefold();return names[int(v)-1] if v.isdigit() and 1<=int(v)<=len(names) else None
def configure(root):
 p=file(root)
 while True:
  d=load(p);profiles=d['profiles'];screen('Cloudflare','Configuração','Perfis de DNS e zonas');item('1.','Criar perfil');item('2.','Editar perfil');item('3.','Excluir perfil');item('X.','Voltar');a=prompt('Opção: ').strip().casefold()
  if a in {'x','\x1b'}:return
  if a=='1':
   n=prompt('Nome [X cancela]: ').strip()
   if not n or n.casefold()=='x' or n in profiles:result(False,'Nome inválido ou existente.');continue
   profiles[n]={k:(prompt(f'{l} [{x}]: ').strip() or x) for k,l,x in FIELDS};save(p,d)
  elif a in {'2','3'}:
   if not profiles:result(False,'Não há perfis configurados.');continue
   n=select(profiles)
   if not n:continue
   if a=='3':
    if prompt(f'Digite EXCLUIR para remover {n}: ').strip()=='EXCLUIR':del profiles[n];save(p,d)
    else:result(False,'Operação cancelada.')
   else:
    q=profiles[n];screen('Cloudflare','Editar perfil',n);[item(f'{i}.',l,q.get(k,'')) for i,(k,l,_) in enumerate(FIELDS,1)];item('X.','Voltar');v=prompt('Editar [X volta]: ').strip()
    if v.isdigit() and 1<=int(v)<=len(FIELDS):k,l,_=FIELDS[int(v)-1];x=prompt(f'{l} [X cancela]: ').strip();q[k]=q[k] if not x or x.casefold()=='x' else x;save(p,d)
  else:result(False,'Opção inválida.')
def main():
 p=argparse.ArgumentParser();p.add_argument('--action',default='configure');p.add_argument('--json',action='store_true');p.add_argument('--onmyoji-root',type=Path);a=p.parse_args();root=a.onmyoji_root.resolve() if a.onmyoji_root else SKILL.parents[1];info={'id':'cloudflare','title':'Cloudflare','description':'DNS e zonas com token no KeePass Vault.'}
 if a.action=='describe':print(json.dumps(info,ensure_ascii=False) if a.json else info['title']);return
 if a.action=='status':print(json.dumps({'configured':bool(load(file(root))['profiles']),'valid':True}));return
 configure(root)
if __name__=='__main__':main()
