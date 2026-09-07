"""흐름 분석 — 조각 하나 받고 → 분석하고 → 지우고 → 다음. 디스크는 한 조각만 쓴다.
1/f 재검증도 시간축을 바꿔 다시 한다:
  · 쉼 수열(앞서 +0.18로 반증) 뿐 아니라
  · 음절 리듬 수열(발음 시작점 간격, IOI) — 문헌에서 1/f 가 보고되는 진짜 시간축
"""
import sys, io, os, json, subprocess
import numpy as np, pyarrow.parquet as pq, soundfile as sf, librosa

B = 'https://huggingface.co/datasets/Bingsu/zeroth-korean/resolve/main/data'
SHARDS = json.loads(sys.argv[1])
PER = int(sys.argv[2])
D = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a/corpus'

def slope(x):
    x = np.asarray(x, float)
    if len(x) < 16 or x.std() == 0: return None
    x = x - x.mean()
    f = np.fft.rfftfreq(len(x))[1:]; P = np.abs(np.fft.rfft(x))[1:] ** 2
    ok = P > 0
    return float(np.polyfit(np.log10(f[ok]), np.log10(P[ok]), 1)[0]) if ok.sum() > 6 else None

pauses, rates, ioi_sl, pause_sl, f0s, decl = [], [], [], [], [], []
n_utt = 0
for sh in SHARDS:
    p = os.path.join(D, 'tmp.parquet')
    if os.path.exists(os.path.join(D, sh)):       # 이미 있는 건 그대로 쓴다
        p = os.path.join(D, sh); keep = True
    else:
        keep = False
        subprocess.run(['curl','-sL','-o',p,f'{B}/{sh}'], check=True)
    tbl = pq.read_table(p)
    ac, tc = tbl.column('audio'), tbl.column('text')
    rows = min(PER, tbl.num_rows)
    for i in range(rows):
        try:
            a = ac[i].as_py(); t = tc[i].as_py()
            y, sr = sf.read(io.BytesIO(a['bytes']), dtype='float32')
            if y.ndim > 1: y = y.mean(1)
            n_utt += 1
            iv = librosa.effects.split(y, top_db=25, frame_length=1024, hop_length=256)
            if len(iv) == 0: continue
            gaps = [(iv[k+1][0]-iv[k][1])/sr*1000 for k in range(len(iv)-1)]
            gaps = [g for g in gaps if 40 <= g <= 2000]
            pauses += gaps
            s = slope(gaps)
            if s is not None: pause_sl.append(s)
            # 음절 리듬: 발음 시작점 간격(IOI) 수열 — 이쪽이 1/f 의 본 무대
            on = librosa.onset.onset_detect(y=y, sr=sr, units='time', backtrack=False)
            if len(on) >= 20:
                ioi = np.diff(on) * 1000
                s2 = slope(ioi)
                if s2 is not None: ioi_sl.append(s2)
            ns = sum(1 for c in t if '가' <= c <= '힣')
            sp = sum(b-a2 for a2,b in iv)/sr
            if ns >= 5 and sp > 0.5: rates.append(ns/sp)
            if i % 12 == 0:
                f0,_,_ = librosa.pyin(y, fmin=60, fmax=400, sr=sr, frame_length=1024)
                v = f0[~np.isnan(f0)]
                if len(v) > 20:
                    f0s.append(np.median(v))
                    h, tl = v[:len(v)//3], v[-len(v)//3:]
                    decl.append((np.median(tl)/np.median(h)-1)*100)
        except Exception:
            continue
    if not keep: os.remove(p)
    print(f'[진행] {sh[:22]} 누적 발화 {n_utt} · 쉼 {len(pauses)} · IOI수열 {len(ioi_sl)}', flush=True)

pz = np.array(pauses); lg = np.log(pz)
out = {
 '발화수': n_utt, '쉼_표본': len(pz),
 '쉼_분위_ms': [round(float(np.percentile(pz,q)),1) for q in (10,25,50,75,90,99)],
 '쉼_로그정규_mu': round(float(lg.mean()),3), '쉼_로그정규_sigma': round(float(lg.std()),3),
 '쉼_변동계수': round(float(pz.std()/pz.mean()),3),
 '음절속도_분위': [round(float(np.percentile(rates,q)),2) for q in (10,25,50,75,90)],
 'F0_중앙값': round(float(np.median(f0s)),1),
 '문장끝_하강_%': round(float(np.median(decl)),1),
 '1f_쉼수열_기울기': round(float(np.median(pause_sl)),3) if pause_sl else None,
 '1f_쉼수열_표본': len(pause_sl),
 '1f_음절리듬_기울기': round(float(np.median(ioi_sl)),3) if ioi_sl else None,
 '1f_음절리듬_표본': len(ioi_sl),
}
print('FINAL ' + json.dumps(out, ensure_ascii=False))
