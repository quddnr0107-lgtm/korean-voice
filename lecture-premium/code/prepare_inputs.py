"""Local explicit encryption preparation. Never uploads or reads secret keys."""
import sys,json,zlib,base64,copy
from pathlib import Path
from lecture_crypto import seal_input,PINNED_PUBLIC,enc,digest
from tts_normalize import normalize,RULESET
ROOT=Path(__file__).resolve().parents[1]
SOURCES=[('tongbang','tongbang-full-20261028',25),('directive','directive-full-20261028',78),('reserve-law','reserve-law-full-20261028',34)]
def build(write=False):
 if write:
  from preflight import require_approval
  require_approval()
 private=[];payloads=[];blocking=[];info_flags=0;recipe=json.loads((ROOT/'recipe.json').read_text())
 for course,directory,count in SOURCES:
  base=ROOT.parent/directory;m=json.loads((base/'manifest.json').read_text());proof=json.loads((base/'source_partition_proof.json').read_text())
  assert len(m['lessons'])==count and proof['source_sha256']==proof['reconstructed_sha256']
  for l in m['lessons']:
   lesson=copy.deepcopy(l);lesson.pop('source_text_path',None);lesson.pop('text_path',None)
   raw=''.join(c['text'] for c in lesson['chunks'])
   assert digest(raw.encode())==lesson['source_sha256']
   for c in lesson['chunks']:
    assert c['tts_text']==' '.join(c['text'].split())
    spoken,edits,flags=normalize(c['tts_text'])
    c.update(spoken_text=spoken,spoken_sha256=digest(spoken.encode()),normalization_edits=edits,normalization_flags=flags,normalization_ruleset=RULESET,normalization_basis='tts_text')
    for f in flags:
     if f['reason']=='english-word-review':info_flags+=1
     else:blocking.append({'id':l['id'],'chunk_id':c['id'],'reason':f['reason']})
   payload={'version':'integrated-lecture-input/v1','course':course,'source_sha256':proof['source_sha256'],'lesson':lesson,'recipe':recipe}
   plain=zlib.compress(enc(payload),9)
   if len(plain)>2*1024*1024:raise ValueError('compressed input too large')
   private.append({'id':l['id'],'plain_bytes':len(plain),'input_sha256':digest(enc(payload)),'chunks':len(l['chunks'])})
   payloads.append((l['id'],plain))
 if write:
  if blocking:raise ValueError('unresolved pronunciation review')
  rows=[]
  for ident,plain in payloads:
   record,cipher=seal_input(PINNED_PUBLIC,ident,plain);obj={'record':record,'cipher_b64':base64.b64encode(cipher).decode()};fn=ident+'.sealed.json'
   (ROOT/'inputs'/fn).write_bytes(enc(obj));rows.append({'id':ident,'file':fn,'cipher_sha256':digest(cipher)})
  (ROOT/'queue.json').write_bytes(enc({'version':'integrated-lecture-queue/v1','count':137,'lessons':rows}))
  (ROOT/'private-preparation-receipt.json').write_text(json.dumps(private,indent=2))
 report={'status':'sealed' if write else 'validated_only','lessons':len(private),'total_chunks':sum(x['chunks'] for x in private),'max_compressed_bytes':max(x['plain_bytes'] for x in private),'total_compressed_bytes':sum(x['plain_bytes'] for x in private),'blocking_pronunciation_flags':len(blocking),'english_pass_through_flags':info_flags}
 print(json.dumps(report));return report
if __name__=='__main__':build('--seal-approved' in sys.argv)
