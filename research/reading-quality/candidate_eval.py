"""Independent ASR candidate comparison; signal features are diagnostics, not MOS.
Input: JSON rows [{name, wav, expected, recognized}], UTF-8.
Usage: python candidate_eval.py input.json result.json
Requires numpy, scipy, soundfile, librosa (existing voice environment).
"""
import importlib.util, json, re, sys
from pathlib import Path
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

spec=importlib.util.spec_from_file_location('reading_quality',Path(__file__).with_name('quality.py'))
q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)

def normalized(text):
    text=q.prepare(text)['spoken']
    text=re.sub(r'[0-9]{1,4}',lambda m:q.number(m[0]),text)
    return ''.join(c for c in text if c.isalnum())

def distance(a,b):
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        row=[i]
        for j,y in enumerate(b,1):
            row.append(min(row[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=row
    return previous[-1]

def content_metrics(expected,recognized,critical=()):
    a,b=normalized(expected),normalized(recognized)
    errors=distance(a,b)
    return dict(character_errors=errors,reference_characters=len(a),
                cer=errors/max(1,len(a)),
                missing_critical=[s for s in critical if normalized(s) not in b],
                status='review' if errors else 'transcript_match_listen_required')

def signal_metrics(path):
    import librosa
    audio,sr=sf.read(path)
    if audio.ndim==2:audio=audio.mean(axis=1)
    if not len(audio) or not np.isfinite(audio).all():raise ValueError('invalid waveform')
    frame=max(1,round(sr*.02));pad=(-len(audio))%frame
    rms=np.sqrt(np.mean(np.pad(audio,(0,pad)).reshape(-1,frame)**2,axis=1))
    # Simple energy proxy, not a speech/non-speech recognizer.
    silent=rms < max(float(rms.max())*.056,1e-6)
    changes=np.diff(np.r_[False,silent,False].astype(int))
    starts=np.flatnonzero(changes==1);ends=np.flatnonzero(changes==-1)
    runs=(ends-starts)*.02;pauses=runs[runs>=.15]
    a=resample_poly(audio,8000,sr)
    f0=librosa.yin(a,fmin=65,fmax=500,sr=8000,frame_length=512,hop_length=160)
    energy=librosa.feature.rms(y=a,frame_length=512,hop_length=160)[0]
    f0=f0[energy > max(float(energy.max())*.1,1e-6)]
    return dict(seconds=len(audio)/sr,peak=float(np.max(np.abs(audio))),
                clipped_fraction=float(np.mean(np.abs(audio)>=.999)),
                energy_silence_fraction=float(silent.mean()),
                pauses_seconds=pauses.tolist(),longest_energy_silence_seconds=float(runs.max(initial=0)),
                f0_median_hz=float(np.median(f0)) if len(f0) else None,
                f0_iqr_hz=float(np.percentile(f0,75)-np.percentile(f0,25)) if len(f0) else None,
                note='F0/energy are approximate diagnostics; not naturalness, emotion, or speaker-identity scores')

def evaluate(rows,critical=()):
    scored=[]
    for row in rows:
        content=content_metrics(row['expected'],row['recognized'],critical)
        signal=signal_metrics(row['wav'])
        technical=[]
        if signal['clipped_fraction']>.001:technical.append('clipping_review')
        if signal['longest_energy_silence_seconds']>3:technical.append('long_silence_review')
        scored.append(dict(**row,content=content,signal=signal,technical_review=technical))
    # Never select by an invented naturalness target or by artificially broad pitch.
    candidates=[r for r in scored if r.get('eligible',True)]
    if not candidates:raise ValueError('no selectable candidates')
    selected=min(candidates,key=lambda r:(len(r['technical_review']),len(r['content']['missing_critical']),r['content']['cer']))
    return dict(version='candidate-eval/1',rows=scored,selected=selected['name'],
                criterion='technical flags, critical omissions, independent-ASR CER; lexicographic minimum',
                release_status='human_listening_required',model_training=False)

if __name__=='__main__':
    rows=json.loads(Path(sys.argv[1]).read_text())
    result=evaluate(rows)
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
