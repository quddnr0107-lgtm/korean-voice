// 즉시 합성 캐시 키 — 워커(R2)와 서버(디스크)가 같은 꼴을 쓴다: sha1("voice|steps|r|조합표식|text").
//   r    = 합성 속도 배수(조각별 완급 · 0.7~1.6 · 소수 둘째 자리) — server.py 의 parse_r/fmt_r 과 같다
//   표식 = server/voice_shape.py 의 RECIPE_TAG — 다듬기 조합이 바뀌면 둘을 같이 올린다(test/tts-key.test.mjs 가 같은지 잰다)
export const RECIPE_TAG = 'k2';
/* 🔴 목소리 두 벌 — 학생이 마이페이지에서 고른다(2026-09-08). server/recipes.py 의 TAGS 와 숫자까지 같아야 한다.
   스텝이 벌에 딸려 있다: 캐시 키가 `voice|steps|r|표식|글` 이라 표식만 바꾸고 스텝을 안 맞추면
   이미 구워 둔 벌을 통째로 못 찾는다(실측: k2 를 스텝 8 로 찾으면 전량 miss).
   rev 는 주소만 바꾸는 자리다 — 같은 벌을 다시 구웠을 때(내용은 바뀌고 키는 그대로) 브라우저의
   1년짜리 immutable 사본을 흘려보내기 위한 것이다. R2 키에는 안 들어간다. */
export const TAGS = {
  u5: { steps: 8,  rev: 1, label: '옛 목소리' },
  k2: { steps: 16, rev: 1, label: '지금 목소리' },
};
export const tagOf = (id) => {
  const t = String(id || '').split('.')[0];
  return Object.prototype.hasOwnProperty.call(TAGS, t) ? t : RECIPE_TAG;
};
export const voiceSetId = (tag) => `${tag}.${TAGS[tag].rev}`;
/* 🔴 **스텝은 벌에서 나온다 — 상수(DEFAULT_STEPS)에서 나오면 안 된다**(2026-09-15).
   `/tts` 는 `TAGS[tag].steps` 를 쓰는데 `/bake/has`·`/warm`·`/bake/prune` 은 `DEFAULT_STEPS`(8)를
   쓰고 있었다. 지금 벌이 u5(8)일 때는 우연히 같아서 안 드러났고, k2(16)로 바꾸는 순간
   **키가 통째로 어긋났다** — 커버리지가 「57,897개 전부 없다」로 거짓 빨강을 냈다(실측). */
export const stepsFor = (tag) => TAGS[tagOf(tag)].steps;
/* 갈아타는 동안의 폴백 — 지금 벌로 아직 안 구운 조각을 옛 벌로 즉시 답하던 자리.
   🔴 **껐다**(2026-09-15) — k2 전편 굽기가 끝났고 **빠진 조각이 0/57,897 임을 전수로 쟀다**
      (Coverage run 34925802646). 폴백이 켜져 있으면 ① 「k2 가 있나」를 묻는 모든 자가
      「둘 중 하나라도 있나」로 답하고(R334) ② 옛 벌 조각이 영영 폐기 대상이 안 된다.
   🔴 다시 켤 자리 — 벌을 또 갈아탈 때. `{ tag: '<옛 벌>', steps: TAGS['<옛 벌>'].steps }`.
      그때는 굽기가 끝날 때까지만 두고, 끝나면 **전수로 0 을 재고 나서** 다시 null 로. */
export const FALLBACK = null;
/* 폐기가 남길 키 — **지금 닿을 수 있는 벌**을 담는다(벌마다 제 스텝으로).
   🔴 한 벌만 담으면 폐기가 **쓰고 있는 다른 벌을 통째로 지운다** — 갈아타는 동안 폴백이 옛 벌을
      읽고 있으므로 그 순간 소리가 사라진다. 그래서 「지금 벌 + 폴백 벌」을 담는다.
   🔴 반대로 **모든 벌**을 담으면 은퇴한 벌이 영영 안 지워진다. 폴백을 `null` 로 만드는 것이
      곧 「옛 벌을 놓아 준다」는 뜻이고, 그 다음 폐기가 그것을 치운다(dry 로 먼저 세어 보고).
   여기서 지우는 것은 **목록에서 빠진 글**이지, 쓰고 있는 벌이 아니다. */
export async function keepKeysFor(voices, items) {
  const 남길벌 = [RECIPE_TAG, ...(FALLBACK && FALLBACK.tag !== RECIPE_TAG ? [FALLBACK.tag] : [])];
  const keep = new Map(voices.map((v) => [v, new Set()]));
  for (const it of items) {
    const t = typeof it === 'string' ? it : (it && it.t);
    if (!t) continue;
    const r = parseR(it && it.r == null ? 1 : it.r);
    for (const v of voices) for (const tag of 남길벌) keep.get(v).add(await cacheKey(v, TAGS[tag].steps, r, t, tag));
  }
  return keep;
}
/** 지금 닿을 수 있는 벌 — 자와 검사기가 같은 목록을 보게 한 곳에서 준다(R31) */
export const 닿는벌 = () => [RECIPE_TAG, ...(FALLBACK && FALLBACK.tag !== RECIPE_TAG ? [FALLBACK.tag] : [])];

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
export const keyString = (v, s, r, t, tag = RECIPE_TAG) => `${v}|${s}|${fmtR(r)}|${tag}|${t}`;
export async function cacheKey(v, s, r, t, tag = RECIPE_TAG) {
  return `tts/${v}/${await sha1(keyString(v, s, r, t, tag))}.mp3`;
}
