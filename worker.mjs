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
import { DurableObject } from 'cloudflare:workers';
import { handleTts, json } from './lib/melotts.mjs';
import { cacheKey, parseR, RECIPE_TAG } from './lib/tts-key.mjs';
import { makeBaker } from './lib/bake.mjs';
import { verifyGithubOidc } from './lib/oidc.mjs';
export { handleTts, MODEL, LANGS, MAX_CHARS, _reset } from './lib/melotts.mjs';

// 컨테이너 = server/server.py (포트 8790). 3분 요청이 없으면 잠든다(비용 0). 첫 요청이 깨우며 모델 로드 약 2초.
export class TtsContainer extends Container {
  defaultPort = 8790;
  sleepAfter = '3m';   // 유휴 3분이면 잠든다(비용 0). 깨우는 데 수 초 · 캐시(R2)는 잠들어도 즉시 답한다
  envVars = { STEPS: '16', ALLOW_ORIGIN: '*', CACHE_DIR: '/app/cache' };
}

/* ── 굽기 대기열(Durable Object) — 전편을 컨테이너가 스스로 굽고 R2 에 넣는다(lib/bake.mjs · 2026-09-04) ──
   POST /bake {action:'enqueue', v, s, items:[{t, r}…]}   ≤400개 · Authorization: Bearer <BAKE_TOKEN>
   POST /bake {action:'stop'|'resume'|'clear'}             같은 토큰
   GET  /bake                                              상태(열려 있다 · 읽기만)
   🔴 BAKE_TOKEN(비밀값)이 안 심겨 있으면 POST 는 503 — 아무나 컨테이너 시간을 못 태운다. */
export class BakeQueue extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.baker = makeBaker({
      storage: ctx.storage, r2: env.TTS_CACHE, recipeTag: RECIPE_TAG,
      // 샤드 i → 컨테이너 'bake-i' (학생 청취는 'main' 이 받는다 · wrangler.jsonc max_instances 가 샤드 수 + 1 이상이어야 한다)
      fetchContainer: (path, init, shard) => { const c = getContainer(env.TTS_CONTAINER, shard == null ? 'main' : 'bake-' + shard); return c.fetch(new Request('http://container' + path, init)); },
      setAlarm: (at) => ctx.storage.setAlarm(at),
    });
  }
  async alarm() { await this.baker.tick(); }
  async enqueue(body) { return this.baker.enqueue(body); }
  async status() { return this.baker.status(); }
  async stop() { return this.baker.stop(); }
  async resume() { return this.baker.resume(); }
  async clear() { return this.baker.clear(); }
  async recount() { return this.baker.recount(); }
}
/* ── 공개 러너가 구운 조각을 올린다 — PUT /bake/put?v&s&r&t  본문 = mp3 · Authorization: Bearer <GitHub OIDC JWT> ──
   비밀값이 없다: GitHub 이 서명한 토큰을 GitHub 공개키(JWKS)로 검증하고 「저장소 BAKE_OIDC_REPO 의 main」만 받는다(lib/oidc.mjs).
   키는 워커가 (v,s,r,t)로 다시 만든다(러너가 준 키를 믿지 않는다) · 표식(X-TTS-Recipe)이 워커 표식과 같아야 한다. */
const BAKE_OIDC = { aud: 'korean-voice-bake', repository: 'quddnr0107-lgtm/korean-voice', ref: 'refs/heads/main' };   // 이 저장소(공개 · 2026-09-04)의 main 워크플로만
async function handleBakePut(request, env) {
  if (!env.TTS_CACHE) return json({ ok: false, error: 'no_r2' }, 503, CORS);
  const tok = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
  const v = await verifyGithubOidc(tok, BAKE_OIDC);
  if (!v.claims) return json({ ok: false, error: 'oidc_' + v.error }, 401, CORS);
  const url = new URL(request.url);
  const voice = url.searchParams.get('v') || 'female';
  const t = cleanText(url.searchParams.get('t'));
  const s = Math.max(4, Math.min(32, parseInt(url.searchParams.get('s') || DEFAULT_STEPS, 10) || DEFAULT_STEPS));
  const r = parseR(url.searchParams.get('r') || 1);
  if (!VOICES.includes(voice) || !t) return json({ ok: false, error: 'bad_input' }, 400, CORS);
  if ((request.headers.get('X-TTS-Recipe') || '') !== RECIPE_TAG) return json({ ok: false, error: 'recipe_mismatch', want: RECIPE_TAG }, 409, CORS);
  if (!(request.headers.get('Content-Type') || '').startsWith('audio/')) return json({ ok: false, error: 'not_audio' }, 400, CORS);
  const bytes = new Uint8Array(await request.arrayBuffer());
  if (bytes.length < 500 || bytes.length > 5_000_000) return json({ ok: false, error: 'bad_size', size: bytes.length }, 400, CORS);
  const key = await cacheKey(voice, s, r, t);
  await env.TTS_CACHE.put(key, bytes, { httpMetadata: { contentType: 'audio/mpeg', cacheControl: 'public, max-age=31536000, immutable' } });
  return json({ ok: true, key, size: bytes.length }, 200, CORS);
}
/* GET /bake/has?v&s&r&t… — 러너가 이미 있는 조각을 건너뛴다(열려 있다 · head 뿐) */
async function handleBakeHas(request, env) {
  let body = {}; try { body = await request.json(); } catch (_) { body = {}; }
  const items = Array.isArray(body.items) ? body.items.slice(0, 400) : [];
  const v = body.v || 'female', s = Math.max(4, Math.min(32, parseInt(body.s || DEFAULT_STEPS, 10) || DEFAULT_STEPS));
  const out = [];
  for (let i = 0; i < items.length; i += 50) {
    const part = items.slice(i, i + 50);
    const res = await Promise.all(part.map(async (it) => { const t = cleanText(it && it.t); if (!t) return false; const key = await cacheKey(v, s, parseR(it.r == null ? 1 : it.r), t); return !!(env.TTS_CACHE && await env.TTS_CACHE.head(key).catch(() => null)); }));
    out.push(...res);
  }
  return json({ ok: true, has: out }, 200, CORS);
}
async function handleBake(request, env) {
  if (!env.BAKE) return json({ ok: false, error: 'bake_unavailable', reason: 'BAKE 바인딩 없음' }, 503, CORS);
  const stub = env.BAKE.get(env.BAKE.idFromName('main'));
  if (request.method === 'GET') return json({ ok: true, ...(await stub.status()) }, 200, CORS);
  if (!env.BAKE_TOKEN) return json({ ok: false, error: 'bake_token_unset', reason: 'wrangler secret put BAKE_TOKEN 이 먼저다' }, 503, CORS);
  if ((request.headers.get('Authorization') || '') !== 'Bearer ' + env.BAKE_TOKEN) return json({ ok: false, error: 'unauthorized' }, 401, CORS);
  let body = {}; try { body = await request.json(); } catch (_) { body = {}; }
  const action = body.action || 'enqueue';
  // 🔴 DO 안의 예외를 삼키지 않는다 — 500 {} 로 나가면 보내는 쪽이 원인을 못 본다(4회차 실측). 이유를 본문에 실어 준다.
  try {
    if (action === 'enqueue') {
      const v = body.v || 'female';
      if (!VOICES.includes(v)) return json({ ok: false, error: 'bad_voice' }, 400, CORS);
      const s = Math.max(4, Math.min(32, parseInt(body.s || DEFAULT_STEPS, 10) || DEFAULT_STEPS));
      const items = Array.isArray(body.items) ? body.items.slice(0, 400) : [];
      return json({ ok: true, ...(await stub.enqueue({ v, s, items })) }, 200, CORS);
    }
    if (action === 'stop') return json({ ok: true, ...(await stub.stop()) }, 200, CORS);
    if (action === 'resume') return json({ ok: true, ...(await stub.resume()) }, 200, CORS);
    if (action === 'clear') return json({ ok: true, ...(await stub.clear()) }, 200, CORS);
    if (action === 'recount') return json({ ok: true, ...(await stub.recount()) }, 200, CORS);
    return json({ ok: false, error: 'bad_action' }, 400, CORS);
  } catch (e) {
    return json({ ok: false, error: 'bake_failed', action, reason: String((e && e.stack) || (e && e.message) || e).slice(0, 600) }, 500, CORS);
  }
}

const SECURITY = {
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
};

/* ── 즉시 합성 서버(컨테이너) — server/server.py 가 Supertonic 3 를 돌린다 ─────────────────────────
   GET  /tts?v=female|male&t=문장[&s=16][&r=1.0]  → R2 캐시(korean-voice-tts) 적중이면 바로, 아니면 컨테이너가 합성 → R2 저장
   POST /warm {v, texts:[…][, r]}         → 컨테이너 대기열(앞서 굽기)
   GET  /health                           → 컨테이너 상태(잠들어 있으면 깨운다)
   캐시 키는 server.py 의 cache_key 와 같다(lib/tts-key.mjs): sha1("voice|steps|r|조합표식|text") · text 는 공백 정리·400자.
   🔴 r(합성 속도 배수)과 조합표식(voice_shape.RECIPE_TAG)이 키에 들어간다 — 다듬기 조합이 바뀌면 옛 R2 캐시는 자연히 안 맞는다. */
const CORS = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, HEAD, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Range, Content-Type', 'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges' };
const VOICES = ['female', 'male'];
const DEFAULT_STEPS = 16;
const cleanText = (t) => String(t == null ? '' : t).replace(/\s+/g, ' ').trim().slice(0, 400);
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
  const r = parseR(url.searchParams.get('r') || 1);
  if (!VOICES.includes(v)) return json({ ok: false, error: 'bad_voice' }, 400, CORS);
  if (!t) return json({ ok: false, error: 'empty_text' }, 400, CORS);
  const key = await cacheKey(v, s, r, t);
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
    target.search = '?v=' + encodeURIComponent(v) + '&t=' + encodeURIComponent(t) + '&s=' + s + '&r=' + r;
    res = await c.fetch(new Request(target.toString(), { method: 'GET' }));
  } catch (e) {
    return json({ ok: false, error: 'container_failed', reason: String((e && e.message) || e).slice(0, 300) }, 502, CORS);
  }
  if (!res.ok || !(res.headers.get('Content-Type') || '').startsWith('audio/')) {
    const body = await res.text().catch(() => '');
    return json({ ok: false, error: 'synthesis_failed', status: res.status, reason: body.slice(0, 300) }, 502, CORS);
  }
  const bytes = new Uint8Array(await res.arrayBuffer());
  // 🔴 컨테이너의 다듬기 표식이 워커의 것과 같을 때만 R2 에 넣는다. Workers Builds 는 워커와 컨테이너 이미지를 따로 올리므로
  //    잠깐 워커만 새 판인 창이 생긴다 — 그때 옛 소리를 새 키로 넣으면 영영 안 지워진다(2026-09-03 에 실제로 그 창이 열렸다).
  const recipe = res.headers.get('X-TTS-Recipe') || '';
  const cacheable = recipe === RECIPE_TAG;
  if (env.TTS_CACHE && cacheable) {
    const put = env.TTS_CACHE.put(key, bytes, { httpMetadata: { contentType: 'audio/mpeg', cacheControl: 'public, max-age=31536000, immutable' } }).catch(() => {});
    if (ctx && ctx.waitUntil) ctx.waitUntil(put); else await put;
  }
  return audioResponse(bytes, bytes.length, 200, cacheable ? { 'X-TTS-Cache': 'miss' } : { 'X-TTS-Cache': 'miss-nocache', 'X-TTS-Recipe-Mismatch': `${recipe || 'none'}!=${RECIPE_TAG}`, 'Cache-Control': 'no-store' });
}
async function handleWarm(request, env) {
  let body = {};
  try { body = await request.json(); } catch (_) { body = {}; }
  const v = body.v || 'female';
  if (!VOICES.includes(v)) return json({ ok: false, error: 'bad_voice' }, 400, CORS);
  const texts = Array.isArray(body.texts) ? body.texts.map(cleanText).filter(Boolean).slice(0, 400) : [];
  const s = Math.max(4, Math.min(32, parseInt(body.s || DEFAULT_STEPS, 10) || DEFAULT_STEPS));
  const r = parseR(body.r == null ? 1 : body.r);
  // R2 에 이미 있는 것은 뺀다(컨테이너 대기열을 아낀다)
  const todo = [];
  if (env.TTS_CACHE) {
    for (const t of texts) { const key = await cacheKey(v, s, r, t); if (!(await env.TTS_CACHE.head(key).catch(() => null))) todo.push(t); }
  } else todo.push(...texts);
  if (!todo.length) return json({ ok: true, queued: 0, cached: texts.length }, 200, CORS);
  const c = container(env);
  if (!c) return json({ ok: false, error: 'tts_unavailable' }, 503, CORS);
  try {
    const target = new URL(request.url); target.pathname = '/warm'; target.search = '';
    const res = await c.fetch(new Request(target.toString(), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ v, s, r, texts: todo }) }));
    const j = await res.json().catch(() => ({}));
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
    return json({ ...j, ok: !!j.ok, available: !!j.ok, cache: env.TTS_CACHE ? 'r2' : 'none', recipe_worker: RECIPE_TAG, recipe_match: j.recipe === RECIPE_TAG, ...(j.ok ? {} : { container_status: r.status, container_body: raw.slice(0, 200) }) }, 200, CORS);
  } catch (e) {
    return json({ ok: false, available: false, reason: String((e && e.message) || e).slice(0, 300) }, 200, CORS);
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS' && ['/tts', '/warm', '/health', '/api/tts', '/bake'].includes(url.pathname)) return new Response(null, { status: 204, headers: { ...CORS, 'Access-Control-Max-Age': '86400', 'Access-Control-Allow-Headers': 'Range, Content-Type, Authorization' } });
    if (url.pathname === '/tts') return handleLiveTts(request, env, ctx);
    if (url.pathname === '/warm' && request.method === 'POST') return handleWarm(request, env);
    if (url.pathname === '/bake' && (request.method === 'GET' || request.method === 'POST')) return handleBake(request, env);
    if (url.pathname === '/bake/put' && (request.method === 'PUT' || request.method === 'POST')) return handleBakePut(request, env);   // PUT 이 엣지에서 403 이 난 적이 있어 POST 도 받는다
    if (url.pathname === '/bake/has' && request.method === 'POST') return handleBakeHas(request, env);
    if (url.pathname === '/health') return handleHealth(request, env);
    if (url.pathname === '/api/tts') return handleTts(request, env, ctx);
    const res = await env.ASSETS.fetch(request);
    const out = new Response(res.body, res);
    for (const [k, v] of Object.entries(SECURITY)) out.headers.set(k, v);
    return out;
  },
};
