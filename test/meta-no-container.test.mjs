import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { json } from '../lib/melotts.mjs';
import { TAGS, RECIPE_TAG } from '../lib/tts-key.mjs';

const src = fs.readFileSync(new URL('../worker.mjs', import.meta.url), 'utf8');

function between(a, b) {
  const i = src.indexOf(a);
  const j = src.indexOf(b, i + a.length);
  assert.ok(i >= 0, `missing start: ${a}`);
  assert.ok(j > i, `missing end: ${b}`);
  return src.slice(i, j);
}

test('/meta is worker-only and never probes the container', () => {
  const body = between('function handleMeta(env) {', '\n\nconst HEALTH_PROBE_MS');
  assert.match(body, /workerMeta\(env\)/);
  assert.doesNotMatch(body, /container\s*\(/);
  assert.doesNotMatch(body, /getContainer\s*\(/);
  assert.doesNotMatch(body, /\.fetch\s*\(/);
  assert.match(body, /container_probe:\s*false/);
});

test('/meta exposes the same recipe and voice-set metadata health uses', () => {
  const meta = between('function workerMeta(env) {', '\nfunction handleMeta');
  assert.match(meta, /Object\.entries\(TAGS\)/);
  assert.match(meta, /recipe_worker:\s*RECIPE_TAG/);
  assert.match(meta, /env\.TTS_CACHE\s*\?\s*'r2'\s*:\s*'none'/);
  const health = between('async function handleHealth(request, env) {', '\n}\n\nexport default');
  assert.match(health, /const 바탕 = workerMeta\(env\)/);
});

test('/meta has a public GET route and CORS preflight', () => {
  assert.match(src, /url\.pathname === '\/meta'\) return handleMeta\(env\)/);
  assert.match(src, /\['\/tts', '\/warm', '\/meta', '\/health'/);
});

// Execute the real router; tripwires count attempts even if a handler catches errors.
function worker() {
  let calls = 0;
  const forbidden = () => { calls++; throw new Error('unexpected service access'); };
  const context = vm.createContext({
    URL, Request, Response, json, TAGS, RECIPE_TAG,
    Container: class {}, DurableObject: class {},
    getContainer: forbidden, fetch: forbidden, setTimeout: forbidden,
  });
  vm.runInContext(src
    .replace(/^import .*;\r?$/gm, '')
    .replace(/^export \{.*;\r?$/gm, '')
    .replace(/^export class /gm, 'class ')
    .replace('export default {', 'globalThis.worker = {'), context);
  const service = new Proxy({}, { get: forbidden });
  return { router: context.worker, service, calls: () => calls };
}

test('/meta GET returns current voice choices without any service access', async () => {
  for (const hasCache of [true, false]) {
    const h = worker();
    const response = await h.router.fetch(new Request('https://voice/meta'), {
      TTS_CACHE: hasCache ? h.service : undefined,
      TTS_CONTAINER: h.service, ASSETS: h.service,
    }, {});
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('Access-Control-Allow-Origin'), '*');
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
    assert.deepEqual(await response.json(), {
      cache: hasCache ? 'r2' : 'none', recipe_worker: RECIPE_TAG,
      voice_sets: Object.entries(TAGS).map(([tag, v]) => ({
        id: `${tag}.${v.rev}`, tag, label: v.label, steps: v.steps,
      })),
      worker_ok: true, ok: true, available: true, container_probe: false,
    });
    assert.equal(h.calls(), 0);
  }
});

test('/meta preflight does not read bindings or call services', async () => {
  const h = worker();
  const response = await h.router.fetch(new Request('https://voice/meta', {
    method: 'OPTIONS', headers: { Origin: 'https://study.example' },
  }), h.service, {});
  assert.equal(response.status, 204);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), '*');
  assert.match(response.headers.get('Access-Control-Allow-Methods'), /GET/);
  assert.equal(await response.text(), '');
  assert.equal(h.calls(), 0);
});

test('service tripwires detect a container probe on the diagnostic route', async () => {
  const h = worker();
  await assert.rejects(h.router.fetch(new Request('https://voice/health'), {
    TTS_CONTAINER: h.service,
  }, {}), /unexpected service access/);
  assert.equal(h.calls(), 1);
});
