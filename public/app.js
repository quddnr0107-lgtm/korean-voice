/* 한국어 낭독 만들기 — 제품 화면. ko-voice.js(window.KoVoice)와 워커의 /tts·/render 를 쓴다.
   🔴 미리듣기와 mp3 만들기는 **같은 조각**(같은 문장·같은 r)을 쓴다 — 캐시 키가 같아서 미리들은 문장은
      mp3 를 만들 때 다시 합성되지 않는다. 조각을 나누는 규칙의 정본은 ko-voice.js 하나다. */
(function () {
  'use strict';
  const K = window.KoVoice;
  const $ = (id) => document.getElementById(id);

  const SAMPLES = {
    numbers: '2026년 9월 3일 기준 접수 인원은 1,234명이고 경쟁률은 2.5:1입니다. 6월 10일부터 10월 3일까지 매일 09:00~18:00에 접수하며, 문의는 1588-9090입니다. 참가비는 150만원이고 정원은 20명입니다.',
    notice: '안녕하세요. 3월 정기 점검 안내입니다. 점검은 3월 6일 오전 2시부터 4시간 동안 진행되며, 이 시간에는 결제와 로그인이 되지 않습니다. 불편을 드려 죄송합니다.',
    law: '근로기준법 제60조에 따라 1년간 80퍼센트 이상 출근한 근로자에게는 15일의 유급휴가를 주어야 합니다. 계속 근로한 기간이 3년 이상인 경우에는 최초 1년을 초과하는 매 2년에 대하여 1일을 가산합니다.',
    tags: '[따뜻] 첫 출근 전날이네요... 긴장되죠? <break time="0.6s"/> [차분] 준비물은 신분증과 통장 사본 2부입니다. [기쁨] 3개월 뒤에는 훨씬 익숙해져 있을 거예요!',
  };
  document.querySelectorAll('[data-sample]').forEach((b) => b.addEventListener('click', () => { $('text').value = SAMPLES[b.dataset.sample]; update(); }));

  Object.keys(K.EMOTIONS).forEach((k) => { const o = document.createElement('option'); o.value = k; o.textContent = K.EMOTIONS[k].label; $('emotion').appendChild(o); });

  const opts = () => ({ emotion: $('emotion').value || undefined, rate: +$('rate').value });

  /* 조각 만들기 — speakNeural 과 같은 규칙(문장 하나 = 요청 하나, 구 쉼은 문장 쉼에 합친다).
     미리듣기와 /render 가 이걸 같이 쓰기 때문에 캐시가 겹친다. */
  function buildItems(plan) {
    return plan.sentences.map((s) => {
      const last = s.chunks[s.chunks.length - 1] || { pause: 0, rate: 1 };
      const pause = s.chunks.reduce((a, c) => a + (c.text ? 0 : c.pause), 0) + (last.pause || 0);
      return { t: K.joinSpokenChunks(s.chunks), pause, r: Math.round((last.rate || 1) * 100) / 100 };
    }).filter((it) => it.t);
  }

  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  let plan = null, items = [];
  function update() {
    const text = $('text').value;
    plan = K.prepare(text, opts());
    items = buildItems(plan);
    // 원문에 없던 낱말 = 고쳐 읽는 곳
    const before = new Set(text.split(/\s+/));
    const after = plan.normalized.split(/\s+/);
    let fixed = 0;
    $('normalized').innerHTML = after.map((w) => {
      if (before.has(w)) return esc(w);
      fixed++; return '<mark>' + esc(w) + '</mark>';
    }).join(' ') || '<span style="color:var(--dim)">대본을 입력하면 읽는 방식을 보여 줍니다.</span>';
    const chars = items.reduce((a, it) => a + it.t.length, 0);
    $('fixcount').innerHTML = text.trim()
      ? '고쳐 읽는 곳 <b>' + fixed + '군데</b> · 문장 ' + items.length + '개 · ' + chars + '자 · 감정 ' + plan.emotionLabel
      : '';
    $('render').disabled = !items.length;
    $('preview').disabled = !items.length;
  }
  ['text', 'emotion', 'rate'].forEach((id) => $(id).addEventListener('input', update));
  $('voice').addEventListener('change', () => { revoke(); });

  const say = (m) => { $('status').textContent = m; };

  /* ── 미리듣기 — 문장 단위로 /tts 를 받아 Web Audio 로 잇는다(다음 문장은 재생 중에 미리 받는다) ── */
  let ctx = null, playing = null;
  function audioCtx() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) throw new Error('이 브라우저는 Web Audio 를 지원하지 않습니다');
    if (!ctx) ctx = new AC();
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  }
  const ttsUrl = (it) => '/tts?v=' + encodeURIComponent($('voice').value) + '&t=' + encodeURIComponent(it.t) + '&r=' + it.r.toFixed(2);
  async function fetchPiece(it) {
    const r = await fetch(ttsUrl(it));
    if (!r.ok) { let why = r.status; try { const j = await r.json(); why = j.reason || j.error || why; } catch (_) { /* */ } throw new Error('합성 실패: ' + why); }
    const ab = await r.arrayBuffer();
    return await new Promise((res, rej) => audioCtx().decodeAudioData(ab.slice(0), res, rej));
  }
  function playBuffer(buf, token) {
    return new Promise((resolve) => {
      const c = audioCtx();
      const src = c.createBufferSource(); src.buffer = buf; src.connect(c.destination);
      src.onended = () => resolve();
      token.src = src; src.start();
    });
  }
  async function preview() {
    stopPreview();
    const token = playing = { src: null };
    try {
      let pending = fetchPiece(items[0]);
      for (let i = 0; i < items.length; i++) {
        if (playing !== token) return;
        say('미리듣기 ' + (i + 1) + '/' + items.length + '…');
        const buf = await pending;
        pending = i + 1 < items.length ? fetchPiece(items[i + 1]).catch((e) => e) : null;
        if (buf instanceof Error) throw buf;
        if (playing !== token) return;
        await playBuffer(buf, token);
        if (playing !== token) return;
        if (items[i].pause) await new Promise((r) => setTimeout(r, items[i].pause));
      }
      say('미리듣기 끝 — 들은 문장은 mp3 를 만들 때 다시 합성되지 않습니다.');
    } catch (e) {
      say(e.message);
    } finally {
      if (playing === token) playing = null;
    }
  }
  function stopPreview() { const t = playing; playing = null; if (t && t.src) { try { t.src.stop(); } catch (_) { /* */ } } }
  $('preview').addEventListener('click', preview);
  $('stop').addEventListener('click', () => { stopPreview(); say('멈춤'); });

  /* ── 브라우저에서 만들기(우리 비용 0) ── 고른 순간에만 모듈·모델을 들여온다.
     서버만 쓰는 사람은 384MB 를 받지 않는다. 기기가 느리면 서버로 물러나라고 알려 준다. */
  let local = null;                       // import('/local-tts.js') 결과
  const where = () => $('where').value;

  /* 모델을 내려받는 것은 라이선스상 **배포**다(OpenRAIL-M §1(g)는 "web access" 로 모델을 제공하는 것을
     Distribution 으로 정의한다). §4(a)는 사용 제한을 집행 가능한 조항으로 두고 **다음 이용자에게 고지**하라고
     요구하므로, 첫 내려받기 직전에 한 번 확인을 받는다. 확인 사실은 이 기기에만 남는다. */
  const CONSENT_KEY = 'ko-voice-model-consent-v1';
  const consented = () => { try { return localStorage.getItem(CONSENT_KEY) === '1'; } catch (_) { return false; } };
  function askConsent() {
    return new Promise((resolve, reject) => {
      const box = $('consent'), check = $('consentCheck'), ok = $('consentOk');
      box.hidden = false; check.checked = false; ok.disabled = true;
      check.onchange = () => { ok.disabled = !check.checked; };
      ok.onclick = () => {
        try { localStorage.setItem(CONSENT_KEY, '1'); } catch (_) { /* 이 기기에 못 남겨도 진행은 한다 */ }
        box.hidden = true; resolve();
      };
      $('where').addEventListener('change', () => { box.hidden = true; reject(new Error('취소했습니다')); }, { once: true });
    });
  }

  async function localEngine() {
    if (!local) { say('브라우저 합성 모듈 불러오는 중…'); local = await import('/local-tts.js'); }
    if (!local.ready() && !consented()) { say('모델을 내려받기 전 확인이 필요합니다.'); await askConsent(); }
    if (!local.ready()) {
      await local.load({
        voice: $('voice').value,
        onStatus: (m, p) => say(m + (p ? ' ' + Math.round(p * 100) + '%' : '') + ' — 한 번만 받습니다'),
      });
      const i = local.info();
      $('engineInfo').textContent = '브라우저 엔진: ' + i.providers[0] + ' · 스레드 ' + i.threads +
        (self.crossOriginIsolated ? '' : ' (교차출처 격리 꺼짐 → 1스레드)');
    }
    return local;
  }
  $('where').addEventListener('change', () => { revoke(); say(where() === 'local' ? '내 브라우저에서 만듭니다 — 처음 한 번 모델 384MB를 받습니다.' : '서버에서 만듭니다.'); });

  /* ── 전체 mp3 만들기 — 워커의 /render 가 조각을 잇고 계획된 쉼을 넣어 한 파일로 돌려준다 ── */
  let url = null;
  function revoke() { if (url) { URL.revokeObjectURL(url); url = null; } $('player').hidden = true; $('download').hidden = true; }
  $('render').addEventListener('click', async () => {
    stopPreview(); revoke();
    $('render').disabled = true;
    if (where() === 'local') {
      try {
        const L = await localEngine();
        say('내 브라우저에서 만들고 있습니다…');
        const t0 = performance.now();
        const r = await L.synth(items, {
          onItem: (i, n, s) => say('내 브라우저에서 만드는 중 ' + i + '/' + n + ' · 속도 ' + s.rtf.toFixed(2) + '배'),
        });
        const blob = L.wavBlob(r.pcm, r.sampleRate);
        url = URL.createObjectURL(blob);
        const p = $('player'); p.src = url; p.hidden = false;
        const a = $('download');
        a.href = url; a.download = '낭독.wav'; a.textContent = '⬇ 내려받기 (' + Math.round(blob.size / 1024) + 'KB · WAV)'; a.hidden = false;
        const slow = r.rtf > L.SPEED_GATE_RTF;
        say('완성 — AI로 생성된 음성입니다. 오디오 ' + r.audioSeconds.toFixed(1) + '초를 ' +
            ((performance.now() - t0) / 1000).toFixed(1) + '초에 만들었습니다(실시간의 ' + (1 / r.rtf).toFixed(2) + '배)' +
            (slow ? ' · 이 기기에서는 서버가 더 빠릅니다.' : '') + ' 서버를 쓰지 않았습니다.');
      } catch (e) {
        say('브라우저 합성 실패: ' + e.message + ' — 만드는 곳을 서버로 바꿔 보세요.');
      } finally {
        $('render').disabled = !items.length;
      }
      return;
    }
    say('만들고 있습니다… 처음 나오는 문장은 합성에 문장당 2~4초가 걸립니다.');
    try {
      const res = await fetch('/render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ v: $('voice').value, items }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        if (j.error === 'quota_exceeded') throw new Error('오늘 무료 한도를 다 썼습니다(하루 ' + j.limit + '자). 내일 다시 쓰거나 유료 이용을 기다려 주세요.');
        if (j.error === 'too_many_chars') throw new Error('한 번에 만들 수 있는 길이를 넘었습니다(최대 ' + (j.limits && j.limits.chars) + '자). 대본을 나눠 주세요.');
        throw new Error('만들지 못했습니다: ' + (j.reason || j.error || res.status));
      }
      const blob = await res.blob();
      url = URL.createObjectURL(blob);
      const p = $('player'); p.src = url; p.hidden = false;
      const a = $('download');
      a.href = url; a.download = '낭독.mp3'; a.textContent = '⬇ 내려받기 (' + Math.round(blob.size / 1024) + 'KB)'; a.hidden = false;
      say(res.headers.get('X-TTS-Cache') === 'r2' ? '완성 — 전에 만든 것과 같은 대본이라 즉시 나왔습니다.' : '완성 — AI로 생성된 음성입니다.');
      quota();
    } catch (e) {
      say(e.message);
    } finally {
      $('render').disabled = !items.length;
    }
  });

  /* ── 남은 무료 한도 ── */
  function quota() {
    fetch('/render', { cache: 'no-store' }).then((r) => r.json()).then((j) => {
      if (!j || !j.ok) return;
      $('quota').textContent = j.metered
        ? '오늘 남은 무료 분량: ' + j.remaining + '자 / ' + j.limit + '자'
        : '무료 한도: 하루 ' + j.limit + '자 (계량기가 아직 붙지 않은 환경입니다)';
    }).catch(() => {});
  }

  update(); quota();
})();
