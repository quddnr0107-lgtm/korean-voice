"""Protected runner-only decrypt/synthesize/encrypt; never publishes or prints text."""
import os
os.environ['ORT_DISABLE_TELEMETRY']='1'
import sys,json,hashlib,base64,zlib,io,zipfile,re,tempfile,shutil,contextlib,socket,time,signal
from pathlib import Path
from tts_normalize import normalize,RULESET
from lecture_pack import seal_part,open_part,make_pack_manifest,validate_pack
from lecture_crypto import open_input,open_range,seal,enc,digest,PINNED_PUBLIC,namespace_for
KEY_ID='integrated-lectures-media-v1'
ROOT=Path(__file__).resolve().parents[1]

def child_env():return {k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','HOME','TMPDIR','ORT_DISABLE_TELEMETRY','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS')}
@contextlib.contextmanager
def quiet_native():
 saved=[os.dup(1),os.dup(2)];dev=os.open(os.devnull,os.O_WRONLY)
 try:
  sys.stdout.flush();sys.stderr.flush();os.dup2(dev,1);os.dup2(dev,2);yield
 finally:
  sys.stdout.flush();sys.stderr.flush();os.dup2(saved[0],1);os.dup2(saved[1],2)
  for f in saved+[dev]:os.close(f)

def parse_payload(data,ident):
 dec=zlib.decompressobj();raw=dec.decompress(data,16*1024*1024+1)
 if len(raw)>16*1024*1024 or dec.unconsumed_tail or not dec.eof or dec.unused_data:raise ValueError('decompression cap/trailing data')
 p=json.loads(raw)
 if set(p)!={'version','course','source_sha256','lesson','recipe'} or p['version']!='integrated-lecture-input/v1':raise ValueError('input schema')
 if namespace_for(ident)!='integrated-lectures/v1/'+p['course'] or p['lesson']['id']!=ident:raise ValueError('input scope')
 if p['recipe']!=json.loads((ROOT/'recipe.json').read_text()):raise ValueError('recipe pin')
 chunks=p['lesson']['chunks'];text=''.join(c['text'] for c in chunks)
 if not chunks or digest(text.encode())!=p['lesson']['source_sha256']:raise ValueError('text coverage')
 pos=0
 for j,c in enumerate(chunks):
  if c['index']!=j or c['start_cp']!=pos or c['end_cp']!=pos+len(c['text']):raise ValueError('chunk offsets')
  if c['tts_text']!=' '.join(c['text'].split()) or not c['tts_text'] or digest(c['text'].encode())!=c['sha256'] or digest(c['tts_text'].encode())!=c['tts_sha256']:raise ValueError('chunk text')
  spoken,edits,flags=normalize(c['tts_text'])
  if c.get('spoken_text')!=spoken or c.get('spoken_sha256')!=digest(spoken.encode()) or c.get('normalization_edits')!=edits or c.get('normalization_flags')!=flags or c.get('normalization_ruleset')!=RULESET or c.get('normalization_basis')!='tts_text':raise ValueError('pronunciation mapping')
  if any(f['reason']!='english-word-review' for f in flags):raise ValueError('unreviewed pronunciation')
  pos=c['end_cp']
 return p

def restore_checkpoint(key,ident,cpdir,work,expected_hash):
 candidates=sorted(cpdir.glob(ident+'.checkpoint.*.json'))
 if not candidates:return
 if len(candidates)!=1:raise ValueError('checkpoint ambiguity')
 m=json.loads(candidates[0].read_text());blob=candidates[0].with_suffix('.bin').read_bytes()
 plain,_=open_range(key,m,blob,expected_id=ident,expected_kind='checkpoint',expected_key_id=KEY_ID)
 with zipfile.ZipFile(io.BytesIO(plain)) as z:
  names=z.namelist()
  if len(names)!=len(set(names)) or any(n!='checkpoint-info.json' and not re.fullmatch(r'[a-f0-9]{64}\.(wav|json)',n) for n in names):raise ValueError('checkpoint paths')
  if sum(i.file_size for i in z.infolist())>1024*1024*1024:raise ValueError('checkpoint expanded cap')
  info=json.loads(z.read('checkpoint-info.json'))
  if info!={'id':ident,'input_hash':expected_hash}:raise ValueError('checkpoint input')
  for n in names:
   if n!='checkpoint-info.json':(work/'cache'/n).write_bytes(z.read(n))

def save_checkpoint(key,ident,work,out,input_hash):
 files=[]
 for wav in (work/'cache').glob('*.wav'):
  if not re.fullmatch(r'[a-f0-9]{64}\.wav',wav.name):continue
  meta=wav.with_suffix('.json')
  if wav.is_symlink() or meta.is_symlink() or not meta.is_file():continue
  try:
   cm=json.loads(meta.read_text())
   if cm.get('key')!=wav.stem or cm.get('wav_sha256')!=digest(wav.read_bytes()) or cm.get('recipe')!=json.loads((ROOT/'recipe.json').read_text()):continue
  except (ValueError,OSError):continue
  files.extend([wav,meta])
 if not files:return False
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as z:
  z.writestr('checkpoint-info.json',enc({'id':ident,'input_hash':input_hash}))
  for f in files:
   if f.is_symlink() or not re.fullmatch(r'[a-f0-9]{64}\.(wav|json)',f.name):raise ValueError('checkpoint file')
   z.write(f,f.name)
 m,ct=seal(key,b.getvalue(),ident,'checkpoint',KEY_ID)
 name=f"{ident}.checkpoint.{os.environ['GITHUB_RUN_ID']}.{os.environ['GITHUB_RUN_ATTEMPT']}"
 (out/(name+'.bin')).write_bytes(ct);(out/(name+'.json')).write_bytes(enc(m));return True

def main():
 from preflight import require_approval
 require_approval()
 ident=sys.argv[1];namespace_for(ident)
 job_start=int(os.environ['LECTURE_JOB_STARTED_AT'])
 if not 0<=time.time()-job_start<6*3600:raise ValueError('job clock scope')
 os.environ['LECTURE_SOFT_DEADLINE_AT']=str(job_start+270*60)
 def terminate(signum,frame):raise RuntimeError('termination_requested')
 signal.signal(signal.SIGTERM,terminate)
 if os.environ.get('GITHUB_REPOSITORY')!='quddnr0107-lgtm/korean-voice':raise ValueError('repo')
 if os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch':raise ValueError('manual only')
 if os.environ.get('GITHUB_REF')!='refs/heads/integrated-lectures-premium-20261028':raise ValueError('branch scope')
 if os.environ.get('GITHUB_WORKFLOW_REF')!='quddnr0107-lgtm/korean-voice/.github/workflows/integrated-lectures-premium.yml@refs/heads/integrated-lectures-premium-20261028':raise ValueError('workflow scope')
 # Secrets never passed to subprocesses: remove immediately and retain only memory keys.
 values=[os.environ.pop(n,None) for n in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')]
 keys=[base64.b64decode(v,validate=True) if v else b'' for v in values];values=None
 if any(len(k)!=32 for k in keys) or keys[0]==keys[1]:raise ValueError('key scope')
 source_key,media_key=keys;keys=None
 # Native inference executes inside workflow's OS network namespace. Python defense in depth:
 def deny(*a,**kw):raise RuntimeError('outbound forbidden')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 receiver=Path(os.environ['LECTURE_RECEIVER_DIR']);public=json.loads((receiver/'receiver-public.json').read_text());wrapped=json.loads((receiver/'receiver-wrapped.json').read_text())
 bundle=json.loads((ROOT/'inputs'/f'{ident}.sealed.json').read_text())
 payload=parse_payload(open_input(source_key,public,wrapped,bundle['record'],base64.b64decode(bundle['cipher_b64'],validate=True),ident),ident);source_key=None
 payload_hash=digest(enc(payload));out=Path(os.environ['LECTURE_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
 if any(out.iterdir()):raise ValueError('fresh output')
 cpdir=Path(os.environ['LECTURE_CHECKPOINT_DIR']);descriptor=cpdir/f'{ident}.descriptor.json'
 if descriptor.exists():
  d=json.loads(descriptor.read_text());meta,_=open_range(media_key,d['content'],(cpdir/f"{ident}.content.{d['content']['object_id']}.bin").read_bytes(),expected_id=ident,expected_kind='private-metadata',expected_key_id=KEY_ID)
  m=json.loads(meta)
  blob=(cpdir/f"{ident}.audio.{d['audio']['object_id']}.bin").read_bytes();validate_pack(d['audio'],blob)
  if d.get('version')!='integrated-lecture-output/v2' or d['lesson_id']!=ident or m.get('sealed_input_hash')!=payload_hash or m.get('audio_pack_sha256')!=digest(blob):raise ValueError('existing output mismatch')
  if len(m['audio_parts'])!=len(d['audio']['parts']):raise ValueError('existing part count')
  for pm,private_part in zip(d['audio']['parts'],m['audio_parts']):
   offset=pm['pack_offset'];part=open_part(media_key,pm,blob[offset:offset+pm['cipher_bytes']],expected_id=ident,expected_part_index=private_part['index'],expected_pack_id=d['audio']['object_id'])
   if digest(part)!=private_part['audio_sha256']:raise ValueError('existing part plaintext digest')
  print(json.dumps({'id':ident,'status':'already_complete'}));return
 assets=Path(os.environ['LECTURE_MODEL_ROOT']);pins=json.loads((ROOT/'model-pins.json').read_text())
 for name,h in pins['sha256'].items():
  if digest((assets/name).read_bytes())!=h:raise ValueError('model asset pin')
 with tempfile.TemporaryDirectory(prefix='lecture-plain-',dir=os.environ['RUNNER_TEMP']) as tmp:
  work=Path(tmp)
  for d in ['cache','audio','metadata','progress']: (work/d).mkdir()
  os.environ['LECTURE_WORKDIR']=str(work)
  try:
   restore_checkpoint(media_key,ident,cpdir,work,payload_hash)
   with quiet_native():
    import audio_engine
    if audio_engine.RECIPE!=payload['recipe']:raise ValueError('engine recipe mismatch')
    audio_engine.child_env=child_env
    audio_engine.init_worker();result=audio_engine.render_lesson(payload['lesson'])
   meta=json.loads((work/'metadata'/f'{ident}.json').read_text());pack_id=os.urandom(16).hex();cipher_parts=[];part_manifests=[];offset=0
   for part in meta['audio_parts']:
    plain=(work/'audio'/part['file_name']).read_bytes()
    if digest(plain)!=part['audio_sha256']:raise ValueError('encoded part digest')
    pm,ct=seal_part(media_key,plain,ident,pack_id,part['index'],part['start_sample'],part['end_sample'],offset,KEY_ID)
    if open_part(media_key,pm,ct,expected_id=ident,expected_part_index=part['index'],expected_pack_id=pack_id)!=plain:raise ValueError('part encryption roundtrip')
    part_manifests.append(pm);cipher_parts.append(ct);offset+=len(ct);part.pop('file_name')
   ac=b''.join(cipher_parts);am=make_pack_manifest(ident,pack_id,part_manifests,ac)
   meta.update(audio_pack_sha256=digest(ac),sealed_input_hash=payload_hash,source_sha256=payload['source_sha256'],course=payload['course'],lesson_source=payload['lesson'],narration_text=''.join(c['text'] for c in payload['lesson']['chunks']))
   mm,mc=seal(media_key,enc(meta),ident,'private-metadata',KEY_ID)
   (out/f"{ident}.audio.{am['object_id']}.bin").write_bytes(ac);(out/f"{ident}.content.{mm['object_id']}.bin").write_bytes(mc)
   (out/f'{ident}.descriptor.json').write_bytes(enc({'version':'integrated-lecture-output/v2','lesson_id':ident,'audio':am,'content':mm}))
   print(json.dumps({'id':ident,'status':'completed','duration_s':meta['validation']['duration_s'],'chunks':len(meta['segments']),'parts':len(meta['audio_parts']),'clipped_samples':0,'wall_s':meta['wall_s']}))
  except BaseException:
   for f in out.iterdir():f.unlink()
   cached=save_checkpoint(media_key,ident,work,out,payload_hash)
   print(json.dumps({'id':ident,'status':'failed','encrypted_checkpoint_saved':cached}));raise
if __name__=='__main__':
 try:main()
 except BaseException:
  print('{"status":"failed","stage":"protected-render"}');sys.exit(1)
