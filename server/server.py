#!/usr/bin/env python3
"""즉시 합성 서버 — 문장 단위로 Supertonic 3 를 돌려 mp3 를 돌려주고 캐시한다. (굽기 없음 · 파일은 캐시일 뿐)

    GET /health                       → {"ok":true,"voices":[…],"cached":N}
    GET /tts?v=female&t=<문장>[&s=16][&r=1.0]  → audio/mpeg  (Cache-Control 1년 · CORS * · Range 지원(iOS))
    GET /voices                       → 목소리 목록
    POST /render {v,items:[{t,r,pause}]} → audio/mpeg  (대본 전체를 잇고 계획된 쉼을 넣은 하나의 파일)

설계
- 텍스트 정규화·문장 나누기·쉼은 **클라이언트**(ko-voice.js)가 한다. 서버는 받은 문장을 그대로 읽는다.
  캐시 키 = 목소리|단계|속도배수|조합표식|문장. 속도(playbackRate)는 브라우저가 바꾸고, r 은 **합성 속도**(조각별 완급)다.
- 🔴 다듬기(voice_shape.py · 사람이 표본을 듣고 정한 「U4」)를 합성 뒤에 건다 — 굽는 자(yebijun build_tts_supertonic.py)와
  **같은 파일**을 읽는다(R31). 문장 끝 조각(hard)은 글자로 판정한다(`is_sentence_end`) — 서버는 운율 계획을 못 본다.
  조합이 바뀌면 voice_shape.RECIPE_TAG 를 올린다 → 캐시 키가 갈려 옛 소리가 다시 안 나온다.
- 한 문장(≤200자)은 4코어 CPU 에서 0.5~1.5초. 첫 청취자만 기다리고 그 뒤는 캐시(디스크) → CDN 에도 캐시된다.
- 합성은 직렬(잠금) — ORT 가 코어를 다 쓰므로 병렬은 이득이 없다. 대기열은 HTTP 스레드가 잠금 앞에 선다.

환경변수: SUPERTONIC_DIR(onnx/·voice_styles/·py/helper.py) · CACHE_DIR(기본 ./cache) · PORT(기본 8790)
          STEPS(기본 16) · ALLOW_ORIGIN(기본 *) · MAX_CHARS(기본 400)
"""
import hashlib, json, os, re, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.abspath(__file__))
ST_DIR = os.environ.get('SUPERTONIC_DIR', os.path.join(ROOT, 'supertonic3'))
CACHE = os.environ.get('CACHE_DIR', os.path.join(ROOT, 'cache'))
PORT = int(os.environ.get('PORT', '8790'))
STEPS = int(os.environ.get('STEPS', '16'))
ORIGIN = os.environ.get('ALLOW_ORIGIN', '*')
MAX_CHARS = int(os.environ.get('MAX_CHARS', '400'))
sys.path.insert(0, ROOT)
from singleflight import KeyedSingleflight  # noqa: E402
import voice_shape as VS  # noqa: E402  — 지금 조합(기본)
import recipes as RC       # noqa: E402  — 조합 등록부: 목소리 두 벌을 동시에 내준다(2026-09-08)
VOICES = {
    'female': {'style': None, 'label': '여성', 'speed': 1.05},   # None = 그 조합이 정한 목소리(조합마다 다르다)
    # 🔴 2026-09-14: 하은 하나만 남긴다. 굽기는 2026-09-04 에 voice=female 로 딱 한 번 성공했고
    #    male 도 원본 10개도 구워진 적이 없다 — 안 구운 목소리는 들을 때마다 합성을 기다려야 했다.
    #    캐시 키에 목소리 이름이 들어가므로 빼도 하은의 구운 조각은 그대로다.
}


def style_spec(voice, tag):
    return VOICES[voice]['style'] or RC.get(tag).RECIPE['style']
R_MIN, R_MAX = 0.7, 1.6   # 합성 속도 배수 r 의 범위(모델이 받는 speed 의 안전 범위)

sys.path.insert(0, os.path.join(ST_DIR, 'py'))
import helper  # noqa: E402

_lock = threading.Lock()
_tts = None
_styles = {}
_stats = {'synth': 0, 'hit': 0, 'dedup': 0, 'synth_s': 0.0}
_singleflight = KeyedSingleflight()


def load():
    global _tts
    _style_cache = {}
    _tts = helper.load_text_to_speech(os.path.join(ST_DIR, 'onnx'), False)
    for tag in RC.TAGS:
     for name, v in VOICES.items():
        ttl = dp = None
        spec = style_spec(name, tag)
        # 🔴 「F2」처럼 가중치가 없는 이름도 받는다 — 없으면 1.0. 이게 없어서 순수 목소리로 바꾸자마자
        #    load() 가 ValueError 로 죽었다(2026-09-08 · 굽기 전 실제 경로 점검에서 잡음).
        parts = []
        for item in spec.split(','):
            n, _, ws = item.partition(':')
            parts.append((n.strip(), float(ws) if ws.strip() else 1.0))
        total = sum(w for _, w in parts) or 1.0
        for n, w in parts:
            w = w / total                                  # 가중치 합을 1 로 정규화(load_blend 와 같다)
            if n not in _style_cache:                      # 목소리 12개 × 벌 2개 = 같은 파일을 수십 번 읽게 된다
                _style_cache[n] = helper.load_voice_style([os.path.join(ST_DIR, 'voice_styles', n + '.json')])
            st = _style_cache[n]
            ttl = st.ttl * w if ttl is None else ttl + st.ttl * w
            dp = st.dp * w if dp is None else dp + st.dp * w
        _styles[(tag, name)] = helper.Style(ttl.astype(np.float32), dp.astype(np.float32))
    os.makedirs(CACHE, exist_ok=True)
    for v in VOICES:
        d = os.path.join(CACHE, v); os.makedirs(d, exist_ok=True)
        for f in os.listdir(d):   # 죽었을 때 남은 임시 파일 청소
            if f.endswith('.tmp.wav') or f.endswith('.part'):
                try: os.remove(os.path.join(d, f))
                except OSError: pass
    threading.Thread(target=_warm_worker, daemon=True).start()


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def trim(wav, sr, thresh_db=-45.0, pad_ms=30):
    frame = int(sr * 0.01); n = len(wav) // frame
    if n < 3:
        return wav
    rms = np.sqrt((wav[:n * frame].reshape(n, frame) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9); idx = np.where(db > db.max() + thresh_db)[0]
    if not len(idx):
        return wav
    pad = int(sr * pad_ms / 1000)
    return wav[max(0, idx[0] * frame - pad):min(len(wav), (idx[-1] + 1) * frame + pad)]


def shape(w, sr, text, hard, tag=None):
    return RC.get(tag).shape(w, sr, text, hard)


def _shape_옛(w, sr, text, hard):
    """합성 뒤 다듬기 — 굽는 자와 같은 순서: trim(앞여유 50ms) → 페이드 → 첫 음절 보강 → Praat PSOLA 억양(문장 끝 올림)."""
    w = trim(np.asarray(w, dtype=np.float32).reshape(-1), sr, pad_ms=VS.RECIPE['lead_pad_ms'])
    k = min(len(w) // 2, int(sr * 0.01))
    if k > 0:
        ramp = np.linspace(0, 1, k, dtype=np.float32); w[:k] *= ramp; w[-k:] *= ramp[::-1]
    w = VS.onset_boost(w, sr)
    # 🔴 사용자가 옛 u4a 소리를 골랐다(2026-09-08) — 궤적 다듬기(k1·k2)를 걷고 u4a 의 Praat PSOLA 로 되돌린다.
    #    「praat_shape 이 기계음의 원인」이라던 앞선 기록은 이번에 재보니 HNR 로 재현되지 않았다(15.35 vs 15.47 · 회차 편차 0.8dB).
    w = VS.praat_shape(w, sr, text, hard)
    w = VS.tail_trim(w, sr)          # 말 끝난 뒤 죽은 공백 잘라내기(제보: 「한 어절이 묵음」)
    return w / (np.abs(w).max() or 1.0) * 0.89


def synthesize(voice, text, steps, r=1.0, tag=None):
    import subprocess
    t0 = time.time()
    M = RC.get(tag); tag = M.RECIPE_TAG
    hard = M.is_sentence_end(text)
    speed = float(np.clip(VOICES[voice]['speed'] * r * M.unit_speed_mult(0, hard), R_MIN, R_MAX))   # 완급: 문장 끝 ×0.92 · 중간 ×1.06(U4)
    with _lock:
        wav, dur = _tts._infer([text], ['ko'], _styles[(tag, voice)], steps, speed)
    w = np.asarray(wav, dtype=np.float32).reshape(-1)[:int(float(np.asarray(dur).reshape(-1)[0]) * _tts.sample_rate)]   # 예측 길이로 자른다(배치 패딩 우웅 · L280 · 러너 bake.py 와 같은 자)
    w = M.shape(w, _tts.sample_rate, text, hard)
    key = cache_key(voice, text, steps, r, tag)
    tmp = os.path.join(CACHE, voice, key + '.tmp.wav'); out = os.path.join(CACHE, voice, key + '.mp3')
    sf.write(tmp, w, _tts.sample_rate)
    subprocess.run([ffmpeg(), '-v', 'error', '-y', '-i', tmp, '-ar', '24000', '-codec:a', 'libmp3lame', '-b:a', '48k', '-f', 'mp3', out + '.part'], check=True)
    os.replace(out + '.part', out); os.remove(tmp)
    _stats['synth'] += 1; _stats['synth_s'] += time.time() - t0
    return out


# 미리 굽기 대기열 — 클라이언트가 강의 시작 때 나머지 문장을 보내 두면 재생 중에 앞서 굽는다(첫 청취자만 기다린다).
import queue
_warm_q = queue.Queue()
_warm_set = set()


def _warm_worker():
    while True:
        voice, text, steps, r, tag = _warm_q.get()
        try:
            cached_or_synthesize(voice, text, steps, r, tag)
        except Exception as e:
            sys.stderr.write('warm 실패: %s\n' % str(e)[:200])
        finally:
            _warm_set.discard((voice, text, steps, r, tag)); _warm_q.task_done()


def warm(voice, texts, steps, r=1.0, tag=None):
    n = 0
    tag = RC.get(tag).RECIPE_TAG
    for t in texts:
        t = clean_text(t)
        if not t: continue
        k = (voice, t, steps, r, tag)
        if k in _warm_set: continue
        if os.path.exists(os.path.join(CACHE, voice, cache_key(voice, t, steps, r, tag) + '.mp3')): continue
        _warm_set.add(k); _warm_q.put(k); n += 1
    return n


# 낭독 전체 음량 — EBU R128(ITU-R BS.1770). 웹 재생이 쓰는 I=-16 LUFS · 트루피크 -1.5dB.
# 🔴 2-pass 다. 1-pass 는 **동적** 정규화라 구간마다 게인이 달라질 수 있고(= 강조·감정의 셈여림이 뭉개진다),
#    2-pass 는 측정값을 넣어 `linear=true` 로 **전체에 단일 게인**을 건다. 비용 차이는 25초당 0.26초뿐이다
#    (실측 2026-09-13: 1-pass 0.62s/-16.71 LUFS · 2-pass 0.88s/-16.48 LUFS · 구간 게인 편차 0.85 vs 0.91 LU —
#     이 길이의 표본으로는 두 방식을 가릴 수 없었다. 그래서 구조적으로 단일 게인이 보장되는 쪽을 쓴다).
LOUDNORM_TARGET = 'I=-16:TP=-1.5:LRA=11'
RENDER_MAX_ITEMS = 400
RENDER_MAX_CHARS = 20000
RENDER_MAX_PAUSE_MS = 3000


def silence_mp3(ms):
    """계획된 쉼 — 조각과 **같은 인코딩 설정**의 무음 mp3. 값의 종류가 몇 개뿐이라 디스크에 두고 다시 쓴다."""
    import subprocess
    d = os.path.join(CACHE, 'sil'); os.makedirs(d, exist_ok=True)
    out = os.path.join(d, f'{int(ms)}.mp3')
    if not os.path.exists(out):
        subprocess.run([ffmpeg(), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono',
                        '-t', f'{ms / 1000.0:.3f}', '-codec:a', 'libmp3lame', '-b:a', '48k', '-f', 'mp3', out + '.part'], check=True)
        os.replace(out + '.part', out)
    return out


def render(voice, items, steps, tag=None):
    """조각을 순서대로 합성해(캐시 우선) 계획된 쉼과 함께 하나의 mp3 로 잇는다.

    이어붙이기는 ffmpeg concat 디먹서 + **디코드 후 한 번 인코딩**이다. `-c copy` 는 쓰지 않는다 —
    조각마다 mp3 프레임(1152샘플 = 24kHz 에서 48ms) 패딩이 남아 **조각 수 × 약 48ms 만큼 길어진다**
    (실측 2026-09-13: 조각 10개 26초에서 +500ms). 재인코딩하면 길이 오차가 0 이고 25초당 0.6초면 된다.
    같은 패스에 loudnorm(EBU R128 · I=-16 LUFS · TP=-1.5dB)을 걸어 문장 간 음량도 고른다
    (조각 간 편차는 이미 1.1 LU 라 문장별 정규화는 하지 않는다 — 강조·감정의 셈여림을 뭉갠다).
    1-pass 로 -16.73 LUFS, 2-pass 로 -16.72 LUFS 라 2-pass 는 쓰지 않는다.

    조각 자체는 /tts 와 **같은 캐시**를 쓴다: 대본을 고쳐 다시 만들면 바뀐 문장만 새로 합성된다.
    """
    import subprocess, tempfile, uuid
    tag = RC.get(tag).RECIPE_TAG
    parts = []
    for it in items:
        text = clean_text(it.get('t'))
        if not text:
            continue
        r = parse_r(it.get('r', 1.0))
        parts.append(cached_or_synthesize(voice, text, steps, r, tag))
        pause = it.get('pause') or 0
        try:
            pause = max(0, min(RENDER_MAX_PAUSE_MS, int(pause)))
        except (TypeError, ValueError):
            pause = 0
        if pause >= 10:                       # 10ms 아래는 무음 파일을 만들 가치가 없다
            parts.append(silence_mp3(pause))
    if not parts:
        raise ValueError('empty_items')
    d = os.path.join(CACHE, 'render'); os.makedirs(d, exist_ok=True)
    out = os.path.join(d, uuid.uuid4().hex + '.mp3')
    with tempfile.NamedTemporaryFile('w', suffix='.txt', dir=d, delete=False, encoding='utf-8') as f:
        listfile = f.name
        for p in parts:
            f.write("file '" + p.replace("'", "'\\''") + "'\n")
    try:
        # 1차: 라우드니스 측정만(출력 없음)
        m = subprocess.run([ffmpeg(), '-v', 'info', '-f', 'concat', '-safe', '0', '-i', listfile,
                            '-af', 'loudnorm=' + LOUDNORM_TARGET + ':print_format=json', '-f', 'null', '-'],
                           capture_output=True, text=True)
        af = 'loudnorm=' + LOUDNORM_TARGET
        hit = re.search(r'\{[^{}]*"input_i"[\s\S]*?\}', m.stderr or '')
        if hit:
            try:
                st = json.loads(hit.group(0))
                af = ('loudnorm=' + LOUDNORM_TARGET +
                      ':measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s:linear=true'
                      % (st['input_i'], st['input_tp'], st['input_lra'], st['input_thresh']))
            except (ValueError, KeyError):
                pass                       # 측정을 못 읽으면 1-pass 로 떨어진다(소리는 나온다)
        # 2차: 이어붙이며 단일 게인 적용 → mp3 한 번만 인코딩
        subprocess.run([ffmpeg(), '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', listfile,
                        '-af', af, '-ar', '24000', '-ac', '1',
                        '-codec:a', 'libmp3lame', '-b:a', '48k', '-f', 'mp3', out], check=True)
    finally:
        try: os.remove(listfile)
        except OSError: pass
    return out


def cache_key(voice, text, steps, r=1.0, tag=None):
    # 🔴 worker.mjs 의 키와 같은 꼴(sha1("voice|steps|r|tag|text")) — r 은 소수 둘째 자리까지
    return hashlib.sha1(f'{voice}|{steps}|{fmt_r(r)}|{RC.get(tag).RECIPE_TAG}|{text}'.encode('utf-8')).hexdigest()


def cached_or_synthesize(voice, text, steps, r=1.0, tag=None):
    """Disk cache + keyed singleflight shared by live and warm requests."""
    tag = RC.get(tag).RECIPE_TAG
    key = cache_key(voice, text, steps, r, tag)
    path = os.path.join(CACHE, voice, key + '.mp3')

    def ready():
        return path if os.path.exists(path) else None

    def produce():
        return synthesize(voice, text, steps, r, tag)

    out, state = _singleflight.run(key, ready, produce)
    if state != 'owner':
        _stats['hit'] += 1
    if state == 'waiter':
        _stats['dedup'] += 1
    return out


def fmt_r(r):
    return f'{float(r):.2f}'


def parse_r(v):
    try:
        r = float(v)
    except (TypeError, ValueError):
        return 1.0
    if not np.isfinite(r):
        return 1.0
    return round(min(R_MAX, max(R_MIN, r)), 2)


def clean_text(t):
    t = re.sub(r'\s+', ' ', str(t or '')).strip()
    return t[:MAX_CHARS]


class H(BaseHTTPRequestHandler):
    server_version = 'korean-voice/0.1'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', ORIGIN)
        self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Range, Content-Type')
        self.send_header('Access-Control-Expose-Headers', 'Content-Length, Content-Range, Accept-Ranges')

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status); self._cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body))); self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.send_header('Access-Control-Max-Age', '86400'); self.end_headers()

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path not in ('/warm', '/render'):
            return self._json({'ok': False, 'error': 'not_found'}, 404)
        try:
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(min(n, 2000000)).decode('utf-8') or '{}')
        except Exception:
            return self._json({'ok': False, 'error': 'bad_json'}, 400)
        if u.path == '/render':
            return self._render(body)
        voice = body.get('v') or 'female'
        if voice not in VOICES:
            return self._json({'ok': False, 'error': 'bad_voice'}, 400)
        try:
            steps = max(4, min(32, int(body.get('s') or STEPS)))
        except (TypeError, ValueError):
            steps = STEPS
        texts = body.get('texts') or []
        if not isinstance(texts, list):
            return self._json({'ok': False, 'error': 'bad_texts'}, 400)
        r = parse_r(body.get('r', 1.0))
        tag = RC.get(body.get('k')).RECIPE_TAG
        return self._json({'ok': True, 'queued': warm(voice, texts[:400], steps, r, tag), 'queue': _warm_q.qsize()})

    def _render(self, body):
        """POST /render {v, s?, k?, items:[{t, r, pause}]} → audio/mpeg (대본 전체 · 계획된 쉼 포함)"""
        voice = body.get('v') or 'female'
        if voice not in VOICES:
            return self._json({'ok': False, 'error': 'bad_voice'}, 400)
        items = body.get('items') or []
        if not isinstance(items, list) or not items:
            return self._json({'ok': False, 'error': 'empty_items'}, 400)
        if len(items) > RENDER_MAX_ITEMS:
            return self._json({'ok': False, 'error': 'too_many_items'}, 400)
        chars = sum(len(clean_text(it.get('t') if isinstance(it, dict) else '')) for it in items)
        if chars > RENDER_MAX_CHARS:
            return self._json({'ok': False, 'error': 'too_many_chars', 'chars': chars}, 400)
        tag = RC.get(body.get('k')).RECIPE_TAG
        try:
            steps = max(4, min(32, int(body.get('s') or RC.steps_for(tag))))
        except (TypeError, ValueError):
            steps = RC.steps_for(tag)
        try:
            path = render(voice, [it for it in items if isinstance(it, dict)], steps, tag)
        except ValueError:
            return self._json({'ok': False, 'error': 'empty_items'}, 400)
        except Exception as e:
            return self._json({'ok': False, 'error': 'synthesis_failed', 'reason': str(e)[:300]}, 502)
        try:
            self._send_file(path, tag)
        finally:
            try: os.remove(path)      # 대본은 이용자의 것이다 — 서버에 남기지 않는다(조각 캐시만 남는다)
            except OSError: pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path); q = urllib.parse.parse_qs(u.query)
        if u.path == '/health':
            cached = sum(len([f for f in os.listdir(os.path.join(CACHE, v)) if f.endswith('.mp3')]) for v in VOICES)
            return self._json({'ok': True, 'voices': list(VOICES), 'cached': cached, 'steps': STEPS, 'recipe': RC.DEFAULT, 'recipes': {t: {'steps': v['steps'], 'style': v['mod'].RECIPE['style']} for t, v in RC.TAGS.items()}, 'queue': _warm_q.qsize(), 'stats': _stats})
        if u.path == '/voices':
            return self._json({k: {'label': v['label']} for k, v in VOICES.items()})
        if u.path != '/tts':
            return self._json({'ok': False, 'error': 'not_found'}, 404)
        voice = (q.get('v') or ['female'])[0]
        text = clean_text((q.get('t') or [''])[0])
        try:
            steps = max(4, min(32, int((q.get('s') or [STEPS])[0])))
        except ValueError:
            steps = STEPS
        r = parse_r((q.get('r') or ['1'])[0])
        tag = RC.get((q.get('k') or [''])[0]).RECIPE_TAG
        if not (q.get('s') or [''])[0]:
            steps = RC.steps_for(tag)   # 🔴 벌마다 스텝이 다르다 — 안 맞추면 그 벌을 통째로 못 찾는다
        if voice not in VOICES:
            return self._json({'ok': False, 'error': 'bad_voice'}, 400)
        if not text:
            return self._json({'ok': False, 'error': 'empty_text'}, 400)
        try:
            path = cached_or_synthesize(voice, text, steps, r, tag)
        except Exception as e:
            return self._json({'ok': False, 'error': 'synthesis_failed', 'reason': str(e)[:300]}, 502)
        self._send_file(path, tag)

    def _send_file(self, path, tag=None):
        size = os.path.getsize(path)
        rng = self.headers.get('Range')
        start, end = 0, size - 1
        status = 200
        if rng and rng.startswith('bytes='):
            try:
                a, b = rng[6:].split('-', 1)
                start = int(a) if a else max(0, size - int(b)); end = int(b) if (b and a) else size - 1
                end = min(end, size - 1); status = 206
            except ValueError:
                start, end, status = 0, size - 1, 200
        self.send_response(status); self._cors()
        self.send_header('Content-Type', 'audio/mpeg'); self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
        self.send_header('X-TTS-Recipe', RC.get(tag).RECIPE_TAG)   # 워커가 이 값이 자기 표식과 같을 때만 R2 에 넣는다(컨테이너가 옛 이미지면 캐시를 더럽히지 않는다)
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(end - start + 1)); self.end_headers()
        if self.command == 'HEAD':
            return
        with open(path, 'rb') as f:
            f.seek(start); remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk); remaining -= len(chunk)


def main():
    t0 = time.time(); load()
    print(f'모델 로드 {time.time()-t0:.1f}s · 목소리 {list(VOICES)} · 캐시 {CACHE} · :{PORT}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()


if __name__ == '__main__':
    main()
