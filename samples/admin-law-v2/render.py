"""Exactly two approved public samples. No production server/cache/network calls."""
import hashlib, json, os, re, socket, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
EXPECTED = {
 'S01-law-priority-reservation': '6810db502dadcb8b84905bb9b6bb00815ce3957385999c94cfea183446fbb1be',
 'S02-number-article-case': '78209041087cca7dec7ce893c4637c69964bb17ebd16a8cb48d3e3467fea60b4',
}
texts={}
for name, sha in EXPECTED.items():
 data=(HERE/(name+'.narration-candidate.txt')).read_bytes()
 assert hashlib.sha256(data).hexdigest()==sha, 'Unapproved sample content'
 texts[name]=data.decode('utf-8')
assert len(texts)==2 and sum(map(len,texts.values()))<1000
# No sockets during synthesis, including accidental production-service access.
def deny(*a,**kw): raise RuntimeError('Network disabled for isolated sample synthesis')
socket.socket.connect=deny; socket.socket.connect_ex=deny; socket.create_connection=deny
import numpy as np
import soundfile as sf
import imageio_ffmpeg
sys.path.insert(0,str(ROOT/'server'))
sys.path.insert(0,os.environ['ADMIN_SAMPLE_ASSETS']+'/py')
import voice_shape_k2 as recipe
import helper
assert recipe.RECIPE_TAG=='k2' and recipe.RECIPE['style']=='F2'
assets=Path(os.environ['ADMIN_SAMPLE_ASSETS'])
out=Path(os.environ['ADMIN_SAMPLE_OUT']); out.mkdir(parents=True,exist_ok=True)
tts=helper.load_text_to_speech(str(assets/'onnx'),False)
style=helper.load_voice_style([str(assets/'voice_styles/F2.json')])
sr=tts.sample_rate; ff=imageio_ffmpeg.get_ffmpeg_exe()
report={'recipe':'k2','voice':'female','style':'F2','steps':16,'base_speed':1.05,'source_commit':os.environ.get('GITHUB_SHA'),'network_during_synthesis':'blocked','ai_generated':True,'listening_pronunciation_check':'not_performed','samples':[]}
np.random.seed(20261026)
for name,text in texts.items():
 # Preserve every character other than whitespace; sentence punctuation/quotes retained.
 chunks=[x.strip() for x in re.split(r'(?<=[.!?])\s+|\n\s*\n',text) if x.strip()]
 assert ''.join(''.join(chunks).split())==''.join(text.split())
 assert len(chunks)<=30 and all(len(x)<=300 for x in chunks)
 waves=[]; detail=[]
 for i,t in enumerate(chunks):
  hard=recipe.is_sentence_end(t)
  speed=float(np.clip(1.05*recipe.unit_speed_mult(0,hard),.7,1.6))
  hum=True
  for attempt in range(2):
   w,d=tts._infer([t],['ko'],style,16,speed)
   y=recipe.shape(np.asarray(w,dtype=np.float32).reshape(-1)[:int(float(np.asarray(d).reshape(-1)[0])*sr)],sr,t,hard)
   hum=bool(recipe.hum_tail(y,sr))
   if not hum: break
  assert len(y)>sr*.15 and np.isfinite(y).all()
  waves.append(y)
  if i<len(chunks)-1: waves.append(np.zeros(int(sr*.48),dtype=np.float32))
  detail.append({'index':i,'text':t,'speed':speed,'duration_s':len(y)/sr,'tail_hum':hum,'attempts':attempt+1})
  print(name,i+1,len(chunks),flush=True)
 y=np.concatenate(waves); wav=out/(name+'.wav'); mp3=out/(name+'.mp3')
 sf.write(wav,y,sr,subtype='PCM_16')
 subprocess.run([ff,'-v','error','-y','-i',str(wav),'-ar','24000','-codec:a','libmp3lame','-b:a','96k',str(mp3)],check=True)
 frame=int(sr*.02); rms=np.sqrt(np.mean(y[:len(y)//frame*frame].reshape(-1,frame)**2,axis=1)+1e-12)
 quiet=rms<10**(-50/20); maxrun=run=0
 for q in quiet:
  run=run+1 if q else 0;maxrun=max(maxrun,run)
 metrics={'id':name,'duration_s':len(y)/sr,'sample_rate':sr,'peak':float(np.max(np.abs(y))),'clipped_samples':int(np.sum(np.abs(y)>=.999)),'silence_fraction_below_minus50db':float(np.mean(quiet)),'max_silence_s':maxrun*.02,'chunks':detail,'mp3_sha256':hashlib.sha256(mp3.read_bytes()).hexdigest()}
 assert 10<metrics['duration_s']<180 and metrics['clipped_samples']==0 and metrics['max_silence_s']<4
 report['samples'].append(metrics)
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
(out/'README.txt').write_text('AI-generated administrative-law voice samples. Supertonic 3 / k2 F2, 16 steps. Not a human recording.\nTwo non-contiguous text excerpts, not complete lessons. Acoustic metrics are automated; Korean pronunciation still requires listening.\nNo production cache or service used. Model: BigScience OpenRAIL-M; helper: MIT; k2: repository proprietary terms. See included licenses and credits.\n')
print('Completed exactly two samples',flush=True)
