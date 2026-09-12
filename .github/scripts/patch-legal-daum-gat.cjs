'use strict';
const fs = require('fs');
const cp = require('child_process');
const K0 = require('../../public/ko-voice.js');
const say = (s, K = K0) => K.prepare(s).sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');

const before = say('제5조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다.');
if (!/다음과,\s*같다/.test(before)) throw new Error('expected pre-patch legal collocation break not reproduced: ' + before);

const file = 'public/ko-voice.js';
let src = fs.readFileSync(file, 'utf8');
const old1 = "      const 범위중간 = 범위연결(bare, words[i - 1], words[i + 1]);\n      if (/,$/.test(w)) { flush(PAUSE.comma); continue; }";
const new1 = "      const 범위중간 = 범위연결(bare, words[i - 1], words[i + 1]);\n      // 법령의 고정 결합 '다음과 같다/같이'는 조사 '과' 뒤 자동 호흡으로 갈라지면 의미가 어색해진다.\n      const 다음과같 = bare === '다음과' && /^같/.test(String(words[i + 1] || '').replace(/[,.!?]+$/, ''));\n      if (/,$/.test(w)) { flush(PAUSE.comma); continue; }";
const old2 = "      if (syl >= 12 && WEAK_BREAK_PARTICLE.test(bare) && !범위중간 && !단위앞) { flush(PAUSE.weak); continue; }";
const new2 = "      if (syl >= 12 && WEAK_BREAK_PARTICLE.test(bare) && !범위중간 && !다음과같 && !단위앞) { flush(PAUSE.weak); continue; }";
if (!src.includes(old1) || !src.includes(old2)) throw new Error('phraseSentence target not found');
src = src.replace(old1, new1).replace(old2, new2);
fs.writeFileSync(file, src);

const testFile = 'test/ko-voice.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
if (!tests.includes("test('법령 고정 결합 다음과 같다·같이는 한 호흡으로 읽는다'")) {
  tests += `\n\ntest('법령 고정 결합 다음과 같다·같이는 한 호흡으로 읽는다', () => {\n` +
`  const say = (s) => K.prepare(s).sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');\n` +
`  const a = say('제5조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다.');\n` +
`  const b = say('세부 처리 절차와 필요한 서류는 다음과 같이 정한다.');\n` +
`  assert.ok(!/다음과,\\s*같다/.test(a), a);\n` +
`  assert.ok(/다음과 같다/.test(a), a);\n` +
`  assert.ok(!/다음과,\\s*같이/.test(b), b);\n` +
`  assert.ok(/다음과 같이/.test(b), b);\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs'], { stdio: 'inherit' });
