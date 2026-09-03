// Workers AI MeloTTS 경로(옛 · 한국어 지원 미확인). worker.mjs 가 /api/tts 에서 부른다. Node 테스트가 직접 import 한다.
import KoVoiceMod from '../public/ko-voice.js';
const KoVoice = (KoVoiceMod && KoVoiceMod.normalize) ? KoVoiceMod : globalThis.KoVoice;

export const json = (obj, status = 200, extra = {}) => new Response(JSON.stringify(obj), {
  status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...extra },
});

export const MODEL = '@cf/myshell-ai/melotts';
export const LANGS = ['kr', 'ko'];
export const MAX_CHARS = 600;
let langOk = null;
export const _reset = () => { langOk = null; };

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

