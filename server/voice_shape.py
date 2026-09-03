#!/usr/bin/env python3
"""강의 음성 다듬기 — **사람이 표본을 듣고 정한 조합 「U4」**(2026-09-03 · R119 · L274). 값은 여기 한 곳(R31).

  판정 여덟 번을 거쳐 살아남은 것:
    목소리   F4:0.6,F2:0.4                      (F2:0.6,F3:0.4 는 첫 음절이 약하고 차분했다)
    쪼개기   「…하고, / …하며, / …참여,」 뒤에서 새 발화   (나열이 한 발화 21초로 이어지면 음이 내려앉는다)
    완급     문장 앞 조각 ×1.06 · 끝 조각 ×0.92 · 끝 조각 앞 한 박자 250ms
    첫 음절  첫 150ms 이득 2.0 램프 · trim 앞여유 50ms   (모델이 「오·무」를 -40dB 로 약하게 낸다)
    억양     Praat PSOLA(파형 자르기 · WORLD 는 낮은 음에서 기계음) · 폭 1.8 **위로만** · 전체 +1반음
             조각 안 0.3 → -0.5 완만 하강 · **문장 끝 +6반음 · 물음 끝 +8반음 · 200ms 에 확**
  🔴 버린 것: 강세 봉우리·조각 흔들기(울먹임) · 앞 봉우리(둘째 음절이 튄다) · WORLD 재합성(낮은 음 버즈) · 스타일 외삽.

  🔴 **정본은 이 파일 하나다**(korean-voice `server/voice_shape.py` · R31). 소비자 둘이 같은 파일을 읽는다:
     ① 즉시 합성 서버 `server/server.py`(같은 디렉터리 · 컨테이너 이미지에 COPY)
     ② yebijun 굽는 자 `build_tts_supertonic.py`(`--korean-voice <clone>` 의 server/ 에서 import)
     조합을 바꾸면 `RECIPE_TAG` 를 올려라 — 워커·서버 캐시 키에 들어가므로 옛 소리가 다시 안 나온다.

  python3 server/voice_shape.py --selftest      (korean-voice `npm test` · yebijun preflight 「강의 음성 다듬기 자」 · parselmouth 없이도 F0 자는 돈다)
"""
import re, sys
import numpy as np

RECIPE_TAG = 'u4'   # 🔴 조합이 바뀌면 올려라 — worker.mjs 의 RECIPE_TAG 와 글자까지 같아야 한다(test/tts.test.mjs 가 잰다)
RECIPE = {
    'style': 'F4:0.6,F2:0.4',
    'contrast': (1.06, 0.92), 'beat_ms': 250,
    'onset_boost': 2.0, 'lead_pad_ms': 50,
    'world': (1.8, 1.0), 'up_only': True, 'hat': (0.3, -0.5),
    'end_rise': 6.0, 'q_rise': 8.0, 'rise_ms': 200,
}
FRAME_S = 0.005   # Praat/WORLD 프레임
Q_END = re.compile(r'(까|가요|나요|죠|습니까)[?.!]*\s*$')

def split_lists(par):
    """나열 항목마다 새 발화 — 「…하고, …하며, …참여, …이며,」 뒤를 문장 끝으로"""
    return re.sub(r'(하고|하며|참여|하거나|이며),\s*', lambda m: m.group(1) + '. ', par)

def is_question(text):
    t = str(text or '')
    return bool(Q_END.search(t)) or t.rstrip().endswith('?')

SENT_END = re.compile(r'([.?!…]|다|요|까|죠|오)\s*$')

def is_sentence_end(text):
    """즉시 합성 서버용 — 받은 조각이 문장 끝(hard)인가. 굽는 자는 운율 계획에서 알지만 서버는 글자뿐이다.
    클라이언트(live-tts.js)가 구로 쪼갠 중간 조각은 쉼표나 낱말로 끝나고, 문장 끝 조각은 문장부호로 끝난다."""
    return bool(SENT_END.search(str(text or '').rstrip()))

def f0_transform(f0, text, hard, r=RECIPE):
    """F0 배열(0=무성) → 새 F0. 폭(위로만)·반음·완만 하강·끝올림. 두 엔진이 같이 쓴다(R31)."""
    f0 = np.asarray(f0, dtype=np.float64); v = f0 > 0
    if v.sum() < 5: return f0.copy()
    alpha, semi = r['world']
    mean = np.exp(np.log(f0[v]).mean()); d = np.log(f0[v] / mean)
    lf = np.zeros_like(f0)
    lf[v] = (np.where(d > 0, d * alpha, d) if r['up_only'] else d * alpha) + np.log(2 ** (semi / 12))
    vi = np.where(v)[0]; nv = len(vi)
    h0, h1 = r['hat']
    if nv > 10:
        up = max(2, int(0.2 / FRAME_S)); k = np.arange(nv)
        shape = np.where(k < up, -1.0 + (h0 + 1.0) * k / up, h0 + (h1 - h0) * (k - up) / max(1, nv - up))
        lf[vi] += np.log(2 ** (shape / 12))
    if hard:
        st = r['q_rise'] if is_question(text) else r['end_rise']
        if st:
            n = min(nv, int(r['rise_ms'] / 1000 / FRAME_S)); tail = vi[-n:]
            lf[tail] += np.linspace(0, np.log(2 ** (st / 12)), n)
    out = np.zeros_like(f0); out[v] = mean * np.exp(lf[v])
    return out

def onset_boost(w, sr, r=RECIPE):
    g = float(r['onset_boost'])
    if g == 1.0 or len(w) == 0: return w
    n = min(int(sr * 0.15), len(w)); ramp = np.linspace(g, 1.0, n, dtype=np.float32)
    w = np.array(w, dtype=np.float32, copy=True); w[:n] *= ramp
    return w

def praat_shape(w, sr, text, hard, r=RECIPE):
    """Praat PSOLA 로 F0 만 바꾼다 — 음색은 안 새로 만든다. parselmouth 가 없으면 소리 내며 멈춘다(R139)."""
    import parselmouth
    from parselmouth.praat import call
    snd = parselmouth.Sound(np.asarray(w, dtype=np.float64), sampling_frequency=sr)
    manip = call(snd, "To Manipulation", FRAME_S, 70, 500)
    pt = call(manip, "Extract pitch tier"); n = call(pt, "Get number of points")
    if n < 5: return np.asarray(w, dtype=np.float32)
    ts = np.array([call(pt, "Get time from index", k + 1) for k in range(n)])
    fs = np.array([call(pt, "Get value at index", k + 1) for k in range(n)])
    f2 = f0_transform(fs, text, hard, r)
    new = call("Create PitchTier", "p", 0, snd.duration)
    for t, f in zip(ts, f2): call(new, "Add point", float(t), float(f))
    call([manip, new], "Replace pitch tier")
    return call(manip, "Get resynthesis (overlap-add)").values[0].astype(np.float32)

def unit_speed_mult(idx_in_sentence, hard, r=RECIPE):
    a, b = r['contrast']; return b if hard else a

def selftest():
    ok = 0; bad = 0
    def t(name, cond):
        nonlocal ok, bad
        print(('  ✅ ' if cond else '  ❌ ') + name); ok += cond; bad += (not cond)
    f0 = np.full(400, 200.0); f0[:20] = 0; f0[200:210] = 0
    f0[50:100] = 150.0; f0[120:160] = 260.0
    s = f0_transform(f0, '예비군법 제5조입니다.', True)
    v = f0 > 0
    t('무성 프레임은 그대로 0', np.all(s[~v] == 0))
    t('위로만 — 낮은 자리(150Hz)는 평균 대비 비율이 안 벌어진다(+반음·하강만 반영)',
      abs(np.log(s[60] / s[30]) - np.log(f0[60] / f0[30])) < np.log(2 ** (1.0 / 12)))   # 기준 프레임 30(200Hz) · 완만 하강 몫만 허용
    t('위로만 — 높은 자리(260Hz)는 1.8배 벌어진다', np.log(s[140] / s[30]) > np.log(f0[140] / f0[30]) * 1.5)
    q = f0_transform(f0, '무슨 일을 합니까.', True); st_end = 12 * np.log2(s[-1] / s[-45]); st_q = 12 * np.log2(q[-1] / q[-45])
    t('문장 끝 200ms 가 +6반음 가까이 올라간다(평서)', 4.5 < st_end < 7.5)
    t('물음 끝은 +8반음 가까이(더 확)', st_q > st_end + 1.0)
    m = f0_transform(f0, '문장 중간 조각이고', False)
    t('문장 중간 조각(hard 아님)엔 끝올림이 없다', 12 * np.log2(m[-1] / m[-45]) < 1.0)
    t('앞 봉우리가 없다 — 첫 200ms 가 뒤보다 높지 않다(「비」가 튀던 자리)', s[25] <= s[45] * 2 ** (0.5 / 12))
    t('나열 쪼개기 — 「…하고, …하며,」 뒤가 문장 끝이 된다', split_lists('대원을 지휘하고, 장비를 관리하며, 임무를 수행한다.').count('. ') == 2)
    t('물음 판정 — 「합니까.」「무엇입니까」는 물음, 「입니다.」는 아님', is_question('합니까.') and is_question('무엇입니까') and not is_question('입니다.'))
    t('문장 끝 판정 — 「…됩니다.」「…합니까?」「…있지 않습니다」는 끝 · 「…지휘하고,」「…예비군대원과 장비의」는 중간',
      is_sentence_end('중대장이나 동대장이 됩니다.') and is_sentence_end('무슨 일을 합니까?') and is_sentence_end('적혀 있지 않습니다')
      and not is_sentence_end('대원을 지휘하고,') and not is_sentence_end('예비군대원과 장비의'))
    t('조합 표식이 있다(캐시 키에 들어간다)', isinstance(RECIPE_TAG, str) and re.fullmatch(r'[a-z0-9]+', RECIPE_TAG) is not None)
    w = np.ones(24000, dtype=np.float32); b = onset_boost(w, 24000)
    t('첫 음절 보강 — 첫 샘플 2.0배 · 150ms 뒤엔 1.0', abs(b[0] - 2.0) < 1e-3 and abs(b[4000] - 1.0) < 1e-3)
    try:
        import parselmouth  # noqa: F401
        sr = 24000; x = (0.3 * np.sin(2 * np.pi * 200 * np.arange(sr) / sr)).astype(np.float32)
        y = praat_shape(x, sr, '테스트입니다.', True)
        t('Praat PSOLA — 길이가 유지되고(±10%) 소리가 있다', 0.9 < len(y) / len(x) < 1.1 and np.abs(y).max() > 0.05)
    except ImportError:
        print('  ⚠️ parselmouth 없음 — PSOLA 자는 건너뛴다(굽는 워크플로엔 있다 · 이 환경만)')
    print(f"{'✅' if not bad else '🔴'} 강의 음성 다듬기 자 {ok}/{ok + bad}")
    sys.exit(1 if bad else 0)

if __name__ == '__main__':
    if '--selftest' in sys.argv: selftest()
    else: print(__doc__)
