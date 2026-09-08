"""어절 '높이'가 예측되는가 — 모양(shape)이 아니라 level.

앞 분석은 어절 평균을 빼버려서 「이 어절을 얼마나 높게 읽는가」를 통째로 버렸다.
사람 귀에 크게 들리는 건 오히려 이쪽이다(declination · 초성 톤 · 강조).
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
print(f'어절 {len(rows)} · 높이 표준편차 {L.std():.3f} 반음')

rng = np.random.default_rng(7); idx = rng.permutation(len(rows)); h = len(rows)//2
A, B = idx[:h], idx[h:]
base = float(np.sqrt(((L[B] - L[A].mean())**2).mean()))
print(f'기준(전체평균) RMSE {base:.3f}')

def rel_bin(r): return int(min(9, r['rel'] * 10))

def cell(keys, minn=25):
    g = collections.defaultdict(list)
    for i in A: g[tuple(f(rows[i]) for f in keys)].append(L[i])
    m = {k: float(np.mean(v)) for k, v in g.items() if len(v) >= minn}
    gm = L[A].mean()
    P = np.array([m.get(tuple(f(rows[i]) for f in keys), gm) for i in B])
    return float(np.sqrt(((L[B]-P)**2).mean())), m

K = {'onset': lambda r: r['onset'], 'syl': lambda r: r['syl'], 'tail': lambda r: r['tail'],
     'pos': lambda r: r['pos'], 'rel10': rel_bin}
for names in (('onset',), ('pos',), ('rel10',), ('tail',), ('rel10','onset'),
              ('rel10','tail'), ('rel10','onset','tail'), ('rel10','onset','tail','syl')):
    r2, _ = cell([K[n] for n in names])
    print(f'  {"×".join(names):26s} RMSE {r2:.3f}  개선 {(base-r2)/base*100:+5.1f}%')

print('\n── 문장 내 위치별 높이(declination) ──')
_, m = cell([rel_bin], minn=50)
for k in sorted(m): print(f'  {k[0]*10:3d}~{k[0]*10+10:3d}%  {m[k]:+6.2f} 반음')
print('\n── 초성별 높이 (문두 0~10% 구간) ──')
g = collections.defaultdict(list)
for i in range(len(rows)):
    if rel_bin(rows[i]) == 0: g[rows[i]['onset']].append(L[i])
for k, v in sorted(g.items(), key=lambda z: -np.mean(z[1])):
    print(f'  {k:12s} n={len(v):6d}  {np.mean(v):+6.2f} 반음')
