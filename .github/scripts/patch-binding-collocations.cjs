'use strict';
const fs = require('fs');
const cp = require('child_process');
const K0 = require('../../public/ko-voice.js');
const say = (s, K = K0) => K.prepare(s).sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');

const legalBefore = say('서비스는 정보통신망법 제44조의2에 따른 임시조치를 이행합니다.');
if (!/이에,\s*따른/.test(legalBefore)) throw new Error('expected pre-patch 에 따른 break not reproduced: ' + legalBefore);
const militaryBefore = say('KATUSA 지원자는 ROTC와 다르다.');
if (!/알오티씨와,\s*다르다/.test(militaryBefore)) throw new Error('expected pre-patch 와 다르다 break not reproduced: ' + militaryBefore);

const file = 'public/ko-voice.js';
let src = fs.readFileSync(file, 'utf8');
const old = `      // 법령의 고정 결합 '다음과 같다/같이'는 조사 '과' 뒤 자동 호흡으로 갈라지면 의미가 어색해진다.\n      const 다음과같 = bare === '다음과' && /^같/.test(String(words[i + 1] || '').replace(/[,.!?]+$/, ''));`;
const repl = `      // 법령·군사 설명의 고정 결합은 조사 뒤 자동 호흡으로 갈라지면 의미가 어색해진다.\n      const 다음말 = String(words[i + 1] || '').replace(/[,.!?]+$/, '');\n      const 고정결합 =\n        (bare === '다음과' && /^같/.test(다음말)) ||\n        (/에$/.test(bare) && /^(?:따라|따른|따르|의하|의해)/.test(다음말)) ||\n        (/(?:와|과)$/.test(bare) && /^(?:같|다르)/.test(다음말));`;
const oldWeak = `      if (syl >= 12 && WEAK_BREAK_PARTICLE.test(bare) && !범위중간 && !다음과같 && !단위앞) { flush(PAUSE.weak); continue; }`;
const newWeak = `      if (syl >= 12 && WEAK_BREAK_PARTICLE.test(bare) && !범위중간 && !고정결합 && !단위앞) { flush(PAUSE.weak); continue; }`;
if (!src.includes(old) || !src.includes(oldWeak)) throw new Error('phrase binding target not found');
src = src.replace(old, repl).replace(oldWeak, newWeak);
fs.writeFileSync(file, src);

const testFile = 'test/ko-voice.test.cjs';
let tests = fs.readFileSync(testFile, 'utf8');
if (!tests.includes("test('법령 인용 에 따른·의한과 비교 와 다르다는 한 호흡으로 읽는다'")) {
  tests += `\n\ntest('법령 인용 에 따른·의한과 비교 와 다르다는 한 호흡으로 읽는다', () => {\n` +
`  const say = (s) => K.prepare(s).sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');\n` +
`  const a = say('서비스는 정보통신망법 제44조의2에 따른 임시조치를 이행합니다.');\n` +
`  const b = say('제3조의2제1항제4호에 따른다.');\n` +
`  const c = say('KATUSA 지원자는 ROTC와 다르다.');\n` +
`  assert.ok(!/이에,\\s*따른/.test(a), a);\n` +
`  assert.ok(/이에 따른/.test(a), a);\n` +
`  assert.ok(!/호에,\\s*따른다/.test(b), b);\n` +
`  assert.ok(/호에 따른다/.test(b), b);\n` +
`  assert.ok(!/알오티씨와,\\s*다르다/.test(c), c);\n` +
`  assert.ok(/알오티씨와 다르다/.test(c), c);\n` +
`});\n\n` +
`test('고정 결합 예외는 일반 장소격·접속 조사 호흡을 건드리지 않는다', () => {\n` +
`  const say = (s) => K.prepare(s).sentences.map(x => K.joinSpokenChunks(x.chunks)).join(' | ');\n` +
`  assert.ok(/세션에서, 이미/.test(say('이번 세션에서 이미 확인한 환경 사실부터 뒤져야 한다는 점을 잊지 마세요.')));\n` +
`  assert.ok(/육군과, 해군은/.test(say('모집 기준과 선발 방식이 여러 차례 바뀌었고 육군과 해군은 적용 시기가 다릅니다.')));\n` +
`});\n`;
  fs.writeFileSync(testFile, tests);
}

cp.execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
cp.execFileSync(process.execPath, ['--test', 'test/private-source.test.mjs', 'test/container-cost.test.mjs', 'test/ko-voice.test.cjs', 'test/frontend-invariants.test.cjs'], { stdio: 'inherit' });
