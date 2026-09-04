// 즉시 합성 캐시 키 — 워커(R2)와 서버(디스크)가 같은 꼴을 쓴다: sha1("voice|steps|r|조합표식|text").
//   r    = 합성 속도 배수(조각별 완급 · 0.7~1.6 · 소수 둘째 자리) — server.py 의 parse_r/fmt_r 과 같다
//   표식 = server/voice_shape.py 의 RECIPE_TAG — 다듬기 조합이 바뀌면 둘을 같이 올린다(test/tts-key.test.mjs 가 같은지 잰다)
export const RECIPE_TAG = 'u4b';
export const R_MIN = 0.7;
export const R_MAX = 1.6;

export function parseR(x) {
  const r = parseFloat(x);
  if (!Number.isFinite(r)) return 1.0;
  return Math.round(Math.min(R_MAX, Math.max(R_MIN, r)) * 100) / 100;
}
export const fmtR = (r) => Number(r).toFixed(2);

export async function sha1(s) {
  const d = await crypto.subtle.digest('SHA-1', new TextEncoder().encode(s));
  return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, '0')).join('');
}
export const keyString = (v, s, r, t) => `${v}|${s}|${fmtR(r)}|${RECIPE_TAG}|${t}`;
export async function cacheKey(v, s, r, t) {
  return `tts/${v}/${await sha1(keyString(v, s, r, t))}.mp3`;
}
