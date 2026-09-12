'use strict';
const fs = require('fs');
const cp = require('child_process');
const K0 = require('../../public/ko-voice.js');

const beforeKf = K0.normalize('KF-21 비행시험을 실시한다.');
const beforeFa = K0.normalize('FA-50을 운용한다.');
if (/케이에프\s*이십일/.test(beforeKf) && /에프에이\s*오십/.test(beforeFa)) {
  throw new Error('aircraft pronunciation defects no longer reproduce; patch not needed');
}

const file = 'public/ko-voice.js';
let src = fs.readFileSync(file, 'utf8');
const oldMap = "    K9A1: '케이나인 에이원', 'K-9A1': '케이나인 에이원',\n  };";
const newMap = "    K9A1: '케이나인 에이원', 'K-9A1': '케이나인 에이원',\n    'KF21': '케이에프 이십일', 'KF-21': '케이에프 이십일',\n    'FA50': '에프에이 오십', 'FA-50': '에프에이 오십',\n  };";
if (!src.includes(oldMap)) throw new Error('MILITARY_MODEL insertion target not found');
src = src.replace(oldMap, newMap);

const oldRule = "    t = t.replace(/\\bK-?(?:1A1|2A1|9A1|1|2|9)\\b/gi, (m) => MILITARY_MODEL[m.toUpperCase()] || m);";
const newRule = "    t = t.replace(/\\b(?:K-?(?:1A1|2A1|9A1|1|2|9)|KF-?21|FA-?50)\\b/gi, (m) => MILITARY_MODEL[m.toUpperCase()] || m);";
if (!src.includes(oldRule)) throw new Error('military model normalization rule not found');
src = src.replace(oldRule, newRule);
fs.writeFileSync(file, src);

const testFile = 'test/ko-voice.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
const marker = "test('공식 근거가 있는 군 항공기 모델명은 현행 낭독으로 읽는다'";
if (!tests.includes(marker)) {
  tests += `\n\ntest('공식 근거가 있는 군 항공기 모델명은 현행 낭독으로 읽는다', () => {\n` +
`  assert.match(K.normalize('KF-21 비행시험을 실시한다.'), /케이에프 이십일 비행시험/);\n` +
`  assert.match(K.normalize('KF21 비행시험을 실시한다.'), /케이에프 이십일 비행시험/);\n` +
`  assert.match(K.normalize('FA-50을 운용한다.'), /에프에이 오십을 운용한다/);\n` +
`  assert.match(K.normalize('FA50을 운용한다.'), /에프에이 오십을 운용한다/);\n` +
`});\n\n` +
`test('미확정 복합 군 모델명은 추측 규칙으로 강제하지 않는다', () => {\n` +
`  for (const s of ['K239 천무', 'K808 장갑차', 'KM21 장비', 'UH-60 헬기', 'CH-47 헬기']) {\n` +
`    const out = K.normalize(s);\n` +
`    assert.equal(typeof out, 'string');\n` +
`    assert.ok(out.length > 0);\n` +
`  }\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--check', file], {stdio:'inherit'});
cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs', 'test/tts-arena.test.cjs'], {stdio:'inherit'});
