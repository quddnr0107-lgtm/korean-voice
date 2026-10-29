"""Keyless synthetic MP3 preflight. Fails before any private synthesis starts."""
import json,math,os,shutil,struct,subprocess,tempfile,wave
from pathlib import Path

def verify_encoder():
    exe=shutil.which('ffmpeg')
    if not exe:raise RuntimeError('ENCODER_NOT_FOUND')
    sr=44100;n=sr+151
    with tempfile.TemporaryDirectory(prefix='encoder-preflight-') as tmp:
        wav=Path(tmp)/'synthetic.wav';mp3=Path(tmp)/'synthetic.mp3'
        with wave.open(str(wav),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(sr)
            f.writeframes(b''.join(struct.pack('<h',int(9000*math.sin(2*math.pi*440*i/sr))) for i in range(n)))
        env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','TMPDIR','SYSTEMROOT')}
        subprocess.run([exe,'-v','error','-y','-i',str(wav),'-map_metadata','-1','-c:a','libmp3lame','-b:a','128k','-write_xing','1',str(mp3)],env=env,check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        raw=subprocess.check_output([exe,'-v','error','-i',str(mp3),'-f','f32le','-ac','1','-ar',str(sr),'pipe:1'],env=env,stderr=subprocess.DEVNULL,timeout=30)
        if len(raw)%4 or abs(len(raw)//4-n)>1:raise RuntimeError('ENCODER_SAMPLE_CONTRACT')
        samples=[x[0] for x in struct.iter_unpack('<f',raw)]
        if not all(math.isfinite(x) and abs(x)<.999 for x in samples):raise RuntimeError('ENCODER_AUDIO_CONTRACT')
    return {'status':'encoder_verified','codec':'libmp3lame','sample_delta':len(samples)-n}
if __name__=='__main__':
    try:print(json.dumps(verify_encoder()))
    except Exception as exc:
        reason=str(exc) if str(exc) in ('ENCODER_NOT_FOUND','ENCODER_SAMPLE_CONTRACT','ENCODER_AUDIO_CONTRACT') else 'ENCODER_EXECUTION_FAILED'
        print(json.dumps({'status':'failed','stage':'keyless-encoder-preflight','reason':reason}));raise SystemExit(1)
