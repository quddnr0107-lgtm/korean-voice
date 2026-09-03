// korean-voice Worker — 정적 실험실(public/) + 신경망 음성 API. 파일도 저장소도 없다.
//   GET  /api/tts           → { ok, available, engine, lang }
//   POST /api/tts {text}    → audio/mpeg  (Cloudflare Workers AI @cf/myshell-ai/melotts, 엣지 캐시 7일)
// 텍스트는 먼저 ko-voice.js의 normalize로 "사람이 읽듯" 고쳐 쓴 뒤 모델에 넘긴다 — 이 한 단계가
// 숫자·단위·약어 오독을 없앤다(대형 음성 서비스들도 같은 순서로 한다: 정규화 → 운율 → 합성).
//
// 정직성: Workers AI의 MeloTTS가 한국어 코드를 받는지 문서에 없다(en·fr 예시뿐). 후보('kr'·'ko')를
// 차례로 시도하고 성공한 코드를 기억하며, 전부 실패하면 사유를 그대로 돌려준다. 프런트는 그때
// 브라우저 음성(Web Speech)으로 폴백한다.
// UMD 파일: 번들러/Node에선 module.exports(default import), 브라우저에선 window.KoVoice.
import { Container, getContainer } from '@cloudflare/containers';
import { handleTts, json } from './lib/melotts.mjs';
export { handleTts, MODEL, LANGS, MAX_CHARS, _reset } from './lib/melotts.mjs';

// 컨테이너 = server/server.py (포트 8790). 3분 요청이 없으면 잠든다(비용 0). 첫 요청이 깨우며 모델 로드 약 2초.
export class TtsContainer extends Container {
  defaultPort = 8790;
  sleepAfter = '3m';   // 유휴 3분이면 잠든다(비용 0). 깨우는 데 수 초 · 캐시(R2)는 잠들어도 즉시 답한다
  envVars = { STEPS: '16', ALLOW_ORIGIN: '*', CACHE_DIR: '/app/cache' };
}

const SECURITY = {
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
};

/* ── 즉시 합성 서버(컨테이너) — server/server.py 가 Supertonic 3 를 돌린다 ─────────────────────────
   GET  /tts?v=female|male&t=문장[&s=16]  → R2 캐시(korean-voice-tts) 적중이면 바로, 아니면 컨테이너가 합성 → R2 저장
   POST /warm {v, texts:[…]}              → 컨테이너 대기열(앞서 굽기)
   GET  /health                           → 컨테이너 상태(잠들어 있으면 깨운다)
   캐시 키는 server.py 의 cache_key 와 같다: sha1("voice|steps|text") · text 는 공백 정리·400자. */
const CORS = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, HEAD, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Range, Content-Type', 'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges' };
const VOICES = ['female', 'male'];
const DEFAULT_STEPS = 16;
const cleanText = (t) => String(t == null ? '' : t).replace(/\s+/g, ' ').trim().slice(0, 400);
async function sha1(s) {
  const d = await crypto.subtle.digest('SHA-1', new TextEncoder().encode(s));
  return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, '0')).join('');
}
function container(env) {
  if (!env.TTS_CONTAINER) return null;
  return getContainer(env.TTS_CONTAINER, 'main');   // 인스턴스 하나가 캐시·대기열을 공유한다
}
function audioResponse(body, size, status, extra) {
  return new Response(body, { status, headers: { 'Content-Type': 'audio/mpeg', 'Accept-Ranges': 'bytes', 'Cache-Control': 'public, max-age=31536000, immutable', ...(size != null ? { 'Content-Length': String(size) } : {}), ...CORS, ...extra } });
}
async function handleLiveTts(request, env, ctx) {
  const url = new URL(request.url);
  const v = url.searchParams.get('v') || 'female';
  const t = cleanText(url.searchParams.get('t'));
  const s = Math.max(4, Math.min(32, parseInt(url.searchParams.get('s') || DEFAULT_STEPS, 10) || DEFAULT_STEPS));
  if (!VOICES.includes(v)) return json({ ok: false, error: 'bad_voice' }, 400, CORS);
  if (!t) return json({ ok: false, error: 'empty_text' }, 400, CORS);
  const key = `tts/${v}/${await sha1(`${v}|${s}|${t}`)}.mp3`;
  // 1) R2 캐시
  if (env.TTS_CACHE) {
    try {
      const range = request.headers.get('Range');
      const obj = await env.TTS_CACHE.get(key, range ? { range: request.headers } : undefined);
      if (obj) {
        if (request.method === 'HEAD') return audioResponse(null, obj.size, 200, { 'X-TTS-Cache': 'r2' });
        if (range && obj.range) {
          const start = obj.range.offset || 0; const len = obj.range.length != null ? obj.range.length : obj.size - start;
          return audioResponse(obj.body, len, 206, { 'Content-Range': `bytes ${start}-${start + len - 1}/${obj.size}`, 'X-TTS-Cache': 'r2' });
        }
        return audioResponse(obj.body, obj.size, 200, { 'X-TTS-Cache': 'r2' });
      }
    } catch (_) { /* 캐시 실패는 합성으로 */ }
  }
  // 2) 컨테이너 합성
  const c = container(env);
  if (!c) return json({ ok: false, error: 'tts_unavailable', reason: '컨테이너 바인딩 없음' }, 503, CORS);
  let res;
  try {
    const target = new URL(request.url); target.pathname = '/tts';
    target.search = '?v=' + encodeURIComponent(v) + '&t=' + encodeURIComponent(t) + '&s=' + s;
    res = await c.fetch(new Request(target.toString(), { method: 'GET' }));
  } catch (e) {
    return json({ ok: false, error: 'container_failed', reason: String((e && e.message) || e).slice(0, 300) }, 502, CORS);
  }
  if (!res.ok || !(res.headers.get('Content-Type') || '').startsWith('audio/')) {
    const body = await res.text().catch(() => '');
    return json({ ok: false, error: 'synthesis_failed', status: res.status, reason: body.slice(0, 300) }, 502, CORS);
  }
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (env.TTS_CACHE) {
    const put = env.TTS_CACHE.put(key, bytes, { httpMetadata: { contentType: 'audio/mpeg', cacheControl: 'public, max-age=31536000, immutable' } }).catch(() => {});
    if (ctx && ctx.waitUntil) ctx.waitUntil(put); else await put;
  }
  return audioResponse(bytes, bytes.length, 200, { 'X-TTS-Cache': 'miss' });
}
async function handleWarm(request, env) {
  let body = {};
  try { body = await request.json(); } catch (_) { body = {}; }
  const v = body.v || 'female';
  if (!VOICES.includes(v)) return json({ ok: false, error: 'bad_voice' }, 400, CORS);
  const texts = Array.isArray(body.texts) ? body.texts.map(cleanText).filter(Boolean).slice(0, 400) : [];
  const s = Math.max(4, Math.min(32, parseInt(body.s || DEFAULT_STEPS, 10) || DEFAULT_STEPS));
  // R2 에 이미 있는 것은 뺀다(컨테이너 대기열을 아낀다)
  const todo = [];
  if (env.TTS_CACHE) {
    for (const t of texts) { const key = `tts/${v}/${await sha1(`${v}|${s}|${t}`)}.mp3`; if (!(await env.TTS_CACHE.head(key).catch(() => null))) todo.push(t); }
  } else todo.push(...texts);
  if (!todo.length) return json({ ok: true, queued: 0, cached: texts.length }, 200, CORS);
  const c = container(env);
  if (!c) return json({ ok: false, error: 'tts_unavailable' }, 503, CORS);
  try {
    const target = new URL(request.url); target.pathname = '/warm'; target.search = '';
    const r = await c.fetch(new Request(target.toString(), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ v, s, texts: todo }) }));
    const j = await r.json().catch(() => ({}));
    return json({ ok: true, ...j, skipped: texts.length - todo.length }, 200, CORS);
  } catch (e) {
    return json({ ok: false, error: 'container_failed', reason: String((e && e.message) || e).slice(0, 300) }, 502, CORS);
  }
}
async function handleHealth(request, env) {
  const c = container(env);
  if (!c) return json({ ok: false, available: false, reason: '컨테이너 바인딩 없음' }, 200, CORS);
  try {
    const target = new URL(request.url); target.pathname = '/health'; target.search = '';
    const r = await c.fetch(new Request(target.toString(), { method: 'GET' }));
    const raw = await r.text().catch(() => '');
    let j = {}; try { j = JSON.parse(raw); } catch (_) { j = {}; }
    return json({ ...j, ok: !!j.ok, available: !!j.ok, cache: env.TTS_CACHE ? 'r2' : 'none', ...(j.ok ? {} : { container_status: r.status, container_body: raw.slice(0, 200) }) }, 200, CORS);
  } catch (e) {
    return json({ ok: false, available: false, reason: String((e && e.message) || e).slice(0, 300) }, 200, CORS);
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS' && ['/tts', '/warm', '/health', '/api/tts'].includes(url.pathname)) return new Response(null, { status: 204, headers: { ...CORS, 'Access-Control-Max-Age': '86400' } });
    if (url.pathname === '/tts') return handleLiveTts(request, env, ctx);
    if (url.pathname === '/warm' && request.method === 'POST') return handleWarm(request, env);
    if (url.pathname === '/health') return handleHealth(request, env);
    if (url.pathname === '/api/tts') return handleTts(request, env, ctx);
    const res = await env.ASSETS.fetch(request);
    const out = new Response(res.body, res);
    for (const [k, v] of Object.entries(SECURITY)) out.headers.set(k, v);
    return out;
  },
};
