"""KsponSpeech(자유 대화) 흡수 — 개별 flac 6,000개. parquet 이 아니라고 빼지 않는다.

이게 중요한 이유: 지금 가진 데이터는 전부 낭독체다. 강의 음성의 억양은 낭독보다 대화에 가깝다.
비어 있던 문체를 메우는 유일한 열린 코퍼스다(CC BY 4.0).

받고→분석하고→지운다. 한 번에 몇 개만 디스크에 둔다.
"""
import os, sys, json, re, io, collections, subprocess
import numpy as np, soundfile as sf

R = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'
sys.path.insert(0, R)
from extract_feats import _split, _f0  # noqa: E402

BASE = 'https://huggingface.co/datasets/yfyeung/ksponspeech-eval/resolve/main'
SPLIT = os.environ.get('SPLIT', 'eval_clean')
N = int(os.environ.get('N', '600'))
OFF = int(os.environ.get('OFF', '0'))

END = [('요', r'요$'), ('다/습니다', r'(습니다|ㅂ니다|다)$'), ('까/의문', r'(까|나요|죠|가요)\??$'),
       ('고/연결', r'(고|며|면서|는데|지만|어서|아서)$'), ('조사_은는이가', r'(은|는|이|가)$'),
       ('조사_을를', r'(을|를)$'), ('조사_에로', r'(에|에서|으로|로|와|과)$')]


def ending_of(w):
    for n, p in END:
        if re.search(p, w):
            return n
    return '기타'


def clean(t):
    """KsponSpeech 전사 표기 정리 — (철자)/(발음) 중 철자 쪽, 잡음 표기 제거."""
    t = re.sub(r'\(([^)/]*)\)/\(([^)/]*)\)', r'\1', t)
    t = re.sub(r'[+*/@#$%^&()\[\]]', ' ', t)
    t = re.sub(r'\b[ubnl]/\b', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


trn = subprocess.run(['curl', '-sL', f'{BASE}/{SPLIT}.trn'], capture_output=True, text=True).stdout
pairs = []
for line in trn.splitlines():
    if '::' in line:
        p, t = line.split('::', 1)
    elif ' :: ' in line:
        p, t = line.split(' :: ', 1)
    else:
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        p, t = parts
    p = p.strip(); t = clean(t)
    # 전사에는 .pcm 으로 적혀 있고 저장소에는 .flac 으로 올라와 있다 — 이름만 바꿔 맞춘다
    if p.endswith(('.pcm', '.flac', '.wav')) and t:
        name = p.split('/')[-1].rsplit('.', 1)[0] + '.flac'
        pairs.append((f'{SPLIT}/{name}', t))
print(f'[전사] {len(pairs)}건 · 이번 처리 {OFF}~{OFF+N}', flush=True)

grid = collections.defaultdict(list); shape = collections.defaultdict(list); nw = 0
tmp = f'{R}/corpus/k_{SPLIT}_{OFF}.flac'
done = 0
for p, txt in pairs[OFF:OFF + N]:
    try:
        name = p.split('/')[-1]
        url = f'{BASE}/{SPLIT}/{name}'
        if subprocess.run(['curl', '-sL', '-o', tmp, url]).returncode != 0:
            continue
        y, sr = sf.read(tmp, dtype='float32')
        os.remove(tmp)
        if y.ndim > 1:
            y = y.mean(1)
        words = txt.split()
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
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        continue

print('PART ' + json.dumps({
    'nw': nw, 'utt': done, 'src': f'kspon/{SPLIT}',
    'shape': {k: [list(map(float, np.mean(np.array(v), 0))), len(v)] for k, v in shape.items()},
    'grid': {f'{k[0]}|{k[1]}': [list(map(float, np.mean(np.array(v), 0))), len(v)]
             for k, v in grid.items() if len(v) >= 8}}, ensure_ascii=False), flush=True)
