"""YODAS 흡수 — 유튜브 CC-BY 영상만 모은 공개 데이터셋(ESPnet · CC BY 3.0).

사용자 요청의 정확한 해법: 유튜브 음성이되 무단이 아니다.
업로더가 재사용을 명시 허락한 영상만 골라 모은 것이고, 유튜브가 아니라 Hugging Face 에서 받는다
(약관 문제 없음). 우리는 여기서도 **운율 통계만** 뽑고 음성은 저장하지 않는다.

이 데이터가 메우는 자리: 지금 가진 건 낭독(코퍼스)과 자유대화(KsponSpeech)뿐이고
**강의·설명체**가 없다. 유튜브 화자들이 바로 그 문체다.
"""
import os, sys, io, json, re, tarfile, collections, subprocess
import numpy as np, soundfile as sf

R = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'
sys.path.insert(0, R)
from extract_feats import _split, _f0  # noqa: E402

BASE = 'https://huggingface.co/datasets/espnet/yodas/resolve/main/data'
SUB = os.environ.get('SUB', 'ko000')
SHARD = os.environ.get('SHARD', '00000000')
N = int(os.environ.get('N', '400'))

END = [('요', r'요$'), ('다/습니다', r'(습니다|ㅂ니다|다)$'), ('까/의문', r'(까|나요|죠|가요)\??$'),
       ('고/연결', r'(고|며|면서|는데|지만|어서|아서)$'), ('조사_은는이가', r'(은|는|이|가)$'),
       ('조사_을를', r'(을|를)$'), ('조사_에로', r'(에|에서|으로|로|와|과)$')]


def ending_of(w):
    for n, p in END:
        if re.search(p, w):
            return n
    return '기타'


txt = subprocess.run(['curl', '-sL', f'{BASE}/{SUB}/text/{SHARD}.txt'], capture_output=True, text=True).stdout
TX = {}
for line in txt.splitlines():
    parts = line.split(None, 1)
    if len(parts) == 2 and parts[1].strip():
        TX[parts[0]] = parts[1].strip()
print(f'[전사] {len(TX)}건 · {SUB}/{SHARD}', flush=True)

tmp = f'{R}/corpus/y_{SUB}_{SHARD}.tar.gz'
print('[받는 중] audio tar.gz', flush=True)
if subprocess.run(['curl', '-sL', '-o', tmp, f'{BASE}/{SUB}/audio/{SHARD}.tar.gz']).returncode != 0:
    sys.exit('받기 실패')

grid = collections.defaultdict(list); shape = collections.defaultdict(list)
nw = done = 0
try:
    with tarfile.open(tmp, 'r:gz') as tf:
        for mem in tf:
            if done >= N:
                break
            if not mem.isfile() or not mem.name.lower().endswith(('.wav', '.flac', '.mp3', '.opus')):
                continue
            uid = os.path.basename(mem.name).rsplit('.', 1)[0]
            t2 = TX.get(uid)
            if not t2:
                continue
            try:
                y, sr = sf.read(io.BytesIO(tf.extractfile(mem).read()), dtype='float32')
            except Exception:
                continue
            if y.ndim > 1:
                y = y.mean(1)
            words = t2.split()
            if len(words) < 3 or len(y) / sr < 1.2:
                continue
            iv = [(a, b) for a, b in _split(y, sr, top_db=28) if (b - a) / sr >= 0.12]
            if len(iv) < 3:
                continue
            t, f0 = _f0(y, sr)
            v = f0[~np.isnan(f0)]
            if len(v) < 20:
                continue
            med = float(np.median(v))
            for k, (a, b) in enumerate(iv):
                m = (t >= a / sr) & (t < b / sr) & ~np.isnan(f0)
                seg = f0[m]
                if len(seg) < 5:
                    continue
                st = 12 * np.log2(seg / med)
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
            done += 1
            if done % 100 == 0:
                print(f'  {done}건 · 어절 {nw}', flush=True)
finally:
    if os.path.exists(tmp):
        os.remove(tmp)

print('PART ' + json.dumps({
    'nw': nw, 'utt': done, 'src': f'yodas/{SUB}/{SHARD}',
    'shape': {k: [list(map(float, np.mean(np.array(v), 0))), len(v)] for k, v in shape.items()},
    'grid': {f'{k[0]}|{k[1]}': [list(map(float, np.mean(np.array(v), 0))), len(v)]
             for k, v in grid.items() if len(v) >= 8}}, ensure_ascii=False), flush=True)
