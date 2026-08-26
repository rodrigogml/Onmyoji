import argparse,json
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument('--action',default='configure');p.add_argument('--json',action='store_true');p.add_argument('--onmyoji-root',type=Path);a=p.parse_args();d={'id':'cloudflare','title':'Cloudflare','description':'DNS e zonas com token no KeePass Vault.'}
 if a.action=='describe':print(json.dumps(d) if a.json else d['title'])
 elif a.action=='status':print(json.dumps({'configured':False,'valid':True}))
 else:print('Use configs/cloudflare.toml a partir de skills/cloudflare/configs/cloudflare.toml.model.')
if __name__=='__main__':main()
