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

def validate_audio(audio,ff,track="easy"):
 import numpy as np
 # No plaintext filenames/command strings or subprocess error text in logs.
 p=subprocess.run([ff,'-v','error','-protocol_whitelist','pipe','-f','mp3','-i','pipe:0','-f','f32le','-ac','1','-ar','24000','pipe:1'],input=audio,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=child_env(),check=True,timeout=120)
 a=np.frombuffer(p.stdout,dtype='<f4');n=len(a)//480
 if not n or not np.isfinite(a).all():raise ValueError('invalid decoded audio')
 rms=np.sqrt(np.mean(a[:n*480].reshape(-1,480)**2,axis=1));run=mx=0
 for q in rms<10**(-50/20):run=run+1 if q else 0;mx=max(mx,run)
 m={'decoded_duration_s':len(a)/24000,'clipped_samples':int(np.sum(np.abs(a)>=.999)),'peak':float(np.abs(a).max()),'max_silence_s':mx*.02}
 from audio_policy import duration_ok
 duration_ok(track,m['decoded_duration_s'])
 if m['clipped_samples'] or m['max_silence_s']>=4:raise ValueError('audio quality check')
 return m

def validate_recipe_file(server_dir):
 if sha((server_dir/'voice_shape_k2.py').read_bytes())!=RECIPE['recipe_code_sha256']:raise ValueError('recipe file hash')

def validate_checkpoint_metrics(meta,metrics):
 for key in ('decoded_duration_s','peak','clipped_samples','max_silence_s'):
  old=meta['validation'][key];new=metrics[key]
  if not math.isfinite(old) or not math.isfinite(new) or not math.isclose(old,new,rel_tol=1e-7,abs_tol=1e-8):raise ValueError('checkpoint decode evidence mismatch')

class Backend:
 def __init__(self,assets,server_dir,seed,track="easy"):
  self.track=track
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
 def render(self,text,temp_dir,planned_chunks=None,inference_data=None):
  np=self.np;sr=self.sr;recipe=self.recipe;self.stage='chunk-planning'
  from chunk_plan import chunks
  if planned_chunks is None:chunks=chunks(text)
  else:
   from chunk_plan import verify
   if type(planned_chunks)!=list:raise ValueError('planned chunks type')
   verify(text,planned_chunks);chunks=list(planned_chunks)
  budget=None
  if inference_data is not None:
   from inference_policy import Budget
   budget=Budget(inference_data)
  waves=[];segments=[];cursor=0
  for i,t in enumerate(chunks):
   self.stage='chunk-infer';self.chunk_index=i
   hard=recipe.is_sentence_end(t);speed=float(np.clip(1.05*recipe.unit_speed_mult(0,hard),.7,1.6));hum=True
   if budget is None:
    for attempt in range(2):
     w,d=self.tts._infer([t],['ko'],self.style,16,speed)
     y=recipe.shape(np.asarray(w,dtype=np.float32).reshape(-1)[:int(float(np.asarray(d).reshape(-1)[0])*sr)],sr,t,hard)
     hum=bool(recipe.hum_tail(y,sr))
     if not hum:break
   else:
    def infer_once():
     w,d=self.tts._infer([t],['ko'],self.style,16,speed)
     value=recipe.shape(np.asarray(w,dtype=np.float32).reshape(-1)[:int(float(np.asarray(d).reshape(-1)[0])*sr)],sr,t,hard)
     return value,bool(recipe.hum_tail(value,sr))
    y,hum,used,accepted_seed=budget.infer(i,t,infer_once,np.random.seed);attempt=used-1
   self.stage='chunk-quality';self.chunk_diagnostic={'chunk_index':i,'finite':bool(np.isfinite(y).all()),'samples':len(y),'sample_rate':sr,'tail_hum':hum}
   if hum or not np.isfinite(y).all() or len(y)<=.15*sr:raise ValueError('chunk quality')
   waves.append(y);duration=len(y)/sr;segments.append({'index':i,'text':t,'start_s':round(cursor,5),'end_s':round(cursor+duration,5),'speed':speed,'tail_hum':hum,'attempts':attempt+1});cursor+=duration
   if budget is not None:segments[-1]['accepted_seed']=accepted_seed
   if i<len(chunks)-1:waves.append(np.zeros(int(sr*.48),dtype=np.float32));cursor+=int(sr*.48)/sr
  wav=Path(temp_dir)/'audio.wav';mp3=Path(temp_dir)/'audio.mp3'
  try:
   self.sf.write(wav,np.concatenate(waves),sr,subtype='PCM_16')
   subprocess.run([self.ff,'-v','error','-y','-protocol_whitelist','file','-f','wav','-i',str(wav),'-ar','24000','-codec:a','libmp3lame','-b:a','96k',str(mp3)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=child_env(),check=True,timeout=120)
   self.stage='decode-quality';audio=mp3.read_bytes();metrics=validate_audio(audio,self.ff,self.track);metrics['tail_hum_chunks']=0;return audio,segments,metrics
  finally:
   wav.unlink(missing_ok=True);mp3.unlink(missing_ok=True)

