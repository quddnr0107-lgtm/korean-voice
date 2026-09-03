/* 나의 군대 — 한국어 음성 자연화 엔진 (ko-voice.js)
   외부 의존 없음. 브라우저(window.KoVoice)와 Node(require)에서 같이 쓴다.

   하는 일 — "말하기 전에 글을 사람이 읽듯 고쳐 쓴다":
   1. normalize  : 숫자·단위·날짜·시간·기호·영문 약어를 소리 나는 대로 (2명→두 명, 6월→유월, 150만원→백오십만 원)
   2. phrase     : 문장 → 억양구(IP)/강세구(AP) 단위로 쪼개고 쉼 길이를 정한다 (K-ToBI: IP 경계=긴 쉼+말끝 늘림)
   3. prosody    : 문장 유형(의문·감탄·명령·평서)과 감정(기쁨·슬픔·화남·차분)에 따라 높낮이·속도·크기 조정
   4. pronounce  : 표준 발음법 음운 변동(연음·비음화·유음화·경음화·구개음화·ㅎ 축약) — 발음 표기 확인용(선택)
   5. speak      : Web Speech API로 위 결과를 조각별로 재생 (Chrome 200자 끊김·15초 중단 회피)
   6. toSSML     : 클라우드 TTS(Google·Azure·Clova)용 SSML 생성

   근거: 표준 발음법(국립국어원) · K-ToBI(Jun) 억양 구조 · 한국어 감정 음성 운율 연구(높낮이·속도·강도).
   음성 데이터는 수집하지 않는다 — 규칙은 공개 언어학 자료에서 왔다. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.KoVoice = factory();
})(typeof globalThis !== 'undefined' ? globalThis : (typeof self !== 'undefined' ? self : this), function () {
  'use strict';

  /* ───────────────────────── 1. 한글 분해·조합 ───────────────────────── */
  const CHO = ['ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ'];
  const JUNG = ['ㅏ', 'ㅐ', 'ㅑ', 'ㅒ', 'ㅓ', 'ㅔ', 'ㅕ', 'ㅖ', 'ㅗ', 'ㅘ', 'ㅙ', 'ㅚ', 'ㅛ', 'ㅜ', 'ㅝ', 'ㅞ', 'ㅟ', 'ㅠ', 'ㅡ', 'ㅢ', 'ㅣ'];
  const JONG = ['', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ', 'ㄻ', 'ㄼ', 'ㄽ', 'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ'];
  const isHangul = (ch) => ch >= '가' && ch <= '힣';
  function decompose(ch) {
    const code = ch.charCodeAt(0) - 0xac00;
    return { c: CHO[Math.floor(code / 588)], v: JUNG[Math.floor((code % 588) / 28)], f: JONG[code % 28] };
  }
  function compose(s) {
    return String.fromCharCode(0xac00 + CHO.indexOf(s.c) * 588 + JUNG.indexOf(s.v) * 28 + JONG.indexOf(s.f || ''));
  }

  /* ───────────────────────── 2. 숫자 읽기 ───────────────────────── */
  const SINO = ['', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구'];
  const NAT_ONES = ['', '하나', '둘', '셋', '넷', '다섯', '여섯', '일곱', '여덟', '아홉'];
  const NAT_ADN = ['', '한', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉'];
  const NAT_TENS = ['', '열', '스물', '서른', '마흔', '쉰', '예순', '일흔', '여든', '아흔'];
  const DIGIT_WORD = ['공', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구'];

  function sino4(n) { // 0~9999
    let s = '';
    const th = Math.floor(n / 1000), hu = Math.floor((n % 1000) / 100), te = Math.floor((n % 100) / 10), on = n % 10;
    if (th) s += (th === 1 ? '' : SINO[th]) + '천';
    if (hu) s += (hu === 1 ? '' : SINO[hu]) + '백';
    if (te) s += (te === 1 ? '' : SINO[te]) + '십';
    if (on) s += SINO[on];
    return s;
  }
  // 한자어 수: 12,345,678 → 천이백삼십사만 오천육백칠십팔. 만 단위로 끊어 띄운다(엔진이 덩어리를 인식하기 쉽다).
  function readSino(n) {
    n = Math.floor(Math.abs(Number(n)));
    if (!isFinite(n)) return '';
    if (n === 0) return '영';
    const units = ['', '만', '억', '조', '경'];
    const groups = [];
    let i = 0;
    while (n > 0 && i < units.length) {
      const g = n % 10000;
      if (g) {
        // "일만"이 아니라 "만" — 단, 억·조는 "일억·일조"로 읽는다. 만 앞에 상위 단위가 있으면(일억 일만) 그대로 둔다.
        const body = (g === 1 && i === 1 && n < 10000) ? '' : sino4(g);
        groups.unshift(body + units[i]);
      }
      n = Math.floor(n / 10000); i++;
    }
    return groups.join(' ');
  }
  // 고유어 수(1~99). adn=true면 관형형(한·두·세·네·스무).
  function readNative(n, adn) {
    n = Math.floor(Number(n));
    if (n === 0) return '영';
    if (n >= 100) return readSino(n);
    const te = Math.floor(n / 10), on = n % 10;
    if (adn && n === 20) return '스무';
    return NAT_TENS[te] + (adn ? NAT_ADN[on] : NAT_ONES[on]);
  }
  function readDigits(str) { return str.replace(/\D/g, '').split('').map((d) => DIGIT_WORD[+d]).join(''); }
  function readDecimal(str) { // "2.5" → 이 점 오
    const [a, b] = str.split('.');
    return readSino(a) + ' 점 ' + readDigits(b);
  }
  function readNumber(str) { // 정수·소수·천단위 콤마
    const s = String(str).replace(/,/g, '');
    return s.includes('.') ? readDecimal(s) : readSino(s);
  }

  // 고유어로 세는 단위(1~99일 때). "명·개·살·시·시간·번·마리…"
  const NATIVE_UNITS = ['시간', '번째', '군데', '켤레', '그릇', '봉지', '상자', '송이', '자루', '포기', '바퀴', '걸음', '가지', '사람',
    '명', '개', '살', '시', '번', '마리', '잔', '병', '장', '권', '대', '벌', '채', '척', '곳', '달', '줄', '통', '판', '알',
    '조각', '방', '끼', '컵', '캔', '쌍', '팀', '과목', '문제', '편', '곡', '박스', '묶음', '꾸러미', '다발', '타', '켤레'];
  // 한자어로 세는 단위
  const SINO_UNITS = ['개월', '주일', '학년', '학기', '등급', '호선', '번지', '페이지', '인분', '사단', '여단', '연대', '대대', '중대', '소대', '분대',
    '킬로미터', '킬로그램', '센티미터', '밀리미터', '킬로칼로리', '퍼센트', '밀리리터', '리터', '미터', '그램',
    '년', '월', '일', '분', '초', '원', '층', '호', '회', '기', '등', '급', '주', '차', '조', '항', '동', '반', '세', '점', '위', '쪽',
    '인', '박', '부', '건', '종', '류', '배', '도', '차원', '차선', '단', '급', '대', '군', '기수', '기간', '차례',
    'km', 'kg', 'cm', 'mm', 'ml', 'kcal', 'm', 'g', 'l', '%', '℃', '°'];
  // 겹치는 단위 정리: '대'는 자동차 '세 대'(고유어)로, '시간'·'시'는 고유어. 한자어 목록에서 '대' 제거.
  const SINO_SET = new Set(SINO_UNITS.filter((u) => u !== '대'));
  const UNIT_ALIASES = { km: '킬로미터', kg: '킬로그램', cm: '센티미터', mm: '밀리미터', ml: '밀리리터', kcal: '킬로칼로리', m: '미터', g: '그램', l: '리터', '%': '퍼센트', '℃': '도', '°': '도' };
  const ALL_UNITS = Array.from(new Set(NATIVE_UNITS.concat(SINO_UNITS))).sort((a, b) => b.length - a.length);
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const UNIT_RE = new RegExp('(\\d[\\d,]*(?:\\.\\d+)?)\\s*(' + ALL_UNITS.map(esc).join('|') + ')(?![A-Za-z])', 'g');
  const BIG_RE = /(\d[\d,]*(?:\.\d+)?)\s*(만|억|조)(?=\s*(?:원|명|개|건|회|배|달러|톤|km|kg|m|점|%|\s|$|[^가-힣]))/g;

  function readWithUnit(numStr, unit) {
    const raw = numStr.replace(/,/g, '');
    const n = Number(raw);
    const spoken = UNIT_ALIASES[unit] || unit;
    if (raw.includes('.')) return readDecimal(raw) + ' ' + spoken;
    if (unit === '월') { if (n === 6) return '유월'; if (n === 10) return '시월'; return readSino(n) + ' 월'; }
    if (SINO_SET.has(unit) && !NATIVE_UNITS.includes(unit)) return readSino(n) + ' ' + spoken;
    if (NATIVE_UNITS.includes(unit)) {
      if (n >= 100 || n === 0) return readSino(n) + ' ' + spoken;
      // '시'는 1~12만 고유어. 24시 같은 표기는 한자어(이십사 시)로.
      if (unit === '시' && n > 12) return readSino(n) + ' 시';
      return readNative(n, true) + ' ' + spoken;
    }
    return readSino(n) + ' ' + spoken;
  }

  /* ───────────────────────── 3. 기호·영문 약어 ───────────────────────── */
  const ABBR = {
    KATUSA: '카투사', ROTC: '알오티씨', TOEIC: '토익', TOEFL: '토플', TEPS: '텝스', OPIc: '오픽', OPIC: '오픽', HSK: '에이치에스케이', JLPT: '제이엘피티',
    DMZ: '디엠지', GOP: '지오피', GP: '지피', PX: '피엑스', UDT: '유디티', SSU: '에스에스유', MP: '엠피', CCTV: '씨씨티비', PT: '피티',
    AI: '에이아이', IT: '아이티', SNS: '에스엔에스', PC: '피씨', TV: '티비', ID: '아이디', URL: '유알엘', QR: '큐알', OK: '오케이',
    FAQ: '에프에이큐', PDF: '피디에프', APP: '앱', App: '앱', app: '앱', KTX: '케이티엑스', SRT: '에스알티', GPS: '지피에스', USB: '유에스비',
    KAIST: '카이스트', UN: '유엔', NATO: '나토', ROK: '알오케이', US: '유에스', JSA: '제이에스에이', ROKA: '로카', RNTC: '알엔티씨',
    KIDA: '키다', MMA: '병무청', vs: '대', VS: '대', kg: '킬로그램', km: '킬로미터', cm: '센티미터', mm: '밀리미터',
  };
  const LETTER = { A: '에이', B: '비', C: '씨', D: '디', E: '이', F: '에프', G: '지', H: '에이치', I: '아이', J: '제이', K: '케이', L: '엘', M: '엠',
    N: '엔', O: '오', P: '피', Q: '큐', R: '알', S: '에스', T: '티', U: '유', V: '브이', W: '더블유', X: '엑스', Y: '와이', Z: '지' };
  const spellLetters = (w) => w.split('').map((ch) => LETTER[ch.toUpperCase()] || ch).join('');

  /* ───────────────────────── 3'. 입력 표기(일레븐랩스·SSML 형식 호환) ─────────────────────────
     <break time="0.5s"/> · <break time="300ms"/> → 쉼.  "..."·"…" → 300ms 쉼.
     [기쁨] [슬픔] [차분] [따뜻] [단호] [긴급] / [joy] [sad] [calm] [warm] [angry] [urgent] [whispers] … → 그 지점부터 감정 전환.
     쉼은 '⏸' 한 글자 = 100ms 로 본문에 남겨 두고(숫자·글자가 아니라 정규화가 건드리지 않는다) 구 나누기에서 소비한다. */
  const TAG_EMOTION = {
    joy: 'joy', happy: 'joy', excited: 'joy', cheerful: 'joy', laughs: 'joy', 기쁨: 'joy', 신남: 'joy', 밝게: 'joy', 축하: 'joy',
    sad: 'sad', sorrow: 'sad', gentle: 'sad', 슬픔: 'sad', 위로: 'sad', 슬프게: 'sad', 조용히: 'sad',
    calm: 'calm', whispers: 'calm', whisper: 'calm', serious: 'calm', 차분: 'calm', 차분히: 'calm', 속삭임: 'calm', 진지: 'calm',
    warm: 'warm', friendly: 'warm', 따뜻: 'warm', 따뜻하게: 'warm', 친근: 'warm',
    angry: 'anger', anger: 'anger', stern: 'anger', firm: 'anger', 화남: 'anger', 단호: 'anger', 단호하게: 'anger', 엄격: 'anger',
    urgent: 'urgent', hurry: 'urgent', 긴급: 'urgent', 급하게: 'urgent', 다급: 'urgent',
    neutral: 'neutral', normal: 'neutral', 보통: 'neutral', 중립: 'neutral',
  };
  const pauseMark = (ms) => ' ' + '⏸'.repeat(Math.max(1, Math.min(30, Math.round(ms / 100)))) + ' ';
  // 반환: [{ emotion: null|string, text }] — 태그가 바뀌는 지점마다 조각.
  function parseTags(text) {
    let t = String(text == null ? '' : text);
    t = t.replace(/<break\s+time\s*=\s*["']?\s*([\d.]+)\s*(ms|s)?\s*["']?\s*\/?>/gi, (m, n, u) => pauseMark(u === 'ms' ? +n : +n * 1000));
    t = t.replace(/<\/?(speak|p|s|prosody|emphasis|voice)[^>]*>/gi, ' ');
    t = t.replace(/(?<![.\d])\.{3,}(?!\d)|…+/g, pauseMark(300));
    const segs = [];
    let cur = { emotion: null, text: '' };
    const re = /\[([^\[\]]{1,20})\]/g;
    let last = 0, m;
    while ((m = re.exec(t))) {
      const key = m[1].trim().toLowerCase();
      const emo = TAG_EMOTION[key] || TAG_EMOTION[m[1].trim()];
      cur.text += t.slice(last, m.index);
      last = re.lastIndex;
      if (emo) { if (cur.text.trim()) segs.push(cur); cur = { emotion: emo, text: '' }; }
      // 모르는 태그는 지운다(소리로 낼 수 없다)
    }
    cur.text += t.slice(last);
    if (cur.text.trim() || !segs.length) segs.push(cur);
    return segs;
  }

  /* ───────────────────────── 4. normalize ───────────────────────── */
  function normalize(text) {
    let t = String(text == null ? '' : text);
    // 마크다운·이모지·URL 등 소리로 낼 수 없는 것부터 제거
    t = t.replace(/https?:\/\/\S+/g, '링크').replace(/www\.\S+/g, '링크');
    t = t.replace(/^[ \t]*(#{1,6}|[-*•]|\d+[.)])\s+/gm, '');
    t = t.replace(/~~/g, '').replace(/[*_`]{1,3}(?=\S)|(?<=\S)[*_`]{1,3}/g, '');
    t = t.replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{200D}]/gu, '');
    // 전화번호: 010-1234-5678 → 공일공 일이삼사 오육칠팔
    t = t.replace(/\b0\d{1,2}-\d{3,4}-\d{4}\b/g, (m) => m.split('-').map(readDigits).join(' '));
    // 날짜: 2026-09-03 · 2026.9.3 · 2026/9/3
    t = t.replace(/\b(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\.?(?!\d)/g, (m, y, mo, d) => readSino(y) + ' 년 ' + readWithUnit(mo, '월') + ' ' + readSino(d) + ' 일');
    // 9/3 → 구 월 삼 일 (월·일 범위일 때만), 아니면 분수
    t = t.replace(/(?<![\d.])(\d{1,2})\/(\d{1,2})(?![\d/])/g, (m, a, b) => (+a >= 1 && +a <= 12 && +b >= 1 && +b <= 31) ? readWithUnit(a, '월') + ' ' + readSino(b) + ' 일' : readSino(b) + ' 분의 ' + readSino(a));
    // 시각: 10:30 → 열 시 삼십 분
    t = t.replace(/\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b/g, (m, h, mi, s) => readWithUnit(h, '시') + ' ' + readSino(mi) + ' 분' + (s ? ' ' + readSino(s) + ' 초' : ''));
    // 비율: 2.5:1 → 이 점 오 대 일 (시각 hh:mm은 위에서 이미 처리됨)
    t = t.replace(/(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)(?![\d:])/g, (m, a, b) => readNumber(a) + ' 대 ' + readNumber(b));
    // 영하: -3도
    t = t.replace(/(^|[\s(])-(\d+)\s*(도|℃)/g, (m, p, n) => p + '영하 ' + readSino(n) + ' 도');
    // 제6회 → 제육 회
    t = t.replace(/제\s*(\d+)\s*(회|차|기|장|조|항|절|편|대)/g, (m, n, u) => '제' + readSino(n) + ' ' + u);
    // 범위: 18~21개월 → 18개월에서 21개월
    t = t.replace(/(\d[\d,]*(?:\.\d+)?)\s*[~∼～]\s*(\d[\d,]*(?:\.\d+)?)\s*([가-힣%a-z℃°]{1,4})?/g, (m, a, b, u) => u ? a + u + '에서 ' + b + u : a + '에서 ' + b);
    // 150만원 · 3억 → 숫자로 환산 후 읽기
    t = t.replace(BIG_RE, (m, n, big) => {
      const mult = { 만: 1e4, 억: 1e8, 조: 1e12 }[big];
      return readSino(Math.round(Number(n.replace(/,/g, '')) * mult)) + ' ';
    });
    // 숫자 + 단위
    t = t.replace(UNIT_RE, (m, n, u) => readWithUnit(n, u));
    // 남은 숫자(단위 없음): 소수·콤마 포함
    t = t.replace(/\d[\d,]*(?:\.\d+)?/g, (m) => readNumber(m));
    // 영문 약어·단어
    t = t.replace(/[A-Za-z][A-Za-z0-9]*/g, (w) => {
      if (ABBR[w]) return ABBR[w];
      if (/^[A-Z]{1,6}$/.test(w)) return spellLetters(w);
      return w; // 일반 영단어는 엔진의 영어 발음에 맡긴다
    });
    // 기호 → 쉼표·낱말
    t = t.replace(/\s*[~∼～]\s*/g, '에서 '); // 남은 물결표(시각 뒤 등): 아홉 시~열 시 → 아홉 시에서 열 시
    t = t.replace(/\s*[·ㆍ]\s*/g, ', ').replace(/\s*\/\s*/g, ', ').replace(/&/g, ' 그리고 ').replace(/\+/g, ' 플러스 ');
    t = t.replace(/[“”"'‘’「」『』]/g, '').replace(/[()\[\]{}<>]/g, ', ');
    t = t.replace(/[→⇒]/g, ' 에서 ').replace(/[※▶▷■□◆◇○●★☆✓✔]/g, '');
    t = t.replace(/…/g, '.').replace(/\.{2,}/g, '.');
    t = t.replace(/[ \t]+/g, ' ').replace(/ +([,.!?])/g, '$1').replace(/\s*,\s*(,\s*)+/g, ', ').replace(/^\s*,\s*|\s*,\s*$/gm, '').replace(/\s*,\s*([.!?])/g, '$1').trim();
    return t;
  }

  /* ───────────────────────── 5. 문장·구 나누기 ───────────────────────── */
  // 종결어미(문장부호 없이 이어진 문장도 끊는다)
  const SENT_END = /(습니다|입니다|니다|세요|십시오|네요|어요|아요|해요|예요|이에요|지요|죠|까요|나요|군요|(?<!누)구나|거든요|잖아요|ㄹ까요)$/;
  // 연결어미 → 중간 쉼
  const CONJ = ['자마자', '으니까', '으려고', '면서', '는데도', '는데', '지만', '거나', '든지', '니까', '라서', '려고', '도록', '다가', '어서', '아서', '해서', '으며', '으면', '더니', '길래', '고', '며', '면', '서'];
  const PARTICLE = /(은|는|이|가|을|를|에서|에게|께서|부터|까지|으로|로|와|과|도|의|만|에)$/;
  // 강조어: 앞에 짧은 쉼 + 조금 느리게 (한국어 초점은 AP 첫머리를 높이고 앞에 쉼을 둔다)
  const EMPH = /^(반드시|절대|꼭|주의|경고|마감|필수|중요|무조건|즉시|바로|특히|단,|단\s|다만|주의:|참고:)/;

  function splitSentences(text) {
    const out = [];
    const lines = String(text).split(/\n+/);
    for (const line of lines) {
      let buf = '';
      const words = line.trim().split(/\s+/).filter(Boolean);
      for (let i = 0; i < words.length; i++) {
        const w = words[i];
        buf += (buf ? ' ' : '') + w;
        const punct = /[.!?]$/.test(w);
        const bare = w.replace(/[.!?,]+$/, '');
        if (punct || (SENT_END.test(bare) && bare.length >= 3)) { out.push(buf.trim()); buf = ''; }
      }
      if (buf.trim()) out.push(buf.trim());
    }
    return out.filter(Boolean);
  }

  function sentenceType(s) {
    const bare = s.replace(/⏸/g, '').replace(/[.!?\s]+$/, '');
    if (/\?$/.test(s) || (/(까요|나요|습니까|입니까|[은는신인]가요|을까|ㄹ까|을래요|ㄹ래요|던가요)$/.test(bare) && !/\.$/.test(s))) return 'question';
    if (/!$/.test(s) || /(네요|구나|군요|다니)$/.test(bare)) return 'exclaim';
    if (/(세요|십시오|주세요|합시다|자)$/.test(bare)) return 'request';
    return 'statement';
  }

  const PAUSE = { ip: 480, question: 520, exclaim: 420, comma: 240, conj: 200, weak: 110, emph: 130 };
  const PAUSE_DEFAULT = Object.assign({}, PAUSE);
  // 하강조 폭·기본 속도·의문문 상승은 화자 프로필(tools/analyze-voice.py 산출)로 바꿀 수 있다.
  const TUNE = { declination: 0.06, rate: 1, questionRise: 1.08 };
  let PROFILE = null;
  function applyProfile(profile) {
    Object.assign(PAUSE, PAUSE_DEFAULT); TUNE.declination = 0.06; TUNE.rate = 1; TUNE.questionRise = 1.08; PROFILE = null;
    if (!profile) return { pause: Object.assign({}, PAUSE), tune: Object.assign({}, TUNE) };
    const e = profile.engine || profile;
    if (e.pause) for (const k of Object.keys(e.pause)) if (typeof e.pause[k] === 'number' && k in PAUSE) PAUSE[k] = Math.max(50, Math.min(1500, e.pause[k]));
    if (typeof e.declination === 'number') TUNE.declination = Math.max(0, Math.min(0.2, e.declination));
    if (typeof e.rate === 'number') TUNE.rate = Math.max(0.6, Math.min(1.5, e.rate));
    if (typeof e.questionRise === 'number') TUNE.questionRise = Math.max(1, Math.min(1.3, e.questionRise));
    PROFILE = profile;
    return { pause: Object.assign({}, PAUSE), tune: Object.assign({}, TUNE) };
  }

  // 문장 하나 → 구(chunk) 배열. 각 구는 {text, pause(ms, 뒤에 둘 쉼), emph}
  function phraseSentence(s, maxChars) {
    maxChars = maxChars || 170;
    const words = s.split(/\s+/).filter(Boolean);
    const chunks = [];
    let cur = [], syl = 0;
    const flush = (pause) => { if (cur.length) { chunks.push({ text: cur.join(' '), pause, emph: false }); cur = []; syl = 0; } };
    for (let i = 0; i < words.length; i++) {
      const w = words[i];
      const isLast = i === words.length - 1;
      if (/^⏸+$/.test(w)) { // 명시적 쉼: 앞 구에 붙이고, 앞 구가 없으면 무음 조각
        const ms = w.length * 100;
        if (cur.length) flush(ms); else if (chunks.length) chunks[chunks.length - 1].pause += ms; else chunks.push({ text: '', pause: ms, emph: false });
        continue;
      }
      if (EMPH.test(w) && cur.length) { flush(PAUSE.emph); }
      cur.push(w); syl += w.replace(/[^가-힣]/g, '').length;
      if (isLast) break;
      const bare = w.replace(/[,.!?]+$/, '');
      if (/,$/.test(w)) { flush(PAUSE.comma); continue; }
      if (bare.length >= 2 && CONJ.some((c) => bare.endsWith(c))) { flush(PAUSE.conj); continue; }
      // 긴 구는 조사 뒤에서 살짝 쉰다(호흡 단위 ≈ 12음절)
      if (syl >= 12 && PARTICLE.test(bare)) { flush(PAUSE.weak); continue; }
      if (cur.join(' ').length >= maxChars) { flush(PAUSE.weak); continue; }
    }
    flush(0);
    for (const c of chunks) c.emph = EMPH.test(c.text);
    return chunks.length ? chunks : [{ text: s.replace(/⏸/g, '').trim(), pause: 0, emph: false }];
  }

  /* ───────────────────────── 6. 감정·운율 ───────────────────────── */
  // 연구 결과 요약: 화남=높은 음도·빠름·큰 소리, 기쁨=높은 음도·빠름, 슬픔=낮은 음도·느림·작은 소리.
  const EMOTIONS = {
    neutral: { rate: 1.0, pitch: 1.0, volume: 1.0, label: '보통' },
    calm: { rate: 0.92, pitch: 0.97, volume: 0.95, label: '차분' },
    warm: { rate: 0.95, pitch: 1.04, volume: 1.0, label: '따뜻' },
    joy: { rate: 1.08, pitch: 1.12, volume: 1.0, label: '기쁨' },
    sad: { rate: 0.85, pitch: 0.9, volume: 0.85, label: '슬픔·위로' },
    anger: { rate: 1.12, pitch: 1.15, volume: 1.0, label: '단호·화남' },
    urgent: { rate: 1.05, pitch: 1.08, volume: 1.0, label: '긴급' },
  };
  const EMO_KW = [
    ['joy', /(축하|합격|기쁘|좋아요|좋네|감사|고마|반가|환영|기대|다행|성공|최고|멋지|잘했)/],
    ['sad', /(걱정|불안|힘들|막막|죄송|안타깝|아쉽|탈락|불합격|우울|슬프|외로|위로|괜찮아요)/],
    ['urgent', /(긴급|서두르|즉시|당장|오늘까지|임박|늦기 전에)/],
    ['anger', /(절대|금지|위반|처벌|엄격|반드시 안|하지 마)/],
    ['calm', /(참고|안내|설명|절차|기준|원칙|정리하면)/],
  ];
  function detectEmotion(text) {
    for (const [emo, re] of EMO_KW) if (re.test(text)) return emo;
    if (/!/.test(text)) return 'joy';
    return 'neutral';
  }

  // prepare: 전체 파이프라인. 반환 {emotion, normalized, sentences:[{text,type,chunks:[{text,pause,rate,pitch,volume,emph}]}]}
  function prepare(text, opts) {
    opts = opts || {};
    const segs = parseTags(text);
    const normalizedAll = segs.map((g) => opts.normalize === false ? g.text : normalize(g.text));
    const normalized = normalizedAll.join(' ').replace(/\s+/g, ' ').trim();
    const fallbackEmotion = opts.emotion && EMOTIONS[opts.emotion] ? opts.emotion : detectEmotion(normalized.replace(/⏸/g, ''));
    let emotion = segs[0].emotion || fallbackEmotion;
    const sentences = [];
    segs.forEach((g, gi) => {
      if (g.emotion) emotion = g.emotion;
      const E = EMOTIONS[emotion];
      const baseRate = (opts.rate || 1) * TUNE.rate * E.rate, basePitch = (opts.pitch || 1) * E.pitch, baseVol = (opts.volume || 1) * E.volume;
      splitSentences(normalizedAll[gi]).forEach((s) => sentences.push(buildSentence(s, E, baseRate, basePitch, baseVol, emotion)));
    });
    const first = segs[0].emotion || fallbackEmotion;
    return { emotion: first, emotionLabel: EMOTIONS[first].label, normalized, sentences };

    function buildSentence(s, E, baseRate, basePitch, baseVol, emo) {
      const type = sentenceType(s);
      const chunks = opts.phrase === false ? [{ text: s, pause: 0, emph: false }] : phraseSentence(s, opts.maxChars);
      const n = chunks.length;
      chunks.forEach((c, i) => {
        // 하강조(declination): 문장 첫 구는 조금 높고, 뒤로 갈수록 낮아진다. 마지막 구는 늘려 읽는다(final lengthening).
        const decl = n > 1 ? (1 + TUNE.declination / 2) - TUNE.declination * (i / (n - 1)) : 1.0;
        let pitch = basePitch * decl, rate = baseRate, volume = baseVol;
        if (i === n - 1) {
          rate *= 0.93;
          if (type === 'question') pitch = basePitch * TUNE.questionRise; // 상승 경계성조(H%)
          else if (type === 'exclaim') { pitch = basePitch * 1.05; volume = Math.min(1, baseVol * 1.05); }
          else if (type === 'request') rate *= 0.98;
          c.pause = type === 'question' ? PAUSE.question : type === 'exclaim' ? PAUSE.exclaim : PAUSE.ip;
        }
        if (c.emph) { rate *= 0.9; pitch *= 1.04; }
        c.rate = +Math.min(2, Math.max(0.5, rate)).toFixed(3);
        c.pitch = +Math.min(2, Math.max(0.5, pitch)).toFixed(3);
        c.volume = +Math.min(1, Math.max(0.2, volume)).toFixed(3);
        if (opts.g2p) c.text = pronounce(c.text);
      });
      return { text: s.replace(/\s*⏸+\s*/g, ' ').trim(), type, emotion: emo, chunks };
    }
  }

  /* ───────────────────────── 7. 음운 변동(표준 발음법) ───────────────────────── */
  const TENSE = { ㄱ: 'ㄲ', ㄷ: 'ㄸ', ㅂ: 'ㅃ', ㅅ: 'ㅆ', ㅈ: 'ㅉ' };
  const ASP = { ㄱ: 'ㅋ', ㄷ: 'ㅌ', ㅈ: 'ㅊ', ㅅ: 'ㅆ' };
  const LIAISON = { ㄳ: ['ㄱ', 'ㅆ'], ㄵ: ['ㄴ', 'ㅈ'], ㄶ: ['ㄴ', ''], ㄺ: ['ㄹ', 'ㄱ'], ㄻ: ['ㄹ', 'ㅁ'], ㄼ: ['ㄹ', 'ㅂ'], ㄽ: ['ㄹ', 'ㅆ'], ㄾ: ['ㄹ', 'ㅌ'], ㄿ: ['ㄹ', 'ㅍ'], ㅀ: ['ㄹ', ''], ㅄ: ['ㅂ', 'ㅆ'], ㅎ: ['', ''] };
  const NEUTRAL = { ㄲ: 'ㄱ', ㅋ: 'ㄱ', ㄳ: 'ㄱ', ㄺ: 'ㄱ', ㅅ: 'ㄷ', ㅆ: 'ㄷ', ㅈ: 'ㄷ', ㅊ: 'ㄷ', ㅌ: 'ㄷ', ㅎ: 'ㄷ', ㅍ: 'ㅂ', ㄿ: 'ㅂ', ㅄ: 'ㅂ', ㄵ: 'ㄴ', ㄶ: 'ㄴ', ㄻ: 'ㅁ', ㄼ: 'ㄹ', ㄽ: 'ㄹ', ㄾ: 'ㄹ', ㅀ: 'ㄹ' };
  const CLUSTER_TENSE = new Set(['ㄼ', 'ㄾ', 'ㄺ', 'ㄳ', 'ㅄ', 'ㄿ']);
  const NASAL = { ㄱ: 'ㅇ', ㄷ: 'ㄴ', ㅂ: 'ㅁ' };

  function pronounceWord(word) {
    const syl = word.split('').map(decompose);
    const n = syl.length;
    // (0) ㅢ: 자음 뒤 ㅢ → ㅣ(희→히), 둘째 음절 이하 '의' → 이
    for (let i = 0; i < n; i++) if (syl[i].v === 'ㅢ' && (syl[i].c !== 'ㅇ' || i > 0)) syl[i].v = 'ㅣ';
    for (let i = 0; i < n - 1; i++) {
      const a = syl[i], b = syl[i + 1];
      // (1) ㅎ: 받침 ㅎ·ㄶ·ㅀ + ㄱㄷㅈ → 격음, +ㅅ → ㅆ, +ㄴ → ㄴ, +모음 → ㅎ 탈락
      if (a.f === 'ㅎ' || a.f === 'ㄶ' || a.f === 'ㅀ') {
        const rest = a.f === 'ㅎ' ? '' : a.f === 'ㄶ' ? 'ㄴ' : 'ㄹ';
        if (ASP[b.c]) { b.c = ASP[b.c]; a.f = rest; continue; }
        if (b.c === 'ㄴ') { a.f = rest || 'ㄴ'; continue; }
        if (b.c === 'ㅇ') { a.f = rest; }
      }
      // (1') 장애음 받침 + ㅎ → 격음 (축하→추카, 못해→모태, 잡히다→자피다, 앉히다→안치다)
      if (b.c === 'ㅎ' && a.f && a.f !== 'ㅎ') {
        const base = NEUTRAL[a.f] || a.f;
        if (a.f === 'ㅈ' || a.f === 'ㅊ' || a.f === 'ㄵ') { b.c = 'ㅊ'; a.f = a.f === 'ㄵ' ? 'ㄴ' : ''; continue; }
        if (a.f === 'ㄷ' && (b.v === 'ㅣ' || b.v === 'ㅕ')) { b.c = 'ㅊ'; a.f = ''; continue; } // 굳히다→구치다
        if (base === 'ㄱ') { b.c = 'ㅋ'; a.f = a.f === 'ㄺ' ? 'ㄹ' : ''; continue; }
        if (base === 'ㄷ') { b.c = 'ㅌ'; a.f = ''; continue; }
        if (base === 'ㅂ') { b.c = 'ㅍ'; a.f = a.f === 'ㄼ' ? 'ㄹ' : ''; continue; }
      }
      // (2) 구개음화: ㄷ·ㅌ 받침 + 이 → 지·치 (같이→가치, 굳이→구지)
      if ((a.f === 'ㄷ' || a.f === 'ㅌ' || a.f === 'ㄾ') && b.c === 'ㅇ' && b.v === 'ㅣ') {
        b.c = a.f === 'ㄷ' ? 'ㅈ' : 'ㅊ'; a.f = a.f === 'ㄾ' ? 'ㄹ' : ''; continue;
      }
      // (3) 연음: 받침 + 모음 → 받침이 다음 초성으로 (꽃이→꼬치, 값이→갑씨, 좋아→조아)
      if (a.f && a.f !== 'ㅇ' && b.c === 'ㅇ') {
        const pair = LIAISON[a.f];
        if (pair) { a.f = pair[0]; if (pair[1]) b.c = pair[1]; } else { b.c = a.f; a.f = ''; }
        continue;
      }
    }
    // (4) 받침 중화·겹받침 단순화 → (5) 경음화 → (6) 비음화·유음화
    for (let i = 0; i < n; i++) {
      const a = syl[i], b = syl[i + 1];
      if (!a.f) continue;
      let tenseNext = false;
      if (b && a.f === 'ㄺ' && b.c === 'ㄱ') { a.f = 'ㄹ'; b.c = 'ㄲ'; continue; } // 읽고→일꼬
      if (a.f === 'ㄼ' && a.c === 'ㅂ' && a.v === 'ㅏ') { a.f = 'ㅂ'; tenseNext = true; } // 밟다→밥따
      else if (NEUTRAL[a.f]) { tenseNext = CLUSTER_TENSE.has(a.f); a.f = NEUTRAL[a.f]; }
      if (!b) continue;
      if ((a.f === 'ㄱ' || a.f === 'ㄷ' || a.f === 'ㅂ' || tenseNext) && TENSE[b.c]) b.c = TENSE[b.c];
      if (NASAL[a.f] && (b.c === 'ㄴ' || b.c === 'ㅁ')) a.f = NASAL[a.f];
      else if (NASAL[a.f] && b.c === 'ㄹ') { a.f = NASAL[a.f]; b.c = 'ㄴ'; }
      else if ((a.f === 'ㅁ' || a.f === 'ㅇ') && b.c === 'ㄹ') b.c = 'ㄴ';
      else if (a.f === 'ㄴ' && b.c === 'ㄹ') a.f = 'ㄹ';
      else if (a.f === 'ㄹ' && b.c === 'ㄴ') b.c = 'ㄹ';
    }
    return syl.map(compose).join('');
  }
  function pronounce(text) {
    return String(text).replace(/[가-힣]+/g, pronounceWord);
  }

  /* ───────────────────────── 8. SSML ───────────────────────── */
  const xml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  function toSSML(text, opts) {
    const p = prepare(text, opts);
    const pct = (v) => (v >= 1 ? '+' : '') + Math.round((v - 1) * 100) + '%';
    const body = p.sentences.map((s) => '  <s>' + s.chunks.map((c) =>
      '<prosody rate="' + Math.round(c.rate * 100) + '%" pitch="' + pct(c.pitch) + '"' + (c.volume < 0.95 ? ' volume="' + Math.round(c.volume * 100) + '%"' : '') + '>' +
      (c.emph ? '<emphasis level="moderate">' + xml(c.text) + '</emphasis>' : xml(c.text)) + '</prosody>' +
      (c.pause ? '<break time="' + c.pause + 'ms"/>' : '')).join('') + '</s>').join('\n');
    return '<speak xml:lang="ko-KR">\n<p>\n' + body + '\n</p>\n</speak>';
  }

  /* ───────────────────────── 9. Web Speech 재생 ───────────────────────── */
  const VOICE_RANK = [/Google/i, /Natural|Online|Neural/i, /Yuna|유나/i, /SunHi|InJoon|Heami|Hyunsu|BongJin/i, /Premium|Enhanced/i, /Siri/i];
  function koVoices() {
    if (typeof speechSynthesis === 'undefined') return [];
    const vs = speechSynthesis.getVoices().filter((v) => /^ko/i.test(v.lang));
    const score = (v) => { let s = 0; VOICE_RANK.forEach((re, i) => { if (re.test(v.name)) s += 100 - i * 10; }); if (/compact/i.test(v.name)) s -= 50; if (!v.localService) s += 5; return s; };
    return vs.sort((a, b) => score(b) - score(a));
  }
  let current = null;
  function stop() { current = null; if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel(); }
  // speak(text, {voice, emotion, rate, pitch, volume, g2p, natural:true, onChunk, onEnd})
  function speak(text, opts) {
    opts = opts || {};
    if (typeof speechSynthesis === 'undefined') return Promise.reject(new Error('이 브라우저는 음성 합성을 지원하지 않습니다'));
    stop();
    const natural = opts.natural !== false;
    const plan = natural ? prepare(text, opts) : { sentences: splitSentences(String(text)).map((s) => ({ text: s, type: 'statement', chunks: [{ text: s, pause: 0, rate: opts.rate || 1, pitch: opts.pitch || 1, volume: opts.volume || 1 }] })) };
    const queue = [];
    plan.sentences.forEach((s) => s.chunks.forEach((c) => queue.push(c)));
    const voice = opts.voice || koVoices()[0] || null;
    const token = current = {};
    return new Promise((resolve) => {
      let i = 0;
      const next = () => {
        if (current !== token) return resolve(false);
        if (i >= queue.length) { current = null; if (opts.onEnd) opts.onEnd(); return resolve(true); }
        const c = queue[i++];
        if (!c.text) { setTimeout(next, c.pause || 0); return; }
        const u = new SpeechSynthesisUtterance(c.text);
        u.lang = 'ko-KR'; if (voice) u.voice = voice;
        u.rate = c.rate; u.pitch = c.pitch; u.volume = c.volume;
        const after = () => { if (current !== token) return; if (c.pause) setTimeout(next, c.pause); else next(); };
        u.onend = after; u.onerror = after;
        if (opts.onChunk) opts.onChunk(c, i - 1, queue.length);
        speechSynthesis.speak(u);
      };
      next();
    });
  }

  /* ───────────────────────── 10. 신경망 음성(/api/tts) + 자동 폴백 ─────────────────────────
     서버(lib/h/tts.js)가 Cloudflare Workers AI로 문장을 MP3로 만든다. 파일·저장소 없음.
     문장 단위로 요청해 (1) 첫 소리가 빨리 나고 (2) 같은 문장은 엣지 캐시에 맞는다.
     재생은 Web Audio(디코드 → 버퍼)로 한다 — blob: URL이 필요 없어 CSP(default-src 'self')를 안 건드린다.
     속도(rate)는 playbackRate로, 문장 사이 쉼은 우리 운율 계획대로. 높낮이(pitch)는 신경망 목소리에 맡긴다. */
  let neuralAvail = null; // null=모름, true/false
  async function neuralStatus(force) {
    if (neuralAvail !== null && !force) return neuralAvail;
    try {
      const r = await fetch('/api/tts', { method: 'GET', cache: 'no-store' });
      const j = await r.json();
      neuralAvail = !!(j && j.available);
    } catch (_) { neuralAvail = false; }
    return neuralAvail;
  }
  let audioCtx = null;
  function ctxGet() {
    const AC = typeof AudioContext !== 'undefined' ? AudioContext : (typeof webkitAudioContext !== 'undefined' ? webkitAudioContext : null);
    if (!AC) throw new Error('Web Audio 미지원');
    if (!audioCtx) audioCtx = new AC();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    return audioCtx;
  }
  async function fetchSentenceAudio(text) {
    const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, normalize: false }) });
    if (!r.ok) { let reason = r.status; try { reason = (await r.json()).reason || (await r.json()).error || reason; } catch (_) { /* */ } throw new Error('tts ' + reason); }
    const ab = await r.arrayBuffer();
    return await new Promise((res, rej) => ctxGet().decodeAudioData(ab.slice(0), res, rej));
  }
  let neuralCurrent = null;
  function playBuffer(buf, rate, volume, token) {
    return new Promise((resolve) => {
      const ctx = ctxGet();
      const src = ctx.createBufferSource(); src.buffer = buf; src.playbackRate.value = rate || 1;
      const gain = ctx.createGain(); gain.gain.value = volume == null ? 1 : volume;
      src.connect(gain); gain.connect(ctx.destination);
      src.onended = () => resolve(true);
      token.src = src;
      src.start();
    });
  }
  // speakNeural(text, opts) — opts: emotion·rate·volume·onChunk·onEnd
  async function speakNeural(text, opts) {
    opts = opts || {};
    stopNeural();
    const plan = prepare(text, opts);
    const items = plan.sentences.map((s) => {
      const last = s.chunks[s.chunks.length - 1] || { pause: 0, rate: 1, volume: 1 };
      const spoken = s.chunks.map((c) => c.text).filter(Boolean).join(', ');
      const pause = s.chunks.reduce((a, c) => a + (c.text ? 0 : c.pause), 0) + (last.pause || 0);
      return { text: spoken, pause, rate: last.rate, volume: last.volume, type: s.type };
    }).filter((it) => it.text);
    const token = neuralCurrent = { src: null };
    let pending = items.length ? fetchSentenceAudio(items[0].text) : null;
    for (let i = 0; i < items.length; i++) {
      if (neuralCurrent !== token) return false;
      const it = items[i];
      const buf = await pending; // 이 문장 디코드
      pending = i + 1 < items.length ? fetchSentenceAudio(items[i + 1].text).catch((e) => e) : null; // 다음 문장 미리 받기
      if (buf instanceof Error) throw buf;
      if (neuralCurrent !== token) return false;
      if (opts.onChunk) opts.onChunk(it, i, items.length);
      await playBuffer(buf, it.rate, it.volume, token);
      if (neuralCurrent !== token) return false;
      if (it.pause) await new Promise((r) => setTimeout(r, it.pause));
    }
    neuralCurrent = null;
    if (opts.onEnd) opts.onEnd();
    return true;
  }
  function stopNeural() { const t = neuralCurrent; neuralCurrent = null; if (t && t.src) { try { t.src.stop(); } catch (_) { /* */ } } }
  function stopAll() { stopNeural(); stop(); }
  // speakAuto: 신경망(서버) → 안 되면 브라우저 음성. opts.engine = 'auto'|'neural'|'web'. opts.onEngine(name)
  async function speakAuto(text, opts) {
    opts = opts || {};
    const engine = opts.engine || 'auto';
    if (engine !== 'web') {
      const ok = await neuralStatus(engine === 'neural');
      if (ok) {
        try { if (opts.onEngine) opts.onEngine('neural'); return await speakNeural(text, opts); }
        catch (e) { if (engine === 'neural') throw e; if (opts.onEngine) opts.onEngine('web:fallback ' + e.message); }
      } else if (engine === 'neural') throw new Error('신경망 음성 서버가 없습니다 (/api/tts available=false)');
    }
    if (opts.onEngine && engine === 'web') opts.onEngine('web');
    return speak(text, opts);
  }

  return {
    normalize, prepare, pronounce, toSSML, speak, stop, koVoices, parseTags,
    speakNeural, stopNeural, stopAll, speakAuto, neuralStatus,
    splitSentences, phraseSentence, sentenceType, detectEmotion,
    readSino, readNative, readWithUnit, EMOTIONS, PAUSE, TUNE, applyProfile, getProfile: () => PROFILE,
  };
});
