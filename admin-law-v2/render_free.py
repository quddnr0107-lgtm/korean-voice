"""Public FREE lessons 1-10 only. Never imports production server or cache."""
import hashlib,json,os,re,socket,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; HERE=Path(__file__).resolve().parent
TAG='admin-law-v2-free-20261026'; REPO='quddnr0107-lgtm/korean-voice'
BASE=f'https://github.com/{REPO}/releases/download/{TAG}/'
RECIPE={'engine':'Supertonic 3','recipe':'k2','style':'F2','voice':'female','steps':16,'base_speed':1.05,'r':1.0,'pause_s':0.48,'model_revision':'3cadd1ee6394adea1bd021217a0e650ede09a323','helper_revision':'1e9799e964ea4c0dad7cde993b65c3c813a7b373'}
sha=lambda b:hashlib.sha256(b).hexdigest()
corpus=json.loads((HERE/'free-corpus.json').read_text())
lessons=corpus['lessons']; assert len(lessons)==10 and [x['number'] for x in lessons]==list(range(1,11))
assert all(x['id']==f"admin-law-v2-AL-{x['number']:03d}" for x in lessons)
shard=int(os.environ['SHARD']);assert shard in (0,1)
out=Path(os.environ['ADMIN_OUT']);out.mkdir(parents=True,exist_ok=True)
def gh(*args,check=True):return subprocess.run(['gh',*args,'--repo',REPO],check=check,capture_output=True,text=True)
def fetch_asset(name,dest):
 p=gh('release','download',TAG,'--pattern',name,'--dir',str(dest),check=False)
 return p.returncode==0
pending=[]
for lesson in lessons:
 if (lesson['number']-1)%2!=shard:continue
 ident=lesson['id']; text=lesson['narration_text']; assert sha(text.encode())==lesson['narration_sha256']
 key=sha(json.dumps({'text_sha':lesson['narration_sha256'],'recipe':RECIPE},sort_keys=True).encode())
 with tempfile.TemporaryDirectory() as d:
  if fetch_asset(ident+'.json',d):
   meta=json.loads((Path(d)/(ident+'.json')).read_text())
   if meta.get('id')==ident and meta.get('number')==lesson['number'] and meta.get('access')=='free' and meta.get('voice')==RECIPE and meta.get('validation',{}).get('clipped_samples')==0 and meta.get('validation',{}).get('tail_hum_chunks')==0 and meta.get('checkpoint_hash')==key and fetch_asset(ident+'.mp3',d) and sha((Path(d)/(ident+'.mp3')).read_bytes())==meta['audio_sha256']:
    print('Verified checkpoint, skipped',ident,flush=True);continue
 lesson['checkpoint_hash']=key;pending.append(lesson)
if not pending:sys.exit(0)
def deny(*a,**kw):raise RuntimeError('No network in synthesis process')
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
import numpy as np
import soundfile as sf
import imageio_ffmpeg
sys.path.insert(0,str(ROOT/'server'));sys.path.insert(0,os.environ['ADMIN_ASSETS']+'/py')
import helper
import voice_shape_k2 as recipe
assert recipe.RECIPE_TAG=='k2' and recipe.RECIPE['style']=='F2'
assets=Path(os.environ['ADMIN_ASSETS']);tts=helper.load_text_to_speech(str(assets/'onnx'),False);style=helper.load_voice_style([str(assets/'voice_styles/F2.json')]);sr=tts.sample_rate;ff=imageio_ffmpeg.get_ffmpeg_exe()
np.random.seed(20261026+shard)
for lesson in pending:
 ident=lesson['id'];text=lesson['narration_text']
 chunks=[x.strip() for x in re.split(r'(?<=[.!?])\s+|\n\s*\n',text) if x.strip()]
 # Break unusually long sentences only on existing whitespace; never truncate.
 final=[]
 for c in chunks:
  while len(c)>220:
   cut=c.rfind(' ',0,220);assert cut>0;final.append(c[:cut]);c=c[cut:].strip()
  if c:final.append(c)
 chunks=final;assert ''.join(''.join(chunks).split())==''.join(text.split()) and len(chunks)<150
 waves=[];segments=[];cursor=0
 for i,t in enumerate(chunks):
  hard=recipe.is_sentence_end(t);speed=float(np.clip(1.05*recipe.unit_speed_mult(0,hard),.7,1.6));hum=True
  for attempt in range(2):
   w,d=tts._infer([t],['ko'],style,16,speed)
   y=recipe.shape(np.asarray(w,dtype=np.float32).reshape(-1)[:int(float(np.asarray(d).reshape(-1)[0])*sr)],sr,t,hard)
   hum=bool(recipe.hum_tail(y,sr))
   if not hum:break
  assert np.isfinite(y).all() and len(y)>.15*sr
  waves.append(y);duration=len(y)/sr
  segments.append({'index':i,'text':t,'start_s':round(cursor,5),'end_s':round(cursor+duration,5),'speed':speed,'tail_hum':hum,'attempts':attempt+1});cursor+=duration
  if i<len(chunks)-1:waves.append(np.zeros(int(sr*.48),dtype=np.float32));cursor+=int(sr*.48)/sr
 y=np.concatenate(waves);wav=out/(ident+'.wav');mp3=out/(ident+'.mp3');sf.write(wav,y,sr,subtype='PCM_16')
 subprocess.run([ff,'-v','error','-y','-i',str(wav),'-ar','24000','-codec:a','libmp3lame','-b:a','96k',str(mp3)],check=True)
 raw=subprocess.check_output([ff,'-v','error','-i',str(mp3),'-f','f32le','-ac','1','-ar','24000','pipe:1']);a=np.frombuffer(raw,dtype='<f4');n=len(a)//480;rms=np.sqrt(np.mean(a[:n*480].reshape(-1,480)**2,axis=1));quiet=rms<10**(-50/20);run=mx=0
 for q in quiet:run=run+1 if q else 0;mx=max(mx,run)
 metrics={'decoded_duration_s':len(a)/24000,'clipped_samples':int(np.sum(np.abs(a)>=.999)),'peak':float(np.abs(a).max()),'max_silence_s':mx*.02,'tail_hum_chunks':sum(x['tail_hum'] for x in segments)}
 assert 20<len(a)/24000<900 and metrics['clipped_samples']==0 and metrics['max_silence_s']<4 and metrics['tail_hum_chunks']==0,metrics
 meta={k:lesson[k] for k in ('id','number','title','topic_ids','narration_text','narration_sha256','checkpoint_hash')};meta.update(namespace='admin-law/v2/free',access='free',voice=RECIPE,audio_url=BASE+ident+'.mp3',duration_s=len(a)/24000,audio_sha256=sha(mp3.read_bytes()),segments=segments,validation=metrics,listening_verified=False,source_commit=os.environ['GITHUB_SHA'])
 meta_path=out/(ident+'.json');meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2))
 # Explicit GitHub publishing subprocess only; never production worker/R2.
 gh('release','upload',TAG,str(mp3),str(meta_path),'--clobber')
 with tempfile.TemporaryDirectory() as d:
  assert fetch_asset(ident+'.mp3',d) and sha((Path(d)/(ident+'.mp3')).read_bytes())==meta['audio_sha256'],'Published bytes differ'
 wav.unlink();print('Published and verified',ident,metrics,flush=True)
