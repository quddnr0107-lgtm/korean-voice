/* 브라우저 다듬기(public/voice-shape.mjs)가 서버(server/voice_shape.py)와 어긋나지 않는지 잰다.
   같은 소리를 두 곳에서 내므로 상수가 갈리면 목소리가 갈린다 — test/tts-key.test.mjs 와 같은 취지다. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { RECIPE, trim, fade, onsetBoost, tailTrim, shape } from '../public/voice-shape.mjs';

const SR = 24000;
function tone({ leadS = 0, toneS = 0.5, tailS = 0, amp = 0.5, hz = 200 } = {}) {
  const n = Math.round(SR * (leadS + toneS + tailS));
  const w = new Float32Array(n);
  const start = Math.round(SR * leadS);
  for (let i = 0; i < Math.round(SR * toneS); i++) w[start + i] = amp * Math.sin(2 * Math.PI * hz * i / SR);
  return w;
}

test('서버 레시피와 숫자가 같다 — 갈리면 목소리가 갈린다', () => {
  const py = readFileSync(new URL('../server/voice_shape.py', import.meta.url), 'utf8');
  const recipe = py.slice(py.indexOf('RECIPE = {'), py.indexOf('FRAME_S'));
  assert.match(recipe, /'onset_boost':\s*2\.0/);
  assert.equal(RECIPE.onsetBoost, 2.0);
  assert.match(recipe, /'lead_pad_ms':\s*50/);
  assert.equal(RECIPE.leadPadMs, 50);
  // 피크·문턱은 py 쪽 함수 본문에 있다
  assert.match(py, /\* 0\.89/);
  assert.equal(RECIPE.peak, 0.89);
  assert.match(py, /thresh_db=-45\.0/);
  assert.equal(RECIPE.trimThreshDb, -45);
  assert.match(py, /drop_db=40\.0, keep_ms=60/);
  assert.equal(RECIPE.tailDropDb, 40);
  assert.equal(RECIPE.tailKeepMs, 60);
});

test('앞뒤 무음을 자르고 앞에 50ms 여유를 남긴다', () => {
  const w = tone({ leadS: 0.3, toneS: 0.5, tailS: 0.4 });
  const t = trim(w, SR);
  const sec = t.length / SR;
  assert.ok(sec > 0.5 && sec < 0.72, '말 0.5초 + 앞뒤 여유 남짓이어야 한다: ' + sec.toFixed(3));
});

test('말이 끝난 뒤 죽은 공백을 자른다 — 1.5초 무음이 붙어도', () => {
  const w = tone({ toneS: 0.5, tailS: 1.5 });
  const out = tailTrim(w, SR);
  assert.ok(out.length / SR < 0.65, '꼬리가 남았다: ' + (out.length / SR).toFixed(3));
  assert.ok(out.length / SR > 0.5, '말을 깎았다: ' + (out.length / SR).toFixed(3));
});

test('첫 음절을 2.0배에서 1.0배로 내리며 보강한다', () => {
  const w = new Float32Array(SR); w.fill(0.25);
  const out = onsetBoost(w, SR);
  assert.ok(Math.abs(out[0] - 0.5) < 1e-6, '첫 샘플은 2.0배: ' + out[0]);
  const at150 = Math.floor(SR * 0.15) - 1;
  assert.ok(Math.abs(out[at150] - 0.25) < 1e-3, '150ms 뒤엔 1.0배: ' + out[at150]);
  assert.equal(out[SR - 1], 0.25);
});

test('페이드는 양끝만 0 으로 만든다', () => {
  const w = new Float32Array(SR); w.fill(0.5);
  const out = fade(w, SR);
  assert.equal(out[0], 0);
  assert.ok(out[out.length - 1] < 0.01);
  assert.equal(out[Math.floor(SR / 2)], 0.5);
});

test('shape 은 조각 피크를 0.89 로 맞춘다(서버와 같다)', () => {
  for (const amp of [0.05, 0.5, 0.99]) {
    const out = shape(tone({ leadS: 0.2, toneS: 0.4, tailS: 0.6, amp }), SR);
    let peak = 0; for (let i = 0; i < out.length; i++) peak = Math.max(peak, Math.abs(out[i]));
    assert.ok(Math.abs(peak - 0.89) < 1e-3, 'amp ' + amp + ' → 피크 ' + peak.toFixed(3));
  }
});

test('무음만 들어오면 그대로 돌려준다(0 으로 나누지 않는다)', () => {
  const out = shape(new Float32Array(SR), SR);
  assert.equal(out.length > 0, true);
  for (let i = 0; i < out.length; i++) assert.equal(out[i], 0);
});
