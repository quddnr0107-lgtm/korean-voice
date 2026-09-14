/* 전역 하루 상한 — 지출 천장이 실제로 걸리는지 워커 코드를 그대로 돌려 잰다(vm · 컨테이너 없이).
   test/container-cost.test.mjs 와 같은 방식이다. 확인하는 것:
     ① R2 적중은 아무 한도도 쓰지 않는다(합성이 없다)
     ② 전역이 남은 양보다 큰 요청은 429 global_cap_reached 이고 **개인 한도를 깎지 않는다**
     ③ 통과하면 개인·전역 둘 다 센다
     ④ DAILY_CHAR_CAP(vars)로 상한을 덮어쓸 수 있다 */
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { normalizeItems, renderKey, dayStamp, quota, FREE_DAILY_CHARS, GLOBAL_DAILY_CHARS, globalCap, LIMITS } from '../lib/render.mjs';

const source = readFileSync(new URL('../worker.mjs', import.meta.url), 'utf8');
const start = source.indexOf('function audioResponse(');
const end = source.indexOf('\n/*', source.indexOf('async function handleWarm('));

function harness({ r2Hit = false, cap = undefined, used = {} } = {}) {
  const store = { ...used };                    // 오브젝트 이름 → { day, used }
  let containerCalls = 0;
  const stub = (name) => ({
    async use(day, chars, limit) {
      const cur = store[name] && store[name].day === day ? store[name].used : 0;
      const q = quota(cur, chars, limit);
      if (q.ok) store[name] = { day, used: q.after };
      return q;
    },
    async peek(day, limit) {
      const cur = store[name] && store[name].day === day ? store[name].used : 0;
      return quota(cur, 0, limit);
    },
  });
  const env = {
    DAILY_CHAR_CAP: cap,
    BAKE: { idFromName: (n) => n, get: (n) => stub(n) },
    TTS_CACHE: {
      async get() { return r2Hit ? { body: new Uint8Array([1]), size: 1 } : null; },
      async put() {},
    },
  };
  const context = vm.createContext({
    URL, Response, Request, Uint8Array, TextEncoder, crypto, performance, console,
    CORS: {}, VOICES: ['female', 'male'], DEFAULT_STEPS: 8,
    RECIPE_TAG: 'u5', FALLBACK: null, TAGS: { u5: { steps: 8 } }, tagOf: () => 'u5',
    cleanText: (t) => (t || '').trim(), parseR: (x) => (x == null ? 1 : Number(x)),
    cacheKey: async () => 'tts/female/k.mp3', sha1: async (s) => 'h' + s.length,
    normalizeItems, renderKey, dayStamp, quota, FREE_DAILY_CHARS, GLOBAL_DAILY_CHARS, globalCap, LIMITS,
    json: (data, status, headers) => Response.json(data, { status, headers }),
    container: () => { containerCalls++; return { fetch: async () => new Response(new Uint8Array([1, 2, 3]), { headers: { 'Content-Type': 'audio/mpeg', 'X-TTS-Recipe': 'u5' } }) }; },
  });
  vm.runInContext(source.slice(start, end), context);
  return { context, env, store, calls: () => containerCalls };
}
const post = (items) => new Request('https://v/render', { method: 'POST', headers: { 'Content-Type': 'application/json', 'CF-Connecting-IP': '1.2.3.4' }, body: JSON.stringify({ v: 'female', items }) });
const items = (chars) => [{ t: '가'.repeat(chars), pause: 0, r: 1 }];

test('R2 적중은 개인·전역 한도를 쓰지 않는다', async () => {
  const h = harness({ r2Hit: true });
  const res = await h.context.handleRender(post(items(500)), h.env, {});
  assert.equal(res.status, 200);
  assert.equal(h.calls(), 0, '컨테이너를 깨우지 않는다');
  assert.deepEqual(h.store, {}, '아무것도 세지 않았다');
});

test('전역 상한을 넘는 요청은 429 이고 개인 한도를 깎지 않는다', async () => {
  const day = dayStamp();
  const h = harness({ cap: '1000', used: { 'meter|global': { day, used: 900 } } });
  const res = await h.context.handleRender(post(items(200)), h.env, {});
  assert.equal(res.status, 429);
  const body = await res.json();
  assert.equal(body.error, 'global_cap_reached');
  assert.equal(body.remaining, 100);
  assert.equal(h.calls(), 0, '컨테이너를 깨우지 않는다');
  assert.ok(!Object.keys(h.store).some((k) => k.startsWith('meter|h')), '개인 계량기를 건드리지 않았다: ' + JSON.stringify(h.store));
});

test('통과하면 개인과 전역 둘 다 센다', async () => {
  const h = harness({ cap: '10000' });
  const res = await h.context.handleRender(post(items(300)), h.env, {});
  assert.equal(res.status, 200);
  assert.equal(h.calls(), 1);
  const g = h.store['meter|global'];
  assert.equal(g.used, 300, '전역이 세어졌다');
  const personal = Object.entries(h.store).find(([k]) => k !== 'meter|global');
  assert.ok(personal, '개인 계량기가 없다');
  assert.equal(personal[1].used, 300);
});

test('개인 하루 한도(3,000자)를 넘으면 429 quota_exceeded', async () => {
  const h = harness({ cap: '1000000' });
  let res = await h.context.handleRender(post(items(400)), h.env, {});   // 조각 400자 × 8 = 3,200자
  for (let i = 0; i < 7; i++) res = await h.context.handleRender(post(items(400)), h.env, {});
  assert.equal(res.status, 429);
  assert.equal((await res.json()).error, 'quota_exceeded');
});

test('vars.DAILY_CHAR_CAP 가 없으면 기본 상한을 쓴다', () => {
  assert.equal(globalCap({}), GLOBAL_DAILY_CHARS);
  assert.equal(globalCap({ DAILY_CHAR_CAP: '5000' }), 5000);
  assert.equal(globalCap({ DAILY_CHAR_CAP: 'abc' }), GLOBAL_DAILY_CHARS);
  assert.equal(globalCap({ DAILY_CHAR_CAP: '0' }), GLOBAL_DAILY_CHARS);
});

test('GET /render 가 전역 남은 양을 알려준다', async () => {
  const day = dayStamp();
  const h = harness({ cap: '50000', used: { 'meter|global': { day, used: 20000 } } });
  const res = await h.context.handleRenderQuota(h.env, new Request('https://v/render', { headers: { 'CF-Connecting-IP': '1.2.3.4' } }));
  const body = await res.json();
  assert.equal(body.global.limit, 50000);
  assert.equal(body.global.remaining, 30000);
});
