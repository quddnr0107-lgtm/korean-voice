"""실측 억양 궤적을 실제 소리에 입힌다 — Praat(parselmouth)으로 F0 를 다시 그린다.
librosa 위상 보코더로 음정을 옮기면 금속성이 생긴다(오늘 확인). Praat 의 PSOLA 계열은 그 왜곡이 없다.

ko-voice.js 가 조각마다 5점 궤적(pitchPoints)을 주면, 그 조각의 F0 를 궤적 모양으로 재배치한다.
평균 음높이는 원래대로 두고 '모양'만 바꾼다 — 목소리는 그대로, 억양만 실측을 따른다.
"""
import json
import numpy as np


def apply_points(y, sr, points, strength=1.0):
    """points: 5개 배수(1.0 = 원래 음높이). 조각의 F0 곡선을 그 모양으로 다시 그린다."""
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
        if n < 3:
            return y
        t0, t1 = snd.xmin, snd.xmax
        dur = max(1e-6, t1 - t0)
        # 조각 안 F0 의 기하평균을 기준으로, 목표 모양의 기하평균을 맞춰 평균 음높이를 보존한다
        cur = np.array([call(tier, 'Get value at index', i + 1) for i in range(n)], dtype=float)
        cur[cur <= 0] = np.nan
        base = np.nanexp = np.exp(np.nanmean(np.log(cur[~np.isnan(cur)])))
        tgt = np.asarray(points, dtype=float)
        tgt = tgt / np.exp(np.mean(np.log(tgt)))          # 모양만 남기고 평균은 1로
        xs = np.linspace(0, 1, len(tgt))
        for i in range(n):
            t = call(tier, 'Get time from index', i + 1)
            v = call(tier, 'Get value at index', i + 1)
            if v is None or v <= 0:
                continue
            u = min(1.0, max(0.0, (t - t0) / dur))
            f = float(np.interp(u, xs, tgt))
            f = 1.0 + (f - 1.0) * strength
            call(tier, 'Remove point', i + 1)
            call(tier, 'Add point', t, max(60.0, min(420.0, v * f)))
        call([tier, manip], 'Replace pitch tier')
        out = call(manip, 'Get resynthesis (overlap-add)')
        w = np.asarray(out.values).reshape(-1).astype(np.float32)
        return w if len(w) else y
    except Exception:
        return y


def plan_with_contour(node_plan_json):
    """ko-voice.js prepare() 결과에서 조각별 (text, pitchPoints, pause, rate) 를 뽑는다."""
    p = json.loads(node_plan_json) if isinstance(node_plan_json, str) else node_plan_json
    out = []
    for s in p['sentences']:
        for c in s['chunks']:
            if not c.get('text'):
                continue
            out.append({'text': c['text'], 'pause_ms': int(c.get('pause') or 0),
                        'rate': float(c.get('rate') or 1.0), 'emph': bool(c.get('emph')),
                        'points': c.get('pitchPoints')})
        if out:
            out[-1]['pause_ms'] = max(out[-1]['pause_ms'], 380)
    return out
