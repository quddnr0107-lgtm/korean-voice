"""어절·어미별 억양 궤적 — 음성인식 없이. 전사문이 이미 정답이므로 정렬만 에너지로 한다.
어절 경계 = 유성 구간 분할. 첫·끝 어절은 전사문에서 바로 읽는다(어미 분류에 필요한 건 그 둘이다).
반음 정규화로 음색을 제거하고 억양 모양만 남긴다."""
import sys, io, json, re, collections
import numpy as np, pyarrow.parquet as pq, soundfile as sf, librosa

SHARD = sys.argv[1]; N = int(sys.argv[2]); OFF = int(sys.argv[3]) if len(sys.argv) > 3 else 0
tbl = pq.read_table(SHARD); ac, tc = tbl.column('audio'), tbl.column('text')
END = [('요', r'요$'), ('다/습니다', r'(습니다|ㅂ니다|다)$'), ('까/의문', r'(까|나요|죠|가요)\??$'),
       ('고/연결', r'(고|며|면서|는데|지만|어서|아서)$'), ('조사_은는이가', r'(은|는|이|가)$'),
       ('조사_을를', r'(을|를)$'), ('조사_에로', r'(에|에서|으로|로|와|과)$')]


def ending_of(w):
    for n, p in END:
        if re.search(p, w):
            return n
    return '기타'


grid = collections.defaultdict(list); shape = collections.defaultdict(list); nw = 0
for i in range(OFF, min(OFF + N, tbl.num_rows)):
    try:
        a = ac[i].as_py(); txt = tc[i].as_py().strip()
        words = txt.split()
        if len(words) < 3:
            continue
        y, sr = sf.read(io.BytesIO(a['bytes']), dtype='float32')
        if y.ndim > 1:
            y = y.mean(1)
        iv = librosa.effects.split(y, top_db=28, frame_length=1024, hop_length=256)
        iv = [(s, e) for s, e in iv if (e - s) / sr >= 0.12]
        if len(iv) < 3:
            continue
        f0, _, _ = librosa.pyin(y, fmin=70, fmax=400, sr=sr, frame_length=1024, hop_length=256)
        t = librosa.times_like(f0, sr=sr, hop_length=256)
        v = f0[~np.isnan(f0)]
        if len(v) < 20:
            continue
        med = float(np.median(v))
        for k, (s, e) in enumerate(iv):
            m = (t >= s / sr) & (t < e / sr) & ~np.isnan(f0)
            seg = f0[m]
            if len(seg) < 5:
                continue
            st = 12 * np.log2(seg / med)                 # 화자 중앙값 대비 반음 — 음색이 사라진다
            pts = [float(np.mean(x)) for x in np.array_split(st, 5)]
            if k == 0:
                pos, w = 'A문두', words[0]
            elif k == len(iv) - 1:
                pos, w = 'C문말', words[-1]
            else:
                pos, w = 'B문중', None
            shape[pos].append(pts); nw += 1
            if w:
                grid[(pos, ending_of(w))].append(pts)
    except Exception:
        continue

print('PART ' + json.dumps({
    'nw': nw,
    'shape': {k: [list(map(float, np.mean(np.array(v), 0))), len(v)] for k, v in shape.items()},
    'grid': {f'{k[0]}|{k[1]}': [list(map(float, np.mean(np.array(v), 0))), len(v)]
             for k, v in grid.items() if len(v) >= 8},
}, ensure_ascii=False), flush=True)
