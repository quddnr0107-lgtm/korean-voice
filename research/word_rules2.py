"""축 평균이 아니라 최소제곱으로 제대로 맞춘다 + 이 자질들의 **상한**을 잰다.

물음 셋:
 ① 원핫 선형모델로 풀면 개선이 커지는가 (내 조합 방식이 문제였나)
 ② 자질 전체 교차(초성×음절×어미×위치) 칸평균 = 이 자질로 가능한 최대치는 얼마인가
 ③ 신호 자체가 있는가 — 반쪽 A 의 칸평균이 반쪽 B 의 칸평균과 상관되는가(분할반분 신뢰도)
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
print(f'어절 {len(rows)}')

Y = np.array([r['pts'] for r in rows], float)
Y = Y - Y.mean(1, keepdims=True)
rng = np.random.default_rng(7)
idx = rng.permutation(len(rows))
h = len(rows) // 2
A, B = idx[:h], idx[h:]

# ① 원핫 최소제곱
def onehot(rs):
    cols = []
    for key in ('onset', 'syl', 'tail', 'pos'):
        vals = sorted({r[key] for r in rows})
        cols += [(key, v) for v in vals]
    cross = sorted({(r['pos'], r['onset']) for r in rows}) 
    cols += [('pos×onset', c) for c in cross]
    cross2 = sorted({(r['pos'], r['tail']) for r in rows})
    cols += [('pos×tail', c) for c in cross2]
    X = np.zeros((len(rs), len(cols) + 1), float)
    X[:, -1] = 1.0
    ci = {c: i for i, c in enumerate(cols)}
    for i, r in enumerate(rs):
        for key in ('onset', 'syl', 'tail', 'pos'):
            X[i, ci[(key, r[key])]] = 1
        X[i, ci[('pos×onset', (r['pos'], r['onset']))]] = 1
        X[i, ci[('pos×tail', (r['pos'], r['tail']))]] = 1
    return X, cols

Xall, cols = onehot(rows)
Xa, Ya = Xall[A], Y[A]
W, *_ = np.linalg.lstsq(Xa.T @ Xa + 1e-3 * np.eye(Xa.shape[1]), Xa.T @ Ya, rcond=None)
P = Xall[B] @ W
base = float(np.sqrt((Y[B] ** 2).mean()))
lsq = float(np.sqrt(((Y[B] - P) ** 2).mean()))
print(f'① 최소제곱   기준 {base:.3f} → {lsq:.3f}  개선 {(base-lsq)/base*100:+.1f}%')

# ② 자질 전체 교차 칸평균 = 상한
def cellmean(tr, te, keys, minn=20):
    g = collections.defaultdict(list)
    for i in tr:
        g[tuple(rows[i][k] for k in keys)].append(Y[i])
    m = {k: np.mean(v, 0) for k, v in g.items() if len(v) >= minn}
    gm = Y[tr].mean(0)
    P = np.array([m.get(tuple(rows[i][k] for k in keys), gm) for i in te])
    return float(np.sqrt(((Y[te] - P) ** 2).mean()))

for keys in (('onset',), ('tail',), ('pos',), ('pos', 'tail'), ('onset', 'syl', 'tail', 'pos')):
    r2 = cellmean(A, B, keys)
    print(f'② 칸평균 {"×".join(keys):28s} {r2:.3f}  개선 {(base-r2)/base*100:+.1f}%')

# ③ 분할반분 신뢰도 — 신호가 있기는 한가
g1 = collections.defaultdict(list); g2 = collections.defaultdict(list)
for i in A: g1[(rows[i]['pos'], rows[i]['tail'])].append(Y[i])
for i in B: g2[(rows[i]['pos'], rows[i]['tail'])].append(Y[i])
ks = [k for k in g1 if k in g2 and len(g1[k]) >= 50 and len(g2[k]) >= 50]
v1 = np.concatenate([np.mean(g1[k], 0) for k in ks])
v2 = np.concatenate([np.mean(g2[k], 0) for k in ks])
print(f'③ 분할반분 상관 r={np.corrcoef(v1, v2)[0,1]:.3f}  (칸 {len(ks)}개)')
print(f'   칸평균 표준편차 {v1.std():.3f} 반음 vs 어절별 표준편차 {Y.std():.3f} 반음')
