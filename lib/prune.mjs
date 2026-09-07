// 옛 조각 폐기 — R2 의 tts/ 아래에서 **지금 조각 목록의 키 집합에 없는 것**을 지운다 (2026-09-05 yebijun 사용자 「이전 꺼 다 폐기해」).
//
//   왜 — 키가 sha1(voice|steps|r|표식|text) 라 글을 되돌릴 수 없다. 「옛 원고의 조각」을 골라낼 길은 하나뿐이다:
//        지금 원고로 만든 조각 목록(tools/chunks.mjs)의 키를 전부 계산해 두고, R2 에 있는데 그 집합에 없는 키를 지운다.
//        표식(RECIPE_TAG)이 바뀐 옛 조각·학생 청취가 남긴 딴 속도(r) 조각도 목록 밖이면 같이 지워진다 — 알고 하는 값이다.
//   🔴 빈 목록·짧은 목록으로 부르면 전부 지워진다 — MIN_KEEP 아래면 거부한다(force 로만 연다).
//   🔴 dry 가 기본이다. 지우는 것은 dry:false 를 명시할 때만.
//
// 순수 논리만 여기(테스트가 가짜 R2 로 돈다). 워커 껍데기는 worker.mjs 의 handleBakePrune.
export const MIN_KEEP = 1000;
export const LIST_LIMIT = 1000;
export const DELETE_BATCH = 500;

export async function prune({ r2, keep, prefix = 'tts/', dry = true, force = false, listLimit = LIST_LIMIT, maxPages = 400 }) {
  const keepSet = keep instanceof Set ? keep : new Set(keep);
  if (keepSet.size < MIN_KEEP && !force) return { ok: false, error: 'keep_too_small', keep: keepSet.size, min: MIN_KEEP };
  let cursor, pages = 0, total = 0, hit = 0; const drop = [];
  do {
    const page = await r2.list({ prefix, cursor, limit: listLimit });
    pages++;
    for (const o of page.objects || []) { total++; if (keepSet.has(o.key)) hit++; else drop.push(o.key); }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor && pages < maxPages);
  let deleted = 0;
  if (!dry) for (let i = 0; i < drop.length; i += DELETE_BATCH) { const part = drop.slice(i, i + DELETE_BATCH); await r2.delete(part); deleted += part.length; }
  return { ok: true, dry, prefix, pages, truncated: !!cursor, total, keep: keepSet.size, hit, drop: drop.length, deleted, sample: drop.slice(0, 5) };
}
