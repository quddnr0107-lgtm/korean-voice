"""자동 판정기 — 사람 귀 없이 음성을 채점한다. 이게 있어야 혼자 반복 실험이 된다.

세 축으로 잰다:
  ① 알아듣는가   : Whisper 로 되받아쓰기 → 원문과의 글자오류율(CER). 낮을수록 또렷.
  ② 사람 같은가   : 실측 한국인 운율 분포와의 거리 (쉼 분포 · 음절속도 · 하강폭).
  ③ 소리가 깨졌나 : 스펙트럼 이상 · 클리핑 · 무음 비율.
CER 이 튀면 C(int8) 같은 파탄을 사람 없이도 즉시 잡아낸다.
"""
import sys, json, re
import numpy as np, librosa, soundfile as sf

# 실측 한국인 기준값 (Zeroth-Korean, CC BY 4.0)
REF = {'pause_med': 80.0, 'pause_cv': 1.13, 'rate_med': 6.58, 'decl': -10.9}

def cer(ref, hyp):
    r = re.sub(r'[^가-힣0-9a-zA-Z]', '', ref); h = re.sub(r'[^가-힣0-9a-zA-Z]', '', hyp)
    if not r: return 1.0
    d = np.arange(len(h)+1)
    for i, rc in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hc in enumerate(h, 1):
            cur = d[j]
            d[j] = min(d[j]+1, d[j-1]+1, prev + (rc != hc))
            prev = cur
    return d[len(h)] / len(r)

def prosody(y, sr, n_syl):
    iv = librosa.effects.split(y, top_db=25, frame_length=1024, hop_length=256)
    gaps = [(iv[k+1][0]-iv[k][1])/sr*1000 for k in range(len(iv)-1)]
    gaps = [g for g in gaps if 40 <= g <= 2000]
    sp = sum(b-a for a, b in iv)/sr if len(iv) else 0
    f0, _, _ = librosa.pyin(y, fmin=60, fmax=400, sr=sr, frame_length=1024)
    v = f0[~np.isnan(f0)]
    dec = None
    if len(v) > 20:
        h, t = v[:len(v)//3], v[-len(v)//3:]
        dec = (np.median(t)/np.median(h)-1)*100
    return {
        'pause_med': float(np.median(gaps)) if gaps else None,
        'pause_cv': float(np.std(gaps)/np.mean(gaps)) if len(gaps) > 1 and np.mean(gaps) > 0 else None,
        'rate': n_syl/sp if sp > 0 else None,
        'decl': float(dec) if dec is not None else None,
        'f0': float(np.median(v)) if len(v) else None,
        'silence_ratio': 1 - sp/(len(y)/sr),
        'clip_ratio': float((np.abs(y) > 0.999).mean()),
    }

def score(path, text, asr):
    y, sr = librosa.load(path, sr=16000)
    hyp = asr(path)
    n_syl = sum(1 for c in text if '가' <= c <= '힣')
    p = prosody(y, sr, n_syl)
    c = cer(text, hyp)
    # 운율 거리: 각 항목을 기준값 대비 상대오차로, 낮을수록 사람에 가깝다
    parts = []
    for k, ref in REF.items():
        v = p.get('rate' if k == 'rate_med' else k.replace('_med', '_med'), None)
        if k == 'rate_med': v = p['rate']
        elif k == 'pause_med': v = p['pause_med']
        elif k == 'pause_cv': v = p['pause_cv']
        elif k == 'decl': v = p['decl']
        if v is not None and ref: parts.append(abs(v-ref)/abs(ref))
    return {'CER': round(c, 4), '운율거리': round(float(np.mean(parts)), 3) if parts else None,
            **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items()}, 'ASR': hyp[:60]}

if __name__ == '__main__':
    from transformers import pipeline
    name = sys.argv[1] if len(sys.argv) > 1 else 'openai/whisper-small'
    pipe = pipeline('automatic-speech-recognition', model=name, device=-1,
                    generate_kwargs={'language': 'korean', 'task': 'transcribe'})
    def asr(p):
        import librosa as _l
        y, _ = _l.load(p, sr=16000)
        return pipe({'raw': y, 'sampling_rate': 16000}, chunk_length_s=30)['text'].strip()
    jobs = json.load(open('/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a/judge_jobs.json', encoding='utf-8'))
    for name, path, text in jobs:
        try: print('JUDGE ' + name + ' ' + json.dumps(score(path, text, asr), ensure_ascii=False), flush=True)
        except Exception as e: print(f'JUDGE {name} ERROR {e}', flush=True)
