// 옛 조각 폐기(lib/prune.mjs) — 가짜 R2 로 잰다. 🔴 대조군: dry 는 안 지운다 · 짧은 목록은 거부한다.
import test from 'node:test';
import assert from 'node:assert';
import { prune, MIN_KEEP } from '../lib/prune.mjs';

function fakeR2(keys, pageSize = 2) {
  const m = new Set(keys); const deleted = [];
  return {
    m, deleted,
    async list({ prefix, cursor, limit }) {
      const all = [...m].filter((k) => k.startsWith(prefix)).sort();
      const start = cursor ? parseInt(cursor, 10) : 0; const n = Math.min(limit || pageSize, pageSize);
      const objects = all.slice(start, start + n).map((key) => ({ key }));
      const truncated = start + n < all.length;
      return { objects, truncated, cursor: truncated ? String(start + n) : undefined };
    },
    async delete(ks) { for (const k of ks) { deleted.push(k); m.delete(k); } },
  };
}
const many = (n, p = 'tts/female/') => Array.from({ length: n }, (_, i) => `${p}${String(i).padStart(4, '0')}.mp3`);

test('dry — 목록 밖 키를 세지만 지우지 않는다 · 페이지를 끝까지 돈다', async () => {
  const keep = many(MIN_KEEP); const r2 = fakeR2([...keep.slice(0, 3), 'tts/female/old1.mp3', 'tts/male/old2.mp3', 'other/x'], 2);
  const r = await prune({ r2, keep, dry: true });
  assert.equal(r.ok, true); assert.equal(r.total, 5); assert.equal(r.hit, 3); assert.equal(r.drop, 2); assert.equal(r.deleted, 0);
  assert.deepEqual(r2.deleted, []); assert.equal(r.pages, 3); assert.equal(r.truncated, false);
});
test('실행 — 목록 밖 키만 지운다(other/ 접두는 안 본다)', async () => {
  const keep = many(MIN_KEEP); const r2 = fakeR2([...keep.slice(0, 3), 'tts/female/old1.mp3', 'tts/male/old2.mp3', 'other/x']);
  const r = await prune({ r2, keep, dry: false });
  assert.equal(r.deleted, 2); assert.deepEqual(r2.deleted.sort(), ['tts/female/old1.mp3', 'tts/male/old2.mp3']);
  assert.ok(r2.m.has('other/x') && r2.m.has(keep[0]));
});
test('[음성] 짧은 목록은 거부한다 — 빈 목록으로 부르면 전부 지워지기 때문 · force 로만 연다', async () => {
  const r2 = fakeR2(['tts/female/a.mp3']);
  const r = await prune({ r2, keep: [], dry: false });
  assert.equal(r.ok, false); assert.equal(r.error, 'keep_too_small'); assert.equal(r2.deleted.length, 0);
  const f = await prune({ r2, keep: [], dry: false, force: true });
  assert.equal(f.deleted, 1);
});
test('Set 을 그대로 받는다 · 지울 것이 없으면 0', async () => {
  const keep = new Set(many(MIN_KEEP)); const r2 = fakeR2([...keep].slice(0, 4));
  const r = await prune({ r2, keep, dry: false });
  assert.equal(r.drop, 0); assert.equal(r.deleted, 0);
});
