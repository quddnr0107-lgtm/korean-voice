"""최종 비교 — 실측 억양 궤적을 넣은 판 vs 안 넣은 판. 목소리·대본·속도 전부 같고 억양만 다르다.
기능 전부 적용: ko-voice 정규화 · 실측 로그정규 쉼 흔들림 · voice_shape(U4) 다듬기 · 완급 · steps 28.
후처리(librosa 음정이동·EQ·압축)는 쓰지 않는다 — 금속성의 원인.
"""
import os, sys, json, time, subprocess, importlib.util
import numpy as np, soundfile as sf

KV = '/home/user/korean-voice'
R = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'
sys.path.insert(0, R)
spec = importlib.util.spec_from_file_location('synth', os.path.join(KV, 'tools', 'synth-supertonic.py'))
synth = importlib.util.module_from_spec(spec); spec.loader.exec_module(synth)
sys.path.insert(0, os.path.join(KV, 'st3', 'py')); import helper
sys.path.insert(0, os.path.join(KV, 'server')); import voice_shape as VS
import contour_render as CR

OUT = R + '/final'; os.makedirs(OUT, exist_ok=True)
TEXT = ("네, 도와드릴게요. 예비군 훈련 준비물은 크게 세 가지예요.\n"
        "통지서와 신분증, 그리고 편한 신발입니다. 훈련장에서 2km 정도 걷는 경우가 많거든요.\n"
        "통지서를 못 받으셨다면 병무청 홈페이지에서 바로 확인하실 수 있어요.\n"
        "혹시 날짜를 바꾸고 싶으시면 훈련일 3일 전까지 연기 신청이 가능할까요? 네, 가능합니다.")
STYLE = 'F1:0.5,F3:0.3,F5:0.2'      # 자동 채점 1위(A05 · CER 0.000)
STEPS, BASE = 28, 1.10

PROF = json.load(open(os.path.join(KV, 'public', 'profiles', 'korean-corpus.json'), encoding='utf-8'))
PROF['engine']['jitter'] = {'sigma': 0.42, 'seed': 2026}


def plan(text, profile):
    js = ("const K=require(process.argv[1]);const a=JSON.parse(process.argv[2]);"
          "if(a.profile)K.applyProfile(a.profile);const p=K.prepare(a.text,{});"
          "process.stdout.write(JSON.stringify(p));")
    arg = json.dumps({'text': text, 'profile': profile}, ensure_ascii=False)
    r = subprocess.run(['node', '-e', js, os.path.join(KV, 'public', 'ko-voice.js'), arg],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('엔진 오류: ' + r.stderr[-400:])
    return json.loads(r.stdout)


tts = helper.load_text_to_speech(os.path.join(KV, 'st3', 'onnx'), False); sr = tts.sample_rate
style, _ = synth.load_blend(helper, os.path.join(KV, 'st3', 'voice_styles'), STYLE)


def build(profile):
    units = []
    for par in [l for l in TEXT.split('\n') if l.strip()]:
        units += CR.plan_with_contour(plan(par, profile))
    return units


def render(units, name, use_contour):
    t0 = time.time(); pieces = []
    for u in units:
        hard = u['pause_ms'] >= 300
        speed = float(np.clip(BASE * u['rate'] * VS.unit_speed_mult(0, hard) * (0.96 if u['emph'] else 1.0), 0.7, 1.6))
        wav, dur = tts._infer([u['text']], ['ko'], style, STEPS, speed)
        w = np.asarray(wav, dtype=np.float32).reshape(-1)[:int(float(np.asarray(dur).reshape(-1)[0]) * sr)]
        # 🔴 자르기를 먼저 한다 — 궤적을 입힌 뒤에 자르면 앞뒤 무음이 덜 잘려 죽은 공백이 남는다(길이 19% 증가).
        w = synth.trim(w, sr)
        if use_contour and u.get('points'):
            w = CR.apply_points(w, sr, u['points'], strength=1.0)      # 실측 억양을 Praat 으로 입힌다
        w = VS.praat_shape(w, sr, u['text'], hard); w = w / (np.abs(w).max() or 1.0) * 0.89
        w = synth.fade(w, sr)
        pieces.append(w); pieces.append(np.zeros(int(sr * u['pause_ms'] / 1000), dtype=np.float32))
    y = np.concatenate(pieces); y = y / (np.abs(y).max() or 1.0) * (10 ** (-1 / 20))
    sf.write(f'{OUT}/{name}.wav', y, sr)
    print(f'FIN|{name}|궤적 {"켬" if use_contour else "끔"}|{len(y)/sr:.1f}s|{time.time()-t0:.0f}s', flush=True)


u = build(PROF)
print(f'[최종] 조각 {len(u)}개 · 궤적 있는 조각 {sum(1 for x in u if x.get("points"))}개 · 쉼 {[x["pause_ms"] for x in u]}', flush=True)
render(u, 'OFF_궤적없음', False)
render(u, 'ON_실측궤적', True)
