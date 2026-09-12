"""자동 판정기 — 합성 음성을 같은 기준으로 반복 채점한다.

네 축:
  ① 표기가 같은가 : ASR 되받아쓰기 → 목표 발음과 literal CER
  ② 의미상 같은가 : 목표와 ASR을 ko-voice로 정규화한 뒤 canonical CER
  ③ 사람 같은가 : 실측 한국인 운율 분포와의 거리
  ④ 깨졌는가 : 클리핑·무음 비율

군사 약어에서는 Whisper가 실제 `케이투` 발화를 `K2`로 적을 수 있다. 이 경우 literal CER는
높지만 발화 자체는 맞다. 따라서 quality gate는 두 CER를 함께 보고, 의미 정확도는 canonical
CER를 우선한다.

사용법:
  python research/judge.py jobs.json
  python research/judge.py jobs.json --asr-model openai/whisper-small

jobs.json은 다음 둘 중 하나를 받는다.
  [["job-name", "/tmp/a.wav", "목표 발음"], ...]
  [{"name":"job-name", "path":"/tmp/a.wav", "target":"목표 발음"}, ...]

특정 에이전트/세션의 /tmp 경로를 코드에 넣지 않는다.
"""
import argparse
import json
import pathlib
import re
import subprocess

# 실측 한국인 기준값 (Zeroth-Korean, CC BY 4.0)
REF = {'pause_med': 80.0, 'pause_cv': 1.13, 'rate_med': 6.58, 'decl': -10.9}
ROOT = pathlib.Path(__file__).resolve().parents[1]
KO_VOICE_JS = ROOT / 'public' / 'ko-voice.js'


def cer(ref, hyp):
    """공백·문장부호를 제외한 문자 오류율. 외부 패키지 없이 계산한다."""
    r = re.sub(r'[^가-힣0-9a-zA-Z]', '', str(ref or ''))
    h = re.sub(r'[^가-힣0-9a-zA-Z]', '', str(hyp or ''))
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hc in enumerate(h, 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (rc != hc))
            prev = cur
    return d[len(h)] / len(r)


def canonicalize_text(text):
    """현재 저장소의 ko-voice normalize를 그대로 써 평가 표기 공간을 통일한다."""
    if text is None:
        return ''
    script = (
        "const fs=require('fs');"
        "const K=require(" + json.dumps(str(KO_VOICE_JS)) + ");"
        "process.stdout.write(K.normalize(fs.readFileSync(0,'utf8')));"
    )
    proc = subprocess.run(
        ['node', '-e', script],
        input=str(text),
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def canonical_cer(ref, hyp):
    return cer(canonicalize_text(ref), canonicalize_text(hyp))


def prosody(y, sr, n_syl):
    import numpy as np
    import librosa

    iv = librosa.effects.split(y, top_db=25, frame_length=1024, hop_length=256)
    gaps = [(iv[k + 1][0] - iv[k][1]) / sr * 1000 for k in range(len(iv) - 1)]
    gaps = [g for g in gaps if 40 <= g <= 2000]
    sp = sum(b - a for a, b in iv) / sr if len(iv) else 0
    f0, _, _ = librosa.pyin(y, fmin=60, fmax=400, sr=sr, frame_length=1024)
    v = f0[~np.isnan(f0)]
    dec = None
    if len(v) > 20:
        h, t = v[:len(v) // 3], v[-len(v) // 3:]
        dec = (np.median(t) / np.median(h) - 1) * 100
    return {
        'pause_med': float(np.median(gaps)) if gaps else None,
        'pause_cv': float(np.std(gaps) / np.mean(gaps)) if len(gaps) > 1 and np.mean(gaps) > 0 else None,
        'rate': n_syl / sp if sp > 0 else None,
        'decl': float(dec) if dec is not None else None,
        'f0': float(np.median(v)) if len(v) else None,
        'silence_ratio': 1 - sp / (len(y) / sr),
        'clip_ratio': float((np.abs(y) > 0.999).mean()),
    }


def score(path, target, asr):
    import numpy as np
    import librosa

    y, sr = librosa.load(path, sr=16000)
    hyp = asr(path)
    n_syl = sum(1 for c in str(target or '') if '가' <= c <= '힣')
    p = prosody(y, sr, n_syl)
    literal = cer(target, hyp) if target else None
    target_canonical = canonicalize_text(target) if target else None
    asr_canonical = canonicalize_text(hyp) if target else None
    semantic = cer(target_canonical, asr_canonical) if target else None
    parts = []
    for key, ref in REF.items():
        if key == 'rate_med':
            v = p['rate']
        elif key == 'pause_med':
            v = p['pause_med']
        elif key == 'pause_cv':
            v = p['pause_cv']
        else:
            v = p['decl']
        if v is not None and ref:
            parts.append(abs(v - ref) / abs(ref))
    return {
        # 기존 소비자 호환: CER는 literal을 유지한다. 새 gate는 CER_canonical을 사용한다.
        'CER': round(literal, 4) if literal is not None else None,
        'CER_literal': round(literal, 4) if literal is not None else None,
        'CER_canonical': round(semantic, 4) if semantic is not None else None,
        'ASR_canonical': asr_canonical[:120] if asr_canonical is not None else None,
        '운율거리': round(float(np.mean(parts)), 3) if parts else None,
        **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items()},
        'ASR': hyp[:120],
    }


def load_jobs(path):
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    out = []
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            name = item.get('name') or item.get('id') or f'job-{i}'
            audio_path = item.get('path')
            target = item.get('target') or item.get('text')
        else:
            if len(item) < 3:
                raise ValueError(f'job {i} needs [name,path,target]')
            name, audio_path, target = item[:3]
        if not audio_path:
            raise ValueError(f'job {name} has no audio path')
        out.append((str(name), str(audio_path), target))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('jobs', help='JSON file containing audio jobs')
    ap.add_argument('--asr-model', default='openai/whisper-small')
    args = ap.parse_args()

    from transformers import pipeline
    import librosa

    pipe = pipeline(
        'automatic-speech-recognition',
        model=args.asr_model,
        device=-1,
        generate_kwargs={'language': 'korean', 'task': 'transcribe'},
    )

    def asr(p):
        y, _ = librosa.load(p, sr=16000)
        return pipe({'raw': y, 'sampling_rate': 16000}, chunk_length_s=30)['text'].strip()

    for name, audio_path, target in load_jobs(args.jobs):
        try:
            result = score(audio_path, target, asr)
            print('JUDGE ' + name + ' ' + json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as e:
            print(f'JUDGE {name} ERROR {e}', flush=True)


if __name__ == '__main__':
    main()
