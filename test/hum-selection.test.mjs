// Targeted repair must never expand into a full bake or silently lose a requested chunk.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const id = (text) => createHash('sha1').update(text).digest('hex').slice(0, 10);
const chunks = [
  { t: '선택 전의 다른 과목.', r: 0.9, k: 'study', w: '예비군법' },
  { t: '첫 번째 재검사 문장.', r: 0.92, k: 'study', w: '훈령' },
  { t: '같은 과목의 정상 문장.', r: 0.92, k: 'study', w: '훈령' },
  { t: '두 번째 재검사 문장.', r: 0.94, k: 'study', w: '훈령' },
  { t: '다른 갈래의 정상 문장.', r: 0.94, k: 'exam', w: '훈령' },
  { t: '세 번째 재검사 문장.', r: 0.96, k: 'study', w: '훈령' },
];
const expected = [chunks[1], chunks[3], chunks[5]];
const ids = expected.map((it) => id(it.t)).join(',');

function fixture(t, items = chunks) {
  const dir = mkdtempSync(join(tmpdir(), 'hum-selection-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const source = join(dir, 'chunks.json'), out = join(dir, 'bake-chunks.json');
  const original = JSON.stringify(items);
  writeFileSync(source, original);
  const select = (args = []) => spawnSync('python3', ['-S', 'tools/hum.py', '--select-chunks', source, '--out', out, ...args], { cwd: ROOT, encoding: 'utf8' });
  return { source, out, original, select };
}

test('three exact IDs produce one study/훈령 job; other chunks and full source stay intact', async (t) => {
  const f = fixture(t);
  const result = f.select(['--chunk-ids', ids, '--kind', 'study']);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(readFileSync(f.out)), expected);
  assert.equal(readFileSync(f.source, 'utf8'), f.original);
  assert.equal(JSON.parse(result.stdout).selected_count, 3);
  assert.ok(chunks.every((it) => !result.stdout.includes(it.t)), 'logs contain no manuscript');
  // Execute the real planner in-process: restricted runtimes may deny a nested Node process.
  const savedArgs = process.argv, savedEnv = process.env, savedLog = console.log;
  const planned = [];
  try {
    process.argv = ['node', 'tools/plan.mjs', f.out];
    process.env = { ...savedEnv, ORDER: 'true', KIND: 'study', SHARDS: '40', PER: '300' };
    console.log = (value) => planned.push(value);
    await import('../tools/plan.mjs');
  } finally {
    process.argv = savedArgs; process.env = savedEnv; console.log = savedLog;
  }
  assert.deepEqual(JSON.parse(planned[0]), [{ k: 'study', w: '훈령', shard: 0, shards: 1 }]);
});

test('no IDs preserve the existing all-chunks selection', (t) => {
  const f = fixture(t);
  const result = f.select();
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(readFileSync(f.out)), chunks);
});

test('malformed, duplicate, unmatched and wrong-scope IDs fail without replacing output', (t) => {
  const f = fixture(t);
  for (const args of [
    ['--chunk-ids', 'bad'],
    ['--chunk-ids', `${ids},${id(expected[0].t)}`],
    ['--chunk-ids', `${ids},0000000000`],
    ['--chunk-ids', ids, '--kind', 'exam'],
    ['--chunk-ids', ids, '--law', '병역법'],
  ]) {
    writeFileSync(f.out, 'do not replace');
    const result = f.select(args);
    assert.equal(result.status, 2, result.stderr);
    assert.equal(readFileSync(f.out, 'utf8'), 'do not replace');
  }
});

test('same text at two speeds is ambiguous and cannot update both cache keys', (t) => {
  const f = fixture(t, [...chunks, { ...expected[0], r: 1.1 }]);
  const result = f.select(['--chunk-ids', ids]);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /expected exactly one match in scope, found 2/);
});

test('targeted selection rejects truncation and deletion settings', (t) => {
  const f = fixture(t);
  for (const args of [['--start', '1'], ['--limit', '1'], ['--prune', 'true']]) {
    const result = f.select(['--chunk-ids', ids, ...args]);
    assert.equal(result.status, 2);
    assert.match(result.stderr, /requires start=0, limit=0 and prune=false/);
  }
});

test('bake validates unmatched IDs before loading any model or third-party modules', (t) => {
  const f = fixture(t);
  const result = spawnSync('python3', ['-S', 'tools/bake.py', '--chunks', f.source, '--chunk-ids', '0000000000'], { cwd: ROOT, encoding: 'utf8' });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /expected exactly one match in scope, found 0/);
  assert.doesNotMatch(result.stderr, /ModuleNotFoundError/);
});
