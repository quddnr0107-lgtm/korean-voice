"""실제 한국인 발화에서 운율 통계를 뽑는다 — Zeroth-Korean (CC BY 4.0, 공개 기증).
음색은 만지지 않는다. 재는 것은 언어의 공유 자산인 운율뿐이다:
  · 구 사이 쉼의 분포 (평균이 아니라 분포 전체)
  · 쉼 수열이 1/f 로 흔들리는가 (스펙트럼 기울기)
  · 음절 속도 · 문장 끝 하강 폭
"""
import sys, io, json
import numpy as np, pyarrow.parquet as pq, soundfile as sf, librosa

N = int(sys.argv[2]) if len(sys.argv) > 2 else 400
tbl = pq.read_table(sys.argv[1])
rows = min(N, tbl.num_rows)
print(f'[코퍼스] {sys.argv[1].split("/")[-1]} · 전체 {tbl.num_rows} · 분석 {rows}', flush=True)

def syllables(t):                      # 한글 음절 수
    return sum(1 for c in t if '가' <= c <= '힣')

pauses, rates, f0s, decl, seqs = [], [], [], [], []
audio_col, text_col = tbl.column('audio'), tbl.column('text')
for i in range(rows):
    try:
        a = audio_col[i].as_py(); t = text_col[i].as_py()
        y, sr = sf.read(io.BytesIO(a['bytes']), dtype='float32')
        if y.ndim > 1: y = y.mean(1)
        # 무음 구간 = 쉼. 상위 25dB 아래를 무음으로 본다
        iv = librosa.effects.split(y, top_db=25, frame_length=1024, hop_length=256)
        if len(iv) == 0: continue
        speech_s = sum(b - a2 for a2, b in iv) / sr
        gaps = [(iv[k+1][0] - iv[k][1]) / sr * 1000 for k in range(len(iv)-1)]
        gaps = [g for g in gaps if 40 <= g <= 2000]      # 40ms 미만은 폐쇄음, 2s 초과는 녹음 여백
        pauses += gaps
        if len(gaps) >= 4: seqs.append(gaps)
        ns = syllables(t)
        if ns >= 5 and speech_s > 0.5: rates.append(ns / speech_s)
        # F0 (pYIN) — 음색이 아니라 억양 궤적을 본다
        if i % 6 == 0:
            f0, vo, _ = librosa.pyin(y, fmin=60, fmax=400, sr=sr, frame_length=1024)
            v = f0[~np.isnan(f0)]
            if len(v) > 20:
                f0s.append(np.median(v))
                h, tl = v[:len(v)//3], v[-len(v)//3:]      # 앞 1/3 대비 끝 1/3
                if len(h) and len(tl): decl.append((np.median(tl)/np.median(h) - 1) * 100)
    except Exception:
        continue

def slope(seq):    # 1/f 이면 -1 근처
    x = np.asarray(seq, float); x = x - x.mean()
    if len(x) < 8 or x.std() == 0: return None
    f = np.fft.rfftfreq(len(x))[1:]; P = np.abs(np.fft.rfft(x))[1:]**2
    ok = P > 0
    return float(np.polyfit(np.log10(f[ok]), np.log10(P[ok]), 1)[0]) if ok.sum() > 3 else None

sl = [s for s in (slope(q) for q in seqs) if s is not None]
p = np.array(pauses)
out = {
 '쉼_표본수': len(p),
 '쉼_중앙값_ms': round(float(np.median(p)), 1),
 '쉼_평균_ms': round(float(p.mean()), 1),
 '쉼_사분위_ms': [round(float(np.percentile(p, q)), 1) for q in (10, 25, 50, 75, 90)],
 '쉼_변동계수': round(float(p.std()/p.mean()), 3),
 '쉼수열_1f기울기_중앙값': round(float(np.median(sl)), 2) if sl else None,
 '쉼수열_표본': len(sl),
 '음절속도_중앙값': round(float(np.median(rates)), 2),
 '음절속도_사분위': [round(float(np.percentile(rates, q)), 2) for q in (25, 50, 75)],
 'F0_중앙값_Hz': round(float(np.median(f0s)), 1) if f0s else None,
 '문장끝_하강_%': round(float(np.median(decl)), 1) if decl else None,
}
print('RESULT ' + json.dumps(out, ensure_ascii=False))
