"""이미 만든 음성에서 문장 조각을 회수해, 쉼 패턴만 바꿔 여러 벌로 재조립한다.
합성 0회. 문장 오디오가 완전히 동일하므로 차이는 오직 쉼에서만 나온다 — 통제 실험."""
import sys, numpy as np, soundfile as sf
sys.path.insert(0, '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a')
import pink

y, sr = sf.read('f2_fixed.wav', dtype='float32')
if y.ndim > 1: y = y.mean(1)

# 480ms 고정 무음으로 이어붙인 파일이므로, 400ms 이상 연속 무음을 경계로 삼는다
amp = np.abs(y)
win = int(sr * 0.02)
env = np.convolve(amp, np.ones(win)/win, mode='same')
thr = env.max() * 0.004
quiet = env < thr
edges, run, start = [], 0, 0
for i, q in enumerate(quiet):
    if q:
        if run == 0: start = i
        run += 1
    else:
        if run >= int(sr*0.40): edges.append((start, i))
        run = 0
if run >= int(sr*0.40): edges.append((start, len(quiet)))

segs, prev = [], 0
for a, b in edges:
    if a - prev > int(sr*0.3): segs.append(y[prev:a])
    prev = b
if len(y) - prev > int(sr*0.3): segs.append(y[prev:])
print(f'[회수] 문장 조각 {len(segs)}개 · 길이(초) {[round(len(s)/sr,2) for s in segs]}', flush=True)

n = len(segs) - 1                      # 조각 사이 쉼 개수
rng = np.random.default_rng(11)
MU, SIG = 4.651, 0.735                 # 실측 로그정규 (Zeroth-Korean 12,424 표본)
base = 480.0
plans = {
  'P1_고정480':   [480]*n,
  'P2_1f요동':    [max(60, int(480*(1+0.22*v))) for v in pink.pink(n, seed=7)],
  'P3_백색요동':  [max(60, int(480*(1+0.22*v))) for v in rng.standard_normal(n)],
  'P4_실측분포':  [int(np.clip(rng.lognormal(MU, SIG), 48, 900)) for _ in range(n)],
  'P5_실측분포_긴판': [int(np.clip(rng.lognormal(MU+0.55, SIG), 80, 1200)) for _ in range(n)],
}
for name, ps in plans.items():
    parts = []
    for i, s in enumerate(segs):
        parts.append(s)
        if i < n: parts.append(np.zeros(int(sr*ps[i]/1000), dtype=np.float32))
    out = np.concatenate(parts)
    sf.write(f'{name}.wav', out, sr)
    print(f'{name}: 쉼 {ps} · 총 {len(out)/sr:.1f}s', flush=True)
