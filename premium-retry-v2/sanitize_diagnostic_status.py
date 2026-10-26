"""LOCAL DRAFT: publish only fixed id/status/stage enums, never raw status input."""
import argparse,json,sys
from pathlib import Path
STAGES=frozenset({'input-load','checkpoint-read','checkpoint-auth','checkpoint-decode','checkpoint-qc','checkpoint-save','backend-setup','render-call','split-validation','tts-inference','wave-shaping','hum-check','chunk-quality','wav-write','mp3-encode','mp3-decode-qc','metadata-build','encrypt-output','output-save','protected-processing'})
IDS=frozenset(f'AL-{n:03}' for n in range(11,79))
def sanitize(raw,assigned):
 if len(raw)>16384 or not assigned or not set(assigned)<=IDS or len(set(assigned))!=len(assigned):raise ValueError('scope')
 d=json.loads(raw)
 if set(d)!={'results'} or not isinstance(d['results'],list) or len(d['results'])!=len(assigned):raise ValueError('schema')
 out=[];seen=set()
 for r in d['results']:
  if not isinstance(r,dict) or r.get('id') not in assigned or r['id'] in seen:raise ValueError('id')
  seen.add(r['id']);state=r.get('status')
  if state in ('encrypted','reused'):
   if set(r)!={'id','status'}:raise ValueError('fields')
   out.append({'id':r['id'],'status':state})
  elif state=='failed':
   if set(r)!={'id','status','stage'} or r['stage'] not in STAGES:raise ValueError('stage')
   out.append({'id':r['id'],'status':'failed','stage':r['stage']})
  else:raise ValueError('state')
 return {'results':out}
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--lessons',required=True);a=ap.parse_args()
 try:
  with a.source.open('rb') as f:raw=f.read(16385)
  result=sanitize(raw,a.lessons.split(','));a.output.parent.mkdir(parents=True,exist_ok=True)
  with a.output.open('x') as f:json.dump(result,f,separators=(',',':'))
 except Exception:raise SystemExit('Diagnostic status rejected; no raw input disclosed.')
