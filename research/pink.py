"""1/f(핑크) 요동 — 자연계 흔들림의 분포. 사람의 말 속도·쉼도 이 분포를 따른다.
백색잡음(완전 무작위)도, 고정값(요동 0)도 아닌 그 중간."""
import numpy as np

def pink(n, seed=None):
    """Voss-McCartney: 옥타브별 난수를 겹쳐 1/f 스펙트럼을 만든다. 평균 0, 표준편차 1."""
    rng = np.random.default_rng(seed)
    rows = max(1, int(np.ceil(np.log2(max(n, 2)))))
    out = np.zeros(n)
    for r in range(rows):
        step = 2 ** r
        vals = rng.standard_normal(n // step + 2)
        out += np.repeat(vals, step)[:n]
    out -= out.mean()
    s = out.std()
    return out / s if s > 0 else out

def apply(units, pause_pct=0.18, rate_pct=0.06, seed=None):
    """계획된 조각들의 쉼·속도에 1/f 요동을 입힌다. 전체 평균 길이는 보존한다."""
    n = len(units)
    if n == 0:
        return units
    fp, fr = pink(n, seed), pink(n, None if seed is None else seed + 1)
    for u, a, b in zip(units, fp, fr):
        u['pause_ms'] = max(0, int(round(u['pause_ms'] * (1 + pause_pct * a))))
        u['rate'] = float(np.clip(u['rate'] * (1 + rate_pct * b), 0.75, 1.35))
    return units
