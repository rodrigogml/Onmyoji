#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess,sys,time,tomllib,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from typing import Mapping
API='https://api.spotify.com/v1'
OPS={'me.get':('GET','/me',0),'search':('GET','/search',0),'albums.get':('GET','/albums/{id}',0),'artists.get':('GET','/artists/{id}',0),'tracks.get':('GET','/tracks/{id}',0),'playlists.list':('GET','/me/playlists',0),'playlists.get':('GET','/playlists/{id}',0),'playlists.items.list':('GET','/playlists/{id}/items',0),'playlists.create':('POST','/users/{user_id}/playlists',1),'playlists.update':('PUT','/playlists/{id}',1),'playlists.items.add':('POST','/playlists/{id}/items',1),'playlists.items.remove':('DELETE','/playlists/{id}/items',1),'library.list':('GET','/me/library',0),'library.save':('PUT','/me/library',1),'library.remove':('DELETE','/me/library',1),'following.list':('GET','/me/following',0),'following.add':('PUT','/me/following',1),'following.remove':('DELETE','/me/following',1),'top.items':('GET','/me/top/{type}',0),'recent.list':('GET','/me/player/recently-played',0),'player.state':('GET','/me/player',0),'player.devices':('GET','/me/player/devices',0),'player.current':('GET','/me/player/currently-playing',0),'player.queue':('GET','/me/player/queue',0),'player.play':('PUT','/me/player/play',1),'player.pause':('PUT','/me/player/pause',1),'player.next':('POST','/me/player/next',1),'player.previous':('POST','/me/player/previous',1),'player.seek':('PUT','/me/player/seek',1),'player.volume':('PUT','/me/player/volume',1),'player.shuffle':('PUT','/me/player/shuffle',1),'player.repeat':('PUT','/me/player/repeat',1),'player.queue.add':('POST','/me/player/queue',1)}
class E(Exception):pass
def fail(c,m):raise E(c,m)
def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--profile',required=True);a=p.parse_args();op=None
 try:
  d=tomllib.loads(a.config.read_text(encoding='utf8'));q=d['profiles'][a.profile];opq=json.load(sys.stdin);op=opq.get('operation');m,path,write=OPS[op]
  if write and (q.get('access','read_only')!='read_write' or opq.get('confirm') is not True):fail('confirmation_required','Escritas exigem perfil read_write e confirm=true.')
  allowed=q.get('allowed_operations',[])
  if allowed and op not in allowed:fail('operation_denied','Operação não permitida pelo perfil.')
  vault=Path(__file__).resolve().parents[2]/'keepass-vault/scripts/keepass_vault.py'; base=a.config.parent/'keepass.toml'
  def read(field):
   x=subprocess.run([sys.executable,str(vault),'--config',str(base),'--profile',q['vault_profile']],input=json.dumps({'operation':'read','path':q['vault_entry_path'],'field':field,'auth':{'mode':'configured'}}),text=True,capture_output=True,check=True);return json.loads(x.stdout)['result']['value']
  token=json.loads(urllib.request.urlopen(urllib.request.Request('https://accounts.spotify.com/api/token',data=urllib.parse.urlencode({'grant_type':'refresh_token','refresh_token':read(q.get('refresh_token_field','password')),'client_id':read(q.get('client_id_field','username'))}).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})).read())['access_token']
  params=opq.get('params',{});query=opq.get('query',{});body=opq.get('body');
  for k in [z.split('}',1)[0] for z in path.split('{')[1:]]:
   if k not in params:fail('missing_parameter',k)
   path=path.replace('{'+k+'}',urllib.parse.quote(str(params[k]),safe=''))
  url=API+path+('?' + urllib.parse.urlencode(query,doseq=True) if query else '');data=json.dumps(body).encode() if body is not None else None
  r=urllib.request.urlopen(urllib.request.Request(url,data=data,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method=m));raw=r.read();print(json.dumps({'ok':True,'version':1,'operation':op,'data':json.loads(raw) if raw else None},ensure_ascii=False));return 0
 except KeyError:err={'code':'unsupported_operation','message':'Operação Spotify não permitida.'}
 except E as e:err={'code':e.args[0],'message':e.args[1]}
 except Exception:err={'code':'request_failed','message':'Falha ao processar a solicitação Spotify.'}
 print(json.dumps({'ok':False,'version':1,'operation':op,'error':err},ensure_ascii=False));return 1
if __name__=='__main__':raise SystemExit(main())
