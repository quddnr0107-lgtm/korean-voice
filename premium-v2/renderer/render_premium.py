"""LOCAL DRAFT ONLY. No publishing/network. Keys supplied only by approved Python step."""
import argparse,base64,contextlib,json,math,os,re,socket,subprocess,sys,tempfile
from pathlib import Path
from private_pipeline import *
KEY_NAMES=('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')

def consume_keys(environ=os.environ):
 values=[environ.pop(name,None) for name in KEY_NAMES]
 if not all(values):raise ValueError('missing key')
 keys=[base64.b64decode(value,validate=True) for value in values]
 if any(len(key)!=32 for key in keys) or keys[0]==keys[1]:raise ValueError('invalid keys')
 return keys

def child_env():
 # Explicit allowlist; keys/tokens/cookies/CI credentials never inherited by ffmpeg.
 return {k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','SYSTEMROOT','TEMP','TMP','TMPDIR')}

@contextlib.contextmanager
def suppress_native_output():
 saved=[os.dup(1),os.dup(2)];dev=os.open(os.devnull,os.O_WRONLY)
 try:
  sys.stdout.flush();sys.stderr.flush();os.dup2(dev,1);os.dup2(dev,2);yield
 finally:
  sys.stdout.flush();sys.stderr.flush();os.dup2(saved[0],1);os.dup2(saved[1],2)
  for fd in saved+[dev]:os.close(fd)

def validate_audio(audio,ff):
 import numpy as np
 # No plaintext filenames/command strings or subprocess error text in logs.
 p=subprocess.run([ff,'-v','error','-protocol_whitelist','pipe','-f','mp3','-i','pipe:0','-f','f32le','-ac','1','-ar','24000','pipe:1'],input=audio,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=child_env(),check=True,timeout=120)
 a=np.frombuffer(p.stdout,dtype='<f4');n=len(a)//480
 if not n or not np.isfinite(a).all():raise ValueError('invalid decoded audio')
 rms=np.sqrt(np.mean(a[:n*480].reshape(-1,480)**2,axis=1));run=mx=0
 for q in rms<10**(-50/20):run=run+1 if q else 0;mx=max(mx,run)
 m={'decoded_duration_s':len(a)/24000,'clipped_samples':int(np.sum(np.abs(a)>=.999)),'peak':float(np.abs(a).max()),'max_silence_s':mx*.02}
 if not 20<m['decoded_duration_s']<900 or m['clipped_samples'] or m['max_silence_s']>=4:raise ValueError('audio quality check')
 return m

def validate_recipe_file(server_dir):
 if sha((server_dir/'voice_shape_k2.py').read_bytes())!=RECIPE['recipe_code_sha256']:raise ValueError('recipe file hash')

def validate_checkpoint_metrics(meta,metrics):
 for key in ('decoded_duration_s','peak','clipped_samples','max_silence_s'):
  old=meta['validation'][key];new=metrics[key]
  if not math.isfinite(old) or not math.isfinite(new) or not math.isclose(old,new,rel_tol=1e-7,abs_tol=1e-8):raise ValueError('checkpoint decode evidence mismatch')

class Backend:
 def __init__(self,assets,server_dir,seed):
  def deny(*a,**kw):raise RuntimeError('network blocked')
  socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
  import numpy as np
  import soundfile as sf
  import imageio_ffmpeg
  sys.path.insert(0,str(server_dir));sys.path.insert(0,str(assets/'py'))
  validate_recipe_file(server_dir)
  import helper
  import voice_shape_k2 as recipe
  if recipe.RECIPE_TAG!='k2' or recipe.RECIPE['style']!='F2':raise ValueError('recipe mismatch')
  self.np=np;self.sf=sf;self.recipe=recipe;self.ff=imageio_ffmpeg.get_ffmpeg_exe()
  self.tts=helper.load_text_to_speech(str(assets/'onnx'),False)
  self.style=helper.load_voice_style([str(assets/'voice_styles/F2.json')]);self.sr=self.tts.sample_rate;np.random.seed(seed)
 def render(self,text,temp_dir):
  np=self.np;sr=self.sr;recipe=self.recipe
  parts=[x.strip() for x in re.split(r'(?<=[.!?])\s+|\n\s*\n',text) if x.strip()];chunks=[]
  for c in parts:
   while len(c)>220:
    cut=c.rfind(' ',0,220)
    if cut<=0:raise ValueError('chunk boundary')
    chunks.append(c[:cut]);c=c[cut:].strip()
   if c:chunks.append(c)
  if not 0<len(chunks)<150 or ''.join(''.join(chunks).split())!=''.join(text.split()):raise ValueError('chunk preservation')
  waves=[];segments=[];cursor=0
  for i,t in enumerate(chunks):
   hard=recipe.is_sentence_end(t);speed=float(np.clip(1.05*recipe.unit_speed_mult(0,hard),.7,1.6));hum=True
   for attempt in range(2):
    w,d=self.tts._infer([t],['ko'],self.style,16,speed)
    y=recipe.shape(np.asarray(w,dtype=np.float32).reshape(-1)[:int(float(np.asarray(d).reshape(-1)[0])*sr)],sr,t,hard)
    hum=bool(recipe.hum_tail(y,sr))
    if not hum:break
   if hum or not np.isfinite(y).all() or len(y)<=.15*sr:raise ValueError('chunk quality')
   waves.append(y);duration=len(y)/sr;segments.append({'index':i,'text':t,'start_s':round(cursor,5),'end_s':round(cursor+duration,5),'speed':speed,'tail_hum':hum,'attempts':attempt+1});cursor+=duration
   if i<len(chunks)-1:waves.append(np.zeros(int(sr*.48),dtype=np.float32));cursor+=int(sr*.48)/sr
  wav=Path(temp_dir)/'audio.wav';mp3=Path(temp_dir)/'audio.mp3'
  try:
   self.sf.write(wav,np.concatenate(waves),sr,subtype='PCM_16')
   subprocess.run([self.ff,'-v','error','-y','-protocol_whitelist','file','-f','wav','-i',str(wav),'-ar','24000','-codec:a','libmp3lame','-b:a','96k',str(mp3)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=child_env(),check=True,timeout=120)
   audio=mp3.read_bytes();metrics=validate_audio(audio,self.ff);metrics['tail_hum_chunks']=0;return audio,segments,metrics
  finally:
   wav.unlink(missing_ok=True);mp3.unlink(missing_ok=True)

def load_input(directory,ident,key):
 m=json.loads((directory/(ident+'.source.json')).read_bytes());b=(directory/(ident+'.source.bin')).read_bytes()
 if m['lesson_id']!=ident or m['kind']!='source' or m['key_id']!='admin-law-premium-source-v1':raise ValueError('source envelope identity')
 data=json.loads(open_range(key,m,b)[0])
 if validate_input(data)!=ident:raise ValueError('source payload identity')
 return data

def read_checkpoint(directory,ident):
 descriptor=json.loads((directory/(ident+'.descriptor.json')).read_bytes())
 if set(descriptor)!={'id','audio','content'} or descriptor['id']!=ident:raise ValueError('checkpoint descriptor')
 manifests={k:descriptor[k] for k in ('audio','content')}
 blobs={k:(directory/(ident+'.'+k+'.bin')).read_bytes() for k in ('audio','content')}
 return manifests,blobs

def save_outputs(out,ident,objects):
 # Only ciphertext + approved crypto metadata reach this directory.
 out.mkdir(parents=True,exist_ok=True,mode=0o700)
 descriptor={'id':ident}
 for k in ('audio','content'):
  m,b=objects[k];descriptor[k]=m
  temp=out/(ident+'.'+k+'.bin.part');temp.write_bytes(b);os.replace(temp,out/(ident+'.'+k+'.bin'))
 tmp=out/(ident+'.descriptor.json.part');tmp.write_bytes(encoded(descriptor));os.replace(tmp,out/(ident+'.descriptor.json'))

def run_batch(args,source_key,media_key,backend_factory=Backend):
 ids=args.lessons.split(',')
 if not 1<=len(ids)<=8 or len(set(ids))!=len(ids) or any(canonical_id(x)!=x for x in ids):raise ValueError('bounded premium lesson allowlist')
 if args.output_dir.exists() and any(args.output_dir.iterdir()):raise ValueError('output directory must be empty')
 backend=None;statuses=[]
 for ident in ids:
  try:
   with suppress_native_output():
    data=load_input(args.input_dir,ident,source_key)
    # Reuse only authenticated checkpoint with source+recipe hash match and fresh decode QC.
    reused=False
    if args.checkpoint_dir and (args.checkpoint_dir/(ident+'.descriptor.json')).exists():
     manifests,blobs=read_checkpoint(args.checkpoint_dir,ident)
     audio,meta=authenticated_checkpoint(data,manifests,blobs,media_key)
     import imageio_ffmpeg
     metrics=validate_audio(audio,imageio_ffmpeg.get_ffmpeg_exe())
     validate_checkpoint_metrics(meta,metrics)
     if meta['validation']['tail_hum_chunks']!=0:raise ValueError('checkpoint hum')
     save_outputs(args.output_dir,ident,{k:(manifests[k],blobs[k]) for k in manifests});reused=True
    if not reused:
     if backend is None:backend=backend_factory(args.assets,args.server_dir,20261026+args.batch_index)
     # plaintext intermediate files never share artifact directory; context always removes them.
     with tempfile.TemporaryDirectory(prefix='admin-law-premium-',dir=args.temp_root) as temp:
      os.chmod(temp,0o700);audio,segments,metrics=backend.render(data['lesson']['narration_text'],temp)
      meta=private_metadata(data,audio,segments,metrics);objects=seal_outputs(data,audio,meta,media_key);save_outputs(args.output_dir,ident,objects)
    statuses.append({'id':ident,'status':'reused' if reused else 'encrypted'})
  except Exception:
   # Remove every assigned output, including partially committed blobs/descriptors.
   for suffix in ('audio.bin','content.bin','descriptor.json'):
    for tail in ('','.part'):
     (args.output_dir/(ident+'.'+suffix+tail)).unlink(missing_ok=True)
   # No traceback/exception message/input/source/model text ever emitted.
   statuses.append({'id':ident,'status':'failed','stage':'protected-processing'})
 return statuses

def main():
 os.umask(0o077)
 # Consume and remove key environment values before loading models or spawning children.
 try:source_key,media_key=consume_keys()
 except Exception:print('Premium renderer unavailable: key gate');return 2
 ap=argparse.ArgumentParser();ap.add_argument('--lessons',required=True);ap.add_argument('--batch-index',type=int,required=True)
 for n in ('input-dir','output-dir','assets','server-dir','temp-root'):ap.add_argument('--'+n,type=Path,required=True)
 ap.add_argument('--checkpoint-dir',type=Path);args=ap.parse_args()
 try:result=run_batch(args,source_key,media_key)
 except Exception:print('Premium renderer failed: configuration');return 2
 print(json.dumps({'results':result},separators=(',',':')))
 return int(any(x['status']=='failed' for x in result))
if __name__=='__main__':sys.exit(main())
