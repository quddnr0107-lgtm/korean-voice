/* 한국어 음성 실험실 — voice-lab.html 전용. ko-voice.js(window.KoVoice)를 쓴다. */
(function () {
  'use strict';
  const K = window.KoVoice;
  const $ = (id) => document.getElementById(id);
  const supported = typeof speechSynthesis !== 'undefined' && typeof SpeechSynthesisUtterance !== 'undefined';
  if (!supported) { $('unsupported').hidden = false; $('playRaw').disabled = true; }

  const SAMPLES = {
    info: '2026년 9월 모집병 접수는 10월 6일 마감입니다. 육군은 18개월, 해군 20개월, 공군 21개월 복무하며 병장 봉급은 150만원이에요. 반드시 기한 안에 접수하세요! 궁금한 점 있으세요?',
    joy: '합격을 축하드려요! 3개월 뒤 입영이니까 지금부터 체력 준비 시작하면 딱 좋아요. 자대 가서도 잘 해낼 거예요.',
    sad: '이번엔 아쉽게 탈락했지만 괜찮아요. 다음 회차는 2주 뒤에 다시 열리고, 가산점 항목을 2개만 더 채워도 합격선에 닿아요. 너무 걱정하지 마세요.',
    numbers: '2026-09-03 기준 접수 인원은 1,234명이고 경쟁률은 2.5:1입니다. 6월 10일부터 10월 3일까지, 매일 09:00~18:00에 접수해요. 문의는 1588-9090, 봉급은 이병 75만원·병장 150만원, 적금 매칭은 월 최대 55만원이에요. 제3회 시험은 21살 이상 만 19세부터 지원 가능하고, TOEIC 700점 또는 JLPT N2가 필요합니다.',
    g2p: '같이 국물을 먹고 싶다면 신라면을 끓여요. 학교 앞에 꽃이 피었고, 읽고 싶은 책이 많아요. 값이 싸서 좋아요. 협력해서 독립을 이뤘어요.',
    tags: '[따뜻] 입영 전날이네요... 긴장되죠? <break time="0.6s"/> [차분] 준비물은 신분증, 통장 사본 2부, 세면도구예요. [기쁨] 그래도 18개월 뒤엔 병장 봉급 150만원 받으면서 전역합니다!',
  };
  document.querySelectorAll('[data-sample]').forEach((b) => b.addEventListener('click', () => { $('text').value = SAMPLES[b.dataset.sample]; update(); }));

  // 감정 목록
  Object.keys(K.EMOTIONS).forEach((k) => { const o = document.createElement('option'); o.value = k; o.textContent = K.EMOTIONS[k].label + ' (' + k + ')'; $('emotion').appendChild(o); });

  // 목소리 목록(비동기 로드)
  function fillVoices() {
    const sel = $('voice'); sel.innerHTML = '';
    const vs = supported ? K.koVoices() : [];
    if (!vs.length) { const o = document.createElement('option'); o.textContent = supported ? '한국어 목소리가 없습니다 (OS 언어팩 설치 필요)' : '지원 안 됨'; sel.appendChild(o); return; }
    vs.forEach((v, i) => { const o = document.createElement('option'); o.value = i; o.textContent = v.name + (v.localService ? '' : ' (온라인)'); sel.appendChild(o); });
  }
  fillVoices();
  if (supported) speechSynthesis.addEventListener('voiceschanged', fillVoices);
  const pickVoice = () => { const vs = K.koVoices(); return vs[+$('voice').value] || vs[0] || null; };

  const opts = () => ({
    emotion: $('emotion').value || undefined,
    rate: +$('rate').value, pitch: +$('pitch').value,
    normalize: $('optNorm').checked, phrase: $('optPhrase').checked, g2p: $('optG2p').checked,
  });

  // 원문과 정규화 결과의 다른 부분을 표시(낱말 단위)
  function diffHtml(a, b) {
    const A = a.split(/\s+/), B = b.split(/\s+/);
    const setA = new Set(A);
    const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    return B.map((w) => /^⏸+$/.test(w) ? '<mark>(쉼 ' + (w.length / 10).toFixed(1) + 's)</mark>' : setA.has(w) ? esc(w) : '<mark>' + esc(w) + '</mark>').join(' ');
  }

  // 화자 프로필(본인 녹음 측정값) — 파생 수치만 있고 음성은 없다
  let profile = null;
  fetch('/profiles/owner.json').then((r) => r.ok ? r.json() : null).then((p) => {
    if (!p) { $('profileInfo').textContent = '프로필 없음 (tools/analyze-voice.py로 만든다)'; return; }
    profile = p;
    $('profileInfo').textContent = p.source + ' · 음높이 중앙 ' + p.f0_hz.median + 'Hz(' + p.f0_hz.p10 + '~' + p.f0_hz.p90 + ') · ' + p.syllables_per_s + '음절/초 · 구 사이 쉼 ' + p.pause_ms.phrase_median + 'ms · 문장 사이 ' + p.pause_ms.sentence_median + 'ms · 하강 ' + p.declination_pct + '%';
    $('optProfile').disabled = false;
  }).catch(() => { $('profileInfo').textContent = '프로필 없음'; });
  $('optProfile').addEventListener('change', () => { K.applyProfile($('optProfile').checked ? profile : null); update(); });

  let plan = null;
  function update() {
    const text = $('text').value;
    const o = opts();
    plan = K.prepare(text, o);
    $('emoBadge').textContent = (o.emotion ? '지정: ' : '감지: ') + plan.emotionLabel;
    $('normalized').innerHTML = diffHtml(text, plan.normalized);
    $('pron').textContent = K.pronounce(plan.normalized);
    const tb = $('plan').querySelector('tbody'); tb.innerHTML = '';
    const TYPE = { statement: '평서', question: '의문 ↗', exclaim: '감탄', request: '요청' };
    let n = 0;
    plan.sentences.forEach((s) => s.chunks.forEach((c) => {
      const tr = document.createElement('tr'); tr.dataset.i = n++;
      const cells = [String(n), (c.text || '(쉼)') + (c.emph ? ' ★' : ''), TYPE[s.type] || s.type, String(c.pause), c.rate.toFixed(2), c.pitch.toFixed(2), c.volume.toFixed(2)];
      cells.forEach((v, i) => { const td = document.createElement('td'); if (i >= 3) td.className = 'num'; td.textContent = v; tr.appendChild(td); });
      tb.appendChild(tr);
    }));
    $('ssml').textContent = K.toSSML(text, o);
  }
  ['text', 'emotion', 'rate', 'pitch', 'optNorm', 'optPhrase', 'optG2p'].forEach((id) => $(id).addEventListener('input', update));
  $('rate').addEventListener('input', () => { $('rateV').textContent = (+$('rate').value).toFixed(2); });
  $('pitch').addEventListener('input', () => { $('pitchV').textContent = (+$('pitch').value).toFixed(2); });

  function highlight(i) { document.querySelectorAll('#plan tbody tr').forEach((tr) => { tr.classList.toggle('now', +tr.dataset.i === i); }); }
  // 신경망 서버 상태 표시
  K.neuralStatus().then((ok) => { $('neural').textContent = ok ? '신경망 음성 서버: 사용 가능' : '신경망 음성 서버: 없음(브라우저 음성으로 폴백)'; });
  let engineName = '';
  function play(natural) {
    const o = opts(); o.voice = pickVoice(); o.natural = natural; o.engine = $('engine').value;
    o.onEngine = (n) => { engineName = n; };
    o.onChunk = (c, i, total) => { $('status').textContent = (natural ? '자연스럽게 ' : '원문 ') + (i + 1) + '/' + total + (engineName ? ' · ' + engineName : ''); if (natural) highlight(i); };
    o.onEnd = () => { $('status').textContent = '끝' + (engineName ? ' · ' + engineName : ''); highlight(-1); };
    $('status').textContent = '재생 중…';
    K.stopAll();
    const p = natural ? K.speakAuto($('text').value, o) : K.speak($('text').value, o);
    p.catch((e) => { $('status').textContent = '오류: ' + e.message; });
  }
  $('playRaw').addEventListener('click', () => play(false));
  $('playNat').addEventListener('click', () => play(true));
  $('stop').addEventListener('click', () => { K.stopAll(); $('status').textContent = '멈춤'; highlight(-1); });
  $('copySsml').addEventListener('click', () => {
    const t = $('ssml').textContent;
    if (navigator.clipboard) navigator.clipboard.writeText(t).then(() => { $('copySsml').textContent = '복사됨'; setTimeout(() => { $('copySsml').textContent = '복사'; }, 1200); });
  });
  update();
})();
