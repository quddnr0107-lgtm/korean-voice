"""판별기가 쓸모 있는지 — 사람 판정과 대조한다. 이걸 안 하고 결론을 냈다.

검증 세트(오늘 사용자가 순위를 매긴 것):
  ① 쉼 패턴 5판: p5 > p4 > p3 > p2 > p1  (실측분포+여유 > 실측 > 백색 > 1/f > 고정)
  ② 조합 3판:   mix3 > mix2 > mix1
둘 다 목소리·대본이 같고 쉼/억양만 다르다 — 판별기가 잰다고 주장하는 바로 그것이다.
"""
import glob, os, sys, json
import numpy as np, soundfile as sf
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
R='/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'
sys.path.insert(0,R)
from extract_feats import features
from extract_grouped import to16k, norm

g=np.load(R+'/disc_groups.npz',allow_pickle=True); real=g['real']; base=g['지금_파이프라인']
X=np.nan_to_num(np.vstack([real,base]),nan=0,posinf=0,neginf=0)
y=np.array([1]*len(real)+[0]*len(base))
clf=make_pipeline(StandardScaler(),LogisticRegression(max_iter=4000)).fit(X,y)

def score(path):
    y0,sr=sf.read(path,dtype='float32')
    if y0.ndim>1: y0=y0.mean(1)
    y0,sr=to16k(y0,sr); y0=norm(y0)
    step=int(sr*8); ps=[]
    for k in range(0,max(1,len(y0)-step//2),step):
        f=features(y0[k:k+step],sr)
        if f: ps.append(clf.predict_proba(np.nan_to_num(np.array([f]),nan=0))[0,1])
    return float(np.mean(ps)) if ps else None

SETS=[('쉼 패턴 5판',[('p1',R+'/P1_고정480.wav',1),('p2',R+'/P2_1f요동.wav',2),('p3',R+'/P3_백색요동.wav',3),
                  ('p4',R+'/P4_실측분포.wav',4),('p5',R+'/P5_실측분포_긴판.wav',5)]),
      ('조합 3판',[('mix1',R+'/final/../f1_whole.wav',1),('mix2',R+'/f2_fixed.wav',2),('mix3',R+'/f3_pink.wav',3)])]
for name,items in SETS:
    hs,ms,labels=[],[],[]
    for lab,p,human in items:
        p=os.path.normpath(p)
        if not os.path.exists(p): print(f'  {lab}: 파일 없음 {p}'); continue
        s=score(p)
        if s is None: continue
        hs.append(human); ms.append(s); labels.append(lab)
    if len(hs)>=3:
        rho,pv=spearmanr(hs,ms)
        print(f'\n[{name}] 사람 선호 순위 vs 판별기 점수')
        for l,h,m in sorted(zip(labels,hs,ms), key=lambda z:-z[1]):
            print(f'   {l:5s} 사람선호 {h}위상 · 판별기 {m:.4f}')
        print(f'   순위상관 ρ={rho:+.3f} (p={pv:.3f})  → {"일치" if rho>0.5 else "불일치 — 판별기는 사람 판정을 예측 못한다"}')
