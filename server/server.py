#!/usr/bin/env python3
"""즉시 합성 서버 — 문장 단위로 Supertonic 3 를 돌려 mp3 를 돌려주고 캐시한다. (굽기 없음 · 파일은 캐시일 뿐)

    GET /health                       → {"ok":true,"voices":[…],"cached":N}
    GET /tts?v=female&t=<문장>[&s=16]  → audio/mpeg  (Cache-Control 1년 · CORS * · Range 지원(iOS))
    GET /voices                       → 목소리 목록

설계
- 텍스트 정규화·문장 나누기·쉼은 **클라이언트**(ko-voice.js)가 한다. 서버는 받은 문장을 그대로 읽는다.
  그래서 캐시 키 = 목소리|문장|단계 뿐이고, 속도(playbackRate)는 브라우저가 바꾼다 → 캐시가 속도와 무관.
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
VOICES = {
    'female': {'style': 'F2:0.6,F3:0.4', 'label': '여성', 'speed': 1.05},
    'male': {'style': 'M1:0.7,M3:0.3', 'label': '남성', 'speed': 1.05},
}

sys.path.insert(0, os.path.join(ST_DIR, 'py'))
import helper  # noqa: E402

_lock = threading.Lock()
_tts = None
_styles = {}
_stats = {'synth': 0, 'hit': 0, 'synth_s': 0.0}


def load():
    global _tts
    _tts = helper.load_text_to_speech(os.path.join(ST_DIR, 'onnx'), False)
    for name, v in VOICES.items():
        ttl = dp = None
        for item in v['style'].split(','):
            n, w = item.split(':'); w = float(w)
            st = helper.load_voice_style([os.path.join(ST_DIR, 'voice_styles', n + '.json')])
            ttl = st.ttl * w if ttl is None else ttl + st.ttl * w
            dp = st.dp * w if dp is None else dp + st.dp * w
        _styles[name] = helper.Style(ttl.astype(np.float32), dp.astype(np.float32))
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


def synthesize(voice, text, steps):
    import subprocess
    t0 = time.time()
    with _lock:
        wav, _ = _tts._infer([text], ['ko'], _styles[voice], steps, VOICES[voice]['speed'])
    w = trim(np.asarray(wav, dtype=np.float32).reshape(-1), _tts.sample_rate)
    k = min(len(w) // 2, int(_tts.sample_rate * 0.01))
    if k > 0:
        ramp = np.linspace(0, 1, k, dtype=np.float32); w[:k] *= ramp; w[-k:] *= ramp[::-1]
    w = w / (np.abs(w).max() or 1.0) * 0.89
    key = cache_key(voice, text, steps)
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
        voice, text, steps = _warm_q.get()
        try:
            path = os.path.join(CACHE, voice, cache_key(voice, text, steps) + '.mp3')
            if not os.path.exists(path):
                synthesize(voice, text, steps)
        except Exception as e:
            sys.stderr.write('warm 실패: %s\n' % str(e)[:200])
        finally:
            _warm_set.discard((voice, text, steps)); _warm_q.task_done()


def warm(voice, texts, steps):
    n = 0
    for t in texts:
        t = clean_text(t)
        if not t: continue
        k = (voice, t, steps)
        if k in _warm_set: continue
        if os.path.exists(os.path.join(CACHE, voice, cache_key(voice, t, steps) + '.mp3')): continue
        _warm_set.add(k); _warm_q.put(k); n += 1
    return n


def cache_key(voice, text, steps):
    return hashlib.sha1(f'{voice}|{steps}|{text}'.encode('utf-8')).hexdigest()


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
        if u.path != '/warm':
            return self._json({'ok': False, 'error': 'not_found'}, 404)
        try:
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(min(n, 200000)).decode('utf-8') or '{}')
        except Exception:
            return self._json({'ok': False, 'error': 'bad_json'}, 400)
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
        return self._json({'ok': True, 'queued': warm(voice, texts[:400], steps), 'queue': _warm_q.qsize()})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path); q = urllib.parse.parse_qs(u.query)
        if u.path == '/health':
            cached = sum(len([f for f in os.listdir(os.path.join(CACHE, v)) if f.endswith('.mp3')]) for v in VOICES)
            return self._json({'ok': True, 'voices': list(VOICES), 'cached': cached, 'steps': STEPS, 'queue': _warm_q.qsize(), 'stats': _stats})
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
        if voice not in VOICES:
            return self._json({'ok': False, 'error': 'bad_voice'}, 400)
        if not text:
            return self._json({'ok': False, 'error': 'empty_text'}, 400)
        path = os.path.join(CACHE, voice, cache_key(voice, text, steps) + '.mp3')
        if os.path.exists(path):
            _stats['hit'] += 1
        else:
            try:
                path = synthesize(voice, text, steps)
            except Exception as e:
                return self._json({'ok': False, 'error': 'synthesis_failed', 'reason': str(e)[:300]}, 502)
        self._send_file(path)

    def _send_file(self, path):
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
