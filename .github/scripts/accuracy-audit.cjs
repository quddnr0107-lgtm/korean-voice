'use strict';

const fs = require('fs');
const path = require('path');
const K = require(path.join(process.cwd(), 'public/ko-voice.js'));
const ROOT = path.join(process.cwd(), 'corpus');
const EXTS = new Set(['.html', '.js', '.md', '.json', '.txt']);

function walk(dir, out = []) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ent.name === '.git' || ent.name === 'node_modules' || ent.name === '.next') continue;
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) walk(p, out);
    else if (EXTS.has(path.extname(ent.name).toLowerCase())) out.push(p);
  }
  return out;
}

const items = new Set();
function clean(s) {
  return String(s || '')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;|&#160;/gi, ' ')
    .replace(/&quot;/gi, '"')
    .replace(/&amp;/gi, '&')
    .replace(/\\n|\\r|\\t/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}
function add(s) {
  s = clean(s);
  if (s.length < 6 || s.length > 500) return;
  const h = (s.match(/[가-힣]/g) || []).length;
  if (h < 4) return;
  if ((s.match(/[{}<>=>]/g) || []).length > Math.max(8, s.length * 0.12)) return;
  for (const part of s.split(/(?<=[.!?])\s+|\s*[|]\s*/)) {
    const t = clean(part);
    if (t.length >= 6 && t.length <= 350 && (t.match(/[가-힣]/g) || []).length >= 4) items.add(t);
  }
}
function addJson(v) {
  if (typeof v === 'string') return add(v);
  if (Array.isArray(v)) return v.forEach(addJson);
  if (v && typeof v === 'object') for (const x of Object.values(v)) addJson(x);
}

const files = walk(ROOT);
for (const f of files) {
  let raw;
  try { raw = fs.readFileSync(f, 'utf8'); } catch { continue; }
  if (path.extname(f).toLowerCase() === '.json') {
    try { addJson(JSON.parse(raw)); } catch { /* fall through */ }
  }
  for (const line of raw.split(/\r?\n/)) {
    add(line);
    const re = /(['"`])([^'"`]{6,500})\1/g;
    let m;
    while ((m = re.exec(line))) add(m[2]);
  }
}

const NUM = /^(?:[영일이삼사오육칠팔구십백천만억조]+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|스물|서른|마흔|쉰|예순|일흔|여든|아흔)/;
const UNITISH = /^(?:년|개월|월|일|시간|분|초|명|개|건|회|번|원|만원|점|퍼센트)/;
const metrics = new Map();
const examples = new Map();
function hit(k, src, canon, extra = '') {
  metrics.set(k, (metrics.get(k) || 0) + 1);
  if (!examples.has(k)) examples.set(k, []);
  const a = examples.get(k);
  if (a.length < 12) a.push({ src, canon, extra });
}
function joined(prep) {
  return prep.sentences.map(s => K.joinSpokenChunks(s.chunks)).join(' | ');
}
function allChunks(prep) {
  return prep.sentences.flatMap(s => s.chunks || []);
}

for (const src of items) {
  let n, prep, canon;
  try {
    n = K.normalize(src);
    prep = K.prepare(src);
    canon = joined(prep);
  } catch (e) {
    hit('exception', src, '', String(e));
    continue;
  }
  if (n !== K.normalize(n)) hit('idempotence', src, canon);
  if (/(?:까지에|까지까지|에서에서|부터부터)/.test(canon)) hit('adjacent-particle-dup', src, canon);
  if (/[,，]\s*[,，]/.test(canon)) hit('double-comma', src, canon);
  if (/[~∼～]/.test(n)) hit('tilde-remains', src, canon, n);
  if (/\d/.test(n)) hit('digit-remains', src, canon, n);
  if (/다음과[, ]*$/.test(canon)) hit('next-and-tail', src, canon);

  const chunks = allChunks(prep).filter(c => c && c.text);
  for (let i = 0; i + 1 < chunks.length; i++) {
    const lw = chunks[i].text.trim().split(/\s+/);
    const rw = chunks[i + 1].text.trim().split(/\s+/);
    const last = (lw[lw.length - 1] || '').replace(/[,.!?]+$/, '');
    const next = (rw[0] || '').replace(/^[('"“”]+|[,.!?]+$/g, '');
    const stem = last.replace(/(?:에서|부터)$/, '');
    const prev = (lw[lw.length - 2] || '').replace(/[,.!?]+$/, '');
    if (/(?:에서|부터)$/.test(last) && NUM.test(next) && (NUM.test(stem) || NUM.test(prev))) {
      hit('range-middle-chunk-break', src, canon, `${chunks[i].text} ⟂ ${chunks[i + 1].text}`);
      break;
    }
    if (NUM.test(last) && UNITISH.test(next)) {
      hit('number-unit-chunk-break', src, canon, `${chunks[i].text} ⟂ ${chunks[i + 1].text}`);
      break;
    }
    if (/다음과$/.test(last) && /^같/.test(next)) {
      hit('daum-gat-chunk-break', src, canon, `${chunks[i].text} ⟂ ${chunks[i + 1].text}`);
      break;
    }
  }

  if (/제\s*\d+\s*조(?:\s*의\s*\d+)?\s*\([^()\n]{1,60}\)/.test(src)) {
    hit('legal-heading-source', src, canon, chunks.slice(0, 4).map(c => `${c.text}{${c.pause}}`).join(' ⟂ '));
  }
  if (/\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}\.?\s*[~∼～]/.test(src)) hit('date-range-source', src, canon);
  if (/\d{1,2}:\d{2}(?::\d{2})?\s*[~∼～]/.test(src)) hit('time-range-source', src, canon);
}

console.log(`AUDIT files=${files.length} candidates=${items.size}`);
for (const [k, v] of [...metrics.entries()].sort((a,b) => b[1]-a[1])) console.log(`METRIC ${k}=${v}`);
for (const [k, a] of examples.entries()) {
  console.log(`\n=== ${k} examples (${a.length}) ===`);
  a.forEach((x, i) => {
    console.log(`[${i+1}] IN : ${x.src}`);
    console.log(`    OUT: ${x.canon}`);
    if (x.extra) console.log(`    AUX: ${x.extra}`);
  });
}

console.log('\n=== synthetic trailing-particle torture ===');
const synthetic = [
  '2026-09-11~2026-09-12에 시행한다.',
  '2026-09-11~2026-09-12에는 시행한다.',
  '2026-09-11~2026-09-12에도 시행한다.',
  '2026-09-11~2026-09-12에만 시행한다.',
  '2026-09-11~2026-09-12까지 시행한다.',
  '2026-09-11~2026-09-12까지는 시행한다.',
  '2026년 9월 11일~2026년 9월 12일에 시행한다.',
  '2026년 9월 11일~2026년 9월 12일까지 시행한다.',
  '18~21개월에 해당한다.',
  '09:00~18:00에 운영한다.'
];
for (const s of synthetic) console.log(`${s} => ${K.normalize(s)}`);
