"""어절 규칙표 생성 + 홀드아웃 검증.

규칙을 "발견했다"고 말하려면 **못 본 표본을 더 잘 맞혀야** 한다. 판별기 때
사람 판정과 대조 안 하고 결론을 쌓았다가 전부 무효가 됐다(ρ=-0.600). 같은 실수 반복 금지.

분해:
  어절 궤적 = 문장 억양(위치) + 어절 고유 모양(초성·음절수·어미)
  어절 고유 모양은 **평균을 뺀** 편차만 남긴다. 그래야 문장 억양과 더해도 이중 계산이 안 된다.

검증: 표본을 반으로 갈라 앞쪽으로 규칙을 만들고 뒤쪽 8점을 예측한다.
  기준선(어절 평균 0, 즉 평평) 대비 RMSE 가 줄어야 규칙이 쓸모 있다.
"""
import glob, json, collections, sys
import numpy as np

R = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'


def load():
    rows = []
    for f in sorted(glob.glob(R + '/deep_*.log')) + sorted(glob.glob(R + '/D_*.log')):
        for line in open(f, encoding='utf-8', errors='ignore'):
            if line.startswith('DEEP '):
                try:
                    rows += json.loads(line[5:])['rows']
                except Exception:
                    pass
    return [r for r in rows if not any(np.isnan(r['pts']))]


def shapes(rows, key, minn):
    """key 별 평균 8점(평균 제거) + 표본수."""
    g = collections.defaultdict(list)
    for r in rows:
        g[r[key] if not isinstance(key, tuple) else tuple(r[k] for k in key)].append(r['pts'])
    out = {}
    for k, v in g.items():
        if len(v) < minn:
            continue
        P = np.array(v, float).mean(0)
        out[k] = {'shape': [round(float(x - P.mean()), 3) for x in P],
                  'level': round(float(P.mean()), 3), 'n': len(v)}
    return out


def predict(r, mdl):
    """규칙으로 이 어절의 8점을 예측 — 있는 축만 더한다."""
    p = np.zeros(8)
    hit = 0
    for key, tbl in mdl:
        k = r[key] if not isinstance(key, tuple) else tuple(r[x] for x in key)
        e = tbl.get(k)
        if e:
            p += np.asarray(e['shape'], float)
            hit += 1
    return p / max(1, hit)      # 축끼리 평균 — 더하면 과장된다


if __name__ == '__main__':
    rows = load()
    print(f'어절 표본 {len(rows)}개')
    if len(rows) < 500:
        sys.exit('표본 부족')
    rng = np.random.default_rng(2026)
    idx = rng.permutation(len(rows))
    half = len(rows) // 2
    tr = [rows[i] for i in idx[:half]]
    te = [rows[i] for i in idx[half:]]

    axes = [('onset', 80), ('syl', 80), ('tail', 80), ('pos', 80),
            (('pos', 'onset'), 60), (('pos', 'tail'), 60), (('syl', 'tail'), 60)]
    built = [(k, shapes(tr, k, n)) for k, n in axes]

    Y = np.array([r['pts'] for r in te], float)
    Y = Y - Y.mean(1, keepdims=True)              # 어절 평균 제거 = 모양만 비교
    base = float(np.sqrt((Y ** 2).mean()))        # 평평한 선으로 예측했을 때
    print(f'기준선 RMSE {base:.3f} 반음 (평평하게 읽기)')

    best = None
    for take in range(1, len(built) + 1):
        for combo in [built[:take]]:
            P = np.array([predict(r, combo) for r in te])
            rmse = float(np.sqrt(((Y - P) ** 2).mean()))
            gain = (base - rmse) / base * 100
            names = '+'.join('×'.join(k) if isinstance(k, tuple) else k for k, _ in combo)
            print(f'  {names:38s} RMSE {rmse:.3f}  개선 {gain:+5.1f}%')
            if best is None or rmse < best[0]:
                best = (rmse, combo, names)

    print(f'\n채택: {best[2]}  RMSE {best[0]:.3f} (개선 {(base-best[0])/base*100:+.1f}%)')
    full = [(k, shapes(rows, k, n)) for k, n in axes]
    keep = {('×'.join(k) if isinstance(k, tuple) else k): {
        ('|'.join(map(str, kk)) if isinstance(kk, tuple) else str(kk)): vv for kk, vv in t.items()}
        for k, t in full if any((('×'.join(k) if isinstance(k, tuple) else k) ==
                                 ('×'.join(b) if isinstance(b, tuple) else b)) for b, _ in best[1])}
    json.dump({'rmse_flat': round(base, 3), 'rmse_rule': round(best[0], 3),
               'gain_pct': round((base - best[0]) / base * 100, 1),
               'n_words': len(rows), 'axes': keep},
              open(R + '/word_rules.json', 'w'), ensure_ascii=False, indent=1)
    print('word_rules.json 저장')
