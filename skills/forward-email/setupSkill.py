import argparse,json
def main():
 p=argparse.ArgumentParser();p.add_argument('--action',default='configure');p.add_argument('--json',action='store_true');p.add_argument('--onmyoji-root');a=p.parse_args();d={'id':'forward-email','title':'Forward Email','description':'Domínios e aliases com token no KeePass Vault.'}
 if a.action=='describe':print(json.dumps(d) if a.json else d['title'])
 elif a.action=='status':print(json.dumps({'configured':False,'valid':True}))
 else:print('Use configs/forward-email.toml a partir do modelo da skill.')
if __name__=='__main__':main()
