"""옛 조합 k2 — 얼려 둔 사본이다(2026-09-08). 사이트가 목소리 두 벌을 동시에 내주기 위해 남긴다.
   F2 순수 · 유튜브 억양 궤적 · 어절 층. 정본은 커밋 c1644f6 의 server/voice_shape.py 였다.
   🔴 이 파일은 고치지 마라 — 이미 구운 조각 5만8천 개가 이 조합으로 만들어져 있다.
      단 하나 예외가 tail_trim(말끝 죽은 공백 잘라내기)이다. 이건 소리를 안 바꾸고 빈 자리만 없앤다.
   스텝은 16 이다(이 벌을 구울 때의 값 · 캐시 키에 들어간다 · server/recipes.py 가 짝지어 둔다)."""
import re, sys
import numpy as np

RECIPE_TAG = 'k2'   # 🔴 조합이 바뀌면 올려라 — lib/tts-key.mjs 의 RECIPE_TAG 와 글자까지 같아야 한다(test/tts-key.test.mjs 가 잰다)
#   u4 → u4a (2026-09-03): 워커가 컨테이너보다 먼저 새 판이 되어 옛 소리가 u4 키로 R2 에 들어갈 수 있던 창을 버린다
#   u4a → k1 (2026-09-07): 두 가지가 바뀌었다.
#     ① praat_shape(U4 다듬기)를 끈다 — 삑사리의 원인이었다(단계별 제거 실험 A~E, 사용자 판정).
#     ② 그 자리에 한국인 실측 억양 궤적을 넣는다(Zeroth-Korean 어절 10,305개).
#     목소리도 F4:0.6,F2:0.4 → F2:0.5,F3:0.5 로 바꿨다 — F4 가 중성음의 원인이었다(사용자 판정 W4).
#   k1 → k2 (2026-09-08): 사용자 청취로 넷이 바뀌었다.
#     ① 목소리 F2:0.5,F3:0.5 → F2 순수 (외삽 F2:1.4,F3:-0.4 는 만들어 보고 뺐다 — 기계음)
#     ② 궤적을 낭독 → **유튜브 화자**로 (문말 -4.31 → -0.76반음 · YODAS CC-BY 실측)
#     ③ 어절 층 추가 — 초성·어미가 어절 높이를 가른다(어절 5.2만개 · 홀드아웃 +17.7%)
#     ④ 조각 안 하강 감쇠 0.7 · 어절 간 도약 상한 0.7반음 (없을 때 한 조각이 1.9반음 급락 —
#        사용자 판정 「갑자기 호러처럼」)
RECIPE = {
    'style': 'F2',
    'contrast': (1.06, 0.92), 'beat_ms': 250,
    'onset_boost': 2.0, 'lead_pad_ms': 50,
    'world': (1.8, 1.0), 'up_only': True, 'hat': (0.3, -0.5),
    'end_rise': 6.0, 'q_rise': 8.0, 'rise_ms': 200,
}
FRAME_S = 0.005   # Praat/WORLD 프레임

# ── 한국인 실측 억양 궤적 (Zeroth-Korean CC BY 4.0 · 어절 10,305개 · 화자 중앙값 대비 반음) ────────
# 문장이 계단으로 내려가고 어절 안에서 또 내려간다. 총 하강 6.42반음(31%) — 옛 직선 하강(9.9%)의 3배였다.
# 2026-09-08: 낭독 코퍼스(Zeroth) → **유튜브 화자**(YODAS CC BY 3.0) 표로 교체. 사용자 선택.
# 문체마다 억양 체계가 다르다 — 문말 하강 낭독 -4.31 · 대화 -1.53 · 유튜브 -0.76반음(실측).
# 강의 음성은 읽는 소리가 아니라 말하는 소리다.
KO_CONTOUR = {
    'head': [0.766, 0.83, 0.68, 0.276, -0.139],    # 문두
    'mid':  [0.492, 0.223, -0.112, -0.467, -0.964],    # 문중
    'tail': [0.002, -0.19, -0.622, -0.759, -0.757],    # 문말
}
# 🔴 표의 하강은 「그 구간 어절들의 평균 모양」이다. 조각마다 통째로 다시 적용하면 조각 안에서 또
#    떨어지고 조각끼리도 떨어져 두 번 센다(한 조각이 1.9반음 급락했다). 조각 안은 눌러 평평하게 한다.
INNER_DAMP = 0.7
CONTOUR_STRENGTH = 0.7    # 실측 궤적을 얼마나 따를지 (사용자 청취로 0.7 채택)
CONTOUR_MAX_SEMI = 5.0    # 보정 상한(반음). 낮으면 궤적이 상한에 눌려 평평해진다

# ── 어절 층 ── 어절 5.2만개 실측(Zeroth). 어절의 '높이'는 첫 자음과 끝 형태가 가른다.
#   문장 추세(위치)를 뺀 잔차만 담았다 — 추세와의 상관 -0.0000 이라 위 표와 겹치지 않는다.
#   홀드아웃: 추세만 +10.7%% → 추세+어절 +17.7%% (평평하게 읽기 대비 RMSE).
#   1음절 어절은 초성 효과가 약하다(평음 -0.08 vs 다음절 -0.38) — 따로 잰 표를 쓴다.
WORD_ONSET = {"N|L_비음유음": -0.65, "N|L_평음": -0.378, "1|L_비음유음": 0.046, "N|H_격음": 1.19, "N|H_ㅅ": 1.11, "1|H_격음": 1.349, "1|L_평음": -0.083, "1|H_경음": 0.408, "N|H_경음": 0.67, "1|H_ㅅ": 0.893}
WORD_TAIL = {"조사_목적": 0.319, "조사_주격": -0.129, "조사_부사": 0.146, "무표지": 0.114, "어미_다": -0.867, "연결_고": -0.251, "어미_요": -0.84}
WORD_STRENGTH = 1.0
WORD_MAX_STEP = 0.7       # 인접 어절 사이 최대 도약(반음). 사람 성대가 못 하는 점프를 막는다
WORD_PPW = 8              # 어절당 격자 점 수 — 고정 점수는 긴 조각에서 어절을 뭉갠다(앨리어싱)

_CHO = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ'
_ASP, _TEN, _SON = 'ㅊㅋㅌㅍㅎ', 'ㄲㄸㅃㅆㅉ', 'ㄴㅁㄹㅇ'
_TAIL_RE = [('어미_다', r'(습니다|ㅂ니다|다)$'), ('어미_요', r'요$'), ('어미_까', r'(까|나요|죠|가요)$'),
            ('연결_고', r'(고|며|면서|는데|지만|어서|아서)$'),
            ('조사_주격', r'(은|는|이|가)$'), ('조사_목적', r'(을|를)$'),
            ('조사_부사', r'(에|에서|으로|로|와|과|도|만|까지|부터)$')]


def onset_class(w):
    """어절 첫 음절의 초성 유형 — K-ToBI 가 톤 시작을 가른다고 말하는 그 자질."""
    for ch in w:
        c = ord(ch)
        if 0xAC00 <= c <= 0xD7A3:
            j = _CHO[(c - 0xAC00) // 588]
            if j in _ASP:
                return 'H_격음'
            if j in _TEN:
                return 'H_경음'
            if j == 'ㅅ':
                return 'H_ㅅ'
            if j in _SON:
                return 'L_비음유음'
            return 'L_평음'
    return None


def tail_class(w):
    for n, pat in _TAIL_RE:
        if re.search(pat, w):
            return n
    return '무표지'


def _syl(w):
    return sum(1 for ch in w if 0xAC00 <= ord(ch) <= 0xD7A3)


def word_offsets(text, n=None):
    """조각 안 어절들의 반음 편차를 음절 비례 위치에 놓고 n점 균일 격자로 낸다.

    ko-voice.js 의 wordGrid 와 같은 계산이다 — 화면(브라우저)과 구운 소리가 갈리면 안 된다.
    계단으로 두면 어절 경계에서 F0 가 순간 이동해 「갑자기 뚝」 하고 들린다. 평활해서 미끄러뜨린다.
    """
    ws = [w for w in re.sub(r'[.,!?…·"\'()\[\]]', ' ', str(text)).split() if w]
    if not ws:
        return None
    syl = [max(1, _syl(w)) for w in ws]
    tot = sum(syl)
    off = []
    for w, sn in zip(ws, syl):
        o = onset_class(w)
        b = '1|' if sn == 1 else 'N|'
        oo = WORD_ONSET.get(b + o, WORD_ONSET.get(o, 0.0)) if o else 0.0
        off.append(oo + WORD_TAIL.get(tail_class(w), 0.0))
    for i in range(1, len(off)):                      # 어절 간 도약 상한
        d = off[i] - off[i - 1]
        if abs(d) > WORD_MAX_STEP:
            off[i] = off[i - 1] + (WORD_MAX_STEP if d > 0 else -WORD_MAX_STEP)
    N = n or min(240, max(24, WORD_PPW * len(ws)))
    edge = np.cumsum([0] + syl) / tot
    grid = np.empty(N)
    for g in range(N):
        u = g / (N - 1) if N > 1 else 0.0
        k = int(np.searchsorted(edge, u, side='right') - 1)
        grid[g] = off[min(max(k, 0), len(off) - 1)]
    win = max(3, int(round(N / len(ws) * 0.8)) | 1)   # 어절 0.8 폭 Hann
    ker = np.hanning(win + 2)[1:-1]
    ker = ker / ker.sum()
    pad = win // 2
    return np.convolve(np.pad(grid, pad, mode='edge'), ker, mode='valid')[:N]


def contour_target(hard, text=None):
    """이 조각이 그려야 할 궤적(배수). 문장 끝 조각은 문두→문말 전체, 중간 조각은 문두→문중까지.

    text 를 주면 어절 층을 얹어 촘촘한 격자로 낸다(어절 수 × 8점). 안 주면 옛 5점 그대로다.
    """
    C = KO_CONTOUR
    seq = C['head'] + C['mid'] + (C['tail'] if hard else [])
    a = np.asarray(seq, dtype=float)
    xs = np.linspace(0, 1, len(a))
    wg = word_offsets(text) if text else None
    n = len(wg) if wg is not None else 5
    st = np.array([np.interp(u, xs, a) for u in np.linspace(0, 1, n)])
    st = st.mean() + (st - st.mean()) * INNER_DAMP        # 조각 안 하강을 눌러 이중 계산을 없앤다
    if wg is not None:
        st = st + wg * WORD_STRENGTH
    return np.power(2.0, st / 12.0)


def hnr(y, sr):
    """조화 대 잡음비(dB) — 높을수록 깨끗하다. 거친 뽑기를 걸러내는 자.

    🔴 합성은 뽑기다: 같은 문장·같은 설정으로 여섯 번 돌리면 10.7~15.1dB 로 갈린다(실측 2026-09-08).
       스텝을 28→56 으로 올려도 평균은 그대로였다(12.58 → 12.69). 여러 번 뽑아 고르는 편이 낫다.
    """
    try:
        import parselmouth
        from parselmouth.praat import call
        snd = parselmouth.Sound(np.asarray(y, dtype=np.float64), sampling_frequency=sr)
        v = np.asarray(call(snd, 'To Harmonicity (cc)', 0.01, 70, 0.1, 1.0).values).reshape(-1)
        v = v[v > -100]
        return float(np.mean(v)) if len(v) else -1e9
    except Exception:
        return 0.0


def ko_contour_shape(w, sr, hard, text=None, strength=None, max_semi=None):
    """실측 억양으로 F0 를 **부분 보정**한다. 곱하면 안 된다 — 합성음이 이미 자기 하강을 갖고 있어
    겹치면 삑사리가 난다. (목표 모양 ÷ 이 조각의 실제 추세) 만큼만, 상한 안에서 옮긴다."""
    strength = CONTOUR_STRENGTH if strength is None else strength
    max_semi = CONTOUR_MAX_SEMI if max_semi is None else max_semi
    try:
        import parselmouth
        from parselmouth.praat import call
    except Exception:
        return np.asarray(w, dtype=np.float32)
    try:
        snd = parselmouth.Sound(np.asarray(w, dtype=np.float64), sampling_frequency=sr)
        manip = call(snd, 'To Manipulation', FRAME_S, 70, 500)
        pt = call(manip, 'Extract pitch tier'); n = call(pt, 'Get number of points')
        if n < 6:
            return np.asarray(w, dtype=np.float32)
        ts = np.array([call(pt, 'Get time from index', k + 1) for k in range(n)])
        fs = np.array([call(pt, 'Get value at index', k + 1) for k in range(n)])
        ok = fs > 0
        if ok.sum() < 6:
            return np.asarray(w, dtype=np.float32)
        u = np.clip((ts - snd.xmin) / max(1e-6, snd.duration), 0, 1)
        lv = np.log(np.where(ok, fs, np.nan))
        # 🔴 자기 추세(own)는 5칸으로 성기게 잡는다. 목표와 같은 해상도로 잡으면 (목표-자기)가 F0 를
        #    통째로 덮어써 미세 요동이 사라진다 — 그게 기계음이다. 큰 흐름만 빼고 어절 구조는 얹는다.
        edges = np.linspace(0, 1, 6)
        own5 = np.array([np.nanmean(lv[(u >= a) & (u < b)]) if ((u >= a) & (u < b) & ok).any() else np.nan
                         for a, b in zip(edges[:-1], edges[1:])])
        if np.isnan(own5).all():
            return np.asarray(w, dtype=np.float32)
        if np.isnan(own5).any():
            good = ~np.isnan(own5)
            own5 = np.interp(np.arange(5), np.flatnonzero(good), own5[good])
        tgt = np.log(contour_target(hard, text)); tgt -= tgt.mean()
        m = len(tgt)
        own = np.interp(np.linspace(0, 1, m), np.linspace(0, 1, 5), own5)
        own -= own.mean()
        cap = np.log(2 ** (max_semi / 12.0))
        corr = np.clip((tgt - own) * strength, -cap, cap)
        factor = np.exp(np.interp(u, np.linspace(0, 1, m), corr))
        new = call('Create PitchTier', 'p', 0, snd.duration)
        for t, f, g, good in zip(ts, fs, factor, ok):
            if good:
                call(new, 'Add point', float(t), float(np.clip(f * g, 70.0, 500.0)))
        call([manip, new], 'Replace pitch tier')
        return call(manip, 'Get resynthesis (overlap-add)').values[0].astype(np.float32)
    except Exception:
        return np.asarray(w, dtype=np.float32)
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

def hum_tail(w, sr):
    """끝 「우웅」 검출 — 배치 합성의 패딩 자리에서 모델이 낮은 순음(~120Hz · HNR 높음 · 400Hz 아래 에너지)을 수백 ms 낸다(L280).
    마지막 유성 구간이 200ms 를 넘고 그 끝 0.3초가 HNR 12dB 초과 · 저역비 0.5 초과이면 True. 실측(라이브 조각 · 사용자 판정):
    우웅 O → 725ms · HNR 18.5 · 저역 0.71 / 우웅 X → 40~70ms · HNR 3~8 · 저역 0.06~0.13. parselmouth 가 없으면 None(못 잰 것을 통과로 세지 않는다 · R139)."""
    try:
        import parselmouth
    except ImportError:
        return None
    w = np.asarray(w, dtype=np.float64).reshape(-1)
    if len(w) < sr // 2: return False
    snd = parselmouth.Sound(w, sampling_frequency=sr); p = snd.to_pitch(time_step=0.005, pitch_floor=75, pitch_ceiling=600)
    f0 = p.selected_array['frequency']; t = p.xs(); v = f0 > 0
    if v.sum() < 10: return False
    vi = np.where(v)[0]; runs = np.split(vi, np.where(np.diff(vi) > 1)[0] + 1); last_run_ms = len(runs[-1]) * 5
    if last_run_ms <= 200: return False
    last = t[v][-1]; a, b = int(max(0, last - 0.3) * sr), int(last * sr)
    x = w[a:b]
    if len(x) < sr // 20: return False
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2; fq = np.fft.rfftfreq(len(x), 1 / sr); low = float(sp[fq < 400].sum() / (sp.sum() or 1))
    hnr = parselmouth.praat.call(parselmouth.praat.call(snd.extract_part(max(0, last - 0.3), last), "To Harmonicity (cc)", 0.01, 75, 0.1, 1.0), "Get mean", 0, 0)
    return bool(hnr > 12 and low > 0.5)


def tail_trim(w, sr, drop_db=40.0, keep_ms=60):
    """말소리가 끝난 뒤 남은 **죽은 공백**을 자른다 (2026-09-08 사용자 제보 「맞추어 정합니다가 묵음」).

    🔴 `trim()` 의 문턱은 최댓값 대비 -45dB 라 아주 낮은 잔향까지 살려 둔다. 모델이 가끔
       예측 길이를 길게 잡아 **1.8초짜리 무음**이 붙은 채로 구워졌다(실측: 10.87초 중 9.04초까지만 말).
       재생기가 그 뒤에 쉼을 또 넣으니 「한 어절이 통째로 묵음」처럼 들린다.
    🔵 자르는 것은 **뒤쪽 무음뿐**이라 말을 깎지 않는다. keep_ms 만큼은 남겨 뚝 끊기지 않게 한다.
    """
    f = int(sr * 0.02)
    n = len(w) // f
    if n < 5:
        return w
    rms = np.sqrt((w[:n * f].reshape(n, f) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    loud = np.flatnonzero(db > db.max() - drop_db)
    if not len(loud):
        return w
    end = min(len(w), (loud[-1] + 1) * f + int(sr * keep_ms / 1000))
    return w[:end] if end < len(w) else w


def _trim(wav, sr, thresh_db=-45.0, pad_ms=30):
    frame = int(sr * 0.01); n = len(wav) // frame
    if n < 3:
        return wav
    rms = np.sqrt((wav[:n * frame].reshape(n, frame) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9); idx = np.where(db > db.max() + thresh_db)[0]
    if not len(idx):
        return wav
    pad = int(sr * pad_ms / 1000)
    return wav[max(0, idx[0] * frame - pad):min(len(wav), (idx[-1] + 1) * frame + pad)]


def shape(w, sr, text, hard):
    """이 조합의 다듬기 전부 — trim(앞여유 50ms) → 페이드 → 첫 음절 보강 → 실측 억양 궤적 → 말끝 공백 → 크기 맞춤."""
    w = _trim(np.asarray(w, dtype=np.float32).reshape(-1), sr, pad_ms=RECIPE['lead_pad_ms'])
    k = min(len(w) // 2, int(sr * 0.01))
    if k > 0:
        ramp = np.linspace(0, 1, k, dtype=np.float32); w[:k] *= ramp; w[-k:] *= ramp[::-1]
    w = onset_boost(w, sr)
    w = ko_contour_shape(w, sr, hard, text)
    w = tail_trim(w, sr)          # 🔴 이 벌을 처음 구울 때는 없었다 — 「묵음」 제보의 원인(2026-09-08)
    return w / (np.abs(w).max() or 1.0) * 0.89

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
        # 끝 우웅 검출 — 「말소리(200Hz 에 잡음 섞임) 1초」 뒤에 「120Hz 순음 0.5초」를 붙이면 잡히고, 말소리만이면 안 잡힌다
        rng = np.random.default_rng(3); tt = np.arange(sr) / sr
        speech = (0.4 * np.sin(2 * np.pi * 200 * tt) * (1 + 0.3 * np.sin(2 * np.pi * 3 * tt)) + 0.15 * rng.standard_normal(sr)).astype(np.float32)
        hum = (0.1 * np.sin(2 * np.pi * 120 * np.arange(sr // 2) / sr)).astype(np.float32)
        t('[양성] 끝 우웅 검출 — 낮은 순음 꼬리 0.5초가 붙으면 True', hum_tail(np.concatenate([speech, np.zeros(sr // 20, dtype=np.float32), hum]), sr) is True)
        t('[음성] 끝 우웅 검출 — 말소리만이면 False', hum_tail(speech, sr) is False)
    except ImportError:
        print('  ⚠️ parselmouth 없음 — PSOLA 자는 건너뛴다(굽는 워크플로엔 있다 · 이 환경만)')
    print(f"{'✅' if not bad else '🔴'} 강의 음성 다듬기 자 {ok}/{ok + bad}")
    sys.exit(1 if bad else 0)

if __name__ == '__main__':
    if '--selftest' in sys.argv: selftest()
    else: print(__doc__)
