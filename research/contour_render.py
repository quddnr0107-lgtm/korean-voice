"""실측 억양 궤적을 실제 소리에 입힌다 — Praat(parselmouth)으로 F0 를 다시 그린다.
librosa 위상 보코더로 음정을 옮기면 금속성이 생긴다(오늘 확인). Praat 의 PSOLA 계열은 그 왜곡이 없다.

ko-voice.js 가 조각마다 5점 궤적(pitchPoints)을 주면, 그 조각의 F0 를 궤적 모양으로 재배치한다.
평균 음높이는 원래대로 두고 '모양'만 바꾼다 — 목소리는 그대로, 억양만 실측을 따른다.
"""
import json
import numpy as np


def apply_points(y, sr, points, strength=0.45, max_semi=1.8):
    """조각의 F0 를 목표 모양 쪽으로 **부분 보정**한다.

    🔴 곱하면 안 된다. 합성음은 이미 자기 하강 곡선을 갖고 있어서, 실측 하강을 곱하면
    하강이 두 겹으로 쌓여 F0 가 극단으로 튀고 삑사리가 난다(2026-09-07 사용자 판정).
    그래서 (목표 모양 ÷ 이 조각의 실제 추세) 만큼만, strength 비율로, ±max_semi 반음 안에서 보정한다.
    보정량에 상한이 있어야 PSOLA 가 따라가다 깨지지 않는다.
    """
    if points is None or len(points) < 2:
        return y
    try:
        import parselmouth
        from parselmouth.praat import call
    except Exception:
        return y
    try:
        snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=sr)
        manip = call(snd, 'To Manipulation', 0.01, 60, 400)
        tier = call(manip, 'Extract pitch tier')
        n = call(tier, 'Get number of points')
        if n < 6:
            return y
        t0, t1 = snd.xmin, snd.xmax
        dur = max(1e-6, t1 - t0)
        ts, vs = [], []
        for i in range(n):
            t = call(tier, 'Get time from index', i + 1)
            v = call(tier, 'Get value at index', i + 1)
            if v and v > 0:
                ts.append(t); vs.append(v)
        if len(vs) < 6:
            return y
        ts = np.asarray(ts); vs = np.asarray(vs)
        u = np.clip((ts - t0) / dur, 0, 1)
        lv = np.log(vs)

        # 이 조각의 실제 추세를 목표와 같은 점수로 뽑는다(구간 평균 — 미세 요동은 남기고 큰 흐름만)
        tgt = np.log(np.asarray(points, dtype=float)); tgt = tgt - tgt.mean()
        m = len(tgt)
        eg = np.linspace(0, 1, m + 1)
        own = np.array([lv[(u >= a) & (u < b)].mean() if ((u >= a) & (u < b)).any() else np.nan
                        for a, b in zip(eg[:-1], eg[1:])])
        if np.isnan(own).any():
            ok = np.flatnonzero(~np.isnan(own))
            if not len(ok):
                return y
            own = np.interp(np.arange(m), ok, own[ok])
        own = own - own.mean()
        corr = (tgt - own) * strength                       # 차이만큼만, 그것도 일부만
        cap = np.log(2 ** (max_semi / 12.0))
        corr = np.clip(corr, -cap, cap)                     # 상한 — 삑사리 방지
        xs = np.linspace(0, 1, len(tgt))
        factor = np.exp(np.interp(u, xs, corr))

        for i in range(n - 1, -1, -1):                      # 뒤에서부터 지워야 색인이 안 밀린다
            t = call(tier, 'Get time from index', i + 1)
            v = call(tier, 'Get value at index', i + 1)
            if not v or v <= 0:
                continue
            f = float(np.interp(min(1.0, max(0.0, (t - t0) / dur)), u, factor))
            call(tier, 'Remove point', i + 1)
            call(tier, 'Add point', t, float(np.clip(v * f, 65.0, 400.0)))
        call([tier, manip], 'Replace pitch tier')
        out = call(manip, 'Get resynthesis (overlap-add)')
        w = np.asarray(out.values).reshape(-1).astype(np.float32)
        return w if len(w) else y
    except Exception:
        return y


def _resample5(curve, n=5):
    """여러 조각의 궤적을 이어붙인 곡선을 5점으로 다시 뽑는다 — 묶음 전체의 모양을 보존한다."""
    a = np.asarray(curve, dtype=float)
    if len(a) <= 1:
        return [1.0] * 5
    xs = np.linspace(0, 1, len(a))
    return [float(np.interp(u, xs, a)) for u in np.linspace(0, 1, n)]


def plan_with_contour(node_plan_json, max_chars=90, npoints=5):
    """ko-voice.js prepare() 결과를 **호흡 묶음**으로 만든다.
    🔴 구 하나하나를 따로 합성하면 뚝뚝 끊긴다(이 저장소가 이미 겪은 함정). 한 호흡에 담을 만큼 합친다.
    묶음의 궤적은 구성 조각들의 궤적을 이어붙여 5점으로 다시 뽑는다 — 묶음 전체가 내려가는 모양이 남는다."""
    p = json.loads(node_plan_json) if isinstance(node_plan_json, str) else node_plan_json
    out = []
    for s in p['sentences']:
        cur = None
        chunks = [c for c in s['chunks'] if c.get('text')]
        for j, c in enumerate(chunks):
            pts = c.get('pitchPoints') or []
            if cur and len(cur['text']) + len(c['text']) + 1 <= max_chars:
                cur['text'] += ' ' + c['text']
                cur['curve'] += list(pts)
                cur['rate'].append(float(c.get('rate') or 1.0))
                cur['emph'] = cur['emph'] or bool(c.get('emph'))
                cur['pause_ms'] = int(c.get('pause') or 0)
            else:
                if cur:
                    out.append(cur)
                cur = {'text': c['text'], 'curve': list(pts), 'rate': [float(c.get('rate') or 1.0)],
                       'emph': bool(c.get('emph')), 'pause_ms': int(c.get('pause') or 0)}
        if cur:
            out.append(cur)
        if out:
            out[-1]['pause_ms'] = max(out[-1]['pause_ms'], 380)
    for u in out:
        u['points'] = _resample5(u['curve'], npoints) if u['curve'] else None
        u['rate'] = float(np.mean(u['rate']))
        u.pop('curve', None)
    return out
