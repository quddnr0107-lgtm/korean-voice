// worker.js의 /api/tts — 가짜 AI 바인딩으로 흐름을 검증한다(실제 모델 호출 없음).
import test from 'node:test';
import assert from 'node:assert';
import { handleTts, _reset, MODEL } from '../worker.mjs';

const post = (body) => new Request('https://x/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const fakeAudio = Buffer.from('ID3fake-mp3-bytes').toString('base64');

test('GET — 바인딩 유무를 정직하게 알린다', async () => {
  _reset();
  const a = await (await handleTts(new Request('https://x/api/tts'), {})).json();
  assert.deepStrictEqual([a.ok, a.available, a.engine], [true, false, null]);
  const b = await (await handleTts(new Request('https://x/api/tts'), { AI: { run: async () => ({}) } })).json();
  assert.strictEqual(b.available, true);
});

test('POST — 정규화된 텍스트로 모델을 부르고 MP3를 돌려준다', async () => {
  _reset();
  const calls = [];
  const AI = { run: async (model, input) => { calls.push({ model, input }); if (input.lang === 'kr') throw new Error('Invalid input'); return { audio: fakeAudio }; } };
  const res = await handleTts(post({ text: '2명이 6월 10일에 150만원' }), { AI });
  assert.strictEqual(res.status, 200);
  assert.strictEqual(res.headers.get('Content-Type'), 'audio/mpeg');
  assert.strictEqual(Buffer.from(await res.arrayBuffer()).toString(), 'ID3fake-mp3-bytes');
  assert.strictEqual(calls[0].model, MODEL);
  assert.strictEqual(calls[0].input.prompt, '두 명이 유월 십 일에 백오십만 원');
  assert.deepStrictEqual(calls.map((c) => c.input.lang), ['kr', 'ko'], '첫 후보 실패 → 다음 후보');
  // 성공한 언어 코드를 기억한다
  calls.length = 0;
  await handleTts(post({ text: '다시' }), { AI });
  assert.strictEqual(calls[0].input.lang, 'ko');
});

test('POST — 빈 글·바인딩 없음·전부 실패는 각각 400·503·502', async () => {
  _reset();
  assert.strictEqual((await handleTts(post({ text: '  ' }), { AI: {} })).status, 400);
  assert.strictEqual((await handleTts(post({ text: '안녕' }), {})).status, 503);
  const bad = await handleTts(post({ text: '안녕' }), { AI: { run: async () => { throw new Error('nope'); } } });
  assert.strictEqual(bad.status, 502);
  const j = await bad.json();
  assert.strictEqual(j.reason, 'nope');
});

test('POST — 600자로 자르고 ⏸ 쉼 표기는 쉼표로 넘긴다', async () => {
  _reset();
  let seen = '';
  const AI = { run: async (m, input) => { seen = input.prompt; return { audio: fakeAudio }; } };
  await handleTts(post({ text: '잠깐 ⏸⏸⏸ 기다려' }), { AI });
  assert.strictEqual(seen, '잠깐 , 기다려');
  await handleTts(post({ text: '가'.repeat(1000) }), { AI });
  assert.ok(seen.length <= 600);
});
