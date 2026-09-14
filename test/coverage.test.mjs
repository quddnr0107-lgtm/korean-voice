/* 빠진 조각 짚기(tools/coverage.mjs) — 가짜 /bake/has 서버로 잰다.
   🔴 대조군: 전부 있으면 「없다」고 말해야 하고, 빠진 것이 있으면 **어디가** 빠졌는지 짚어야 한다. */
import test from 'node:test';
import assert from 'node:assert';
import { createServer } from 'node:http';
import { execFile } from 'node:child_process';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

/** 없는 것으로 칠 글 집합을 받아 /bake/has 를 흉내 내는 서버 */
function 가짜워커(없는글) {
  let 요청수 = 0, 최대묶음 = 0;
  const s = createServer((q, res) => {
    let body = '';
    q.on('data', (c) => { body += c; });
    q.on('end', () => {
      const items = JSON.parse(body).items || [];
      요청수++; 최대묶음 = Math.max(최대묶음, items.length);
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ ok: true, has: items.map((it) => !없는글.has(it.t)) }));
    });
  });
  return { s, 센것: () => ({ 요청수, 최대묶음 }) };
}

function 돌려(chunks, 없는글, 더 = []) {
  return new Promise((ok) => {
    const { s, 센것 } = 가짜워커(없는글);
    s.listen(0, () => {
      const dir = mkdtempSync(join(tmpdir(), 'cov-'));
      const f = join(dir, 'chunks.json');
      writeFileSync(f, JSON.stringify(chunks));
      execFile(process.execPath, ['tools/coverage.mjs', '--chunks', f, '--base', `http://127.0.0.1:${s.address().port}`, ...더],
        (err, out) => { s.close(); ok({ code: err ? err.code : 0, out, 센것: 센것() }); });
    });
  });
}

const 조각 = (n, w, k) => Array.from({ length: n }, (_, i) => ({ t: `${w}${k}문장${i}`, r: 1, w, k }));

test('전부 있으면 「빠진 조각이 없다」고 말한다', async () => {
  const r = await 돌려(조각(5, '통합방위법', 'exam'), new Set());
  assert.equal(r.code, 0);
  assert.match(r.out, /빠진 조각이 없다/);
});

test('빠진 것을 법·갈래로 묶어 짚고 본문을 보여 준다', async () => {
  const chunks = [...조각(3, '예비군법', 'easy'), ...조각(2, '병역법', 'study')];
  const r = await 돌려(chunks, new Set(['예비군법easy문장1', '병역법study문장0']));
  assert.match(r.out, /빠진 조각 2개 \/ 5/);
  assert.match(r.out, /예비군법 · easy: 1개/);
  assert.match(r.out, /병역법 · study: 1개/);
  assert.match(r.out, /「예비군법easy문장1」/, '어느 문장이 빠졌는지 안 보여 준다 — 그러면 고칠 수가 없다');
});

test('400개씩 나눠 묻는다 — /bake/has 가 한 번에 400까지만 받는다', async () => {
  const r = await 돌려(조각(950, '훈령', 'exam'), new Set());
  assert.equal(r.센것.요청수, 3);
  assert.ok(r.센것.최대묶음 <= 400, `한 번에 ${r.센것.최대묶음}개를 보냈다`);
});

test('--fail-over 를 넘으면 빨갛게 끝낸다 · 안 넘으면 초록', async () => {
  const chunks = 조각(4, '통합방위법', 'exam');
  const 없음 = new Set(['통합방위법exam문장0', '통합방위법exam문장1']);
  assert.equal((await 돌려(chunks, 없음, ['--fail-over', '1'])).code, 1);
  assert.equal((await 돌려(chunks, 없음, ['--fail-over', '2'])).code, 0);
  assert.equal((await 돌려(chunks, 없음)).code, 0, '기본값은 막지 않는다(보고용)');
});
