import test from 'node:test';
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
