'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const result = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'research/tts-arena/results/supertonic-browser-fp16-wasm-five-case-20260912.json'), 'utf8'));

test('browser Supertonic WASM은 무조건 live 기본값이 아니다', () => {
  assert.equal(result.execution.paid_tts_api_calls, 0);
  assert.equal(result.execution.cloudflare_calls, 0);
  assert.equal(result.execution.audio_git_commit, 0);
  assert.equal(result.runtime.provider, 'wasm');
  assert.equal(result.cases.length, 5);
  assert.ok(result.cases.every((x) => x.rtf > 1));
  assert.ok(result.summary.aggregate_rtf > 1.8);
  assert.equal(result.summary.near_silence_cases, 0);
  assert.equal(result.summary.decision.wasm_unconditional_live_default, false);
  assert.equal(result.summary.decision.require_runtime_speed_gate, true);
  assert.equal(result.summary.decision.fallback_required, true);
});
