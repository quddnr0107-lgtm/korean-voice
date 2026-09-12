'use strict';
const fs = require('fs');
const cp = require('child_process');
const K0 = require('../../public/ko-voice.js');

const b1 = K0.normalize('신검에서 1~3급이면 가능하다.');
if (!/일 급이면에서 삼 급이면/.test(b1)) throw new Error('expected pre-patch 이면 duplication not reproduced: ' + b1);
const b2 = K0.normalize('기준이 1~3급이라면 지원할 수 있다.');
if (!/일 급이라면에서 삼 급이라면/.test(b2)) throw new Error('expected pre-patch 이라면 duplication not reproduced: ' + b2);

const file = 'public/ko-voice.js';
let src = fs.readFileSync(file, 'utf8');
const oldLine = "  const RANGE_PREDICATE = /^(.+?)(이다|입니다|이에요|예요|이었다|였다|이며|이고|이지|이야|까지|으로|로|이|가|은|는|을|를|에|의|과|와)$/;";
const newLine = "  const RANGE_PREDICATE = /^(.+?)(입니다|이에요|이었다|이라면|이다|예요|였다|이며|이고|이지|이야|이면|라면|까지|으로|로|이|가|은|는|을|를|에|의|과|와)$/;";
if (!src.includes(oldLine)) throw new Error('RANGE_PREDICATE target not found');
src = src.replace(oldLine, newLine);
fs.writeFileSync(file, src);

const testFile = 'test/ko-voice.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
if (!tests.includes("test('범위 조건형 이면·이라면은 오른쪽 항에만 남긴다'")) {
  tests += `\n\ntest('범위 조건형 이면·이라면은 오른쪽 항에만 남긴다', () => {\n` +
`  assert.strictEqual(K.normalize('신검에서 1~3급이면 가능하다.'), '신검에서 일 급에서 삼 급이면 가능하다.');\n` +
`  assert.strictEqual(K.normalize('기준이 1~3급이라면 지원할 수 있다.'), '기준이 일 급에서 삼 급이라면 지원할 수 있다.');\n` +
`  assert.strictEqual(K.normalize('복무기간이 18~21개월이면 대상이다.'), '복무기간이 십팔 개월에서 이십일 개월이면 대상이다.');\n` +
`});\n\n` +
`test('조건형 범위 교정은 기존 범위 꼬리를 보존한다', () => {\n` +
`  assert.strictEqual(K.normalize('1~3급이다'), '일 급에서 삼 급이다');\n` +
`  assert.strictEqual(K.normalize('1~3급은'), '일 급에서 삼 급은');\n` +
`  assert.strictEqual(K.normalize('1~3급으로'), '일 급에서 삼 급으로');\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs'], { stdio: 'inherit' });
