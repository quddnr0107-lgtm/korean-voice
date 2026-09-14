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

/* 물러난 목소리의 조각을 통째로 지운다 (2026-09-14 사용자 「하은만 두고 나머지 r2 캐시에서 모두 지워」).
   캐시 키가 `tts/<목소리>/<sha1>.mp3` 라 목소리마다 우리가 하나씩이다. 남길 목소리의 우리는 **건드리지 않고**,
   그 밖의 우리를 통째로 비운다.
   🔴 남길 목록은 부르는 쪽이 주는 것이 아니라 **코드의 VOICES** 여야 한다 — 요청이 정하게 하면 한 번의 실수로
      쓰고 있는 목소리가 날아간다. 그래서 워커가 VOICES 를 넣어 부른다(worker.mjs handleBakePrune).
   🔴 남길 목록이 비었으면 거부한다. 빈 목록은 「전부 지워라」가 되는데, 그건 사고다. */
export async function retireVoices({ r2, keep, prefix = 'tts/', dry = true, listLimit = LIST_LIMIT, maxPages = 400 }) {
  const keepSet = keep instanceof Set ? keep : new Set(keep);
  if (!keepSet.size) return { ok: false, error: 'keep_empty', reason: '남길 목소리가 없다 — 전부 지우는 것을 막는다' };

  const 우리 = [];
  let cursor, pages = 0;
  do {
    const page = await r2.list({ prefix, delimiter: '/', cursor, limit: listLimit });
    pages++;
    for (const p of page.delimitedPrefixes || []) 우리.push(p);
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor && pages < maxPages);

  const 이름 = (p) => String(p).slice(prefix.length).replace(/\/$/, '');
  const 물러남 = 우리.filter((p) => !keepSet.has(이름(p)));
  const per = [];
  let deleted = 0, found = 0, truncated = false;
  for (const p of 물러남) {
    const keys = [];
    let c, n = 0;
    do {
      const page = await r2.list({ prefix: p, cursor: c, limit: listLimit });
      n++;
      for (const o of page.objects || []) keys.push(o.key);
      c = page.truncated ? page.cursor : undefined;
    } while (c && n < maxPages);
    if (c) truncated = true;
    found += keys.length;
    if (!dry) for (let i = 0; i < keys.length; i += DELETE_BATCH) { const part = keys.slice(i, i + DELETE_BATCH); await r2.delete(part); deleted += part.length; }
    per.push({ voice: 이름(p), prefix: p, found: keys.length, deleted: dry ? 0 : keys.length, truncated: !!c });
  }
  return { ok: true, dry, prefix, keep: [...keepSet], kept: 우리.filter((p) => keepSet.has(이름(p))).map(이름),
           retired: 물러남.map(이름), found, deleted, truncated, per };
}
