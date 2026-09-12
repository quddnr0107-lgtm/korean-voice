'use strict';
const fs = require('fs');
const cp = require('child_process');

const providerFile = 'research/tts-arena/providers.json';
const providers = JSON.parse(fs.readFileSync(providerFile, 'utf8'));
const q = providers.providers.find((p) => p.id === 'qwen3-tts-0.6b-customvoice-sohee');
if (!q) throw new Error('Qwen provider missing');
q.korean = 'official-and-verified';
q.cpu_verified = true;
q.required_generation_policy = 'sampling';
q.notes = 'Official Korean support with native Sohee preset. Verified 2026-09-12 on a 4-core GitHub Ubuntu CPU runner. Sampling is required: do_sample=false produced near-silence at the token cap, while sampled inference produced normal Korean speech. In a same-seed K2 pair, raw and ko-voice canonical inputs both reached semantic CER 0.0, so frontend gains must be judged per backend/case; see results/qwen3-tts-06b-sohee-k2-20260912.json.';
fs.writeFileSync(providerFile, JSON.stringify(providers, null, 2) + '\n');

const testFile = 'test/tts-arena.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
if (!tests.includes("Qwen3-TTS Sohee CPU 증거는 sampling 필수와 K2 동률을 보존한다")) {
  tests += `\n\ntest('Qwen3-TTS Sohee CPU 증거는 sampling 필수와 K2 동률을 보존한다', () => {\n` +
`  const p = providers.providers.find((x) => x.id === 'qwen3-tts-0.6b-customvoice-sohee');\n` +
`  assert.equal(p?.korean, 'official-and-verified');\n` +
`  assert.equal(p?.cpu_verified, true);\n` +
`  assert.equal(p?.required_generation_policy, 'sampling');\n` +
`  const r = JSON.parse(fs.readFileSync(path.join(ROOT, 'research/tts-arena/results/qwen3-tts-06b-sohee-k2-20260912.json'), 'utf8'));\n` +
`  assert.equal(r.paid_tts_api_calls, 0);\n` +
`  assert.equal(r.cloudflare_calls, 0);\n` +
`  assert.equal(r.greedy_failure.canonical_cer, 1);\n` +
`  assert.ok(r.greedy_failure.rms < 0.001);\n` +
`  assert.ok(r.sampled_smoke.rms > 0.05);\n` +
`  assert.ok(r.sampled_smoke.canonical_cer < r.greedy_failure.canonical_cer);\n` +
`  assert.equal(r.sampled_k2_pair.raw.canonical_cer, 0);\n` +
`  assert.equal(r.sampled_k2_pair.canonical.canonical_cer, 0);\n` +
`  assert.ok(Math.abs(r.sampled_k2_pair.raw.synthesis_seconds - r.sampled_k2_pair.canonical.synthesis_seconds) < 1);\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs', 'test/tts-arena.test.cjs'], {stdio:'inherit'});
cp.execFileSync('python', ['test/judge_test.py'], {stdio:'inherit'});
