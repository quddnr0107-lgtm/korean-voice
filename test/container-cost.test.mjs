import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../worker.mjs', import.meta.url), 'utf8');
const start = source.indexOf('function audioResponse(');
const end = source.indexOf('\n/*', source.indexOf('async function handleWarm('));
function handlers() {
  let calls = 0;
  const context = vm.createContext({
    URL, Response, Request, Uint8Array, CORS: {}, VOICES: ['female'], DEFAULT_STEPS: 16,
    RECIPE_TAG: 'current', FALLBACK: null, TAGS: { current: { steps: 16 } },
    tagOf: () => 'current', cleanText: text => text || '', parseR: Number, cacheKey: async () => 'key',
    json: (data, status, headers) => Response.json(data, { status, headers }),
    container: () => { calls++; return { fetch: async request => request.url.includes('/warm')
      ? Response.json({ queued: 1 }) : new Response(new Uint8Array([1]), { headers: { 'Content-Type': 'audio/mpeg', 'X-TTS-Recipe': 'current' } }) }; }
  });
  vm.runInContext(source.slice(start, end), context);
  return { context, calls: () => calls };
}
test('R2 hits, errors, absent binding and HEAD misses never wake a Container', async () => {
  for (const [get, method, expected] of [
    [async () => ({ body: new Uint8Array([1]), size: 1 }), 'GET', 200],
    [async () => { throw new Error('R2 down'); }, 'GET', 503],
    [async () => null, 'HEAD', 404],
  ]) {
    const h = handlers();
    const r = await h.context.handleLiveTts(new Request('https://voice/tts?t=private', { method }), { TTS_CACHE: { get } }, {});
    assert.equal(r.status, expected); assert.equal(h.calls(), 0);
  }
  const h = handlers();
  assert.equal((await h.context.handleLiveTts(new Request('https://voice/tts?t=a'), {}, {})).status, 503);
  assert.equal(h.calls(), 0);
});
test('confirmed GET miss synthesizes once; warm stops on cache errors', async () => {
  const h = handlers();
  const r = await h.context.handleLiveTts(new Request('https://voice/tts?t=a'), { TTS_CACHE: { get: async () => null, put: async () => {} } }, {});
  assert.equal(r.status, 200); assert.equal(h.calls(), 1);
  for (const head of [async () => true, async () => { throw new Error('R2 down'); }]) {
    const warm = handlers();
    await warm.context.handleWarm(new Request('https://voice/warm', { method: 'POST', body: JSON.stringify({ texts: ['private'] }) }), { TTS_CACHE: { head } });
    assert.equal(warm.calls(), 0);
  }
});
