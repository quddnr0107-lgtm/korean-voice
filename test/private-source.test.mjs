import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSourceReader } from '../tools/private-source.mjs';
test('private source has no public fallback when OIDC is absent', async () => {
  let calls = 0;
  const read = makeSourceReader('https://site', { env: {}, fetchFn: async () => { calls++; } });
  await assert.rejects(read('/exam_prep.json'), /authorized/); assert.equal(calls, 0);
});
test('private datasets use the pinned source endpoint and scoped token', async () => {
  let tokenRequests = 0;
  const seen = [];
  const read = makeSourceReader('https://site', {
    env: { ACTIONS_ID_TOKEN_REQUEST_URL: 'https://github.example/token?x=1', ACTIONS_ID_TOKEN_REQUEST_TOKEN: 'request-secret' },
    fetchFn: async (url, options) => {
      if (String(url).startsWith('https://github.example/')) {
        tokenRequests++; assert.equal(new URL(url).searchParams.get('audience'), 'yebijun-tts-source');
        return Response.json({ value: 'scoped-token' });
      }
      assert.equal(url, 'https://yebijun-sync.quddnr0107.workers.dev/api/tts-source');
      assert.equal(options.headers.Authorization, 'Bearer scoped-token');
      seen.push(JSON.parse(options.body).kind); return new Response('{}');
    }
  });
  await Promise.all(['/exam_prep.json', '/easy_explain.json', '/study_data.json'].map(read));
  assert.equal(tokenRequests, 1); assert.deepEqual(seen.sort(), ['easy', 'exam', 'study']);
});
test('source denial stops the job; public scripts carry no private credentials', async () => {
  const calls = [];
  const read = makeSourceReader('https://site', { env: {}, fetchFn: async (url, options) => {
    calls.push(url); assert.equal(options.headers.Authorization, undefined); return new Response('script');
  } });
  assert.equal(await read('/live-tts.js'), 'script'); assert.equal(calls.length, 1);
});
