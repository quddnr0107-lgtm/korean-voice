/* 낭독 이어붙이기의 불변식 — 2026-09-13 실측으로 정한 것이 코드에서 되돌아가지 않게 잰다.
   실측(4코어 CPU · 조각 5개 + 쉼 5개 · 25.54초):
     - `concat -c copy`          → 길이 +500ms (조각마다 mp3 프레임 1개 = 1152샘플 = 24kHz 에서 48ms 패딩)
     - 디코드 후 1회 인코딩      → 길이 오차 0ms · 0.20초
     - + loudnorm 2-pass linear  → 길이 오차 0ms · 0.88초 · -16.48 LUFS
   ffmpeg 이 없는 환경에서도 도는 정적 검사다(소리를 만들지 않는다). */
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const src = readFileSync(join(__dirname, '..', 'server', 'server.py'), 'utf8');
const render = src.slice(src.indexOf('def render('), src.indexOf('def cache_key('));

test('낭독을 이어붙일 때 스트림 복사를 쓰지 않는다 — 조각마다 48ms 씩 길어진다', () => {
  assert.ok(render.includes('concat'), 'concat 디먹서로 잇는다');
  assert.ok(!/'-c',\s*'copy'/.test(render), "-c copy 금지: 실측 26초에서 +500ms 밀렸다");
  assert.ok(/libmp3lame/.test(render), '디코드 후 한 번만 인코딩한다');
});

test('전체에 단일 게인을 거는 2-pass loudnorm 이다 — 1-pass 는 동적이라 셈여림을 뭉갠다', () => {
  assert.ok(/print_format=json/.test(render), '1차 패스에서 라우드니스를 측정한다');
  assert.ok(/measured_I=/.test(render) && /linear=true/.test(render), '2차 패스는 측정값 + linear=true');
  assert.match(src, /LOUDNORM_TARGET = 'I=-16:TP=-1\.5:LRA=11'/, '웹 재생 타깃 -16 LUFS · TP -1.5dB');
});

test('무음 조각은 말 조각과 같은 인코딩 설정이다 — 다르면 이음새에서 튄다', () => {
  const sil = src.slice(src.indexOf('def silence_mp3('), src.indexOf('def render('));
  assert.ok(/anullsrc=r=24000:cl=mono/.test(sil), '24kHz 모노 무음');
  assert.ok(/'-b:a', '48k'/.test(sil), '48kbps — 말 조각과 같다');
  const synth = src.slice(src.indexOf('def synthesize('), src.indexOf('# 미리 굽기 대기열'));
  assert.ok(/'-ar', '24000'/.test(synth) && /'-b:a', '48k'/.test(synth), '말 조각도 24kHz·48kbps');
});

test('만든 낭독 파일은 서버에 남기지 않는다 — 대본은 이용자의 것이다', () => {
  const handler = src.slice(src.indexOf('def _render('), src.indexOf('    def do_HEAD('));
  assert.ok(/os\.remove\(path\)/.test(handler), '응답 뒤 지운다');
  assert.ok(/finally/.test(handler), '실패해도 지운다');
});

test('목소리 목록이 워커와 서버에서 같다 — 어긋나면 400 나거나 캐시가 갈린다', () => {
  const worker = readFileSync(join(__dirname, '..', 'worker.mjs'), 'utf8');
  const list = (worker.match(/const VOICES = \[([^\]]+)\]/) || [])[1];
  assert.ok(list, '워커의 VOICES 를 못 찾았다');
  const names = list.match(/'([a-z0-9]+)'/g).map((s) => s.replace(/'/g, ''));
  const py = src.slice(src.indexOf('VOICES = {'), src.indexOf('def style_spec'));
  for (const n of names) assert.match(py, new RegExp("'" + n + "':"), `server.py 에 목소리 ${n} 이 없다`);
  for (const m of py.matchAll(/^\s{4}'([a-z0-9]+)':/gm)) {
    assert.ok(names.includes(m[1]), `워커 VOICES 에 목소리 ${m[1]} 이 없다`);
  }
  // 브라우저 경로도 같은 이름을 알아야 한다 — 모르면 그 목소리를 고른 사람이 여성 조합을 듣는다
  const local = readFileSync(join(__dirname, '..', 'public', 'local-tts.js'), 'utf8');
  const pure = (local.match(/const PURE = \{([^}]+)\}/) || [])[1] || '';
  for (const n of names) {
    if (n === 'female' || n === 'male') continue;
    assert.match(pure, new RegExp('\\b' + n + ':'), `local-tts.js PURE 에 목소리 ${n} 이 없다`);
  }
  // 화면에도 있어야 한다
  const html = readFileSync(join(__dirname, '..', 'public', 'app.html'), 'utf8');
  for (const n of names) assert.match(html, new RegExp('value="' + n + '"'), `app.html 선택지에 ${n} 이 없다`);
});

test('굽는 목소리는 둘뿐이다 — 폐기 보존 키가 목소리 수에 비례해 터지면 안 된다', () => {
  const worker = readFileSync(join(__dirname, '..', 'worker.mjs'), 'utf8');
  assert.match(worker, /const BAKED_VOICES = \['female', 'male'\]/);
  const prune = worker.slice(worker.indexOf('async function handleBakePrune'), worker.indexOf('async function handleMeta'));
  assert.match(prune, /for \(const voice of BAKED_VOICES\)/, '폐기가 VOICES 전체를 돌면 조각 수 × 목소리 수만큼 sha1 을 쌓는다');
  assert.ok(!/for \(const voice of VOICES\)/.test(prune), '폐기에서 VOICES 전체를 쓰고 있다');
});
