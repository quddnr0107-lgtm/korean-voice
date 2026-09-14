/* 라이선스 의무가 화면·파일에서 사라지지 않게 잰다 — 지우면 배포 자체가 위반이 된다.
   근거(2026-09-13 원문 확인 · BigScience Open RAIL-M 2022-08-18):
     §1(g) "Distribution … including providing the Model as a hosted service made available by
            electronic or other remote means - e.g. API-based or web access"
            → 브라우저로 모델을 내려주는 것은 **배포**다.
     §4(b) "You must give any Third Party recipients of the Model … a copy of this License"
     §4(a) 사용 제한은 "an enforceable provision … in any type of legal agreement" 이어야 하고
            "give notice to subsequent users" 해야 한다.
     Attachment A "without expressly and intelligibly disclaiming that the text is machine generated" 금지
            → 만든 파일 안에도 AI 생성 고지를 남긴다. */
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync, existsSync } = require('node:fs');
const { join } = require('node:path');
const P = (...a) => join(__dirname, '..', ...a);
const read = (...a) => readFileSync(P(...a), 'utf8');

test('§4(b) — 라이선스 원문 사본을 우리가 직접 제공한다', () => {
  const p = P('public', 'licenses', 'OpenRAIL-M-supertonic-3.txt');
  assert.ok(existsSync(p), '모델 라이선스 사본이 없다 — 배포자는 사본을 줘야 한다');
  const text = readFileSync(p, 'utf8');
  assert.match(text, /BigScience Open RAIL-M/);
  assert.match(text, /Attachment A/, '사용 제한 목록(Attachment A)이 포함돼야 한다');
  assert.ok(text.length > 10000, '전문이어야 한다: ' + text.length);
  const page = read('public', 'licenses.html');
  assert.match(page, /licenses\/OpenRAIL-M-supertonic-3\.txt/, '라이선스 페이지가 원문으로 이어져야 한다');
});

test('§4(a) — 사용 제한이 약관 본문에 편입돼 있다(링크만으로는 부족하다)', () => {
  const terms = read('public', 'terms.html');
  assert.match(terms, /약관의 일부로 편입/);
  assert.match(terms, /실존 인물의 음성을 그 사람의 동의 없이 모사/);
  assert.match(terms, /AI로 생성된 음성임을 숨기고/);
  assert.match(terms, /licenses\.html/, '약관에서 라이선스 원문으로 갈 수 있어야 한다');
  // 제3자 전달 시 같은 제한과 사본을 함께 넘기라는 조항
  assert.match(terms, /라이선스 원문 사본[\s\S]{0,40}함께 전달/);
});

test('§4(a) — 모델을 내려받기 전에 이용자에게 고지하고 확인을 받는다', () => {
  const html = read('public', 'app.html');
  assert.match(html, /id="consent"/);
  assert.match(html, /id="consentCheck"/);
  assert.match(html, /Open RAIL-M/);
  const js = read('public', 'app.js');
  assert.match(js, /askConsent/);
  assert.match(js, /consented\(\)/);
});

test('Attachment A — 만든 파일 안에도 AI 생성 고지를 남긴다', () => {
  const js = read('public', 'local-tts.js');
  assert.match(js, /AI로 생성된 음성입니다/, 'WAV 메타데이터에 고지 문구가 있어야 한다');
  assert.match(js, /LIST/, 'RIFF LIST/INFO 청크로 심는다');
  assert.match(js, /ICMT/);
  // vendor 의 WAV 쓰기(헤더 44바이트뿐 · 고지 없음)로 되돌아가면 안 된다 — 주석의 언급은 괜찮다
  assert.ok(!/writeWavFile\(/.test(js), 'vendor 의 writeWavFile 을 다시 호출하고 있다');
  assert.ok(!/import\s*\{[^}]*writeWavFile[^}]*\}/.test(js), 'vendor 의 writeWavFile 을 다시 들여오고 있다');
});

test('파생 화자를 파일로 내보내지 않는다 — 내보내면 파생물 배포가 된다', () => {
  const js = read('public', 'local-tts.js');
  assert.match(js, /blendStyle/);
  // 스타일을 Blob/파일로 만들어 내보내는 코드가 없어야 한다
  const blend = js.slice(js.indexOf('async function blendStyle'), js.indexOf('/* ── 엔진 ──'));
  assert.ok(!/Blob|createObjectURL|download/.test(blend), '스타일을 파일로 내보내는 코드가 생겼다');
});

/* 서버 경로가 없는 배포(정적 미리보기·컨테이너 부재)에서 사용자가 막다른 404 를 보면 안 된다.
   2026-09-14 실제 미리보기에서 「합성 실패: 404」가 나왔고, 그게 기능이 없는 것처럼 보였다. */
test('서버가 없으면 브라우저 경로로 넘어간다', () => {
  const js = read('public', 'app.js');
  assert.match(js, /res\.status === 404 \|\| res\.status === 503/, '서버 부재를 가려내야 한다');
  assert.match(js, /\$\('where'\)\.value = 'local'/, '브라우저 경로로 전환해야 한다');
  assert.match(js, /「내 브라우저」로 바꾸면/, '미리듣기도 무엇을 하라고 알려 줘야 한다');
});
