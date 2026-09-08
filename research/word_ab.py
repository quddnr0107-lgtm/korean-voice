"""어절 층 A/B — 같은 목소리·같은 대본·같은 쉼. 다른 건 어절 규칙표뿐이다.

끄면 문장 추세만(기존), 켜면 추세 위에 초성·어미 편차가 얹힌다.
귀로 판단할 수 있게 만든다 — 숫자로 +17.3% 라도 들리지 않으면 의미 없다.
"""
import os, sys, json, time, subprocess, importlib.util
import numpy as np, soundfile as sf

KV = '/home/user/korean-voice'
R = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'
sys.path.insert(0, R)
spec = importlib.util.spec_from_file_location('synth', os.path.join(KV, 'tools', 'synth-supertonic.py'))
synth = importlib.util.module_from_spec(spec); spec.loader.exec_module(synth)
sys.path.insert(0, os.path.join(KV, 'st3', 'py')); import helper          # noqa: E402
sys.path.insert(0, os.path.join(KV, 'server')); import voice_shape as VS  # noqa: E402
import contour_render as CR                                              # noqa: E402

OUT = R + '/wordab'; os.makedirs(OUT, exist_ok=True)
STYLE = 'F2:0.5,F3:0.5'          # 사용자가 고른 W4
BASE, STEPS = 1.16, 28
STRENGTH, MAXSEMI = float(os.environ.get('STRENGTH', '0.55')), float(os.environ.get('MAXSEMI', '3.0'))
NPTS = int(os.environ.get('NPTS', '24'))
TEXT = open(R + '/lecture_text.txt', encoding='utf-8').read().strip()

JS = ("const K=require(process.argv[1]);const a=JSON.parse(process.argv[2]);"
      "K.applyProfile(a.profile);const p=K.prepare(a.text,{});process.stdout.write(JSON.stringify(p));")


def units(prof):
    us = []
    for par in [l for l in TEXT.split('\n') if l.strip()]:
        r = subprocess.run(['node', '-e', JS, os.path.join(KV, 'public', 'ko-voice.js'),
                            json.dumps({'text': par, 'profile': prof}, ensure_ascii=False)],
                           capture_output=True, text=True)
        if not r.stdout:
            sys.exit('node 실패: ' + r.stderr[:400])
        us += CR.plan_with_contour(r.stdout, npoints=NPTS)
    return us


if __name__ == '__main__':
    base = json.load(open(os.path.join(KV, 'public', 'profiles', 'korean-corpus.json'), encoding='utf-8'))
    base['engine']['jitter'] = {'sigma': 0.42, 'seed': 2026}
    off = json.loads(json.dumps(base)); off['engine'].pop('word', None)

    tts = helper.load_text_to_speech(os.path.join(KV, 'st3', 'onnx'), False); sr = tts.sample_rate
    style, _ = synth.load_blend(helper, os.path.join(KV, 'st3', 'voice_styles'), STYLE)

    for name, prof in (('어절끔', off), ('어절켬', base)):
        U = units(prof)
        t0 = time.time(); pieces = []
        for u in U:
            hard = u['pause_ms'] >= 300
            speed = float(np.clip(BASE * u['rate'] * VS.unit_speed_mult(0, hard) * (0.96 if u['emph'] else 1.0), 0.7, 1.6))
            wav, dur = tts._infer([u['text']], ['ko'], style, STEPS, speed)
            w = np.asarray(wav, dtype=np.float32).reshape(-1)[:int(float(np.asarray(dur).reshape(-1)[0]) * sr)]
            w = synth.trim(w, sr)                                # 자르기 먼저 — 뒤에 하면 죽은 공백이 남는다
            if u['points']:
                w = CR.apply_points(w, sr, u['points'], strength=STRENGTH, max_semi=MAXSEMI)
            w = w / (np.abs(w).max() or 1.0) * 0.89              # praat_shape 는 쓰지 않는다(삑사리 원인)
            w = synth.fade(w, sr)
            pieces.append(w); pieces.append(np.zeros(int(sr * u['pause_ms'] / 1000), dtype=np.float32))
        y = np.concatenate(pieces); y = y / (np.abs(y).max() or 1.0) * (10 ** (-1 / 20))
        sf.write(f'{OUT}/{name}.wav', y, sr)
        npt = len(U[0]['points'] or [])
        print(f'AB|{name}|{len(y)/sr:.1f}s|{time.time()-t0:.0f}s|묶음 {len(U)}|점 {npt}', flush=True)
