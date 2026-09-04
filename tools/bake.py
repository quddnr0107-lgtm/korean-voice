#!/usr/bin/env python3
"""공개 러너용 굽기 — 조각 목록의 내 몫(shard)을 Supertonic 3 로 굽고 워커(/bake/put)에 GitHub OIDC 로 올린다 (2026-09-04).

  python3 tools/bake.py --chunks chunks.json --shard 0 --shards 20 [--base https://korean-voice…workers.dev] [--limit N] [--no-upload]

- 합성·다듬기는 server/server.py 그대로다(같은 파일 · SUPERTONIC_DIR 환경변수).
  배치(8개)로 부른다 — 한 번에 부르면 조각당 약 1.4배 빠르다(실측 2.7 → 1.9초). 속도(speed)가 배치 안에서 하나라
  같은 (r, 문장끝) 끼리 묶고 길이순으로 정렬한다(패딩 낭비를 줄인다).
- 이미 R2 에 있는 조각은 /bake/has 로 물어 건너뛴다. 올리기는 PUT /bake/put?v&s&r&t (본문 mp3 · X-TTS-Recipe).
- OIDC 토큰은 ACTIONS_ID_TOKEN_REQUEST_URL 에서 audience=korean-voice-bake 로 받고 4분마다 새로 받는다.
"""
import argparse, io, json, os, subprocess, sys, time, urllib.request, urllib.parse, urllib.error
import numpy as np, soundfile as sf

ap = argparse.ArgumentParser()
ap.add_argument('--chunks', required=True); ap.add_argument('--shard', type=int, default=0); ap.add_argument('--shards', type=int, default=1)
ap.add_argument('--base', default='https://korean-voice.quddnr0107.workers.dev'); ap.add_argument('--voice', default='female'); ap.add_argument('--steps', type=int, default=16)
ap.add_argument('--batch', type=int, default=8); ap.add_argument('--limit', type=int, default=0); ap.add_argument('--no-upload', action='store_true')
ap.add_argument('--start', type=int, default=0, help='목록의 이 번호부터(시험용 · 앞쪽은 컨테이너 대기열이 이미 구웠을 수 있다)')
a = ap.parse_args()

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('SUPERTONIC_DIR', os.path.join(HERE, '..', 'supertonic3'))
os.environ.setdefault('CACHE_DIR', os.path.join(HERE, '..', 'cache'))
sys.path.insert(0, os.path.join(HERE, '..', 'server'))
import server  # noqa: E402  (server/server.py · voice_shape.py 는 같은 디렉터리)
import helper  # noqa: E402
VS = server.VS

items = json.load(open(a.chunks, encoding='utf-8'))
mine = [it for i, it in enumerate(items) if i >= a.start and (i - a.start) % a.shards == a.shard]
if a.limit: mine = mine[:a.limit]
print(f'조각 전체 {len(items)} · 내 몫(shard {a.shard}/{a.shards}) {len(mine)}', flush=True)

UA = 'korean-voice-bake/1 (+https://github.com/quddnr0107-lgtm/korean-voice)'   # 🔴 기본 Python-urllib UA 는 Cloudflare 엣지가 403 으로 막는다(1회차 실측)
def http(method, path, body=None, headers=None, raw=False):
    req = urllib.request.Request(a.base + path, data=body, method=method, headers={'User-Agent': UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as res:
        data = res.read()
        return data if raw else json.loads(data.decode('utf-8') or '{}')

# 이미 있는 것 건너뛰기
todo = []
for i in range(0, len(mine), 400):
    part = mine[i:i + 400]
    try:
        j = http('POST', '/bake/has', json.dumps({'v': a.voice, 's': a.steps, 'items': part}).encode(), {'Content-Type': 'application/json'})
        has = j.get('has') or [False] * len(part)
    except Exception as e:
        print('has 실패(전부 굽는다):', str(e)[:100]); has = [False] * len(part)
    todo += [it for it, h in zip(part, has) if not h]
print(f'이미 R2 에 있음 {len(mine) - len(todo)} · 구울 것 {len(todo)}', flush=True)

_tok = {'v': None, 'at': 0}
def oidc():
    if _tok['v'] and time.time() - _tok['at'] < 240: return _tok['v']
    url = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_URL'); bearer = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN')
    if not url: return ''
    req = urllib.request.Request(url + '&audience=korean-voice-bake', headers={'Authorization': 'bearer ' + bearer, 'Accept': 'application/json; api-version=2.0'})
    with urllib.request.urlopen(req, timeout=30) as res:
        _tok['v'] = json.loads(res.read().decode())['value']; _tok['at'] = time.time()
    return _tok['v']

def upload(it, mp3):
    q = urllib.parse.urlencode({'v': a.voice, 's': a.steps, 'r': server.fmt_r(it['r']), 't': it['t']})
    for k in range(4):
        try:
            j = http('PUT', '/bake/put?' + q, mp3, {'Authorization': 'Bearer ' + oidc(), 'Content-Type': 'audio/mpeg', 'X-TTS-Recipe': VS.RECIPE_TAG})
            if j.get('ok'): return True
            print('put 거절:', json.dumps(j, ensure_ascii=False)[:200])
            if j.get('error', '').startswith('oidc_') or j.get('error') == 'recipe_mismatch': return False
        except urllib.error.HTTPError as e:
            print('put 실패:', e.code, (e.read() or b'')[:200].decode('utf-8', 'replace'))
        except Exception as e:
            print('put 실패:', str(e)[:120])
        time.sleep(5 * (k + 1))
    return False

if not todo:
    print('할 것이 없다'); sys.exit(0)
t0 = time.time(); server.load(); print(f'모델 로드 {time.time() - t0:.1f}s', flush=True)
sr = server._tts.sample_rate; style = server._styles[a.voice]
ff = server.ffmpeg()

# 같은 속도끼리 묶는다: speed = 1.05 * r * 완급(hard)
groups = {}
for it in todo:
    hard = VS.is_sentence_end(it['t']); r = server.parse_r(it['r'])
    speed = float(np.clip(server.VOICES[a.voice]['speed'] * r * VS.unit_speed_mult(0, hard), server.R_MIN, server.R_MAX))
    groups.setdefault((round(speed, 3), hard), []).append(it)
done = fail = 0; t0 = time.time()
for (speed, hard), lst in groups.items():
    lst.sort(key=lambda it: len(it['t']))
    for i in range(0, len(lst), a.batch):
        part = lst[i:i + a.batch]
        texts = [server.clean_text(it['t']) for it in part]
        st = helper.Style(np.repeat(style.ttl, len(texts), axis=0), np.repeat(style.dp, len(texts), axis=0))
        with server._lock:
            wavs, _ = server._tts._infer(texts, ['ko'] * len(texts), st, a.steps, speed)
        wavs = np.asarray(wavs, dtype=np.float32)
        for it, t, w in zip(part, texts, wavs):
            try:
                y = server.shape(w.reshape(-1), sr, t, hard)
                tmp = os.path.join(os.environ['CACHE_DIR'], f'b{a.shard}.tmp.wav'); os.makedirs(os.environ['CACHE_DIR'], exist_ok=True)
                sf.write(tmp, y, sr)
                mp3 = subprocess.run([ff, '-v', 'error', '-i', tmp, '-ar', '24000', '-codec:a', 'libmp3lame', '-b:a', '48k', '-f', 'mp3', 'pipe:1'], check=True, capture_output=True).stdout
                if a.no_upload or upload(it, mp3): done += 1
                else: fail += 1
            except Exception as e:
                fail += 1; print('조각 실패:', t[:30], str(e)[:120])
        n = done + fail
        if n % 40 < len(part):
            el = time.time() - t0; print(f'[{n}/{len(todo)}] 구움 {done} · 실패 {fail} · 조각당 {el / max(1, n):.2f}s · 남은 약 {(len(todo) - n) * el / max(1, n) / 60:.0f}분', flush=True)
print(f'끝 — 구움 {done} · 실패 {fail} · {(time.time() - t0) / 60:.1f}분', flush=True)
sys.exit(1 if fail and not done else 0)
