"""엔진에 넣을 어절 규칙표 — 문체 궤적과 겹치지 않게 분해한다.

  어절 높이 = 문장 추세(rel) + 어절 고유값(초성·어미)
문체표(낭독/대화/유튜브)가 이미 추세를 갖고 있으므로, 여기서는 **추세를 뺀 나머지**만 낸다.
그래야 문체를 바꿔도 어절 규칙이 그대로 얹힌다.

검증: 뺀 나머지가 정말 rel 과 무관한지(잔차 상관 ~0) 확인하고, 홀드아웃 개선도 다시 잰다.
"""
import glob, json, collections
import numpy as np

R = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'
rows = []
for f in sorted(glob.glob(R + '/deep_*.log')):
    for line in open(f, encoding='utf-8', errors='ignore'):
        if line.startswith('DEEP '):
            rows += json.loads(line[5:])['rows']
rows = [r for r in rows if not any(np.isnan(r['pts']))]
L = np.array([np.mean(r['pts']) for r in rows])
rb = np.array([int(min(9, r['rel'] * 10)) for r in rows])

# ① 추세: rel10 별 평균 (문체표가 대체할 부분)
trend = np.array([L[rb == b].mean() for b in range(10)])
resid = L - trend[rb]
print('추세(rel10):', [round(float(x), 2) for x in trend])
print(f'잔차 표준편차 {resid.std():.3f} 반음 · 잔차와 rel 상관 {np.corrcoef(resid, rb)[0,1]:+.4f}')

# ② 잔차에서 초성·어미 효과
def eff(key, minn=200):
    g = collections.defaultdict(list)
    for i, r in enumerate(rows):
        g[r[key]].append(resid[i])
    return {k: (round(float(np.mean(v)), 3), len(v)) for k, v in g.items() if len(v) >= minn}

onset = eff('onset'); tail = eff('tail'); syl = eff('syl')
print('\n초성 잔차효과:', {k: v[0] for k, v in sorted(onset.items(), key=lambda z: -z[1][0])})
print('어미 잔차효과:', {k: v[0] for k, v in sorted(tail.items(), key=lambda z: -z[1][0])})
print('음절 잔차효과:', {k: v[0] for k, v in sorted(syl.items())})

# ③ 초성 효과가 문두에서 더 큰가 — rel 구간별
print('\n초성효과 × 위치 (잔차)')
for b in (0, 1, 2, 5, 9):
    m = rb == b
    line = []
    for k in ('H_격음', 'H_ㅅ', 'H_경음', 'L_평음', 'L_비음유음'):
        v = resid[m & np.array([r['onset'] == k for r in rows])]
        line.append(f'{k.split("_")[1]} {np.mean(v):+.2f}' if len(v) >= 30 else f'{k.split("_")[1]} -')
    print(f'  {b*10:3d}~{b*10+10:3d}%  ' + ' · '.join(line))

# ④ 홀드아웃 — 잔차 모델이 실제로 맞히는가
rng = np.random.default_rng(11); idx = rng.permutation(len(rows)); h = len(rows)//2
A, B = idx[:h], idx[h:]
tr2 = np.array([L[A][rb[A] == b].mean() if (rb[A] == b).sum() else L[A].mean() for b in range(10)])
res_a = L[A] - tr2[rb[A]]
oa = collections.defaultdict(list); ta = collections.defaultdict(list)
for j, i in enumerate(A):
    oa[rows[i]['onset']].append(res_a[j]); ta[rows[i]['tail']].append(res_a[j])
om = {k: float(np.mean(v)) for k, v in oa.items() if len(v) >= 100}
tm = {k: float(np.mean(v)) for k, v in ta.items() if len(v) >= 100}
P0 = tr2[rb[B]]
P1 = P0 + np.array([om.get(rows[i]['onset'], 0) + tm.get(rows[i]['tail'], 0) for i in B])
e0 = float(np.sqrt(((L[B]-P0)**2).mean())); e1 = float(np.sqrt(((L[B]-P1)**2).mean()))
b0 = float(np.sqrt(((L[B]-L[A].mean())**2).mean()))
print(f'\n홀드아웃  평균만 {b0:.3f} → 추세만 {e0:.3f} ({(b0-e0)/b0*100:+.1f}%) → 추세+어절 {e1:.3f} ({(b0-e1)/b0*100:+.1f}%)')

json.dump({'n_words': len(rows), 'trend_rel10': [round(float(x), 3) for x in trend],
           'onset': {k: v[0] for k, v in onset.items()},
           'tail': {k: v[0] for k, v in tail.items()},
           'syl': {str(k): v[0] for k, v in syl.items()},
           'holdout': {'flat': round(b0, 3), 'trend': round(e0, 3), 'trend_word': round(e1, 3),
                       'gain_pct': round((b0-e1)/b0*100, 1)}},
          open(R + '/word_table.json', 'w'), ensure_ascii=False, indent=1)
print('word_table.json 저장')
