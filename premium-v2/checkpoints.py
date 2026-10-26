"""Keyless checkpoint download; no network errors interpreted as empty release."""
import json,os,subprocess,sys,tempfile
from pathlib import Path
from publish_cipher import IDS,TAG,checked
REPO='quddnr0107-lgtm/korean-voice'
def asset_names():
 p=subprocess.run(['gh','release','view',TAG,'--repo',REPO,'--json','assets'],check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=60)
 return {a['name'] for a in json.loads(p.stdout)['assets']}
def download(name,directory):
 subprocess.run(['gh','release','download',TAG,'--repo',REPO,'--pattern',name,'--dir',str(directory)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=180)
def collect(out,ids,allow_orphans_for_publisher=False):
 if not set(ids)<=IDS:raise ValueError('scope')
 names=asset_names();out.mkdir(parents=True,exist_ok=True)
 if any(out.iterdir()):raise ValueError('nonempty checkpoint directory')
 for ident in sorted(ids):
  required={ident+'.'+suffix for suffix in ('audio.bin','content.bin','descriptor.json')}
  if ident+'.descriptor.json' not in names:
   if required & names and not allow_orphans_for_publisher:raise ValueError('uncommitted orphan: rerun publisher only from retained artifact')
   continue
  if not required<=names:raise ValueError('incomplete committed checkpoint')
  for name in sorted(required):download(name,out)
  checked(out,ident)
if __name__=='__main__':
 try:collect(Path(sys.argv[1]),IDS if sys.argv[2]=='all' else sys.argv[2].split(','),len(sys.argv)==4 and sys.argv[3]=='--allow-orphans-for-publisher')
 except Exception:print('Checkpoint download failed',file=sys.stderr);sys.exit(1)
