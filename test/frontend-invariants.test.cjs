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
