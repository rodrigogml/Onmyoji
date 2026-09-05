#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
SKILL=Path(__file__).resolve().parent
sys.path.insert(0,str(SKILL.parent))
from setup_profile_api import Field,handle as handle_profile,interactive_configure,simple_load,simple_save,wrapper_test
DEFAULTS={'executable':'mysql','port':3306,'connect_timeout':10,'timeout_seconds':120,'allow_client_commands':True}
PROFILE_FIELDS=(Field('host','Host','127.0.0.1',True),Field('database','Banco padrão (opcional)',''),Field('vault_profile','Perfil KeePass',required=True),Field('vault_entry_path','Entrada KeePass',required=True),Field('username_field','Campo do usuário','username'),Field('password_field','Campo da senha','password'))
def config_path(root):return root/'configs'/'mysql.toml'
def profile_test(name,file,data):return wrapper_test(SKILL/'scripts'/'mysql.py',file,name,{'version':1,'operation':'ping'},'MySQL')
def main():
 p=argparse.ArgumentParser();p.add_argument('--action',default='configure');p.add_argument('--json',action='store_true');p.add_argument('--onmyoji-root',type=Path);p.add_argument('--profile');p.add_argument('--set',action='append');p.add_argument('--confirm-delete');a=p.parse_args();root=a.onmyoji_root.resolve() if a.onmyoji_root else SKILL.parents[1];info={'id':'mysql','title':'MySQL','description':'Consultas e administração MySQL com credenciais no KeePass Vault.'}
 if a.action=='describe':print(json.dumps(info,ensure_ascii=False) if a.json else info['title']);return
 if a.action=='status':print(json.dumps({'configured':(root/'configs/mysql.toml').exists(),'valid':True}));return
 if a.action.startswith('profile-'):
  code,value=handle_profile(action=a.action,profile_name=a.profile,values=a.set,confirm_delete=a.confirm_delete,path=config_path(root),load=lambda p:simple_load(p,DEFAULTS),save=simple_save,fields=PROFILE_FIELDS,test=profile_test);print(json.dumps(value,ensure_ascii=False));return code
 interactive_configure(root=root,path=config_path(root),title='MySQL',subtitle='Perfis de acesso a bancos MySQL',integration='MySQL',defaults=DEFAULTS,fields=PROFILE_FIELDS,test=profile_test)
if __name__=='__main__':main()
