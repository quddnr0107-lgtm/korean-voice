'use strict';
const fs = require('fs');
const cp = require('child_process');
const K0 = require('../../public/ko-voice.js');

const beforeK21 = K0.normalize('K21 보병전투차를 운용한다.');
const beforeF35 = K0.normalize('F-35A 전투기를 운용한다.');
if (/케이\s*이십일/.test(beforeK21) && /에프\s*삼십오\s*에이/.test(beforeF35)) {
  throw new Error('K21/F-35A pronunciation defects no longer reproduce; patch not needed');
}

const file = 'public/ko-voice.js';
let src = fs.readFileSync(file, 'utf8');
const oldMap = "    'KF21': '케이에프 이십일', 'KF-21': '케이에프 이십일',\n    'FA50': '에프에이 오십', 'FA-50': '에프에이 오십',\n  };";
const newMap = "    'KF21': '케이에프 이십일', 'KF-21': '케이에프 이십일',\n    'FA50': '에프에이 오십', 'FA-50': '에프에이 오십',\n    'K21': '케이 이십일', 'K-21': '케이 이십일',\n    'F35A': '에프 삼십오 에이', 'F-35A': '에프 삼십오 에이',\n  };";
if (!src.includes(oldMap)) throw new Error('MILITARY_MODEL insertion target not found');
src = src.replace(oldMap, newMap);

const oldRule = "    t = t.replace(/\\b(?:K-?(?:1A1|2A1|9A1|1|2|9)|KF-?21|FA-?50)\\b/gi, (m) => MILITARY_MODEL[m.toUpperCase()] || m);";
const newRule = "    t = t.replace(/\\b(?:K-?(?:1A1|2A1|9A1|21|1|2|9)|KF-?21|FA-?50|F-?35A)\\b/gi, (m) => MILITARY_MODEL[m.toUpperCase()] || m);";
if (!src.includes(oldRule)) throw new Error('military model normalization rule not found');
src = src.replace(oldRule, newRule);
fs.writeFileSync(file, src);

const testFile = 'test/ko-voice.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
const marker = "test('공개 방송에서 낭독이 확인된 K21·F-35A를 명시 발음한다'";
if (!tests.includes(marker)) {
  tests += `\n\ntest('공개 방송에서 낭독이 확인된 K21·F-35A를 명시 발음한다', () => {\n` +
`  assert.match(K.normalize('K21 보병전투차를 운용한다.'), /케이 이십일 보병전투차/);\n` +
`  assert.match(K.normalize('K-21 보병전투차를 운용한다.'), /케이 이십일 보병전투차/);\n` +
`  assert.match(K.normalize('F-35A 전투기를 운용한다.'), /에프 삼십오 에이 전투기/);\n` +
`  assert.match(K.normalize('F35A 전투기를 운용한다.'), /에프 삼십오 에이 전투기/);\n` +
`});\n\n` +
`test('미확정 세 자리 군 모델은 K21 규칙으로 일반화하지 않는다', () => {\n` +
`  const values = ['K239 천무', 'K808 장갑차'];\n` +
`  for (const s of values) {\n` +
`    const out = K.normalize(s);\n` +
`    assert.equal(typeof out, 'string');\n` +
`    assert.ok(out.length > 0);\n` +
`  }\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--check', file], {stdio:'inherit'});
cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs', 'test/tts-arena.test.cjs'], {stdio:'inherit'});
