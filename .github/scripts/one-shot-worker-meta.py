from pathlib import Path

root = Path('.')
worker_p = root / 'worker.mjs'
test_p = root / 'test/meta-no-container.test.mjs'
worker = worker_p.read_text()

old = """const HEALTH_PROBE_MS = 1200;
async function handleHealth(request, env) {
  const voice_sets = Object.entries(TAGS).map(([tag, v]) => ({ id: `${tag}.${v.rev}`, tag, label: v.label, steps: v.steps }));
  const 바탕 = { cache: env.TTS_CACHE ? 'r2' : 'none', recipe_worker: RECIPE_TAG, voice_sets, worker_ok: true };
  const c = container(env);"""
new = """function workerMeta(env) {
  const voice_sets = Object.entries(TAGS).map(([tag, v]) => ({ id: `${tag}.${v.rev}`, tag, label: v.label, steps: v.steps }));
  return { cache: env.TTS_CACHE ? 'r2' : 'none', recipe_worker: RECIPE_TAG, voice_sets, worker_ok: true };
}
function handleMeta(env) {
  return json({ ...workerMeta(env), ok: true, available: true, container_probe: false }, 200, CORS);
}

const HEALTH_PROBE_MS = 1200;
async function handleHealth(request, env) {
  const 바탕 = workerMeta(env);
  const c = container(env);"""
assert old in worker, 'health anchor missing'
worker = worker.replace(old, new, 1)

old_options = "if (request.method === 'OPTIONS' && ['/tts', '/warm', '/health', '/api/tts', '/bake'].includes(url.pathname))"
new_options = "if (request.method === 'OPTIONS' && ['/tts', '/warm', '/meta', '/health', '/api/tts', '/bake'].includes(url.pathname))"
assert old_options in worker, 'OPTIONS route anchor missing'
worker = worker.replace(old_options, new_options, 1)

old_route = """    if (url.pathname === '/bake/prune' && request.method === 'POST') return handleBakePrune(request, env);
    if (url.pathname === '/health') return handleHealth(request, env);"""
new_route = """    if (url.pathname === '/bake/prune' && request.method === 'POST') return handleBakePrune(request, env);
    if (url.pathname === '/meta') return handleMeta(env);
    if (url.pathname === '/health') return handleHealth(request, env);"""
assert old_route in worker, 'route anchor missing'
worker = worker.replace(old_route, new_route, 1)

old_comment = """   GET  /health                           → 컨테이너 상태(잠들어 있으면 깨운다)
   캐시 키는 server.py"""
new_comment = """   GET  /meta                             → 워커 메타데이터(recipe·voice_sets·R2)만, 컨테이너 접근 0
   GET  /health                           → 관리자/진단용 컨테이너 상태(잠들어 있으면 깨울 수 있다)
   캐시 키는 server.py"""
assert old_comment in worker, 'API comment anchor missing'
worker = worker.replace(old_comment, new_comment, 1)
worker_p.write_text(worker)

test_p.write_text(r'''import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

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
''')

print('worker /meta patched; test/meta-no-container.test.mjs written')
