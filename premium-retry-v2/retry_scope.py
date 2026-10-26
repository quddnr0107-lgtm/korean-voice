"""Fail-closed, keyless scope and existing-code pin gate. No network at import."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
REPO='quddnr0107-lgtm/korean-voice'
REF='refs/heads/admin-law-v2-premium-20261026'
INITIAL_RUN=40301748335
INITIAL_SHA='9347cbcec54ef3a218382c5d2acc6848ed0654e3'
TAG='admin-law-v2-premium-20261026'
def validate_scope(d):
 if set(d)!={'version','frozen','initial_run','initial_sha','failed','pins'} or d['version']!='admin-law-failed-retry/v1' or d['frozen'] is not True or d['initial_run']!=INITIAL_RUN or d['initial_sha']!=INITIAL_SHA:raise ValueError('unfrozen scope')
 if not isinstance(d['failed'],list) or not 1<=len(d['failed'])<=68:raise ValueError('retry count')
 seen=set()
 for row in d['failed']:
  if set(row)!={'id','original_batch'}:raise ValueError('retry schema')
  ident=row['id']
  if ident not in {f'AL-{n:03}' for n in range(11,79)} or ident in seen or type(row['original_batch']) is not int or row['original_batch']!=(int(ident[-3:])-11)//8:raise ValueError('retry identity/batch')
  seen.add(ident)
 return d['failed']
def pin_files(d,root):
 import re
 if not d['pins']:raise ValueError('missing code pins')
 for name,digest in d['pins'].items():
  if not (name.startswith('premium-v2/') or name.startswith('premium-retry-v2/')) or '..' in Path(name).parts or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('pin path')
  p=root/name
  if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('reviewed file mismatch')
def current_run_gate(env):
 sha=env.get('GITHUB_SHA','');approved=env.get('APPROVED_RETRY_SHA','')
 if env.get('GITHUB_REPOSITORY')!=REPO or env.get('GITHUB_REF')!=REF or env.get('GITHUB_RUN_ATTEMPT')!='1' or len(sha)!=40 or sha!=approved:raise ValueError('execution authorization gate')
def get_json(args):
 p=subprocess.run(['gh',*args],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=True,timeout=60)
 return json.loads(p.stdout)
def initial_terminal():
 import urllib.request
 with urllib.request.urlopen('https://api.github.com/repos/'+REPO+'/actions/runs/'+str(INITIAL_RUN),timeout=60) as response:d=json.load(response)
 if d['status']!='completed' or d['head_sha']!=INITIAL_SHA:raise ValueError('initial run not terminal')
def absent_checkpoint(ident):
 assets=get_json(['release','view',TAG,'--repo',REPO,'--json','assets'])
 names={x['name'] for x in assets['assets']}
 if any(ident+'.'+suffix in names for suffix in ('audio.bin','content.bin','descriptor.json')):raise ValueError('existing success/orphan forbids retry')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--scope',type=Path,required=True);ap.add_argument('--root',type=Path,default=Path('.'));ap.add_argument('--id');a=ap.parse_args()
 try:
  d=json.loads(a.scope.read_bytes());rows=validate_scope(d);current_run_gate(os.environ);pin_files(d,a.root);initial_terminal()
  if a.id:
   if a.id not in {r['id'] for r in rows}:raise ValueError('not assigned')
   absent_checkpoint(a.id)
  else:
   for row in rows:absent_checkpoint(row['id'])
   print(json.dumps({'include':[{'id':r['id'],'batch':r['original_batch']} for r in rows]},separators=(',',':')))
 except Exception:raise SystemExit('Retry scope/preflight rejected; no private details disclosed.')
if __name__=='__main__':main()
