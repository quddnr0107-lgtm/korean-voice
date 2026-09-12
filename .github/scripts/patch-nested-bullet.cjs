'use strict';
const fs = require('fs');
const cp = require('child_process');
const K0 = require('../../public/ko-voice.js');

const samples = ['* - 기본: 예비군 훈련 안내', '- * 기본: 예비군 훈련 안내', '"* - 기본: 예비군 훈련 안내'];
for (const s of samples) {
  const once = K0.normalize(s);
  const twice = K0.normalize(once);
  if (once === twice) throw new Error('expected pre-patch nested-marker idempotence defect not reproduced: ' + s + ' => ' + once);
}

const file = 'public/ko-voice.js';
let src = fs.readFileSync(file, 'utf8');
const oldLine = "    t = t.replace(/^[ \\t]*[\"'“”‘’/\\\\]*[ \\t]*(#{1,6}|[-*•]|(?!\\d{4}\\.\\s+\\d{1,2}\\.)\\d+[.)])\\s+/gm, '');";
const newBlock = `    const LEADING_MARKER_RE = /^[ \\t]*[\"'“”‘’/\\\\]*[ \\t]*(#{1,6}|[-*•]|(?!\\d{4}\\.\\s+\\d{1,2}\\.)\\d+[.)])\\s+/gm;\n    // Markdown 변환물이 \"* - 항목\"처럼 중첩 표지를 남기는 경우도 한 번의 normalize로 안정화한다.\n    for (let i = 0; i < 4; i++) {\n      const before = t;\n      t = t.replace(LEADING_MARKER_RE, '');\n      if (t === before) break;\n    }`;
if (!src.includes(oldLine)) throw new Error('target marker line not found');
src = src.replace(oldLine, newBlock);
fs.writeFileSync(file, src);

const testFile = 'test/ko-voice.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
if (!tests.includes("test('중첩 Markdown 표지도 한 번에 제거해 멱등성을 보장한다'")) {
  tests += `\n\ntest('중첩 Markdown 표지도 한 번에 제거해 멱등성을 보장한다', () => {\n` +
`  const cases = [\n` +
`    '* - 기본: 예비군 훈련 안내',\n` +
`    '- * 기본: 예비군 훈련 안내',\n` +
`    '\"* - 기본: 예비군 훈련 안내',\n` +
`    '// * - 기본: 예비군 훈련 안내',\n` +
`  ];\n` +
`  for (const input of cases) {\n` +
`    const once = K.normalize(input);\n` +
`    assert.strictEqual(once, '기본: 예비군 훈련 안내', input);\n` +
`    assert.strictEqual(K.normalize(once), once, '멱등성: ' + input);\n` +
`  }\n` +
`  assert.strictEqual(K.normalize('-3도'), '영하 삼 도', '음수는 불릿으로 제거하지 않는다');\n` +
`  assert.strictEqual(K.normalize('2026. 9. 11.'), '이천이십육 년 구 월 십일 일', '점 날짜는 ordered-list로 오인하지 않는다');\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs'], { stdio: 'inherit' });
