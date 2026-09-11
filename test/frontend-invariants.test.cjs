'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const K = require('../public/ko-voice.js');

const SYNTHETIC_CORPUS = [
  '2026년 9월 3일 제3조제2항에 따라 8시간 훈련한다.',
  '경쟁률 2.5:1이고 지원자는 1,234명입니다.',
  'KATUSA·ROTC 교육은 09:00~18:00입니다.',
  '문의는 010-1234-5678로 해주세요.',
  '150만원과 12.5%를 비교합니다.',
  '제12조제3항제2호에 따른 대상자는 21명입니다.',
  '제3조의2제1항제4호에 따라 2026. 9. 11.부터 시행한다.',
  '별지 제2호의2서식과 별표 1 제1호가목을 확인한다.',
  '별표 1 제1호가목2)에 따른 가격을 적용한다.',
  '제3조의2(적용 범위)에 따라 대상자를 정한다.',
  '제1조(목적) ① 이 법은 목적을 정한다. ② 국가는 지원하여야 한다.',
  '제1호가목. 교육 대상자 나. 훈련 장소',
  '문의는 1577-0000 또는 1588-1234로 연락하세요.',
  'K2 전차와 K9 자주포를 운용한다.',
];

test('normalize는 대표 한국어 TTS 입력에서 멱등적이다', () => {
  for (const raw of SYNTHETIC_CORPUS) {
    const once = K.normalize(raw);
    const twice = K.normalize(once);
    assert.ok(once.length > 0, raw);
    assert.strictEqual(twice, once, `${raw} -> ${once} -> ${twice}`);
  }
});

test('지원하는 숫자·법령 형식은 canonical text에 아라비아 숫자를 남기지 않는다', () => {
  const cases = [
    '2026년 3월 15일까지 8시간 이상 훈련한다.',
    '제3조제2항제1호에 따라 21명을 선발한다.',
    '09:00~18:00에 12.5%를 적용한다.',
    '010-1234-5678로 문의한다.',
  ];
  for (const raw of cases) {
    const normalized = K.normalize(raw);
    assert.ok(!/\d/.test(normalized), `${raw} -> ${normalized}`);
  }
});

test('영하 접두어가 있는 음수 온도는 영하를 중복하지 않는다', () => {
  assert.strictEqual(K.normalize('영하 -3도'), '영하 삼 도');
  assert.strictEqual(K.normalize('최저 영하 -10℃'), '최저 영하 십 도');
  assert.strictEqual(K.normalize('-3~3도'), '영하 삼 도에서 삼 도');
  assert.strictEqual(K.normalize('영하 -3~-1도'), '영하 삼 도에서 영하 일 도');
});

test('가지조문과 공포문 점 표기 날짜를 의미 경계대로 읽는다', () => {
  const cases = [
    ['제3조의2', '제삼 조의 이'],
    ['제3조의2에 따라', '제삼 조의 이에 따라'],
    ['제3조의2제1항제4호', '제삼 조의 이 제일 항 제사 호'],
    ['2026. 9. 11.', '이천이십육 년 구 월 십일 일'],
    ['2026 . 9 . 11', '이천이십육 년 구 월 십일 일'],
  ];
  for (const [raw, expected] of cases) assert.strictEqual(K.normalize(raw), expected, raw);
});

test('시각 범위는 0분을 불필요하게 읽지 않고부터·까지 경계를 보존한다', () => {
  assert.strictEqual(K.normalize('09:00'), '아홉 시');
  assert.strictEqual(K.normalize('09:00~18:00'), '아홉 시부터 십팔 시까지');
  assert.strictEqual(K.normalize('09:30~12:00'), '아홉 시 삼십 분부터 열두 시까지');
  assert.ok(!K.normalize('KATUSA 교육은 09:00~18:00입니다.').includes('영 분'));
});

test('법령 호 가지번호와 목·서식 경계를 보존한다', () => {
  const cases = [
    ['제1호의2', '제일 호의 이'],
    ['별지 제2호의2서식', '별지 제이 호의 이 서식'],
    ['제1호가목', '제일 호 가목'],
    ['제1조제2항제3호나목', '제일 조 제이 항 제삼 호 나목'],
    ['별지 제1호서식', '별지 제일 호 서식'],
    ['별표 1', '별표 일'],
    ['제1항부터 제3항까지', '제일 항부터 제삼 항까지'],
  ];
  for (const [raw, expected] of cases) assert.strictEqual(K.normalize(raw), expected, raw);
});

test('법령 목의 숫자 하위번호에서 닫는 괄호를 불필요한 쉼으로 만들지 않는다', () => {
  const cases = [
    ['별표 1 제1호가목2)에 따른 가격', '별표 일 제일 호 가목 이에 따른 가격'],
    ['제1호나목3)을 적용한다.', '제일 호 나목 삼을 적용한다.'],
    ['나목12)까지', '나목 십이까지'],
    ['제2호다목4)', '제이 호 다목 사'],
  ];
  for (const [raw, expected] of cases) assert.strictEqual(K.normalize(raw), expected, raw);
});

test('법령 조 제목 괄호는 불필요한 쉼표 없이 의미 경계를 보존한다', () => {
  const cases = [
    ['제1조(목적)', '제일 조 목적'],
    ['제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다.', '제이 조 정의 이 법에서 사용하는 용어의 뜻은 다음과 같다.'],
    ['제3조의2(적용 범위)', '제삼 조의 이 적용 범위'],
    ['제10조 (신청 및 처리)', '제십 조 신청 및 처리'],
    ['제3조의2(적용 범위)에 따라', '제삼 조의 이 적용 범위에 따라'],
  ];
  for (const [raw, expected] of cases) assert.strictEqual(K.normalize(raw), expected, raw);
});

test('법령 원문의 항 번호 기호를 남기지 않고 canonical 항 경계로 읽는다', () => {
  const normalized = K.normalize('제1조(목적) ① 이 법은 목적을 정한다. ② 국가는 지원하여야 한다.');
  assert.strictEqual(normalized, '제일 조 목적 제일 항 이 법은 목적을 정한다. 제이 항 국가는 지원하여야 한다.');
  assert.ok(!/[①-⑳]/.test(normalized), normalized);
});

test('법령 목 표지의 점이 canonical 문장 분리를 오염시키지 않는다', () => {
  const raw = '가. 교육 대상자 나. 훈련 장소 다. 소집 일자';
  const normalized = K.normalize(raw);
  assert.strictEqual(normalized, '가목 교육 대상자 나목 훈련 장소 다목 소집 일자');
  assert.strictEqual(K.prepare(raw, { emotion: 'neutral' }).sentences.length, 1);
});

test('대표번호는 일반 숫자·하이픈 규칙보다 먼저 전화번호로 읽는다', () => {
  assert.strictEqual(K.normalize('1577-0000'), '일오칠칠 공공공공');
  assert.strictEqual(K.normalize('1588-1234'), '일오팔팔 일이삼사');
  const normalized = K.normalize('문의는 1577-0000 또는 1588-1234로 연락하세요.');
  assert.ok(!normalized.includes('-') && !/\d/.test(normalized), normalized);
});

test('군 장비 모델명의 숫자를 일반 숫자 규칙으로 훼손하지 않는다', () => {
  assert.strictEqual(K.normalize('K2 전차와 K9 자주포를 운용한다.'), '케이투 전차와 케이나인 자주포를 운용한다.');
  assert.ok(!K.normalize('K2 전차').includes('케이이'));
});

test('군 장비 개량형은 기본 모델 발음을 보존한다', () => {
  assert.strictEqual(K.normalize('K2A1 전차와 K9A1 자주포를 운용한다.'), '케이투 에이원 전차와 케이나인 에이원 자주포를 운용한다.');
});

test('production canonical은 중복 쉼표와 핵심 조사 뒤 오분절을 만들지 않는다', () => {
  const cases = [
    '예비군 대원은 해마다 정해진 날수의 훈련을 받아야 하며, 훈련 소집 통지서를 받은 사람이 정당한 사유 없이 훈련에 참석하지 않으면 고발 대상이 될 수 있습니다. 다만 질병이나 재해처럼 불가피한 사정이 있으면 미리 연기를 신청할 수 있습니다.',
    '제3조제2항에 따라 2026년 3월 15일까지 8시간 이상 훈련한다.',
    '육군은 18개월, 해군은 20개월이며, 공군은 21개월입니다.',
  ];
  for (const raw of cases) {
    const plan = K.prepare(raw, { emotion: 'neutral' });
    const spoken = plan.sentences.map((s) => K.joinSpokenChunks(s.chunks)).join(' ');
    assert.ok(!/,,|,\s*,/.test(spoken), spoken);
    assert.ok(!spoken.includes('날수의, 훈련을'), spoken);
    assert.ok(!spoken.includes('사람이, 정당한'), spoken);
    assert.ok(!spoken.includes('사정이, 있으면'), spoken);
    assert.ok(!spoken.includes('조제'), spoken);
  }
});
