"""Keyless launch, artifact and output gates; strict reviewed source/easy queues."""
import hashlib,json,os,re,subprocess,sys,zipfile
from pathlib import Path
from retry_policy import launch_attempt,generation,check_descriptor
PINS={'source':'05c4ac85b25e3f6dcb4451e6b16f352f50b16269e84d1b7ab1687e637b16daa7','easy':'9b90d1d32b9d66a96f396efcef03d4905f3f7b062a36ca8de2f29b43c8faf4bc'}
sha=lambda b:hashlib.sha256(b).hexdigest()
def context():
 if os.environ['GITHUB_REPOSITORY']!='quddnr0107-lgtm/korean-voice' or os.environ['GITHUB_REF']!='refs/heads/admin-law-v2-premium-20261026' or os.environ['GITHUB_RUN_ATTEMPT']!='1':raise ValueError('scope')
 root=Path('premium-v3');launch=json.loads((root/'launch.json').read_bytes())
 if set(launch) not in ({'track','ids','queue_sha256'},{'track','ids','queue_sha256','generation_attempt'}) or launch['track'] not in PINS or launch['queue_sha256']!=PINS[launch['track']]:raise ValueError('launch')
 ids=launch['ids']
 if not isinstance(ids,list) or not 1<=len(ids)<=68 or len(set(ids))!=len(ids) or any(not re.fullmatch(r'AL-0(?:1[1-9]|[2-6][0-9]|7[0-8])',i) for i in ids):raise ValueError('ids')
 launch_attempt(launch)
 path=root/'inputs'/launch['track']/'queue.json';raw=path.read_bytes()
 if sha(raw)!=launch['queue_sha256']:raise ValueError('queue pin')
 q=json.loads(raw)
 if {(e['track'],e['id']) for e in q['entries']}!={(launch['track'],f'AL-{i:03}') for i in range(11,79)}:raise ValueError('queue completeness')
 return root,launch,q

def main():
 root,l,q=context();mode=sys.argv[1]
 if mode=='plan':
  with open(os.environ['GITHUB_OUTPUT'],'a') as f:
   f.write('matrix='+json.dumps({'include':[{'track':l['track'],'id':i} for i in l['ids']]},separators=(',',':'))+'\ngeneration_attempt='+str(launch_attempt(l))+'\ntrack='+l['track']+'\nqueue_sha='+l['queue_sha256']+'\n')
 elif mode=='receiver':
  dest=Path(os.environ['INPUT_ROOT']);dest.mkdir(parents=True,exist_ok=True);z=Path(os.environ['RUNNER_TEMP'])/'receiver.zip';b=q['bootstrap']
  # Download only authenticated exact artifact id, then verify independently reviewed ZIP digest.
  with z.open('wb') as f:subprocess.run(['gh','api',f'repos/quddnr0107-lgtm/korean-voice/actions/artifacts/{b["artifact_id"]}/zip'],stdout=f,stderr=subprocess.DEVNULL,check=True)
  if sha(z.read_bytes())!=b['artifact_sha256']:raise ValueError('artifact hash')
  with zipfile.ZipFile(z) as a:
   if sorted(a.namelist())!=['receiver-public.json','receiver-wrapped.json']:raise ValueError('artifact names')
   for n,k in [('receiver-public.json','public_record_sha256'),('receiver-wrapped.json','wrapped_record_sha256')]:
    raw=a.read(n)
    if len(raw)>4096 or sha(raw)!=q[k]:raise ValueError('artifact member')
    (dest/n).write_bytes(raw)
  # Ciphertext only; no secret or manuscript appears here.
  ident=os.environ['LESSON_ID'];e=next(e for e in q['entries'] if e['id']==ident);src=root/'inputs'/l['track']
  for suffix,key in [('.json','record_sha256'),('.bin','cipher_sha256')]:
   raw=(src/(e['bundle_id']+suffix)).read_bytes()
   if sha(raw)!=e[key]:raise ValueError('cipher pin')
   (dest/(e['bundle_id']+suffix)).write_bytes(raw)
 elif mode=='publish':
  directory=Path(os.environ['INCOMING']);allowed=set(l['ids']);actual=set()
  for p in directory.iterdir():
   if p.is_symlink() or not p.is_file():raise ValueError('unexpected output')
   ident=p.name[:6]
   if ident not in allowed or p.name not in [ident+'.'+s for s in ('audio.bin','content.bin','descriptor.json')]:raise ValueError('output scope')
   actual.add(ident)
  if not actual:raise ValueError('no successful outputs')
  for ident in actual:
   d=json.loads((directory/(ident+'.descriptor.json')).read_bytes())
   check_descriptor(d,l['track'],ident,generation(l['track'],ident,launch_attempt(l)),enforce_expected=True)
  from cipher_delivery import publish,Release
  count=publish(Release(l['track']),directory,actual)
  print(json.dumps({'track':l['track'],'validated_lessons':count,'complete':count==68}))
 else:raise ValueError('mode')
if __name__=='__main__':
 try:main()
 except Exception:print('production gate failed');sys.exit(1)
