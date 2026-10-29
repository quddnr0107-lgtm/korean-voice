"""Private local CPU synthesis only. No outbound connections or publishing."""
import os,sys,json,hashlib,time,re,subprocess,socket,concurrent.futures,argparse,traceback
from pathlib import Path
os.environ['ORT_DISABLE_TELEMETRY']='1'  # Official process-lifetime opt-out, before any ORT import.
ROOT=Path(os.environ['LECTURE_WORKDIR'])
BASE=Path(os.environ['LECTURE_MODEL_ROOT'])
RECIPE={'id': 'tongbang-rhythm-f2-v1', 'model_revision': '3cadd1ee6394adea1bd021217a0e650ede09a323', 'style': 'F2', 'steps': 16, 'default_speed': 0.95, 'invitation_question_speed': 0.93, 'long_speed': 0.98, 'definition_speed': 0.92, 'silence_threshold_db': -48, 'head_keep_s': 0.08, 'tail_keep_s': 0.16, 'fade_s': 0.008, 'peak': 0.86, 'pitch_shift': False, 'output': 'independent-mp3-pack-v2', 'max_part_seconds': 60, 'max_part_plain_bytes': 2097152, 'part_boundary': 'next-chunk-start', 'normalization_ruleset': 'lecture-tts-normalize/v1'}
def encoded(x):return json.dumps(x,ensure_ascii=False,indent=2).encode()
def sha(x):return hashlib.sha256(x).hexdigest()
def atomic(p,x):
 p=Path(p);tmp=p.with_suffix(p.suffix+f'.{os.getpid()}.tmp');tmp.write_bytes(encoded(x));tmp.replace(p)
def deny(*a,**k):raise RuntimeError('Network blocked during synthesis')
def init_worker():
 global np,sf,tts,style,sr
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 sys.path.insert(0,str(BASE/'py'))
 import numpy as np
 import soundfile as sf
 import onnxruntime as ort
 ort.disable_telemetry_events()
 import helper
 opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1
 onnx=BASE/'onnx';sessions=helper.load_onnx_all(str(onnx),opts,['CPUExecutionProvider'])
 tts=helper.TextToSpeech(helper.load_cfgs(str(onnx)),helper.load_text_processor(str(onnx)),*sessions)
 style=helper.load_voice_style([str(BASE/'voice_styles/F2.json')]);sr=tts.sample_rate

def child_env():
 return {k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','TMPDIR','SYSTEMROOT')}

def quality_flags(m,text):
 spoken=max(1,len(re.sub(r'\s','',text)));flags=[]
 if not m.get('finite') or m.get('clipped_samples',1):flags.append('nonfinite_or_clipped')
 if m.get('duration_s',0)<.15 or m.get('duration_s',0)/spoken<.022:flags.append('unusually_short')
 if m.get('duration_s',999)>55 or m.get('duration_s',999)/spoken>.8:flags.append('unusually_long')
 if m.get('max_silence_s',999)>2:flags.append('long_internal_silence')
 if m.get('rms',0)<.002:flags.append('low_energy')
 return flags

def policy(text):
 speed=.95;gain=1.
 if len(text)>=45:speed=.98
 if '?' in text or any(x in text for x in ['보세요','볼까요','떠올려']):speed=.93;gain=1.02
 if '것이' in text and any(x in text for x in ['입니다','이고','말합니다']):speed=.92;gain=1.04
 return speed,gain

def metrics(y):
 f=max(1,int(.02*sr));n=len(y)//f
 if not n:return {'invalid':True}
 rms=np.sqrt(np.mean(y[:n*f].reshape(n,f)**2,axis=1));silent=rms<10**(-50/20);run=longest=0
 for b in silent:run=run+1 if b else 0;longest=max(longest,run)
 return {'duration_s':len(y)/sr,'peak':float(np.max(np.abs(y))),'clipped_samples':int(np.sum(np.abs(y)>=.999)),'max_silence_s':longest*.02,'rms':float(np.sqrt(np.mean(y*y))),'finite':bool(np.isfinite(y).all())}

def render_lesson(lesson):
 lid=lesson['id'];start=time.time();chunks=lesson['chunks'];meta_path=ROOT/'metadata'/f'{lid}.json';out=ROOT/'audio'/f'{lid}.mp3'
 waves=[];segments=[];cursor=0.;cache_hits=0;cache_rejected=[];warnings=[]
 for j,c in enumerate(chunks):
  if os.environ.get('LECTURE_SOFT_DEADLINE_AT') and time.time()>=float(os.environ['LECTURE_SOFT_DEADLINE_AT']):raise RuntimeError('soft_deadline')
  text=c['spoken_text'];speed,gain=policy(text);key=sha(encoded({'text':text,'recipe':RECIPE,'speed':speed,'gain':gain}));wav=ROOT/'cache'/f'{key}.wav';cp=wav.with_suffix('.json');cached=False
  if wav.exists() and cp.exists():
   try:
    cm=json.loads(cp.read_text())
    if cm.get('wav_sha256')!=sha(wav.read_bytes()):raise ValueError('cached chunk digest')
    if set(cm)!={'text','key','attempts','metrics','wav_sha256','recipe'} or cm['text']!=text or cm['key']!=key or cm['recipe']!=RECIPE or type(cm['attempts']) is not int or not 1<=cm['attempts']<=3:raise ValueError('cached chunk metadata')
    y,r=sf.read(wav,dtype='float32')
    if r!=sr or quality_flags(metrics(y),text):raise ValueError('cached chunk quality')
    cached=True;cache_hits+=1
   except (ValueError,KeyError,TypeError,OSError):
    wav.unlink(missing_ok=True);cp.unlink(missing_ok=True);cache_rejected.append(j)
  if not cached:
   for attempt in range(3):
    flags=['invalid_audio']
    np.random.seed((int(key[:8],16)+attempt)%2**32);w,d=tts._infer([text],['ko'],style,16,speed)
    y=np.asarray(w,dtype=np.float32).reshape(-1)[:int(float(np.asarray(d).reshape(-1)[0])*sr)]
    if not np.isfinite(y).all() or not len(y):continue
    frame=max(1,int(.01*sr));n=len(y)//frame;rms=np.sqrt(np.mean(y[:n*frame].reshape(n,frame)**2,axis=1));live=np.flatnonzero(rms>10**(-48/20))
    if not len(live):continue
    y=y[max(0,int(live[0]*frame-.08*sr)):min(len(y),int((live[-1]+1)*frame+.16*sr))]
    fade=min(int(.008*sr),len(y)//2);y[:fade]*=np.linspace(0,1,fade);y[-fade:]*=np.linspace(1,0,fade)
    y*=gain
    if np.max(np.abs(y))>.94:y*=.94/np.max(np.abs(y))
    m=metrics(y);flags=quality_flags(m,text)
    if not flags:break
   else:raise ValueError(f'{lid} chunk {j} failed audio checks: {flags if len(y) else "empty"}')
   wtmp=wav.with_name(wav.stem+f'.{os.getpid()}.tmp.wav');sf.write(wtmp,y,sr,subtype='PCM_16');wtmp.replace(wav);cm={'text':text,'key':key,'attempts':attempt+1,'metrics':m,'wav_sha256':sha(wav.read_bytes()),'recipe':RECIPE};atomic(cp,cm)
  m=metrics(y);duration=len(y)/sr
  segments.append({**c,'start_s':round(cursor,6),'end_s':round(cursor+duration,6),'synthesis_speed':speed,'gain':gain,'audio_cache_key':key,'validation':m,'attempts':cm['attempts']});waves.append(y);cursor+=duration
  if j<len(chunks)-1:
   pause=.46 if re.search(r'[.!?。！？][”’"\']?\s*$',text) else .22
   if '?' in text:pause=.52
   if '\n\n' in c.get('text',''):pause=.6
   silence=np.zeros(round(sr*pause),np.float32);waves.append(silence);cursor+=len(silence)/sr
  atomic(ROOT/'progress'/f'{lid}.json',{'id':lid,'status':'rendering','done_chunks':j+1,'total_chunks':len(chunks),'audio_s':cursor,'wall_s':time.time()-start,'cache_hits':cache_hits,'updated_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
 y=np.concatenate(waves);y*=.86/float(np.max(np.abs(y)));source_metrics=metrics(y);del waves
 total_samples=len(y);boundaries=[0];starts=[round(x['start_s']*sr) for x in segments[1:]]
 while boundaries[-1]<total_samples:
  begin=boundaries[-1];limit=min(begin+60*sr,total_samples)
  if limit==total_samples:end=limit
  else:
   candidates=[x for x in starts if begin<x<=limit]
   if not candidates:raise ValueError('no safe sentence boundary for audio part')
   end=max(candidates)
  boundaries.append(end)
 parts=[]
 for part_index,(begin,end) in enumerate(zip(boundaries,boundaries[1:])):
  temp=ROOT/'audio'/f'{lid}.part-{part_index:04d}.wav';sf.write(temp,y[begin:end],sr,subtype='PCM_16')
  target=ROOT/'audio'/f'{lid}.part-{part_index:04d}.mp3';tmp=target.with_suffix('.tmp.mp3')
  subprocess.run(['ffmpeg','-v','error','-y','-i',str(temp),'-map_metadata','-1','-c:a','libmp3lame','-b:a','128k','-write_xing','1',str(tmp)],env=child_env(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True);temp.unlink()
  decoded=np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(tmp),'-f','f32le','-ac','1','-ar',str(sr),'pipe:1'],env=child_env(),stderr=subprocess.DEVNULL),dtype='<f4');pm=metrics(decoded)
  gapless_corrected=False
  if 1<len(decoded)-(end-begin)<=46:
   from mp3_gapless import repair
   try:corrected=repair(tmp.read_bytes(),end-begin,len(decoded))
   except ValueError:pass  # Unknown cases still fail the unchanged quality gate below.
   else:
    tmp.write_bytes(corrected);gapless_corrected=True
    decoded=np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(tmp),'-f','f32le','-ac','1','-ar',str(sr),'pipe:1'],env=child_env(),stderr=subprocess.DEVNULL),dtype='<f4');pm=metrics(decoded)
  if not pm['finite'] or pm['clipped_samples'] or pm['max_silence_s']>=3 or abs(len(decoded)-(end-begin))>1:raise ValueError(f"part_decode_validation part={part_index} finite={int(pm['finite'])} clipped={pm['clipped_samples']} silence_ms={round(pm['max_silence_s']*1000)} sample_delta={len(decoded)-(end-begin)}")
  if not 0<tmp.stat().st_size<=2*1024*1024 or end-begin>60*sr:raise ValueError('part bounds')
  tmp.replace(target)
  parts.append({'index':part_index,'start_sample':begin,'end_sample':end,'start_s':begin/sr,'end_s':end/sr,'sample_rate':sr,'plain_bytes':target.stat().st_size,'audio_sha256':sha(target.read_bytes()),'file_name':target.name,'gapless_metadata_corrected':gapless_corrected,'validation':pm})
 del y
 validation={'duration_s':sum(p['validation']['duration_s'] for p in parts),'peak':max(p['validation']['peak'] for p in parts),'clipped_samples':sum(p['validation']['clipped_samples'] for p in parts),'max_silence_s':max(source_metrics['max_silence_s'],max(p['validation']['max_silence_s'] for p in parts)),'rms':float(np.sqrt(sum(p['validation']['rms']**2*(p['end_sample']-p['start_sample']) for p in parts)/total_samples)),'finite':all(p['validation']['finite'] for p in parts)}
 assert abs(validation['duration_s']-total_samples/sr)<=len(parts)/sr and validation['max_silence_s']<3
 info={'id':lid,'title':lesson['title'],'access':'premium-only','ai_generated':True,'recipe':RECIPE,'input_sha256':sha(encoded(chunks)),'validation':validation,'audio_parts':parts,'sample_rate':sr,'total_samples':total_samples,'segments':segments,'source_text_coverage':'text-input coverage only, not ASR verification','pcm_part_coverage':'contiguous exact sample partition; independent MP3 decode delta <=1 sample each','human_listening_verified':False,'asr_verified':False,'network_during_synthesis':'isolated Linux network namespace; ORT telemetry disabled before import','wall_s':time.time()-start,'cache_hits':cache_hits,'cache_rejected_indices':cache_rejected,'warnings':warnings}
 atomic(meta_path,info);result={'id':lid,'status':'completed','wall_s':time.time()-start,'duration_s':validation['duration_s'],'chunks':len(chunks),'parts':len(parts)};atomic(ROOT/'progress'/f'{lid}.json',result);return result
