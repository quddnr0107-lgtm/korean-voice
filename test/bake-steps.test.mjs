/* 스텝은 **벌에서** 나온다 — 상수(DEFAULT_STEPS)에서 나오면 안 된다 (2026-09-15)
 *
 * 🔴 왜 있나 — 지금 벌을 u5(스텝 8) → k2(스텝 16)로 바꾼 날, `/bake/has` 가 **57,897개 전부 없다**고 했다.
 *    그런데 같은 조각을 `/tts` 로 HEAD 하면 200(x-tts-recipe: k2)이었다. 자가 틀린 것이었다 —
 *    `has`·`warm`·`prune`·`put` 이 스텝을 `DEFAULT_STEPS`(8)로 잡고 있었다. 키가
 *    `voice|steps|r|벌|글` 이라 **스텝이 어긋나면 통째로 안 맞는다.**
 * 🔴 `prune` 이 제일 위험했다 — 남길 키를 실재하지 않는 조합(8·k2)으로 만들어서, 그대로 돌렸으면
 *    **구운 음성을 통째로 지웠다.** 지금은 남길 키를 벌마다 제 스텝으로 · 모든 벌로 만든다.
 */
import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import { cacheKey, stepsFor, keepKeysFor, RECIPE_TAG, TAGS, tagOf } from '../lib/tts-key.mjs';

/* 🔴 worker.mjs 는 `@cloudflare/containers` 를 import 한다 — CI 는 node_modules 없이 돌므로
   여기서 불러오면 **자가 아니라 검사기가 죽는다**(R100). 그래서 순수 함수는 직접 재고,
   워커가 그 함수를 쓰는지는 소스로 잰다(test/tts-key.test.mjs 가 server.py 를 재는 것과 같은 꼴). */
const W = fs.readFileSync(new URL('../worker.mjs', import.meta.url), 'utf8');
/** 그 핸들러의 **코드만** 잘라 온다 — 파일 전체를 세면 다른 곳에 걸리고,
 *  주석을 안 지우면 「DEFAULT_STEPS 를 쓰고 있었다」는 설명 문장에 자기가 걸린다(R173 · 실제로 걸렸다). */
const 핸들러 = (이름) => {
  const i = W.indexOf(`async function ${이름}(`);
  assert.ok(i >= 0, `${이름} 가 없다`);
  const j = W.indexOf('\nasync function ', i + 10);
  return W.slice(i, j < 0 ? W.length : j)
    .replace(/\/\*[\s\S]*?\*\//g, ' ')      // 블록 주석
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');    // 줄 주석(주소의 // 는 앞이 : 라 안 지운다)
};

const 다른벌 = Object.keys(TAGS).find((t) => t !== RECIPE_TAG);

test('stepsFor — 벌마다 제 스텝 · 모르는 값은 지금 벌', () => {
  for (const [tag, v] of Object.entries(TAGS)) assert.strictEqual(stepsFor(tag), v.steps, tag);
  assert.strictEqual(stepsFor('없는벌'), TAGS[RECIPE_TAG].steps);
  assert.strictEqual(stepsFor(undefined), TAGS[RECIPE_TAG].steps);
  assert.notStrictEqual(TAGS[RECIPE_TAG].steps, TAGS[다른벌].steps, '두 벌의 스텝이 같으면 이 자가 아무것도 못 잰다');
});

test('스텝이 어긋나면 키가 통째로 안 맞는다 — 이 자가 재는 것이 그것이다', async () => {
  const t = '국방부장관은 예비군을 지휘한다.', r = 0.89;
  const 맞는키 = await cacheKey('female', TAGS[RECIPE_TAG].steps, r, t, RECIPE_TAG);
  const 옛스텝키 = await cacheKey('female', TAGS[다른벌].steps, r, t, RECIPE_TAG);
  assert.notStrictEqual(맞는키, 옛스텝키, '스텝이 키에 안 들어간다면 이 병 자체가 없다');
});

/* 🔴 여기가 핵심 — 스텝을 **상수**에서 잡으면 지금 벌이 바뀌는 날 통째로 어긋난다.
   음성 대조군: 이 자는 `DEFAULT_STEPS` 를 도로 넣으면 빨개져야 한다(그래서 낱말로 잰다). */
for (const 이름 of ['handleBakeHas', 'handleWarm', 'handleBakePut']) {
  test(`${이름} — 스텝을 벌에서 얻는다(stepsFor) · DEFAULT_STEPS 를 안 쓴다`, () => {
    const 몸 = 핸들러(이름);
    assert.ok(/stepsFor\(/.test(몸), `${이름} 가 stepsFor 를 안 쓴다 — 스텝이 벌을 안 따라간다`);
    assert.ok(!/DEFAULT_STEPS/.test(몸), `${이름} 에 DEFAULT_STEPS 가 남아 있다 — 벌이 바뀌면 키가 어긋난다`);
  });
}

test('handleBakePrune — 남길 키를 keepKeysFor 로 만든다(한 벌만 만들지 않는다)', () => {
  const 몸 = 핸들러('handleBakePrune');
  assert.ok(/keepKeysFor\(/.test(몸), '폐기가 남길 키를 제 손으로 만든다 — 벌 하나만 담으면 다른 벌이 통째로 지워진다');
  assert.ok(!/DEFAULT_STEPS/.test(몸));
});

test('/tts 는 여전히 주소의 s 를 존중한다 — 벌의 스텝은 s 가 없을 때만', () => {
  assert.ok(/const steps = url\.searchParams\.get\('s'\) \? s : TAGS\[tag\]\.steps;/.test(W),
    '/tts 의 스텝 규칙이 바뀌었다 — 나머지 엔드포인트가 그 규칙을 따라가는 것이 이 자의 전제다');
});

test('🔴 폐기가 남길 키 — **모든 벌**을 담는다(한 벌만 담으면 다른 벌을 통째로 지운다)', async () => {
  const t = '통합방위사태는 갑종·을종·병종으로 나뉜다.', r = 0.92;
  const keep = await keepKeysFor(['female'], [{ t, r }]);
  const 집합 = keep.get('female');
  for (const tag of Object.keys(TAGS)) {
    assert.ok(집합.has(await cacheKey('female', TAGS[tag].steps, r, t, tag)), `${tag} 벌의 키가 남길 목록에 없다 — 폐기가 그 벌을 지운다`);
  }
  assert.strictEqual(집합.size, Object.keys(TAGS).length, '벌 수만큼만 있어야 한다');
  // 음성 대조군 — 목록에 없는 글은 남기지 않는다(그게 폐기의 목적이다)
  assert.ok(!집합.has(await cacheKey('female', TAGS[RECIPE_TAG].steps, r, '목록에 없는 글', RECIPE_TAG)));
});

test('tagOf — 밖에서 온 값은 지금 벌로 떨어진다', () => {
  assert.strictEqual(tagOf('k2.1'), 'k2');
  assert.strictEqual(tagOf('없는것'), RECIPE_TAG);
});
