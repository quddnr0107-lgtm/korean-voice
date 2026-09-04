// 굽기 대기열(lib/bake.mjs) — 가짜 저장소·가짜 R2·가짜 컨테이너로 흐름을 잰다.
import test from 'node:test';
import assert from 'node:assert';
import { makeBaker, memoryStorage, MAX_TRIES, MISMATCH_WAIT_MS, TICK_MS } from '../lib/bake.mjs';
import { cacheKey, RECIPE_TAG } from '../lib/tts-key.mjs';

function fakeR2(initial = []) {
  const m = new Map(initial.map((k) => [k, true]));
  return { m, async head(k) { return m.has(k) ? { key: k } : null; }, async put(k, bytes) { m.set(k, bytes); } };
}
function fakeContainer({ recipe = RECIPE_TAG, fail = () => false } = {}) {
  const calls = [];
  return {
    calls,
    async fetch(path, init) {
      calls.push({ path, init });
      if (path === '/warm') return new Response('{"ok":true}', { status: 200 });
      const t = decodeURIComponent(new URL('http://x' + path).searchParams.get('t'));
      if (fail(t)) return new Response('boom', { status: 502 });
      return new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { 'Content-Type': 'audio/mpeg', 'X-TTS-Recipe': recipe } });
    },
  };
}
function harness(opts = {}) {
  const storage = memoryStorage(); const r2 = fakeR2(opts.r2 || []); const c = fakeContainer(opts);
  let t = 1000; const alarms = [];
  const baker = makeBaker({ storage, r2, fetchContainer: (p, i) => c.fetch(p, i), recipeTag: RECIPE_TAG, now: () => t, setAlarm: async (at) => { alarms.push(at); }, batch: 2, warmAhead: 3 });
  return { storage, r2, c, baker, alarms, tick: (ms) => { t += ms; } };
}

test('enqueue — R2 에 있는 것은 건너뛰고 · 같은 키는 한 번만 · 알람을 건다', async () => {
  const already = await cacheKey('female', 16, 1, '이미 있다.');
  const h = harness({ r2: [already] });
  const r = await h.baker.enqueue({ v: 'female', s: 16, items: [{ t: '이미 있다.' }, { t: '새 문장.' }, { t: '새 문장.' }, { t: '  새  문장. ' }, { t: '느린 문장.', r: 0.86 }] });
  assert.deepStrictEqual([r.queued, r.skipped, r.pending], [2, 1, 2]);
  assert.strictEqual(h.alarms.length, 1);
  const st = await h.baker.status();
  assert.strictEqual(st.pending, 2); assert.strictEqual(st.started_at, 1000);
});

test('tick — 앞서 굽기(/warm)를 보내고 · 조각을 R2 에 넣고 · 다음 알람 · 다 끝나면 finished_at', async () => {
  const h = harness();
  await h.baker.enqueue({ items: [{ t: '하나.' }, { t: '둘.' }, { t: '셋.' }] });
  const t1 = await h.baker.tick();
  assert.strictEqual(t1.done, 2);
  assert.ok(h.c.calls.some((c) => c.path === '/warm' && JSON.parse(c.init.body).texts.length === 3), '앞서 굽기가 3개를 보낸다');
  assert.strictEqual(h.r2.m.size, 2);
  assert.strictEqual(h.alarms[h.alarms.length - 1], 1000 + TICK_MS);
  const t2 = await h.baker.tick(); assert.strictEqual(t2.done, 1);
  const t3 = await h.baker.tick(); assert.strictEqual(t3.idle, true);
  const st = await h.baker.status();
  assert.deepStrictEqual([st.done, st.pending, st.error], [3, 0, 0]); assert.ok(st.finished_at);
});

test('표식이 다르면 넣지 않고 60초 뒤 다시 본다(옛 컨테이너 이미지 창)', async () => {
  const h = harness({ recipe: 'old' });
  await h.baker.enqueue({ items: [{ t: '하나.' }] });
  const r = await h.baker.tick();
  assert.strictEqual(r.mismatch, true);
  assert.strictEqual(h.r2.m.size, 0, 'R2 에 넣으면 안 된다');
  assert.strictEqual(h.alarms[h.alarms.length - 1], 1000 + MISMATCH_WAIT_MS);
  assert.strictEqual((await h.baker.status()).pending, 1);
});

test('실패 조각은 MAX_TRIES 뒤 e: 로 옮기고 나머지는 계속 간다', async () => {
  const h = harness({ fail: (t) => t === '깨진다.' });
  await h.baker.enqueue({ items: [{ t: '깨진다.' }, { t: '멀쩡하다.' }] });
  for (let i = 0; i < MAX_TRIES + 1; i++) await h.baker.tick();
  const st = await h.baker.status();
  assert.deepStrictEqual([st.done, st.error, st.pending], [1, 1, 0]);
  assert.strictEqual(st.error_sample[0].t, '깨진다.');
});

test('stop 은 알람을 멈추고 resume 은 다시 건다 · clear 는 대기열을 비운다', async () => {
  const h = harness();
  await h.baker.enqueue({ items: [{ t: '하나.' }, { t: '둘.' }, { t: '셋.' }] });
  await h.baker.stop();
  assert.strictEqual((await h.baker.tick()).stopped, true);
  const n = h.alarms.length; await h.baker.resume(); assert.strictEqual(h.alarms.length, n + 1);
  await h.baker.clear();
  assert.strictEqual((await h.baker.status()).pending, 0);
  assert.strictEqual((await h.baker.tick()).idle, true);
});

test('같은 조각을 다시 보내면 대기열에 두 번 안 들어간다(재실행이 멱등) · recount 가 pending 을 저장소에서 다시 센다', async () => {
  const h = harness();
  const a = await h.baker.enqueue({ items: [{ t: '하나.' }, { t: '둘.' }] });
  const b = await h.baker.enqueue({ items: [{ t: '하나.' }, { t: '셋.' }] });
  assert.deepStrictEqual([a.queued, b.queued, b.skipped], [2, 1, 1]);
  await h.storage.put('stat', { ...(await h.storage.get('stat')), pending: 99 });   // 집계가 어긋난 척
  const st = await h.baker.recount();
  assert.strictEqual(st.pending, 3);
  await h.baker.tick(); await h.baker.tick();
  assert.strictEqual([...h.storage._map.keys()].filter((k) => k.startsWith('k:')).length, 0, '다 구우면 키 색인도 비운다');
});
