'use strict';
const fs = require('fs');
const cp = require('child_process');
const K0 = require('../../public/ko-voice.js');

const broken = K0.normalize('2026-09-11~2026-09-12에 시행한다.');
if (!broken.includes('일까지에')) throw new Error('expected pre-patch defect not reproduced: ' + broken);
const broken2 = K0.normalize('2026-09-11~2026-09-12까지 시행한다.');
if (!broken2.includes('일까지까지')) throw new Error('expected pre-patch duplicate not reproduced: ' + broken2);

const file = 'public/ko-voice.js';
let s = fs.readFileSync(file, 'utf8');
const old1 = String.raw`    t = t.replace(/\b(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})\.?\s*[~∼～]\s*(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})\.?(?!\d)/g,
      (m, y1, mo1, d1, y2, mo2, d2) => readSino(y1) + ' 년 ' + readWithUnit(mo1, '월') + ' ' + readSino(d1) + ' 일부터 ' + readSino(y2) + ' 년 ' + readWithUnit(mo2, '월') + ' ' + readSino(d2) + ' 일까지');`;
const new1 = String.raw`    t = t.replace(/\b(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})\.?\s*[~∼～]\s*(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})\.?(?!\d)(?:(까지(?:는|도|만)?|에(?:는|도|만)?)(?=\s|[,.!?)]|$))?/g,
      (m, y1, mo1, d1, y2, mo2, d2, tail) => {
        const focus = tail && tail.match(/(?:는|도|만)$/);
        return readSino(y1) + ' 년 ' + readWithUnit(mo1, '월') + ' ' + readSino(d1) + ' 일부터 ' + readSino(y2) + ' 년 ' + readWithUnit(mo2, '월') + ' ' + readSino(d2) + ' 일까지' + (focus ? focus[0] : '');
      });`;
const old2 = String.raw`    t = t.replace(/\b(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?\s*[~∼～]\s*(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?(?!\d)/g,
      (m, y1, mo1, d1, y2, mo2, d2) => readSino(y1) + ' 년 ' + readWithUnit(mo1, '월') + ' ' + readSino(d1) + ' 일부터 ' + readSino(y2) + ' 년 ' + readWithUnit(mo2, '월') + ' ' + readSino(d2) + ' 일까지');`;
const new2 = String.raw`    t = t.replace(/\b(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?\s*[~∼～]\s*(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?(?!\d)(?:(까지(?:는|도|만)?|에(?:는|도|만)?)(?=\s|[,.!?)]|$))?/g,
      (m, y1, mo1, d1, y2, mo2, d2, tail) => {
        const focus = tail && tail.match(/(?:는|도|만)$/);
        return readSino(y1) + ' 년 ' + readWithUnit(mo1, '월') + ' ' + readSino(d1) + ' 일부터 ' + readSino(y2) + ' 년 ' + readWithUnit(mo2, '월') + ' ' + readSino(d2) + ' 일까지' + (focus ? focus[0] : '');
      });`;
if (!s.includes(old1) || !s.includes(old2)) throw new Error('target date range blocks not found');
s = s.replace(old1, new1).replace(old2, new2);
fs.writeFileSync(file, s);

const testFile = 'test/ko-voice.test.cjs';
let t = fs.readFileSync(testFile, 'utf8');
const marker = "test('날짜 범위 뒤 조사는 생성된 까지와 자연스럽게 합친다', () => {";
if (!t.includes(marker)) {
  t += `\n\n${marker}\n` +
`  assert.strictEqual(K.normalize('2026-09-11~2026-09-12에 시행한다.'), '이천이십육 년 구 월 십일 일부터 이천이십육 년 구 월 십이 일까지 시행한다.');\n` +
`  assert.strictEqual(K.normalize('2026-09-11~2026-09-12에는 시행한다.'), '이천이십육 년 구 월 십일 일부터 이천이십육 년 구 월 십이 일까지는 시행한다.');\n` +
`  assert.strictEqual(K.normalize('2026-09-11~2026-09-12에도 시행한다.'), '이천이십육 년 구 월 십일 일부터 이천이십육 년 구 월 십이 일까지도 시행한다.');\n` +
`  assert.strictEqual(K.normalize('2026-09-11~2026-09-12에만 시행한다.'), '이천이십육 년 구 월 십일 일부터 이천이십육 년 구 월 십이 일까지만 시행한다.');\n` +
`  assert.strictEqual(K.normalize('2026-09-11~2026-09-12까지 시행한다.'), '이천이십육 년 구 월 십일 일부터 이천이십육 년 구 월 십이 일까지 시행한다.');\n` +
`  assert.strictEqual(K.normalize('2026-09-11~2026-09-12까지는 시행한다.'), '이천이십육 년 구 월 십일 일부터 이천이십육 년 구 월 십이 일까지는 시행한다.');\n` +
`  assert.strictEqual(K.normalize('2026년 9월 11일~2026년 9월 12일에 시행한다.'), '이천이십육 년 구 월 십일 일부터 이천이십육 년 구 월 십이 일까지 시행한다.');\n` +
`  assert.strictEqual(K.normalize('2026년 9월 11일~2026년 9월 12일까지 시행한다.'), '이천이십육 년 구 월 십일 일부터 이천이십육 년 구 월 십이 일까지 시행한다.');\n` +
`});\n\n` +
`test('날짜 범위 조사 결합은 시각·일반 수량 범위에 새지 않는다', () => {\n` +
`  assert.strictEqual(K.normalize('09:00~18:00에 운영한다.'), '아홉 시부터 십팔 시까지 운영한다.');\n` +
`  assert.strictEqual(K.normalize('18~21개월에 해당한다.'), '십팔 개월에서 이십일 개월에 해당한다.');\n` +
`});\n`;
  fs.writeFileSync(testFile, t);
}

cp.execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs'], { stdio: 'inherit' });
