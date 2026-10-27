"""Protected single premium lesson: pinned sealed ingress -> encrypted output only."""
import json,os,re,socket,sys,tempfile
from pathlib import Path
from production_input_gate import load_entry
from retry_policy import generation,attach,check_descriptor
from sealed_ingress import open_input
from private_pipeline import validate_input,private_metadata,checkpoint_hash,encoded,sha,RECIPE
from crypto_chunks_v3 import seal,open_range
from audio_policy import validate_timeline
from audio_backend import Backend,consume_keys,suppress_native_output,validate_audio,validate_checkpoint_metrics

def unpack_existing(data,root,key,ff):
 ident=data['lesson']['id'];names=[ident+'.'+s for s in ('audio.bin','content.bin','descriptor.json')];present=[(root/n).exists() for n in names]
 if not any(present):return None
 if not all(present):raise ValueError('incomplete checkpoint')
 d=json.loads((root/names[2]).read_bytes())
 check_descriptor(d,data['track'],ident,data['lineage'].get('generation'),enforce_expected=True)
 blobs={k:(root/(ident+'.'+k+'.bin')).read_bytes() for k in ('audio','content')}
 for k,kind in [('audio','audio'),('content','private-metadata')]:
  m=d[k]
  if m['namespace']!=data['namespace'] or m['lesson_id']!=ident or m['kind']!=kind or m['key_id']!='admin-law-premium-media-v1':raise ValueError('checkpoint context')
 audio=open_range(key,d['audio'],blobs['audio'])[0];meta=json.loads(open_range(key,d['content'],blobs['content'])[0]);fields=('index','text','start_s','end_s','speed','tail_hum','attempts');segments=[{k:s[k] for k in fields} for s in meta['segments']]
 expected=private_metadata(data,audio,segments,meta['validation'])
 if meta!=expected:raise ValueError('checkpoint content')
 validate_checkpoint_metrics(meta,validate_audio(audio,ff,data['track']));validate_timeline(data['track'],meta['segments'],meta['duration_s']);return d,blobs,meta

def save(root,descriptor,blobs):
 root.mkdir(parents=True,exist_ok=True)
 if any(root.iterdir()):raise ValueError('fresh outputs')
 ident=descriptor['id']
 for k in ('audio','content'):(root/(ident+'.'+k+'.bin')).write_bytes(blobs[k])
 (root/(ident+'.descriptor.json')).write_bytes(encoded(descriptor))

def main():
 if os.environ['GITHUB_REPOSITORY']!='quddnr0107-lgtm/korean-voice' or os.environ['GITHUB_REF']!='refs/heads/admin-law-v2-premium-20261026' or os.environ['GITHUB_RUN_ATTEMPT']!='1':raise ValueError('run scope')
 track,ident=sys.argv[1:3]
 if track not in ('easy','source') or not re.fullmatch(r'AL-0(?:1[1-9]|[2-6][0-9]|7[0-8])',ident):raise ValueError('premium scope')
 attempt_raw=os.environ.get('GENERATION_ATTEMPT','1')
 if attempt_raw not in ('1','2'):raise ValueError('generation attempt')
 attempt=int(attempt_raw);generation(track,ident,attempt)
 p,w,r,ct=load_entry(Path(os.environ['INPUT_ROOT']),Path(os.environ['QUEUE_PATH']),os.environ['QUEUE_SHA256'],track,ident)
 source_key,media_key=consume_keys();backend=None;stage='decrypt-input'
 try:
  def deny(*a,**kw):raise RuntimeError('network disabled')
  socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
  plain=open_input(source_key,p,w,r,ct,expected_track=track,expected_id=ident);source_key=None;data=json.loads(plain);plain=None
  if validate_input(data)!=ident or data['track']!=track or data['lineage']['revision']!={'source':'rebuild-r1-20261027','easy':'easy-r2-20261027'}[track]:raise ValueError('input identity')
  data,seed=attach(data,attempt)
  stage='model-init'
  with suppress_native_output():
   backend=Backend(Path(os.environ['ADMIN_ASSETS']),Path(os.environ['SERVER_DIR']),seed,track)
   stage='checkpoint';cached=unpack_existing(data,Path(os.environ['CHECKPOINT_DIR']),media_key,backend.ff)
   if cached:descriptor,blobs,meta=cached;state='reused'
   else:
    stage='synthesis'
    with tempfile.TemporaryDirectory(prefix='admin-law-private-',dir=os.environ['PLAIN_TEMP']) as td:audio,segments,metrics=backend.render(data['lesson']['narration_text'],td)
    stage='metadata';meta=private_metadata(data,audio,segments,metrics);validate_checkpoint_metrics(meta,validate_audio(audio,backend.ff,track));validate_timeline(track,segments,metrics['decoded_duration_s'])
    stage='encrypt-output';descriptor={'id':ident};blobs={}
    if 'generation' in data['lineage']:descriptor['generation']=data['lineage']['generation']
    for k,kind,body in [('audio','audio',audio),('content','private-metadata',encoded(meta))]:
     m,b=seal(media_key,body,ident,kind,'admin-law-premium-media-v1',namespace=data['namespace'])
     if open_range(media_key,m,b)[0]!=body:raise ValueError('output roundtrip')
     descriptor[k]=m;blobs[k]=b
    state='encrypted'
  stage='save-cipher';save(Path(os.environ['ADMIN_OUTPUT']),descriptor,blobs)
  print(json.dumps({'id':ident,'track':track,'status':state,'duration_s':meta['duration_s'],'segments':len(meta['segments']),'normalized_chars':len(data['lesson']['narration_text']),'timing':validate_timeline(track,meta['segments'],meta['duration_s']),'clipped_samples':meta['validation']['clipped_samples'],'tail_hum_chunks':meta['validation']['tail_hum_chunks']}))
 except Exception:
  report={'id':ident,'track':track,'status':'failed','stage':stage}
  if stage=='synthesis' and backend is not None:
   report['stage']=getattr(backend,'stage','synthesis');report.update(getattr(backend,'chunk_diagnostic',{}))
  print(json.dumps(report));raise RuntimeError('sanitized failure') from None
 finally:source_key=None;media_key=None
if __name__=='__main__':
 try:main()
 except Exception:sys.exit(1)
