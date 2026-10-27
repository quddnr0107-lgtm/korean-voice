"""Pure allowlisted ingress gate. No keys, network or plaintext output."""
import json,re
from pathlib import Path
from sealed_ingress import digest,validate_public

def load_entry(root,queue_path,queue_sha,track,ident):
 root=Path(root);raw=Path(queue_path).read_bytes()
 if digest(raw)!=queue_sha:raise ValueError('reviewed queue digest')
 q=json.loads(raw)
 if set(q)!={'version','receiver','bootstrap','public_record_sha256','wrapped_record_sha256','entries'} or q['version']!='admin-law-v3-production-queue/v1':raise ValueError('queue schema')
 p=q['receiver'];validate_public(p);b=q['bootstrap']
 if set(b)!={'run_id','commit','artifact_id','artifact_sha256'} or str(b['run_id'])!=p['run_id'] or b['commit']!=p['commit'] or type(b['artifact_id'])!=int or b['artifact_id']<=0 or not re.fullmatch('[a-f0-9]{64}',b['artifact_sha256']):raise ValueError('trusted bootstrap')
 public_raw=(root/'receiver-public.json').read_bytes();wrapped_raw=(root/'receiver-wrapped.json').read_bytes()
 if digest(public_raw)!=q['public_record_sha256'] or digest(wrapped_raw)!=q['wrapped_record_sha256'] or json.loads(public_raw)!=p:raise ValueError('receiver artifact pin')
 if not 1<=len(q['entries'])<=136:raise ValueError('bounded queue')
 seen=set();selected=None
 for e in q['entries']:
  if set(e)!={'track','id','bundle_id','record_sha256','cipher_sha256'} or e['track'] not in ('easy','source') or not re.fullmatch(r'AL-0(?:1[1-9]|[2-6][0-9]|7[0-8])',e['id']) or not re.fullmatch('[a-f0-9]{32}',e['bundle_id']):raise ValueError('entry scope')
  if (e['track'],e['id']) in seen:raise ValueError('duplicate entry')
  seen.add((e['track'],e['id']))
  if any(not re.fullmatch('[a-f0-9]{64}',e[k]) for k in ('record_sha256','cipher_sha256')):raise ValueError('entry digest encoding')
  if e['track']==track and e['id']==ident:selected=e
 if selected is None:raise ValueError('not approved in queue')
 rr=(root/(selected['bundle_id']+'.json')).read_bytes();ct=(root/(selected['bundle_id']+'.bin')).read_bytes()
 if digest(rr)!=selected['record_sha256'] or digest(ct)!=selected['cipher_sha256']:raise ValueError('independent input pins')
 r=json.loads(rr)
 if r['header']['bundle_id']!=selected['bundle_id']:raise ValueError('bundle context')
 return p,json.loads(wrapped_raw),r,ct
