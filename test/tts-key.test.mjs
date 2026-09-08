// 즉시 합성 캐시 키 — 워커(lib/tts-key.mjs)와 서버(server/server.py · voice_shape.py)가 같은 꼴·같은 값인가.
import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import { RECIPE_TAG, TAGS, tagOf, R_MIN, R_MAX, parseR, fmtR, keyString, cacheKey } from '../lib/tts-key.mjs';

const py = (f) => fs.readFileSync(new URL('../server/' + f, import.meta.url), 'utf8');

test('조합 표식 — voice_shape.py 의 RECIPE_TAG 와 글자까지 같다', () => {
  const m = py('voice_shape.py').match(/^RECIPE_TAG\s*=\s*'([a-z0-9]+)'/m);
  assert.ok(m, 'voice_shape.py 에 RECIPE_TAG 가 없다');
  assert.strictEqual(m[1], RECIPE_TAG);
});

test('표식 대조 — 서버는 X-TTS-Recipe 를 보내고 워커는 그 값이 같을 때만 R2 에 넣는다', () => {
  // 목소리 두 벌(2026-09-08): 서버는 **그 요청의 벌** 표식을 보내고, 워커는 그것과 같을 때만 넣는다
  assert.ok(/send_header\('X-TTS-Recipe', RC\.get\(tag\)\.RECIPE_TAG\)/.test(py('server.py')), 'server.py 가 그 요청의 벌 표식을 안 보낸다');
  const w = fs.readFileSync(new URL('../worker.mjs', import.meta.url), 'utf8');
  assert.ok(/const cacheable = recipe === tag;/.test(w) && /if \(env\.TTS_CACHE && cacheable\)/.test(w), 'worker.mjs 가 표식이 다를 때도 R2 에 넣는다');
});

test('속도 배수 r — 범위·반올림이 server.py 의 parse_r 과 같다', () => {
  const m = py('server.py').match(/^R_MIN, R_MAX = ([\d.]+), ([\d.]+)/m);
  assert.ok(m, 'server.py 에 R_MIN, R_MAX 가 없다');
  assert.deepStrictEqual([parseFloat(m[1]), parseFloat(m[2])], [R_MIN, R_MAX]);
  assert.strictEqual(parseR('abc'), 1.0);
  assert.strictEqual(parseR(''), 1.0);
  assert.strictEqual(parseR('0.1'), R_MIN);
  assert.strictEqual(parseR('9'), R_MAX);
  assert.strictEqual(parseR('0.926'), 0.93);
  assert.strictEqual(fmtR(1), '1.00');
  assert.strictEqual(fmtR(0.93), '0.93');
});

test('키 꼴 — voice|steps|r|표식|text · r 이 다르면 키가 다르다 · 표식이 들어간다', async () => {
  assert.strictEqual(keyString('female', 16, 1, '안녕'), `female|16|1.00|${RECIPE_TAG}|안녕`);
  const a = await cacheKey('female', 16, 1, '안녕');
  const b = await cacheKey('female', 16, 0.92, '안녕');
  assert.match(a, /^tts\/female\/[0-9a-f]{40}\.mp3$/);
  assert.notStrictEqual(a, b);
  // server.py 의 cache_key 가 같은 f-string 꼴인가(글자로 잰다 — 파이썬을 안 띄운다)
  const s = py('server.py');
  assert.ok(/f'\{voice\}\|\{steps\}\|\{fmt_r\(r\)\}\|\{RC\.get\(tag\)\.RECIPE_TAG\}\|\{text\}'/.test(s), 'server.py cache_key 의 꼴이 다르다');
  assert.ok(/return f'\{float\(r\):\.2f\}'/.test(s), 'server.py fmt_r 이 소수 둘째 자리가 아니다');
});

/* 🔴 벌 표가 두 저장소에 나뉘어 있다(lib/tts-key.mjs · server/recipes.py). 어긋나면 조용히
   한 벌이 통째로 안 잡힌다 — 스텝이 키에 들어가기 때문이다(실측: k2 를 스텝 8 로 찾으면 전량 miss).
   그래서 이름과 스텝을 글자로 대조한다(파이썬을 안 띄운다). */
test('벌 표 — lib/tts-key.mjs 의 TAGS 가 server/recipes.py 와 이름·스텝까지 같다', () => {
  const r = py('recipes.py');
  const 이름 = [...r.matchAll(/^\s*(\w+)\.RECIPE_TAG:\s*\{'mod':\s*\w+,\s*'steps':\s*(\d+)\}/gm)];
  assert.strictEqual(이름.length, Object.keys(TAGS).length, `벌 수가 다르다: py ${이름.length} vs js ${Object.keys(TAGS).length}`);
  const 모듈스텝 = Object.fromEntries(이름.map((m) => [m[1], Number(m[2])]));
  // 모듈 이름 → 표식은 그 모듈 파일의 RECIPE_TAG 에서 읽는다
  for (const [mod, steps] of Object.entries(모듈스텝)) {
    const t = py(mod + '.py').match(/^RECIPE_TAG\s*=\s*'([a-z0-9]+)'/m);
    assert.ok(t, `${mod}.py 에 RECIPE_TAG 가 없다`);
    assert.ok(TAGS[t[1]], `js TAGS 에 벌 ${t[1]} 이 없다`);
    assert.strictEqual(TAGS[t[1]].steps, steps, `벌 ${t[1]} 의 스텝이 다르다: py ${steps} vs js ${TAGS[t[1]].steps}`);
  }
  assert.ok(TAGS[RECIPE_TAG], '기본 벌이 표에 없다');
});

test('벌 고르기 — 모르는 값·장난 값은 기본 벌로 떨어진다(밖에서 온 값이다)', () => {
  assert.strictEqual(tagOf('k2'), 'k2');
  assert.strictEqual(tagOf('k2.1'), 'k2');          // 주소용 판 번호는 떼고 본다
  assert.strictEqual(tagOf('u5.7'), 'u5');
  for (const 나쁜 of ['', null, undefined, 'zzz', '../secret', 'constructor', '__proto__', 'toString'])
    assert.strictEqual(tagOf(나쁜), RECIPE_TAG, `${나쁜} 이 기본 벌로 안 떨어진다`);
});
