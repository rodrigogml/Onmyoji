#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys,tomllib
from pathlib import Path
S=Path(__file__).resolve().parent; F=(('vault_profile','Perfil KeePass','example'),('vault_entry_path','Entrada KeePass','APIs/ForwardEmail'),('vault_field','Campo do token','password'),('domain','Domínio (opcional)',''))
sys.path.insert(0,str(S.parent))
from setup_ui import item,prompt,result,screen
def path(root):return root/'configs'/'forward-email.toml'
def load(p):return tomllib.loads(p.read_text(encoding='utf-8')) if p.exists() else {'schema_version':1,'defaults':{'api_base':'https://api.forwardemail.net/v1','timeout_seconds':30,'max_retries':2},'profiles':{}}
def render(d):
 a=['schema_version = 1','','[defaults]',*[f'{k} = {json.dumps(v)}' for k,v in d['defaults'].items()]]
 for n,p in sorted(d['profiles'].items()):a+=['',f'[profiles.{n}]',*[f'{k} = {json.dumps(v)}' for k,v in p.items()]]
 return '\n'.join(a)+'\n'
def save(p,d):
 old=p.read_text(encoding='utf-8') if p.exists() else None
 try:p.parent.mkdir(parents=True,exist_ok=True);p.write_text(render(d),encoding='utf-8',newline='\n');tomllib.loads(p.read_text(encoding='utf-8'))
 except Exception as e:
  if old is None:p.unlink(missing_ok=True)
  else:p.write_text(old,encoding='utf-8',newline='\n')
  result(False,f'Não salvo: {e}');return
 result(True,'Configuração salva e validada.')
def select(q):
 n=sorted(q);screen('Forward Email','Selecionar perfil','Escolha um perfil ou pressione X para voltar');[item(f'{i}.',x) for i,x in enumerate(n,1)];item('X.','Voltar');v=prompt('Perfil [X cancela]: ').strip();return n[int(v)-1] if v.isdigit() and 1<=int(v)<=len(n) else None
def configure(root):
 p=path(root)
 while True:
  d=load(p);q=d['profiles'];screen('Forward Email','Configuração','Perfis de domínios e aliases');item('1.','Criar perfil');item('2.','Editar perfil');item('3.','Excluir perfil');item('X.','Voltar');a=prompt('Opção: ').strip().casefold()
  if a in {'x','\x1b'}:return
  if a=='1':
   n=prompt('Nome [X cancela]: ').strip()
   if not n or n.casefold()=='x' or n in q:result(False,'Nome inválido ou existente.');continue
   q[n]={k:(prompt(f'{l} [{x}]: ').strip() or x) for k,l,x in F};save(p,d)
  elif a in {'2','3'}:
   if not q:result(False,'Não há perfis configurados.');continue
   n=select(q)
   if not n:continue
   if a=='3':
    if prompt(f'Digite EXCLUIR para remover {n}: ').strip()=='EXCLUIR':del q[n];save(p,d)
   else:
    z=q[n];screen('Forward Email','Editar perfil',n);[item(f'{i}.',l,z.get(k,'')) for i,(k,l,_) in enumerate(F,1)];item('X.','Voltar');v=prompt('Editar [X volta]: ').strip()
    if v.isdigit() and 1<=int(v)<=len(F):k,l,_=F[int(v)-1];x=prompt(f'{l} [X cancela]: ').strip();z[k]=z[k] if not x or x.casefold()=='x' else x;save(p,d)
  else:result(False,'Opção inválida.')
def main():
 p=argparse.ArgumentParser();p.add_argument('--action',default='configure');p.add_argument('--json',action='store_true');p.add_argument('--onmyoji-root',type=Path);a=p.parse_args();r=a.onmyoji_root.resolve() if a.onmyoji_root else S.parents[1];i={'id':'forward-email','title':'Forward Email','description':'Domínios e aliases com token no KeePass Vault.'}
 if a.action=='describe':print(json.dumps(i,ensure_ascii=False) if a.json else i['title']);return
 if a.action=='status':print(json.dumps({'configured':bool(load(path(r))['profiles']),'valid':True}));return
 configure(r)
if __name__=='__main__':main()
