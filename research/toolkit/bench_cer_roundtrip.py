"""STT 되받아쓰기로 TTS 오독을 잡을 수 있는가 — 결론: 숫자에서는 못 잡는다.
   (근거: research/toolkit/2026-09-13-cer-roundtrip.json · 게이트로 쓰지 말고 산문 스크리닝에만 쓴다)

    SUPERTONIC_DIR=<자산> CACHE_DIR=<캐시> python3 research/toolkit/bench_cer_roundtrip.py [--model small]

CER·정규화는 research/judge.py 의 자를 그대로 쓴다(평가 표기 공간 통일).
필요한 것: faster-whisper(MIT) · Whisper 가중치(MIT) · soundfile · imageio-ffmpeg.
"""
import os, sys, json, time, glob, subprocess
SC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('KOREAN_VOICE_ROOT', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'research'))
sys.path.insert(0, os.path.join(ROOT, 'server'))
import numpy as np, soundfile as sf, imageio_ffmpeg
from judge import canonical_cer, canonicalize_text, cer
from faster_whisper import WhisperModel
FF = imageio_ffmpeg.get_ffmpeg_exe()

CASES = [
    '2026년 9월 3일 기준 접수 인원은 1,234명이고 경쟁률은 2.5:1입니다.',
    '근로기준법 제60조에 따라 15일의 유급휴가를 주어야 합니다.',
    '문의는 1588-9090, 접수는 매일 09:00~18:00입니다.',
    '참가비는 150만원이고 정원은 20명입니다.',
    '제6회 시험은 6월 10일, 결과는 시월 육일에 나옵니다.',
]
import server as S
S.load()
paths = [S.cached_or_synthesize('female', c, 8, 1.0, None) for c in CASES]

def wav(p):
    w = p + '.stt.wav'; subprocess.run([FF,'-v','error','-y','-i',p,'-ar','16000','-ac','1',w], check=True); return w

out = {}
import argparse
for name, size in [(os.environ.get('WHISPER_MODEL', 'small'),) * 2]:
    t = time.time(); m = WhisperModel(size, device='cpu', compute_type='int8', cpu_threads=4); load_s = time.time()-t
    rows, audio_s, stt_s = [], 0.0, 0.0
    for c, p in zip(CASES, paths):
        w = wav(p); x, sr = sf.read(w); audio_s += len(x)/sr
        t = time.time()
        segs, info = m.transcribe(w, language='ko', beam_size=5, vad_filter=False)
        hyp = ' '.join(s.text for s in segs).strip(); stt_s += time.time()-t
        rows.append({'ref': c, 'hyp': hyp,
                     'cer_raw': round(cer(c, hyp), 4),
                     'cer_canon': round(canonical_cer(c, hyp), 4)})
    out[name] = {'load_s': round(load_s,1), 'audio_s': round(audio_s,1), 'stt_s': round(stt_s,1),
                 'rtf': round(stt_s/audio_s, 3),
                 'cer_canon_mean': round(float(np.mean([r['cer_canon'] for r in rows])), 4),
                 'cer_raw_mean': round(float(np.mean([r['cer_raw'] for r in rows])), 4),
                 'rows': rows}
print(json.dumps(out, ensure_ascii=False, indent=1))
