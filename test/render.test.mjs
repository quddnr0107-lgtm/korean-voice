// 대본 낭독 만들기 — 한도·쉼 합치기·결과 키. 외부 의존 없음.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeItems, renderKey, dayStamp, quota, LIMITS, FREE_DAILY_CHARS } from '../lib/render.mjs';

test('빈 입력과 한도', () => {
  assert.equal(normalizeItems([]).error, 'empty_items');
  assert.equal(normalizeItems(null).error, 'empty_items');
  assert.equal(normalizeItems([{ t: '   ' }]).error, 'empty_items');
  assert.equal(normalizeItems(new Array(LIMITS.items + 1).fill({ t: '가' })).error, 'too_many_items');
  const long = new Array(60).fill({ t: '가'.repeat(400) });          // 24,000자 > 20,000
  assert.equal(normalizeItems(long).error, 'too_many_chars');
});

test('조각 다듬기 — 공백 정리·400자 자르기·r 범위', () => {
  const { ok, items, chars } = normalizeItems([{ t: '  두  칸 ', r: 9, pause: 480 }, { t: '가'.repeat(500), r: 0.1 }]);
  assert.ok(ok);
  assert.equal(items[0].t, '두 칸');
  assert.equal(items[0].r, 1.6);                                     // R_MAX 로 잘린다
  assert.equal(items[0].pause, 480);
  assert.equal(items[1].t.length, LIMITS.itemChars);
  assert.equal(items[1].r, 0.7);                                     // R_MIN
  assert.equal(chars, 3 + LIMITS.itemChars);   // "두 칸" 3자 + 400자
});

test('글 없이 쉼만 있는 조각은 앞 조각의 쉼에 더한다 — 쉼이 사라지면 낭독이 붙어 버린다', () => {
  const { items } = normalizeItems([{ t: '첫 문장', pause: 200 }, { t: '', pause: 600 }, { t: '다음 문장', pause: 480 }]);
  assert.equal(items.length, 2);
  assert.equal(items[0].pause, 800);
  assert.equal(items[1].pause, 480);
});

test('쉼은 상한까지만 · 맨 앞의 쉼뿐인 조각은 버린다', () => {
  const { items } = normalizeItems([{ t: '', pause: 900 }, { t: '문장', pause: 99999 }]);
  assert.equal(items.length, 1);
  assert.equal(items[0].pause, LIMITS.pauseMs);
});

test('결과 키 — 같은 대본은 같고, 목소리·쉼·속도가 달라지면 갈린다', async () => {
  const a = [{ t: '안녕하세요', r: 1, pause: 480 }];
  const k = await renderKey('female', 8, 'u5', a);
  assert.match(k, /^render\/[0-9a-f]{40}\.mp3$/);
  assert.equal(k, await renderKey('female', 8, 'u5', [{ t: '안녕하세요', r: 1, pause: 480 }]));
  assert.notEqual(k, await renderKey('male', 8, 'u5', a));
  assert.notEqual(k, await renderKey('female', 16, 'u5', a));
  assert.notEqual(k, await renderKey('female', 8, 'k2', a));
  assert.notEqual(k, await renderKey('female', 8, 'u5', [{ t: '안녕하세요', r: 1, pause: 240 }]));
  assert.notEqual(k, await renderKey('female', 8, 'u5', [{ t: '안녕하세요', r: 1.1, pause: 480 }]));
});

test('무료 한도', () => {
  assert.deepEqual(quota(0, 100, 3000), { ok: true, used: 0, after: 100, limit: 3000, remaining: 3000 });
  assert.equal(quota(2950, 100, 3000).ok, false);
  assert.equal(quota(3000, 0, 3000).ok, true);                       // 정확히 한도까지는 통과
  assert.equal(quota(2900, 100, 3000).remaining, 100);
  assert.equal(FREE_DAILY_CHARS > 0, true);
});

test('날짜 도장은 UTC 날짜', () => {
  assert.equal(dayStamp(Date.UTC(2026, 8, 13, 23, 59)), '2026-09-13');
  assert.equal(dayStamp(Date.UTC(2026, 8, 14, 0, 1)), '2026-09-14');
});
