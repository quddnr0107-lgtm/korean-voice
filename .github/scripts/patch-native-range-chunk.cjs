'use strict';
const fs = require('fs');
const cp = require('child_process');
const K0 = require('../../public/ko-voice.js');
const spoken = (s, K = K0) => K.prepare(s).sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');

const before1 = spoken('동원훈련은 연 약 28~32시간 실시한다.');
if (!/스물여덟 시간에서,\s*서른두 시간/.test(before1)) throw new Error('expected native-number range break not reproduced: ' + before1);
const before2 = spoken('신장 기준은 140~146=5급 구간이다.');
if (!/백사십에서,\s*백사십육/.test(before2)) throw new Error('expected operator-adjacent range break not reproduced: ' + before2);

const file = 'public/ko-voice.js';
let src = fs.readFileSync(file, 'utf8');
const oldBlock = `  // 다음 어절이 '수사 한 덩어리'일 때만 범위로 본다. 앞글자만 보면 '이미'·'일괄'까지 수사로 오인한다.\n  const NUM_WORD = /^(?:[영일이삼사오육칠팔구십백천만억조]+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|스물|서른|마흔|쉰|예순|일흔|여든|아흔)$/;\n  const NUM_HEAD = /^(?:[영일이삼사오육칠팔구십백천만억조]|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|스물|서른|마흔|쉰|예순|일흔|여든|아흔)/;\n  // 왼쪽이 수사여야 범위로 본다 — 왼쪽을 확인하면 오른쪽은 느슨히 봐도 '세션에서 이미'를 범위로 오인하지 않는다.\n  const 수사꼬리 = (w) => String(w || '').replace(/[,.!?]+$/, '').replace(/^(?:[영일이삼사오육칠팔구십백천만억조]+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|스물|서른|마흔|쉰|예순|일흔|여든|아흔)/, '');\n  const oi = (a, b) => a.indexOf(b) === 0 || b.indexOf(a) === 0;\n  const 범위연결 = (bare, prev, next) => {\n    if (!RANGE_TAIL.test(bare)) return false;\n    const 왼 = bare.replace(RANGE_TAIL, ''), 오 = String(next || '').replace(/^[(\"']+/, '').replace(/[,.!?]+$/, '');\n    if (!NUM_HEAD.test(오)) return false;\n    // 오른쪽도 수사 한 덩어리이거나(이천이십육) 왼쪽과 같은 단위를 달고 있어야 한다(삼문장 / 육문장).\n    const 왼꼬리 = 수사꼬리(왼), 오꼬리 = 수사꼬리(오);   // '삼문장 / 육문장으로' 처럼 뒤쪽에 조사가 더 붙어도 같은 단위다\n    const 짝 = NUM_WORD.test(오) || (왼꼬리 !== '' && (오꼬리 === 왼꼬리 || oi(오꼬리, 왼꼬리)));\n    if (!짝) return false;\n    return NUM_WORD.test(왼) || NUM_WORD.test(String(prev || '').replace(/[,.!?]+$/, '')) || NUM_HEAD.test(왼);\n  };`;
const newBlock = `  // 다음 어절이 '수사 한 덩어리'일 때만 범위로 본다. 복합 고유어 수(스물여덟·서른두)도 한 덩어리다.\n  const NUM_WORD = /^(?:[영일이삼사오육칠팔구십백천만억조]+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|스무|(?:열|스물|서른|마흔|쉰|예순|일흔|여든|아흔)(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉)?)$/;\n  const NUM_HEAD = /^(?:[영일이삼사오육칠팔구십백천만억조]|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|스물|서른|마흔|쉰|예순|일흔|여든|아흔)/;\n  const NUM_PREFIX = /^(?:[영일이삼사오육칠팔구십백천만억조]+|스무|(?:열|스물|서른|마흔|쉰|예순|일흔|여든|아흔)(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉)?|한|두|세|네|다섯|여섯|일곱|여덟|아홉)/;\n  // 왼쪽이 수사여야 범위로 본다 — 왼쪽을 확인하면 '세션에서 이미' 같은 장소격은 범위로 오인하지 않는다.\n  const 수사꼬리 = (w) => String(w || '').replace(/[,.!?]+$/, '').replace(NUM_PREFIX, '');\n  const oi = (a, b) => a.indexOf(b) === 0 || b.indexOf(a) === 0;\n  const 범위연결 = (bare, prev, next) => {\n    if (!RANGE_TAIL.test(bare)) return false;\n    const 왼 = bare.replace(RANGE_TAIL, ''), 오 = String(next || '').replace(/^[(\"']+/, '').replace(/[,.!?]+$/, '');\n    // 표의 '140~146=5급'처럼 오른쪽 수 뒤에 비교·등호 표현이 붙어도 숫자 핵만 판정한다.\n    const 오핵 = 오.replace(/[=<>:;].*$/, '');\n    if (!NUM_HEAD.test(오핵)) return false;\n    const 왼꼬리 = 수사꼬리(왼), 오꼬리 = 수사꼬리(오핵);\n    const 짝 = NUM_WORD.test(오핵) || (왼꼬리 !== '' && (오꼬리 === 왼꼬리 || oi(오꼬리, 왼꼬리)));\n    if (!짝) return false;\n    return NUM_WORD.test(왼) || NUM_WORD.test(String(prev || '').replace(/[,.!?]+$/, '')) || NUM_HEAD.test(왼);\n  };`;
if (!src.includes(oldBlock)) throw new Error('range-link block not found');
src = src.replace(oldBlock, newBlock);
fs.writeFileSync(file, src);

const testFile = 'test/ko-voice.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
if (!tests.includes("test('복합 고유어 수와 표 형식 범위 한가운데는 끊지 않는다'")) {
  tests += `\n\ntest('복합 고유어 수와 표 형식 범위 한가운데는 끊지 않는다', () => {\n` +
`  const say = (s) => K.prepare(s).sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');\n` +
`  assert.ok(!/스물여덟 시간에서,\\s*서른두 시간/.test(say('동원훈련은 연 약 28~32시간 실시한다.')));\n` +
`  assert.ok(!/백사십에서,\\s*백사십육/.test(say('신장 기준은 140~146=5급 구간이다.')));\n` +
`  assert.ok(!/사 에서,\\s*스물한 개/.test(say('공군은 색약 지원가능 분야 4→21개로 확대한다.')));\n` +
`});\n\n` +
`test('복합 수 범위 판정은 진짜 장소격 에서를 범위로 오인하지 않는다', () => {\n` +
`  const p = K.prepare('이번 세션에서 이미 확인한 환경 사실부터 뒤져야 한다는 점을 잊지 마세요.');\n` +
`  const out = p.sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');\n` +
`  assert.ok(/세션에서, 이미/.test(out), out);\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs'], { stdio: 'inherit' });
