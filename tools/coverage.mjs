#!/usr/bin/env node
/* 구운 조각이 얼마나 빠졌나 — **어느 법·어느 갈래의 무엇이** 빠졌는지까지 짚는다 (2026-09-14 사용자
   「예비준 사이트에서 하은 목소리가 안 들리는 곳도 있어 부분적으로 빠진 건지 확인해봐」).

   폐기(prune)의 마른 실행은 「몇 개 있나」만 알려 준다. 빠진 것이 **어디**인지 모르면 다시 굽는 것 말고
   할 수 있는 게 없다. 여기서는 /bake/has 로 조각마다 물어 빠진 것을 법·갈래별로 모아 보여 준다.

   🔴 읽기만 한다(HEAD). 지우지도 굽지도 않는다.
   사용: node tools/coverage.mjs --chunks chunks.json [--base https://…] [--sample 5] [--fail-over 0] */
import fs from 'node:fs';
const 인자 = process.argv.slice(2);
const 값 = (k, d) => { const i = 인자.indexOf(k); return i >= 0 && 인자[i + 1] ? 인자[i + 1] : d; };
const BASE = 값('--base', 'https://korean-voice.quddnr0107.workers.dev').replace(/\/$/, '');
const SAMPLE = Math.max(0, parseInt(값('--sample', '5'), 10) || 0);
const FAIL_OVER = parseInt(값('--fail-over', '-1'), 10);   // 빠진 것이 이 수를 넘으면 exit 1 (-1 = 안 막는다)
const UA = 'korean-voice-bake/1 (+https://github.com/quddnr0107-lgtm/korean-voice)';
const BATCH = 400;   // /bake/has 가 한 번에 받는 최대치

const 목록 = JSON.parse(fs.readFileSync(값('--chunks', 'chunks.json'), 'utf8'));
console.log(`조각 ${목록.length}개 → ${BASE}/bake/has`);

const 빠짐 = [];
for (let i = 0; i < 목록.length; i += BATCH) {
  const part = 목록.slice(i, i + BATCH);
  const r = await fetch(BASE + '/bake/has', { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'User-Agent': UA },
    body: JSON.stringify({ items: part.map((c) => ({ t: c.t, r: c.r })) }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok || !Array.isArray(j.has)) throw new Error(`/bake/has ${r.status} ${JSON.stringify(j).slice(0, 200)}`);
  j.has.forEach((있나, n) => { if (!있나) 빠짐.push(part[n]); });
  if ((i / BATCH) % 25 === 24) console.log(`  …${Math.min(i + BATCH, 목록.length)}/${목록.length} (지금까지 빠짐 ${빠짐.length})`);
}

const 비율 = 목록.length ? (빠짐.length / 목록.length * 100) : 0;
console.log(`\n빠진 조각 ${빠짐.length}개 / ${목록.length} (${비율.toFixed(3)}%)`);
if (!빠짐.length) { console.log('✅ 빠진 조각이 없다 — 전편이 구워져 있다'); process.exit(0); }

const 묶음 = new Map();
for (const c of 빠짐) { const key = `${c.w || '기타'} · ${c.k || '?'}`; if (!묶음.has(key)) 묶음.set(key, []); 묶음.get(key).push(c); }
console.log('\n어디가 빠졌나 (법 · 갈래):');
for (const [key, cs] of [...묶음].sort((a, b) => b[1].length - a[1].length)) {
  console.log(`  ${key}: ${cs.length}개`);
  for (const c of cs.slice(0, SAMPLE)) console.log(`      「${String(c.t).slice(0, 60)}${String(c.t).length > 60 ? '…' : ''}」 r=${c.r}`);
  if (cs.length > SAMPLE) console.log(`      … 그 밖 ${cs.length - SAMPLE}개`);
}
console.log('\n채우려면: Bake voice (전편 굽기) 를 돌린다 — /bake/has 로 이미 있는 것은 건너뛴다.');
if (FAIL_OVER >= 0 && 빠짐.length > FAIL_OVER) { console.log(`🔴 빠진 것이 ${FAIL_OVER}개를 넘는다`); process.exit(1); }
