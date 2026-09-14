/* 컨테이너 이미지가 기동할 수 있는가 — Dockerfile 의 COPY 가 server.py 의 import 를 전부 담는지 잰다.
   2026-09-13: COPY 가 2026-09-04 에 멈춰 있어 recipes.py·voice_shape_k2.py·singleflight.py 가 빠졌고,
   server/ 를 고친 첫 커밋에서 이미지가 재빌드되자 컨테이너가 import 에서 죽어 배포가 실패했다.
   Docker 없이 도는 정적 검사다. */
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync, readdirSync } = require('node:fs');
const { join } = require('node:path');
const DIR = join(__dirname, '..', 'server');
const dockerfile = readFileSync(join(DIR, 'Dockerfile'), 'utf8');
const pyFiles = readdirSync(DIR).filter((f) => f.endsWith('.py'));

/** server 폴더 안의 .py 를 import 하는 줄에서 모듈 이름을 걷는다(표준·외부 라이브러리는 파일이 없으니 걸러진다). */
function localImports(file) {
  const src = readFileSync(join(DIR, file), 'utf8');
  const names = new Set();
  for (const m of src.matchAll(/^\s*(?:import|from)\s+([A-Za-z_][\w]*)/gm)) {
    if (pyFiles.includes(m[1] + '.py')) names.add(m[1] + '.py');
  }
  return names;
}

test('Dockerfile 의 COPY 가 컨테이너가 실행하는 모듈을 전부 담는다', () => {
  const copyLine = dockerfile.split('\n').find((l) => l.startsWith('COPY '));
  assert.ok(copyLine, 'COPY 줄이 있다');
  // 컨테이너가 실행하는 것은 server.py 이고, 그것이 끌어오는 것까지 따라간다(recipes.py → voice_shape_k2.py)
  const need = new Set(['server.py']);
  for (const f of [...need]) for (const dep of localImports(f)) need.add(dep);
  for (const f of [...need]) for (const dep of localImports(f)) need.add(dep);
  for (const f of need) {
    assert.ok(copyLine.includes(f), `${f} 가 Dockerfile COPY 에 없다 — 이미지를 재빌드하면 컨테이너가 죽는다`);
  }
});

test('CMD 가 실행하는 파일도 COPY 에 있다', () => {
  const cmd = dockerfile.match(/CMD \[([^\]]+)\]/);
  assert.ok(cmd, 'CMD 가 있다');
  const entry = (cmd[1].match(/"([\w.]+\.py)"/) || [])[1];
  assert.ok(entry, 'CMD 가 .py 를 실행한다');
  assert.ok(dockerfile.split('\n').find((l) => l.startsWith('COPY ')).includes(entry), `${entry} 가 COPY 에 없다`);
});
