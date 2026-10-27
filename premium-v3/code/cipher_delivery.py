"""Keyless v3 ciphertext delivery. Explicit CLI only; no import-time IO/network.
Single publisher per track is REQUIRED (workflow concurrency). No clobber/delete.
"""
import argparse,hashlib,json,os,re,shutil,subprocess,sys,tempfile
from pathlib import Path
from crypto_chunks_v3 import validate
from retry_policy import check_descriptor
REPO='quddnr0107-lgtm/korean-voice'
TRACKS={'source':('admin-law/v3/source','admin-law-v3-source-premium-20261027'),'easy':('admin-law/v3/easy','admin-law-v3-easy-r2-premium-20261027')}
IDS={f'AL-{i:03}' for i in range(11,79)}
SUFFIXES=('audio.bin','content.bin','descriptor.json')
KEY_ID='admin-law-premium-media-v1'
MKEYS={'version','key_id','namespace','lesson_id','kind','object_id','salt_b64','nonce_prefix_b64','plain_bytes','chunk_bytes','cipher_sha256'}
MAX_BLOB=64*1024*1024+16*1024
MAX_JSON=2*1024*1024
MAX_INDEX=1024*1024
def cap_for(name):return MAX_INDEX if name=='index.json' else (MAX_JSON if name.endswith('.json') else MAX_BLOB)
sha=lambda b:hashlib.sha256(b).hexdigest()
def encoded(x):return json.dumps(x,separators=(',',':'),ensure_ascii=True).encode('ascii')
def pairs(items):
 d={}
 for k,v in items:
  if k in d:raise ValueError('duplicate key')
  d[k]=v
 return d
def loads(b):return json.loads(b,object_pairs_hook=pairs)
def track_scope(track):
 if track not in TRACKS:raise ValueError('track')
 return TRACKS[track]
def assigned(ids):
 ids=list(ids)
 if not ids or len(ids)!=len(set(ids)) or not set(ids)<=IDS:raise ValueError('ids')
 return set(ids)
def names(ident):return {ident+'.'+s for s in SUFFIXES}
def read(path,cap):
 if path.is_symlink() or not path.is_file() or path.stat().st_size>cap:raise ValueError('file')
 return path.read_bytes()
def checked(directory,ident,track):
 namespace,_=track_scope(track)
 if ident not in IDS:raise ValueError('id')
 d=loads(read(directory/(ident+'.descriptor.json'),MAX_JSON))
 check_descriptor(d,track,ident)
 for k,kind in [('audio','audio'),('content','private-metadata')]:
  m=d[k]
  if type(m)!=dict or set(m)!=MKEYS:raise ValueError('manifest schema')
  if m['namespace']!=namespace or m['lesson_id']!=ident or m['kind']!=kind or m['key_id']!=KEY_ID:raise ValueError('manifest scope')
  if type(m['chunk_bytes'])!=int or type(m['cipher_sha256'])!=list:raise ValueError('manifest types')
  b=read(directory/(ident+'.'+k+'.bin'),MAX_BLOB);validate(m,b)
  if k=='content' and m['plain_bytes']>1048576:raise ValueError('metadata cap')
  for i,h in enumerate(m['cipher_sha256']):
   if sha(b[i*65552:(i+1)*65552])!=h:raise ValueError('chunk digest')
 return d

def files(directory,track,allowed=None):
 track_scope(track);found={}
 if directory.is_symlink() or not directory.is_dir():raise ValueError('directory')
 for p in directory.iterdir():
  if p.is_symlink() or not p.is_file() or not re.fullmatch(r'AL-0(?:1[1-9]|[2-6][0-9]|7[0-8])\.(?:audio\.bin|content\.bin|descriptor\.json)',p.name):raise ValueError('output allowlist')
  found.setdefault(p.name[:6],set()).add(p.name)
 if allowed is not None and not set(found)<=assigned(allowed):raise ValueError('assigned scope')
 for ident,ns in found.items():
  if ns!=names(ident):raise ValueError('incomplete output')
  checked(directory,ident,track)
 return set(found)

def gh(*args,json_output=False,timeout=180):
 r=subprocess.run(['gh',*args],check=True,stdout=subprocess.PIPE if json_output else subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout)
 return loads(r.stdout) if json_output else None
class Release:
 """One API snapshot per invocation. Always re-download after writes; no URL/token logs."""
 def __init__(self,track,prepare=False):
  self.track=track;self.namespace,self.tag=track_scope(track)
  if prepare:
   pages=gh('api',f'repos/{REPO}/releases?per_page=100','--paginate','--slurp',json_output=True)
   matches=[r for page in pages for r in page if r['tag_name']==self.tag]
   if len(matches)>1:raise ValueError('duplicate release')
   if not matches:
    target=os.environ.get('GITHUB_SHA','')
    if not re.fullmatch('[a-f0-9]{40}',target):raise ValueError('release commit pin')
    gh('release','create',self.tag,'--repo',REPO,'--title',self.tag,'--notes','Encrypted administrative law audio.','--target',target,'--latest=false')
  r=gh('api',f'repos/{REPO}/releases/tags/{self.tag}',json_output=True)
  if r['tag_name']!=self.tag or r['draft']:raise ValueError('release scope')
  pages=gh('api',f'repos/{REPO}/releases/{r["id"]}/assets?per_page=100','--paginate','--slurp',json_output=True)
  self.assets={}
  for page in pages:
   for a in page:
    n=a['name']
    if n in self.assets or (n!='index.json' and not re.fullmatch(r'AL-0(?:1[1-9]|[2-6][0-9]|7[0-8])\.(?:audio\.bin|content\.bin|descriptor\.json)',n)):raise ValueError('remote asset allowlist')
    cap=cap_for(n)
    if type(a['size'])!=int or not 0<a['size']<=cap:raise ValueError('remote asset size')
    self.assets[n]=a
 def download(self,selected,out):
  selected=set(selected)
  if not selected:return
  if not selected<=set(self.assets):raise ValueError('missing asset')
  out.mkdir(parents=True,exist_ok=True)
  if out.is_symlink() or any(out.iterdir()):raise ValueError('nonempty download')
  args=['release','download',self.tag,'--repo',REPO,'--dir',str(out)]
  for n in sorted(selected):args+=['--pattern',n]
  gh(*args,timeout=900)
  if {p.name for p in out.iterdir()}!=selected:raise ValueError('download allowlist')
  for n in selected:
   a=self.assets[n];b=read(out/n,cap_for(n))
   if len(b)!=a['size']:raise ValueError('download length')
   if a.get('digest') and a['digest']!='sha256:'+sha(b):raise ValueError('asset digest')
 def upload(self,path):
  if path.name in self.assets:raise ValueError('overwrite forbidden')
  b=read(path,cap_for(path.name))
  gh('release','upload',self.tag,'--repo',REPO,str(path))
  self.assets[path.name]={'size':len(b),'digest':'sha256:'+sha(b)}

def complete_remote(remote,ids,allow_orphans=False):
 complete=set();selected=set()
 for ident in sorted(assigned(ids)):
  needed=names(ident);present=needed & set(remote.assets)
  if ident+'.descriptor.json' in present:
   if present!=needed:raise ValueError('committed incomplete')
   complete.add(ident);selected|=needed
  elif present and not allow_orphans:raise ValueError('orphan: publisher recovery required')
 return complete,selected

def collect(remote,out,ids):
 ids=assigned(ids)
 if out.exists() and (out.is_symlink() or any(out.iterdir())):raise ValueError('nonempty checkpoints')
 complete,selected=complete_remote(remote,ids)
 out.mkdir(parents=True,exist_ok=True);remote.download(selected,out)
 if files(out,remote.track,ids)!=complete:raise ValueError('checkpoint set')
 return len(complete)

def make_index(directory,track):
 if files(directory,track)!=IDS:raise ValueError('not complete')
 return index_from_validated(directory,track)
def index_from_validated(directory,track):
 namespace,tag=track_scope(track)
 prefix=f'https://github.com/{REPO}/releases/download/{tag}/'
 rows=[]
 for ident in sorted(IDS):
  d=checked(directory,ident,track)
  rows.append({'id':ident,**{k:{'manifest':d[k],'cipher_url':prefix+ident+'.'+k+'.bin'} for k in ('audio','content')}})
 return {'version':'admin-law-premium-index/v1','namespace':namespace,'lessons':rows}

def publish(remote,incoming,ids):
 ids=assigned(ids);new=files(incoming,remote.track,ids)
 # Full cached release inventory, including orphan bytes, validates immutable reuse.
 complete,_=complete_remote(remote,IDS,allow_orphans=True)
 with tempfile.TemporaryDirectory(prefix='cipher-delivery-') as tmp:
  root=Path(tmp);before=root/'before';before.mkdir()
  remote.download(set(remote.assets),before)
  for ident in complete:checked(before,ident,remote.track)
  if 'index.json' in remote.assets and complete!=IDS:raise ValueError('premature index')
  # Validate ALL immutable conflicts before the first upload.
  for ident in new:
   for n in names(ident):
    if n in remote.assets and read(before/n,MAX_BLOB)!=read(incoming/n,MAX_BLOB):raise ValueError('immutable conflict')
  for ident in sorted(new):
   for suffix in SUFFIXES: # descriptor last, never replace existing bytes
    p=incoming/(ident+'.'+suffix)
    if p.name not in remote.assets:remote.upload(p)
  # Old objects were remotely downloaded and checked above; re-fetch every
  # incoming/reused object after upload. Avoid another full 68-object copy.
  confirmed=root/'confirmed';confirmed.mkdir()
  remote.download(set().union(*(names(i) for i in new)) if new else set(),confirmed)
  for ident in new:
   checked(confirmed,ident,remote.track)
   for n in names(ident):
    if sha(read(confirmed/n,MAX_BLOB))!=sha(read(incoming/n,MAX_BLOB)):raise ValueError('remote confirmation')
    shutil.move(str(confirmed/n),str(before/n))
  done,selected=complete_remote(remote,IDS,allow_orphans=True)
  for ident in done:checked(before,ident,remote.track)
  if done==IDS:
   body=encoded(index_from_validated(before,remote.track));p=root/'index.json';p.write_bytes(body)
   if 'index.json' in remote.assets:
    if read(before/'index.json',MAX_INDEX)!=body:raise ValueError('immutable index conflict')
   else:remote.upload(p)
   proof=root/'index-proof';proof.mkdir();remote.download({'index.json'},proof)
   if read(proof/'index.json',MAX_INDEX)!=body:raise ValueError('index confirmation')
  return len(done)

def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare','checkpoint','gate','publish'));p.add_argument('--track',required=True,choices=tuple(TRACKS));p.add_argument('--directory',type=Path);p.add_argument('--ids');a=p.parse_args()
 try:
  ids=assigned(a.ids.split(',')) if a.ids else None
  if a.mode=='prepare':r=Release(a.track,prepare=True);n=0
  elif a.mode=='gate':
   if ids is None or a.directory is None:raise ValueError('arguments')
   n=len(files(a.directory,a.track,ids))
  else:
   if ids is None or a.directory is None:raise ValueError('arguments')
   r=Release(a.track);n=collect(r,a.directory,ids) if a.mode=='checkpoint' else publish(r,a.directory,ids)
  print(json.dumps({'validated_lessons':n,'complete':a.mode=='publish' and n==68}))
 except Exception:
  print('{"failed":true}',file=sys.stderr);return 1
 return 0
if __name__=='__main__':sys.exit(main())
