import fs from 'node:fs';

const runtimePath = 'public/ko-voice.js';
const testPath = 'test/ko-voice.test.cjs';

let runtime = fs.readFileSync(runtimePath, 'utf8');
const oldRange = String.raw`    t = t.replace(/\b(\d{1,2}):(\d{2})(?::(\d{2}))?\s*[~∼～]\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\b/g,
      (m, h1, m1, s1, h2, m2, s2) => readClock(h1, m1, s1) + '부터 ' + readClock(h2, m2, s2) + '까지');`;
const newRange = String.raw`    t = t.replace(/\b(\d{1,2}):(\d{2})(?::(\d{2}))?\s*[~∼～]\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\b(?:(까지(?:는|도|만)?|에(?:는|도|만)?)(?=\s|[,.!?)]|$))?/g,
      (m, h1, m1, s1, h2, m2, s2, tail) => {
        const focus = tail && tail.match(/(?:는|도|만)$/);
        return readClock(h1, m1, s1) + '부터 ' + readClock(h2, m2, s2) + '까지' + (focus ? focus[0] : '');
      });`;

if (!runtime.includes(oldRange)) {
  throw new Error('expected time-range normalization block not found exactly once');
}
if (runtime.indexOf(oldRange) !== runtime.lastIndexOf(oldRange)) {
  throw new Error('time-range normalization block appears more than once');
}
runtime = runtime.replace(oldRange, newRange);
fs.writeFileSync(runtimePath, runtime);

let test = fs.readFileSync(testPath, 'utf8');
const marker = "test('시각 범위 뒤 조사는 생성된 까지와 자연스럽게 합친다'";
if (!test.includes(marker)) {
  test += String.raw`

test('시각 범위 뒤 조사는 생성된 까지와 자연스럽게 합친다', () => {
  assert.strictEqual(K.normalize('운영시간은 09:00~18:00에 한정한다.'), '운영시간은 아홉 시부터 십팔 시까지 한정한다.');
  assert.strictEqual(K.normalize('09:00~18:00에는 출입할 수 있다.'), '아홉 시부터 십팔 시까지는 출입할 수 있다.');
  assert.strictEqual(K.normalize('09:00~18:00에도 출입할 수 있다.'), '아홉 시부터 십팔 시까지도 출입할 수 있다.');
  assert.strictEqual(K.normalize('09:00~18:00에만 출입할 수 있다.'), '아홉 시부터 십팔 시까지만 출입할 수 있다.');
  assert.strictEqual(K.normalize('회의는 09:00~18:00까지 진행한다.'), '회의는 아홉 시부터 십팔 시까지 진행한다.');
  assert.strictEqual(K.normalize('회의는 09:00~18:00까지는 진행한다.'), '회의는 아홉 시부터 십팔 시까지는 진행한다.');
  assert.strictEqual(K.normalize('09:00~18:00 동안 운영한다.'), '아홉 시부터 십팔 시까지 동안 운영한다.');
});

test('시각 범위 조사 결합은 기존 시각·수량 범위에 새지 않는다', () => {
  assert.strictEqual(K.normalize('09:00'), '아홉 시');
  assert.strictEqual(K.normalize('09:30~12:00'), '아홉 시 삼십 분부터 열두 시까지');
  assert.strictEqual(K.normalize('복무기간은 18~21개월에 해당한다.'), '복무기간은 십팔 개월에서 이십일 개월에 해당한다.');
});
`;
  fs.writeFileSync(testPath, test);
}
