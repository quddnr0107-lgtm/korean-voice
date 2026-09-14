#!/usr/bin/env node
/* 물러난 목소리의 R2 조각을 통째로 지운다 (2026-09-14 사용자 「하은만 두고 나머지 r2 캐시에서 모두 지워」).
   🔴 남길 목소리는 여기서 정하지 않는다 — **워커의 VOICES** 다. 러너가 목록을 주면 한 번의 실수로
      쓰고 있는 목소리가 날아간다(/bake/put·/bake/prune 과 같은 원칙: 워커가 키를 다시 만든다).
   마른 실행을 먼저 하고 무엇이 지워질지 보인 뒤에 실제로 지운다.
   OIDC: audience=korean-voice-bake (main 워크플로만 워커가 받는다).
   사용: node tools/retire-voices.mjs [--base https://…] [--dry] */
const 인자 = process.argv.slice(2);
const 값 = (k, d) => { const i = 인자.indexOf(k); return i >= 0 && 인자[i + 1] ? 인자[i + 1] : d; };
const BASE = 값('--base', 'https://korean-voice.quddnr0107.workers.dev').replace(/\/$/, '');
const DRY = 인자.includes('--dry');
const UA = 'korean-voice-bake/1 (+https://github.com/quddnr0107-lgtm/korean-voice)';

async function oidc() {
  const url = process.env.ACTIONS_ID_TOKEN_REQUEST_URL, bearer = process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN;
  if (!url || !bearer) throw new Error('OIDC 환경변수가 없다 — GitHub Actions(main · permissions.id-token: write)에서만 돈다');
  const r = await fetch(url + '&audience=korean-voice-bake', { headers: { Authorization: 'bearer ' + bearer, Accept: 'application/json; api-version=2.0' } });
  if (!r.ok) throw new Error('OIDC ' + r.status);
  return (await r.json()).value;
}
async function call(dry) {
  const r = await fetch(BASE + '/bake/prune', { method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + (await oidc()), 'User-Agent': UA },
    body: JSON.stringify({ retire: true, dry }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) throw new Error(`/bake/prune retire ${r.status} ${JSON.stringify(j).slice(0, 300)}`);
  return j;
}
const 마른 = await call(true);
console.log(`남길 목소리: ${마른.keep.join(', ')}   (R2 에 있는 우리: ${마른.kept.concat(마른.retired).join(', ') || '없음'})`);
if (!마른.retired.length) { console.log('✅ 물러난 목소리의 조각이 없다 — 지울 것이 없다'); process.exit(0); }
for (const p of 마른.per) console.log(`  ${p.voice}: ${p.found}개${p.truncated ? ' 🔴 다 못 봤다' : ''}`);
console.log(`마른 실행: ${마른.found}개를 지운다`);
if (DRY) { console.log('(--dry — 여기서 멈춘다)'); process.exit(0); }
if (마른.truncated) { console.log('🔴 R2 를 끝까지 못 봤다 — 안 지운다'); process.exit(2); }
const 실제 = await call(false);
console.log(`✅ 지웠다 ${실제.deleted}개 · 남긴 우리: ${실제.kept.join(', ') || '없음'}`);
