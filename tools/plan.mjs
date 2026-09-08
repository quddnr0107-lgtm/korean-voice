#!/usr/bin/env node
/* 굽기 계획 — 조각 목록을 「뭉탱이(갈래×과목)」로 끊고, 사용자가 정한 차례대로 잡을 늘어놓는다(2026-09-08).
   차례: 개념강의 → 출제핵심강의 → 회독,  각각 통합방위법 → 예비군법 → 훈령 → 병역법.
   🔴 GitHub 는 이 배열 차례대로 잡을 집어간다(동시 40개). 그래서 앞 뭉탱이가 먼저 끝나고,
      워커의 두 표식 폴백(lib/tts-key.mjs FALLBACK) 덕에 끝난 뭉탱이부터 새 목소리로 바뀐다.
   🔴 matrix 는 256개가 상한이다 — 한 러너 몫(PER)을 늘려 총 잡 수를 240 이하로 맞춘다.
   사용: ORDER=true KIND=all SHARDS=40 PER=300 node tools/plan.mjs chunks.json [--설명] */
import fs from 'node:fs';
const 파일 = process.argv[2] || 'chunks.json';
const 설명 = process.argv.includes('--설명');
const ORDER = (process.env.ORDER || 'true') !== 'false';
const KIND = process.env.KIND || 'all';
const SHARDS = Math.max(1, parseInt(process.env.SHARDS || '40', 10) || 40);
const PER0 = Math.max(20, parseInt(process.env.PER || '300', 10) || 300);
const 최대잡 = 240;

const 갈래차례 = ['easy', 'exam', 'study'];
const 과목차례 = ['통합방위법', '예비군법', '훈령', '병역법', '기타'];
const 갈래이름 = { easy: '개념강의', exam: '출제핵심강의', study: '회독' };

const items = JSON.parse(fs.readFileSync(파일, 'utf8'));
const 걸러 = KIND === 'all' ? items : items.filter((c) => c.k === KIND);

/* 뭉탱이가 아니면 옛 방식 그대로 — 한 덩어리를 SHARDS 개로 나눈다 */
let 뭉탱이;
if (!ORDER) {
  뭉탱이 = [{ k: KIND, w: 'all', n: 걸러.length }];
} else {
  뭉탱이 = [];
  for (const k of 갈래차례) {
    if (KIND !== 'all' && k !== KIND) continue;
    for (const w of 과목차례) {
      const n = 걸러.filter((c) => c.k === k && c.w === w).length;
      if (n > 0) 뭉탱이.push({ k, w, n });
    }
  }
  const 없는것 = 걸러.filter((c) => !갈래차례.includes(c.k)).length;
  if (없는것) console.error(`⚠ 갈래를 모르는 조각 ${없는것}개는 안 굽는다 — chunks.mjs 를 보라`);
}

/* 한 러너 몫을 키워 가며 총 잡 수를 상한 아래로 */
let PER = PER0, 잡들 = [];
for (;;) {
  잡들 = [];
  for (const g of 뭉탱이) {
    const shards = Math.min(SHARDS, Math.max(1, Math.ceil(g.n / PER)));
    for (let i = 0; i < shards; i++) 잡들.push({ k: g.k, w: g.w, shard: i, shards });
  }
  if (잡들.length <= 최대잡) break;
  PER = Math.ceil(PER * 1.3);
}

if (설명) {
  console.log(`한 러너 몫 ${PER}조각 · 잡 ${잡들.length}개 · 동시 ${SHARDS}개 → 대략 ${Math.ceil(잡들.length / SHARDS)}차례`);
  for (const g of 뭉탱이) {
    const s = 잡들.filter((j) => j.k === g.k && j.w === g.w).length;
    console.log(`  ${(갈래이름[g.k] || g.k).padEnd(7)} ${g.w.padEnd(6)} 조각 ${String(g.n).padStart(6)} · 러너 ${s}`);
  }
} else {
  console.log(JSON.stringify(잡들));
}
