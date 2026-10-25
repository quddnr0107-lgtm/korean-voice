#!/usr/bin/env node
// Fixed public-law phrases only. This probe never loads private course datasets.
import { writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

export function probeChunks() {
  const contexts = [
    { name: 'both-types', r: 0.88, render: (one, two) => `동원훈련${one}과 동원훈련${two}을 구분합니다.` },
    { name: 'article-opening', r: 0.87, render: one => `동원훈련${one}은 다음 각 호에 따릅니다.` },
    { name: 'entry-training', r: 0.87, render: one => `병력동원소집 대상자는 동원훈련${one} 입영훈련을 원칙으로 합니다.` },
  ];
  const variants = [
    { name: 'joined', one: '일형', two: '이형' },
    { name: 'word-space', one: ' 일형', two: ' 이형' },
    { name: 'type-space', one: ' 일 형', two: ' 이 형' },
  ];
  return contexts.flatMap(context => variants.map(variant => {
    const t = context.render(variant.one, variant.two);
    return { t, r: context.r, k: 'easy', w: '훈령', probe: context.name + '/' + variant.name, id: createHash('sha1').update(t).digest('hex').slice(0, 10) };
  }));
}

const chunks = probeChunks();
if (chunks.length !== 9 || new Set(chunks.map(x => x.id)).size !== 9) throw new Error('Expected exactly nine distinct fixed probes');
if (process.argv.includes('--verify')) {
  const response = await fetch('https://korean-voice.quddnr0107.workers.dev/bake/has', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'User-Agent': 'korean-voice-bake/1 (+https://github.com/quddnr0107-lgtm/korean-voice)' },
    body: JSON.stringify({ v: 'female', s: 16, k: 'k2', items: chunks }),
  });
  if (!response.ok) throw new Error('Coverage check failed: ' + response.status);
  const data = await response.json();
  if (!Array.isArray(data.has) || data.has.length !== 9 || data.has.some(x => x !== true)) throw new Error('A probe is missing; synthesis/upload was not complete');
  console.log('Verified all 9 fixed probe MP3 cache keys; no pronunciation judgment implied.');
} else {
  const outIndex = process.argv.indexOf('--out');
  const out = outIndex >= 0 ? process.argv[outIndex + 1] : 'type-pronunciation-probe.json';
  if (!out) throw new Error('Missing output path');
  writeFileSync(out, JSON.stringify(chunks, null, 2) + '\n');
  for (const x of chunks) console.log(x.id + ' ' + x.probe + ' r=' + x.r);
}
