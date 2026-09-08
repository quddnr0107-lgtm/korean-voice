"""tar 로 묶인 코퍼스 흡수 — Common Voice(CC0) · FLEURS(CC BY 4.0).

「API 로 안 열린다」며 넘어갔던 것들이다. 공식 저장소가 막혔을 뿐 열린 미러가 있었다.
포기하지 않으면 대개 길이 있다.

tar 를 통째로 풀지 않는다 — 스트림으로 열어 필요한 만큼만 꺼내 분석하고 버린다.
"""
import os, sys, io, json, re, csv, tarfile, collections, subprocess
import numpy as np, soundfile as sf

R = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'
sys.path.insert(0, R)
from extract_feats import _split, _f0  # noqa: E402

SRC = os.environ.get('SRC', 'cv')          # cv | fleurs
N = int(os.environ.get('N', '400'))
PART = int(os.environ.get('PART', '0'))

CV = 'https://huggingface.co/datasets/fsicoli/common_voice_19_0/resolve/main'
FL = 'https://huggingface.co/datasets/google/fleurs/resolve/main/data/ko_kr'

END = [('요', r'요$'), ('다/습니다', r'(습니다|ㅂ니다|다)$'), ('까/의문', r'(까|나요|죠|가요)\??$'),
       ('고/연결', r'(고|며|면서|는데|지만|어서|아서)$'), ('조사_은는이가', r'(은|는|이|가)$'),
       ('조사_을를', r'(을|를)$'), ('조사_에로', r'(에|에서|으로|로|와|과)$')]


def ending_of(w):
    for n, p in END:
        if re.search(p, w):
            return n
    return '기타'


def transcripts():
    """파일이름 → 전사문."""
    m = {}
    if SRC == 'cv':
        for split in ('train', 'test', 'dev'):
            t = subprocess.run(['curl', '-sL', f'{CV}/transcript/ko/{split}.tsv'],
                               capture_output=True, text=True).stdout
            for row in csv.DictReader(io.StringIO(t), delimiter='\t'):
                p = row.get('path') or ''
                s = (row.get('sentence') or '').strip()
                if p and s:
                    m[os.path.basename(p)] = s
    else:
        for split in ('train', 'dev', 'test'):
            t = subprocess.run(['curl', '-sL', f'{FL}/{split}.tsv'], capture_output=True, text=True).stdout
            for line in t.splitlines():
                c = line.split('\t')
                if len(c) >= 3 and c[1].endswith('.wav'):
                    m[c[1]] = c[2].strip()
    return m


def tar_urls():
    if SRC == 'cv':
        return [f'{CV}/audio/ko/train/ko_train_{i}.tar' for i in range(0, 3)] + \
               [f'{CV}/audio/ko/test/ko_test_0.tar', f'{CV}/audio/ko/dev/ko_dev_0.tar']
    return [f'{FL}/audio/train.tar.gz', f'{FL}/audio/dev.tar.gz', f'{FL}/audio/test.tar.gz']


TX = transcripts()
print(f'[전사] {len(TX)}건 ({SRC})', flush=True)
urls = tar_urls()
url = urls[PART % len(urls)]
tmp = f'{R}/corpus/t_{SRC}_{PART}.tar'
print(f'[받는 중] {url.split("/")[-1]}', flush=True)
if subprocess.run(['curl', '-sL', '-o', tmp, url]).returncode != 0:
    sys.exit('받기 실패')

grid = collections.defaultdict(list); shape = collections.defaultdict(list)
nw = done = 0
mode = 'r:gz' if url.endswith('.gz') else 'r'
try:
    with tarfile.open(tmp, mode) as tf:
        for mem in tf:
            if done >= N:
                break
            if not mem.isfile() or not mem.name.lower().endswith(('.mp3', '.wav', '.flac')):
                continue
            base = os.path.basename(mem.name)
            txt = TX.get(base) or TX.get(base.rsplit('.', 1)[0] + '.mp3') or TX.get(base.rsplit('.', 1)[0] + '.wav')
            if not txt:
                continue
            try:
                data = tf.extractfile(mem).read()
                y, sr = sf.read(io.BytesIO(data), dtype='float32')
            except Exception:
                continue
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
finally:
    if os.path.exists(tmp):
        os.remove(tmp)

print('PART ' + json.dumps({
    'nw': nw, 'utt': done, 'src': f'{SRC}/{PART}',
    'shape': {k: [list(map(float, np.mean(np.array(v), 0))), len(v)] for k, v in shape.items()},
    'grid': {f'{k[0]}|{k[1]}': [list(map(float, np.mean(np.array(v), 0))), len(v)]
             for k, v in grid.items() if len(v) >= 8}}, ensure_ascii=False), flush=True)
