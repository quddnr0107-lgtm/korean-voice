#!/usr/bin/env node
/* 옛 조각 폐기 — 러너가 조각 목록(chunks.json)을 워커 /bake/prune 에 보내고, 워커가 그 목록의 키 집합에 없는 R2 조각을 지운다 (2026-09-05).
   🔴 워커가 키를 다시 만든다(러너가 준 키를 믿지 않는다 · /bake/put 과 같은 원칙). 목소리 전부(VOICES)에 대해 만든다.
   🔴 마른 실행(dry)을 먼저 하고, 지울 수가 전체의 절반을 넘으면 멈춘다(목록이 깨진 것이지 옛 조각이 그렇게 많을 리 없다) — --max-drop-ratio 로 푼다.
   OIDC: GitHub Actions 의 ACTIONS_ID_TOKEN_REQUEST_URL 에서 audience=korean-voice-bake 로 받는다(main 워크플로만 워커가 받는다).
   사용: node tools/prune.mjs --chunks chunks.json [--base https://korean-voice.quddnr0107.workers.dev] [--dry] [--max-drop-ratio 0.5] */
import fs from 'node:fs';
const 인자 = process.argv.slice(2);
const 값 = (k, d) => { const i = 인자.indexOf(k); return i >= 0 && 인자[i + 1] ? 인자[i + 1] : d; };
const BASE = 값('--base', 'https://korean-voice.quddnr0107.workers.dev').replace(/\/$/, '');
const DRY = 인자.includes('--dry');
const MAX_RATIO = parseFloat(값('--max-drop-ratio', '0.5'));
const UA = 'korean-voice-bake/1 (+https://github.com/quddnr0107-lgtm/korean-voice)';   // 기본 UA 는 엣지가 403 으로 막는다

async function oidc() {
  const url = process.env.ACTIONS_ID_TOKEN_REQUEST_URL, bearer = process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN;
  if (!url || !bearer) throw new Error('OIDC 환경변수가 없다 — GitHub Actions(main · permissions.id-token: write)에서만 돈다');
  const r = await fetch(url + '&audience=korean-voice-bake', { headers: { Authorization: 'bearer ' + bearer, Accept: 'application/json; api-version=2.0' } });
  if (!r.ok) throw new Error('OIDC ' + r.status);
  return (await r.json()).value;
}
async function call(body) {
  const r = await fetch(BASE + '/bake/prune', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + (await oidc()), 'User-Agent': UA }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) throw new Error(`/bake/prune ${r.status} ${JSON.stringify(j).slice(0, 300)}`);
  return j;
}
const items = JSON.parse(fs.readFileSync(값('--chunks', 'chunks.json'), 'utf8')).map((c) => ({ t: c.t, r: c.r }));
console.log(`조각 목록 ${items.length}개 → ${BASE}/bake/prune`);
const dry = await call({ items, dry: true });
console.log(`마른 실행: R2 전체 ${dry.total} · 목록에 있음 ${dry.hit} · 목록 밖 ${dry.drop} (keep ${dry.keep}키 · 페이지 ${dry.pages}${dry.truncated ? ' · 🔴 다 못 봤다' : ''})`);
if (dry.sample && dry.sample.length) console.log('  본보기:', dry.sample.join(' '));
if (DRY) { console.log('(--dry — 여기서 멈춘다)'); process.exit(0); }
if (dry.truncated) { console.log('🔴 R2 를 끝까지 못 봤다 — 안 지운다'); process.exit(2); }
if (dry.total && dry.drop / dry.total > MAX_RATIO) { console.log(`🔴 지울 것이 ${(dry.drop / dry.total * 100).toFixed(0)}% — 목록이 깨졌을 수 있다. 안 지운다(--max-drop-ratio 로 푼다)`); process.exit(2); }
const real = await call({ items, dry: false });
console.log(`✅ 지웠다 ${real.deleted} · 남은 것 ${real.hit}`);
