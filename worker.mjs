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
import KoVoiceMod from './public/ko-voice.js';
const KoVoice = (KoVoiceMod && KoVoiceMod.normalize) ? KoVoiceMod : globalThis.KoVoice;

export const MODEL = '@cf/myshell-ai/melotts';
export const LANGS = ['kr', 'ko'];
export const MAX_CHARS = 600;
let langOk = null;
export const _reset = () => { langOk = null; };

const json = (obj, status = 200, extra = {}) => new Response(JSON.stringify(obj), {
  status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...extra },
});

async function toBytes(out) {
  if (!out) return null;
  if (typeof out.audio === 'string') return Uint8Array.from(atob(out.audio), (c) => c.charCodeAt(0));
  if (out instanceof ArrayBuffer) return new Uint8Array(out);
  if (ArrayBuffer.isView(out)) return new Uint8Array(out.buffer, out.byteOffset, out.byteLength);
  if (typeof out.getReader === 'function') return new Uint8Array(await new Response(out).arrayBuffer());
  if (typeof out.arrayBuffer === 'function') return new Uint8Array(await out.arrayBuffer());
  return null;
}

async function synthesize(ai, spoken) {
  const order = langOk ? [langOk].concat(LANGS.filter((l) => l !== langOk)) : LANGS;
  let lastErr = null;
  for (const lang of order) {
    try {
      const bytes = await toBytes(await ai.run(MODEL, { prompt: spoken, lang }));
      if (bytes && bytes.length) { langOk = lang; return bytes; }
      lastErr = new Error('empty audio (lang=' + lang + ')');
    } catch (e) { lastErr = e; }
  }
  throw lastErr || new Error('synthesis failed');
}

async function cacheKey(spoken) {
  const d = await crypto.subtle.digest('SHA-1', new TextEncoder().encode(spoken));
  const hex = Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, '0')).join('');
  return new Request('https://korean-voice.internal/tts/' + hex);
}

export async function handleTts(request, env, ctx) {
  const ai = env && env.AI;
  if (request.method === 'GET') return json({ ok: true, available: !!ai, engine: ai ? 'workers-ai:melotts' : null, lang: langOk, maxChars: MAX_CHARS });
  if (request.method !== 'POST') return json({ ok: false, error: 'method_not_allowed' }, 405);
  let body = {};
  try { body = await request.json(); } catch (_) { body = {}; }
  const text = String(body.text == null ? '' : body.text).slice(0, MAX_CHARS).trim();
  if (!text) return json({ ok: false, error: 'empty_text' }, 400);
  if (!ai) return json({ ok: false, error: 'tts_unavailable', reason: 'AI 바인딩이 없습니다 (wrangler.jsonc의 ai 바인딩)' }, 503);

  const spoken = (body.normalize === false ? text : KoVoice.normalize(text)).replace(/⏸+/g, ',');
  const cache = (typeof caches !== 'undefined' && caches && caches.default) ? caches.default : null;
  const key = cache ? await cacheKey(spoken) : null;
  const audioHeaders = { 'Content-Type': 'audio/mpeg', 'Cache-Control': 'public, max-age=604800' };
  if (cache) {
    try {
      const hit = await cache.match(key);
      if (hit) return new Response(hit.body, { headers: { ...audioHeaders, 'X-TTS-Cache': 'hit' } });
    } catch (_) { /* 캐시 실패는 무시 */ }
  }
  let bytes;
  try { bytes = await synthesize(ai, spoken); } catch (e) {
    return json({ ok: false, error: 'synthesis_failed', reason: String((e && e.message) || e), tried: LANGS }, 502);
  }
  if (cache) {
    const put = cache.put(key, new Response(bytes, { headers: audioHeaders })).catch(() => {});
    if (ctx && ctx.waitUntil) ctx.waitUntil(put); else await put;
  }
  return new Response(bytes, { headers: { ...audioHeaders, 'X-TTS-Cache': 'miss', 'X-TTS-Lang': langOk || '' } });
}

const SECURITY = {
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
};

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === '/api/tts') return handleTts(request, env, ctx);
    const res = await env.ASSETS.fetch(request);
    const out = new Response(res.body, res);
    for (const [k, v] of Object.entries(SECURITY)) out.headers.set(k, v);
    return out;
  },
};
