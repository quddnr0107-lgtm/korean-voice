"""어절 심층 분석 보고 — 초성·음절수·어미·위치가 억양을 어떻게 가르는가."""
import glob, json, collections
import numpy as np
R='/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'
rows=[]
for f in glob.glob(R+'/D_*.log'):
    try:
        for line in open(f,encoding='utf-8'):
            if line.startswith('DEEP '):
                rows+=json.loads(line[5:])['rows']
    except Exception: pass
print(f'어절 표본 {len(rows)}개\n')
def agg(key, minn=60):
    g=collections.defaultdict(list)
    for r in rows: g[r[key]].append(r)
    out=[]
    for k,v in g.items():
        if len(v)<minn: continue
        P=np.array([x['pts'] for x in v])
        out.append((k,len(v),P.mean(0),np.mean([x['peak'] for x in v]),np.mean([x['range'] for x in v])))
    return sorted(out,key=lambda z:-z[1])

print('① 초성 유형별 — 이론: 격음·경음·ㅅ 은 높게(H) 시작, 평음은 낮게(L)')
print(f"{'유형':12s} {'n':>6s} {'시작':>6s} {'끝':>6s} {'최고점위치':>9s} {'폭(반음)':>8s}")
for k,n,P,pk,rg in agg('onset'):
    print(f'{k:12s} {n:6d} {P[0]:+6.2f} {P[-1]:+6.2f} {pk:9.2f} {rg:8.2f}')
print('\n② 음절 수별 — 긴 어절일수록 산이 뒤로 간다?')
for k,n,P,pk,rg in sorted(agg('syl'),key=lambda z:z[0]):
    print(f'{k}음절 {n:6d}  궤적 {[round(float(x),2) for x in P]}  최고점 {pk:.2f}')
print('\n③ 어절 끝(조사·어미)별')
for k,n,P,pk,rg in agg('tail'):
    print(f'{k:10s} {n:6d} 시작{P[0]:+6.2f} 끝{P[-1]:+6.2f} 변화{P[-1]-P[0]:+6.2f} 폭{rg:5.2f}')
print('\n④ 위치 × 초성 (교차)')
g=collections.defaultdict(list)
for r in rows: g[(r['pos'],r['onset'])].append(r)
for (p,o),v in sorted(g.items()):
    if len(v)<40: continue
    P=np.array([x['pts'] for x in v]).mean(0)
    print(f'  {p} {o:12s} n={len(v):5d} 시작{P[0]:+6.2f} 끝{P[-1]:+6.2f}')
