#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
SKILL=Path(__file__).resolve().parent
sys.path.insert(0,str(SKILL.parent))
from setup_ui import note,screen
from setup_profile_api import Field,handle as handle_profile,simple_load,simple_save,wrapper_test
DEFAULTS={'api_base':'https://www.googleapis.com','oauth_base':'https://oauth2.googleapis.com','scopes':['openid','email','profile','https://mail.google.com/','https://www.googleapis.com/auth/contacts','https://www.googleapis.com/auth/drive','https://www.googleapis.com/auth/calendar'],'timeout_seconds':30,'max_retries':2,'page_size':100,'user_id':'me','download_dir':''}
PROFILE_FIELDS=(Field('oauth_profile','Perfil OAuth',required=True),Field('credentials_file','Arquivo de credenciais',required=True),Field('vault_profile','Perfil KeePass',required=True),Field('vault_entry_path','Entrada KeePass',required=True),Field('client_id_field','Campo client_id','username'),Field('client_secret_field','Campo client_secret','password'),Field('profiles_field','Campo dos tokens','notes'))
def config_path(root):return root/'configs'/'google.toml'
def profile_test(name,file,data):return wrapper_test(SKILL/'scripts'/'google.py',file,name,{'version':1,'service':'gmail','operation':'profile.get'},'Google')
def main():
 p=argparse.ArgumentParser();p.add_argument('--action',default='configure');p.add_argument('--json',action='store_true');p.add_argument('--onmyoji-root',type=Path);p.add_argument('--profile');p.add_argument('--set',action='append');p.add_argument('--confirm-delete');a=p.parse_args();root=a.onmyoji_root.resolve() if a.onmyoji_root else SKILL.parents[1];info={'id':'google','title':'Google','description':'Gmail, Drive, Calendar e Contatos via OAuth e KeePass Vault.'}
 if a.action=='describe':print(json.dumps(info,ensure_ascii=False) if a.json else info['title']);return
 if a.action=='status':print(json.dumps({'configured':(root/'configs/google.toml').exists(),'valid':True}));return
 if a.action.startswith('profile-'):
  code,value=handle_profile(action=a.action,profile_name=a.profile,values=a.set,confirm_delete=a.confirm_delete,path=config_path(root),load=lambda p:simple_load(p,DEFAULTS),save=simple_save,fields=PROFILE_FIELDS,test=profile_test);print(json.dumps(value,ensure_ascii=False));return code
 screen('Google','Configuração','Perfil local da integração');note(f'Crie {root/"configs/google.toml"} a partir de {SKILL/"configs/google.toml.model"}.')
if __name__=='__main__':main()
