'use strict';
const fs = require('fs');
const cp = require('child_process');
const K0 = require('../../public/ko-voice.js');
const say = (s, K = K0) => K.prepare(s).sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');

// Reproduce the four residual defects before touching source.
const conditionalBefore = K0.normalize('신검에서 1~3급이면 가능');
if (!/일 급이면에서 삼 급이면/.test(conditionalBefore)) throw new Error('conditional range defect not reproduced: ' + conditionalBefore);
const octoberBefore = say('연말 10~11월 경쟁률이 가장 낮습니다.');
if (!/시월에서,\s*십일 월/.test(octoberBefore)) throw new Error('October range pause defect not reproduced: ' + octoberBefore);
const approxBefore = K0.normalize('합격선은 ~97점이다.');
if (!/에서\s*구십칠 점/.test(approxBefore)) throw new Error('unary tilde defect not reproduced: ' + approxBefore);
const betweenBefore = K0.normalize('점수를 0~120 사이로 입력해 주세요.');
if (!/영사이에서\s*백이십사이로/.test(betweenBefore)) throw new Error('between range defect not reproduced: ' + betweenBefore);

const file = 'public/ko-voice.js';
let src = fs.readFileSync(file, 'utf8');

const oldPred = "  const RANGE_PREDICATE = /^(.+?)(이다|입니다|이에요|예요|이었다|였다|이며|이고|이지|이야|까지|으로|로|이|가|은|는|을|를|에|의|과|와)$/;";
const newPred = "  const RANGE_PREDICATE = /^(.+?)(입니다|이에요|이었다|이라면|이다|예요|였다|이며|이고|이지|이야|이면|라면|까지|으로|로|이|가|은|는|을|를|에|의|과|와)$/;";
if (!src.includes(oldPred)) throw new Error('RANGE_PREDICATE target not found');
src = src.replace(oldPred, newPred);

const oldNumWord = "  const NUM_WORD = /^(?:[영일이삼사오육칠팔구십백천만억조]+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|스무|(?:열|스물|서른|마흔|쉰|예순|일흔|여든|아흔)(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉)?)$/;";
const newNumWord = "  const NUM_WORD = /^(?:[영일이삼사오육칠팔구십백천만억조]+|시월|한|두|세|네|다섯|여섯|일곱|여덟|아홉|스무|(?:열|스물|서른|마흔|쉰|예순|일흔|여든|아흔)(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉)?)$/;";
const oldNumHead = "  const NUM_HEAD = /^(?:[영일이삼사오육칠팔구십백천만억조]|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|스물|서른|마흔|쉰|예순|일흔|여든|아흔)/;";
const newNumHead = "  const NUM_HEAD = /^(?:시월|[영일이삼사오육칠팔구십백천만억조]|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|스물|서른|마흔|쉰|예순|일흔|여든|아흔)/;";
const oldNumPrefix = "  const NUM_PREFIX = /^(?:[영일이삼사오육칠팔구십백천만억조]+|스무|(?:열|스물|서른|마흔|쉰|예순|일흔|여든|아흔)(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉)?|한|두|세|네|다섯|여섯|일곱|여덟|아홉)/;";
const newNumPrefix = "  const NUM_PREFIX = /^(?:시월|[영일이삼사오육칠팔구십백천만억조]+|스무|(?:열|스물|서른|마흔|쉰|예순|일흔|여든|아흔)(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉)?|한|두|세|네|다섯|여섯|일곱|여덟|아홉)/;";
for (const [a, b, name] of [[oldNumWord,newNumWord,'NUM_WORD'],[oldNumHead,newNumHead,'NUM_HEAD'],[oldNumPrefix,newNumPrefix,'NUM_PREFIX']]) {
  if (!src.includes(a)) throw new Error(name + ' target not found');
  src = src.replace(a, b);
}

const oldRangeCallback = "      if (!u) return a + '에서 ' + b;\n      const k = RANGE_PREDICATE.exec(u);";
const newRangeCallback = "      if (!u) return a + '에서 ' + b;\n      // '0~120 사이로'의 '사이'는 단위가 아니라 범위 관계어다. 왼쪽 끝에 복제하지 않는다.\n      if (/^사이(?:로|에|의|에서|까지)?$/.test(u)) return a + '에서 ' + b + ' ' + u;\n      const k = RANGE_PREDICATE.exec(u);";
if (!src.includes(oldRangeCallback)) throw new Error('generic range callback target not found');
src = src.replace(oldRangeCallback, newRangeCallback);

const oldTilde = "    t = t.replace(/\\s*[~∼～]\\s*/g, '에서 '); // 남은 물결표(시각 뒤 등): 아홉 시~열 시 → 아홉 시에서 열 시";
const newTilde = "    // 이 단계까지 남은 숫자 앞 단독 물결표는 범위가 아니라 근사치 표기다: ~97점 → 약 97점.\n    t = t.replace(/(^|[\\s([,:])\\s*[~∼～]\\s*(?=\\d)/g, '$1약 ');\n    t = t.replace(/\\s*[~∼～]\\s*/g, '에서 '); // 남은 이항 물결표만 범위로 읽는다";
if (!src.includes(oldTilde)) throw new Error('leftover tilde target not found');
src = src.replace(oldTilde, newTilde);
fs.writeFileSync(file, src);

const testFile = 'test/ko-voice.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
const marker = "test('잔존 범위 표기 배치는 조건형·시월·근사치·사이 관계를 보존한다'";
if (!tests.includes(marker)) {
  tests += `\n\ntest('잔존 범위 표기 배치는 조건형·시월·근사치·사이 관계를 보존한다', () => {\n` +
`  const say = (s) => K.prepare(s).sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');\n` +
`  const a = K.normalize('신검에서 1~3급이면 가능');\n` +
`  assert.match(a, /일 급에서 삼 급이면/);\n` +
`  assert.doesNotMatch(a, /급이면에서/);\n` +
`  const b = say('연말 10~11월 경쟁률이 가장 낮습니다.');\n` +
`  assert.match(b, /시월에서 십일 월/);\n` +
`  assert.doesNotMatch(b, /시월에서,\\s*십일 월/);\n` +
`  const c = K.normalize('합격선은 ~97점이다.');\n` +
`  assert.match(c, /약 구십칠 점이다/);\n` +
`  assert.doesNotMatch(c, /에서\\s*구십칠 점/);\n` +
`  const d = K.normalize('점수를 0~120 사이로 입력해 주세요.');\n` +
`  assert.match(d, /영에서 백이십 사이로/);\n` +
`  assert.doesNotMatch(d, /영사이에서/);\n` +
`});\n\n` +
`test('잔존 범위 교정은 기존 날짜·시각·수량 범위와 장소격 에서를 보존한다', () => {\n` +
`  assert.match(K.normalize('2026-09-11~2026-09-12에 시행한다.'), /십일 일부터 .* 십이 일까지/);\n` +
`  assert.match(K.normalize('09:00~18:00에 운영한다.'), /아홉 시부터 십팔 시까지 운영한다/);\n` +
`  assert.match(K.normalize('18~21개월이다'), /십팔 개월에서 이십일 개월이다/);\n` +
`  const say = (s) => K.prepare(s).sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');\n` +
`  assert.match(say('이번 세션에서 이미 확인한 사실을 다시 봅니다.'), /세션에서, 이미/);\n` +
`  assert.match(say('9~10월에 시행한다.'), /구 월에서 시월/);\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs'], { stdio: 'inherit' });
