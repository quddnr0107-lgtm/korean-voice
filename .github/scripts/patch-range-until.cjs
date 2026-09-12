'use strict';
const fs = require('fs');
const cp = require('child_process');
const K0 = require('../../public/ko-voice.js');

const before = K0.normalize('기사·산업기사면 68~70점대까지.');
if (!before.includes('육십팔 점대까지에서 칠십 점대까지')) throw new Error('expected pre-patch until-duplication defect not reproduced: ' + before);

const file = 'public/ko-voice.js';
let src = fs.readFileSync(file, 'utf8');
const oldLine = "  const RANGE_PREDICATE = /^(.+?)(이다|입니다|이에요|예요|이었다|였다|이며|이고|이지|이야|으로|로|이|가|은|는|을|를|에|의|과|와)$/;";
const newLine = "  const RANGE_PREDICATE = /^(.+?)(이다|입니다|이에요|예요|이었다|였다|이며|이고|이지|이야|까지|으로|로|이|가|은|는|을|를|에|의|과|와)$/;";
if (!src.includes(oldLine)) throw new Error('RANGE_PREDICATE definition not found');
src = src.replace(oldLine, newLine);
fs.writeFileSync(file, src);

const testFile = 'test/ko-voice.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
if (!tests.includes("test('일반 수량 범위의 까지는 오른쪽 끝에만 남긴다'")) {
  tests += `\n\ntest('일반 수량 범위의 까지는 오른쪽 끝에만 남긴다', () => {\n` +
`  assert.strictEqual(K.normalize('68~70점대까지'), '육십팔 점대에서 칠십 점대까지');\n` +
`  assert.strictEqual(K.normalize('68~70점대까지는'), '육십팔 점대에서 칠십 점대까지는');\n` +
`  assert.strictEqual(K.normalize('18~21개월까지'), '십팔 개월에서 이십일 개월까지');\n` +
`  assert.strictEqual(K.normalize('1~3시간까지'), '한 시간에서 세 시간까지');\n` +
`});\n\n` +
`test('범위 까지 교정은 기존 서술어·조사·복합 단위를 보존한다', () => {\n` +
`  assert.strictEqual(K.normalize('복무기간은 18~21개월이다.'), '복무기간은 십팔 개월에서 이십일 개월이다.');\n` +
`  assert.strictEqual(K.normalize('3~6문장으로'), '삼문장에서 육문장으로');\n` +
`  assert.strictEqual(K.normalize('2025~2026년에'), '이천이십오 년에서 이천이십육 년에');\n` +
`  assert.strictEqual(K.normalize('1~4년차'), '일 년차에서 사 년차');\n` +
`  assert.strictEqual(K.normalize('4~5만원이'), '사만 원에서 오만 원이');\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs'], { stdio: 'inherit' });
