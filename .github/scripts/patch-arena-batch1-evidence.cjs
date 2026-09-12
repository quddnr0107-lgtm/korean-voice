'use strict';
const fs = require('fs');
const cp = require('child_process');

const caseFile = 'research/tts-arena/cases.json';
let cases = fs.readFileSync(caseFile, 'utf8');
const oldK21 = '    {"id":"research-k21","domain":"military","gate":false,"source":"K21 보병전투차를 운용한다.","target":null,"research_question":"현장 표준 낭독이 케이 이십일인지 케이 이원인지 공개 군 자료/청취로 확정"},';
const newK21 = '    {"id":"mil-k21","domain":"military","gate":true,"source":"K21 보병전투차를 운용한다.","target":"케이 이십일 보병전투차를 운용한다"},';
const oldF35 = '    {"id":"research-f35a","domain":"military","gate":false,"source":"F-35A를 운용한다.","target":null,"research_question":"영문·숫자·개량형 혼합 모델 낭독 규칙 확정"},';
const newF35 = '    {"id":"mil-f35a","domain":"military","gate":true,"source":"F-35A를 운용한다.","target":"에프 삼십오 에이를 운용한다"},';
if (!cases.includes(oldK21) || !cases.includes(oldF35)) throw new Error('research K21/F-35A rows not found');
cases = cases.replace(oldK21, newK21).replace(oldF35, newF35);
fs.writeFileSync(caseFile, cases);

const testFile = 'test/tts-arena.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
const marker = "test('MOSS Nano batch1 증거와 K21 F-35A 승격을 보존한다'";
if (!tests.includes(marker)) {
  tests += `\n\ntest('MOSS Nano batch1 증거와 K21 F-35A 승격을 보존한다', () => {\n` +
`  const k21 = cases.cases.find((c) => c.id === 'mil-k21');\n` +
`  const f35 = cases.cases.find((c) => c.id === 'mil-f35a');\n` +
`  assert.equal(k21?.gate, true);\n` +
`  assert.equal(k21?.target, '케이 이십일 보병전투차를 운용한다');\n` +
`  assert.equal(f35?.gate, true);\n` +
`  assert.equal(f35?.target, '에프 삼십오 에이를 운용한다');\n` +
`  assert.equal(cases.cases.some((c) => c.id === 'research-k21' || c.id === 'research-f35a'), false);\n` +
`  const result = JSON.parse(fs.readFileSync(path.join(ROOT, 'research/tts-arena/results/moss-nano-batch1-20260912.json'), 'utf8'));\n` +
`  assert.equal(result.paid_tts_api_calls, 0);\n` +
`  assert.equal(result.cloudflare_calls, 0);\n` +
`  assert.equal(result.summary.pair_count, 6);\n` +
`  assert.equal(result.summary.canonical_improves_or_ties, 5);\n` +
`  assert.equal(result.summary.canonical_worse, 1);\n` +
`  const k2 = result.pairs.find((p) => p.id === 'mil-k2');\n` +
`  const legal = result.pairs.find((p) => p.id === 'legal-chain');\n` +
`  assert.equal(k2.canonical_cer_canonical, 0);\n` +
`  assert.ok(k2.canonical_synth_seconds < k2.raw_synth_seconds);\n` +
`  assert.ok(legal.canonical_cer_canonical > legal.raw_cer_canonical);\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs', 'test/tts-arena.test.cjs'], {stdio:'inherit'});
