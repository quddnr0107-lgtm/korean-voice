"""Keyless, fail-closed ciphertext artifact gate and release publisher. Local draft."""
import argparse,hashlib,json,os,re,shutil,subprocess,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'renderer'))
from crypto_chunks import validate
TAG='admin-law-v2-premium-20261026'
IDS={f'AL-{n:03}' for n in range(11,79)}
sha=lambda b:hashlib.sha256(b).hexdigest()
def checked(directory,ident):
 p=directory/(ident+'.descriptor.json')
 if p.is_symlink():raise ValueError('symlink')
 d=json.loads(p.read_bytes())
 if set(d)!={'id','audio','content'} or d['id']!=ident or ident not in IDS:raise ValueError('descriptor schema')
 for k,kind in [('audio','audio'),('content','private-metadata')]:
  m=d[k]
  if set(m)!={'version','key_id','namespace','lesson_id','kind','object_id','salt_b64','nonce_prefix_b64','plain_bytes','chunk_bytes','cipher_sha256'}:raise ValueError('manifest exact schema')
  p=directory/(ident+'.'+k+'.bin')
  if p.is_symlink() or not p.is_file():raise ValueError('blob path')
  b=p.read_bytes();validate(m,b)
  if m['lesson_id']!=ident or m['kind']!=kind or m['key_id']!='admin-law-premium-media-v1':raise ValueError('manifest scope')
  if kind=='private-metadata' and m['plain_bytes']>1048576:raise ValueError('metadata size')
  for i,h in enumerate(m['cipher_sha256']):
   if sha(b[i*65552:(i+1)*65552])!=h:raise ValueError('cipher hash')
 return d

def files(directory):
 found={}
 for p in directory.iterdir():
  if not p.is_file() or p.is_symlink() or not re.fullmatch(r'AL-0(?:1[1-9]|[2-6][0-9]|7[0-8])\.(?:audio\.bin|content\.bin|descriptor\.json)',p.name):raise ValueError('unexpected output file')
  found.setdefault(p.name[:6],set()).add(p.name)
 for ident,names in found.items():
  if names!={ident+'.audio.bin',ident+'.content.bin',ident+'.descriptor.json'}:raise ValueError('incomplete output')
  checked(directory,ident)
 return sorted(found)

def gate(source,destination,status,assigned):
 if destination.exists() and any(destination.iterdir()):raise ValueError('nonempty staging')
 r=json.loads(status.read_bytes())
 if set(r)!={'results'} or len(r['results'])!=len(assigned):raise ValueError('status schema')
 seen=set();success=set()
 for item in r['results']:
  ident=item['id'];state=item['status']
  if ident not in assigned or ident in seen or state not in ('encrypted','reused','failed'):raise ValueError('status scope')
  seen.add(ident)
  if state!='failed':success.add(ident)
 available=set(files(source)) if source.exists() else set()
 if available!=success:raise ValueError('status file mismatch')
 destination.mkdir(parents=True,exist_ok=True)
 for ident in sorted(success):
  for suffix in ('audio.bin','content.bin','descriptor.json'):shutil.copyfile(source/(ident+'.'+suffix),destination/(ident+'.'+suffix))
 return len(success)

def gh(*args):subprocess.run(['gh',*args],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=180)
def upload_immutable(path,repo):
 # A previously interrupted upload can leave orphan blobs; only byte-identical reuse.
 from checkpoints import asset_names,download
 if path.name in asset_names():
  with tempfile.TemporaryDirectory() as tmp:
   download(path.name,Path(tmp))
   if (Path(tmp)/path.name).read_bytes()!=path.read_bytes():raise ValueError('remote immutable conflict')
 else:gh('release','upload',TAG,'--repo',repo,str(path))

def publish(incoming,existing,repo):
 if repo!='quddnr0107-lgtm/korean-voice':raise ValueError('repository scope')
 old=set(files(existing));new=set(files(incoming))
 for ident in sorted(new):
  paths=[incoming/(ident+'.'+s) for s in ('audio.bin','content.bin','descriptor.json')]
  if ident in old:
   if any(p.read_bytes()!=(existing/p.name).read_bytes() for p in paths):raise ValueError('immutable checkpoint conflict')
  else:
   # Descriptor last: interrupted upload is not a reusable checkpoint.
   for p in paths:upload_immutable(p,repo)
   for p in paths:shutil.copyfile(p,existing/p.name)
 complete=set(files(existing))
 if complete==IDS:
  prefix=f'https://github.com/{repo}/releases/download/{TAG}/'
  index={'version':'admin-law-premium-index/v1','namespace':'admin-law/v2','lessons':[]}
  for ident in sorted(IDS):
   d=checked(existing,ident);index['lessons'].append({'id':ident,**{k:{'manifest':d[k],'cipher_url':prefix+ident+'.'+k+'.bin'} for k in ('audio','content')}})
  # Index written outside strict ciphertext directory; immutable once published.
  target=existing.parent/'index.json';target.write_text(json.dumps(index,separators=(',',':')))
  upload_immutable(target,repo)
 return len(complete)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['gate','publish']);ap.add_argument('--source',type=Path,required=True);ap.add_argument('--destination',type=Path,required=True);ap.add_argument('--status',type=Path);ap.add_argument('--lessons');ap.add_argument('--repo');a=ap.parse_args()
 try:
  if a.mode=='gate':
   ids=a.lessons.split(',')
   if not 1<=len(ids)<=8 or len(set(ids))!=len(ids) or not set(ids)<=IDS:raise ValueError('batch scope')
   n=gate(a.source,a.destination,a.status,set(ids))
  else:n=publish(a.source,a.destination,a.repo)
  print(json.dumps({'validated_lessons':n,'complete':n==68 if a.mode=='publish' else False}))
 except Exception:print('Ciphertext validation/publication failed',file=sys.stderr);sys.exit(1)
