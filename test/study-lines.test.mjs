import test from 'node:test';
import assert from 'node:assert/strict';
import { 회독글들, 근거만들기 } from '../tools/study-lines.mjs';

const 줄 = (art, original) => ({ law: '훈령', period: 'P', type: '본훈', articleNum: art, original });
const sd = { blanks: [줄('1-1', '보통 줄'), 줄('1-2', '[별표 23] 깨진 표 글자 구 분P-999K'), 줄('1-3', '숨긴 표 조각'), 줄('1-4', '보통 줄')] };
const 열쇠 = (b) => `${b.law}|${b.period}|${b.type}|${b.articleNum}|${b.original.substring(0, 40)}`;
const 새S = {
  읽기용: (t) => t.replace(/·/g, ', '),
  줄글(b, 근거) {
    const k = 열쇠(b);
    const sp = 근거 && 근거.studySpeech && 근거.studySpeech[k];
    if (sp) return sp.flatMap((x) => x.소리).join(' ');
    if (근거 && (근거.studyHideLineIds || []).includes(k)) return '';
    return b.original.trim();
  },
};
const 근거 = { studySpeech: { [열쇠(sd.blanks[1])]: [{ label: '별표23', 소리: ['별표 23, 통신장비 보유기준입니다.'] }] }, studyHideLineIds: [열쇠(sd.blanks[2])] };

test('새 사이트 — 별표 줄은 글로 푼 표, 숨김 줄은 빠진다, 같은 글은 한 번', () => {
  const out = 회독글들(sd, 새S, 근거).map((x) => x.t);
  assert.deepEqual(out, ['보통 줄', '별표 23, 통신장비 보유기준입니다.']);
});
test('옛 사이트(줄글 없음) — 지금과 같게 원문을 쓴다', () => {
  const out = 회독글들(sd, { 읽기용: (t) => t }, 근거).map((x) => x.t);
  assert.deepEqual(out, ['보통 줄', '[별표 23] 깨진 표 글자 구 분P-999K', '숨긴 표 조각']);
});
test('읽기용을 줄글 **뒤에** 탄다(화면 _followSpoken(_lineSpokenText) 와 같은 순서)', () => {
  const sd2 = { blanks: [줄('1-9', '동·읍·면')] };
  assert.equal(회독글들(sd2, 새S, 근거)[0].t, '동, 읍, 면');
});
test('근거만들기 — 파일이 없거나 깨져도 멈추지 않는다', () => {
  assert.deepEqual(근거만들기(null, '{깨짐'), { studySpeech: null, studyHideLineIds: null });
  assert.deepEqual(근거만들기('{"studyHideLineIds":["a"]}', '{"studySpeech":{"k":[]}}'), { studySpeech: { k: [] }, studyHideLineIds: ['a'] });
});
