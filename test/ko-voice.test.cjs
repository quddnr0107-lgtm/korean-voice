// 한국어 음성 자연화 엔진(ko-voice.js) 회귀 테스트. 외부 의존 없음.
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const K = require('../public/ko-voice.js');

test('숫자·단위 정규화 — 고유어/한자어·달·큰 수·시각·범위', () => {
  const cases = [
    ['2명', '두 명'], ['100명', '백 명'], ['21살', '스물한 살'], ['20살', '스무 살'], ['3시간', '세 시간'],
    ['6월 10일', '유월 십 일'], ['10월', '시월'], ['18개월', '십팔 개월'], ['1주일', '일 주일'],
    ['150만원', '백오십만 원'], ['1,500,000원', '백오십만 원'], ['10000원', '만 원'], ['11000원', '만 천 원'], ['1억', '일억'],
    ['2026년 9월 3일', '이천이십육 년 구 월 삼 일'], ['2026-09-03', '이천이십육 년 구 월 삼 일'], ['9/3', '구 월 삼 일'],
    ['09:00', '아홉 시'], ['10:30', '열 시 삼십 분'], ['10:30:05', '열 시 삼십 분 오 초'], ['24시', '이십사 시'],
    ['09:00~18:00', '아홉 시부터 십팔 시까지'], ['09:30~12:00', '아홉 시 삼십 분부터 열두 시까지'], ['18~21개월', '십팔 개월에서 이십일 개월'],
    ['12.5%', '십이 점 오 퍼센트'], ['0.5배', '영 점 오 배'], ['5km', '오 킬로미터'], ['-3도', '영하 삼 도'],
    ['제6회', '제육 회'], ['3번째', '세 번째'], ['경쟁률 2.5:1', '경쟁률 이 점 오 대 일'], ['육군 18, 해군 20, 공군 21.', '육군 십팔, 해군 이십, 공군 이십일.'], ['1,234명', '천이백삼십사 명'], ['만 19세', '만 십구 세'], ['010-1234-5678', '공일공 일이삼사 오육칠팔'], ['1577-0000', '일오칠칠 공공공공'], ['1588-1234', '일오팔팔 일이삼사'],
  ];
  for (const [i, e] of cases) assert.strictEqual(K.normalize(i), e, i);
});

test('법령 조·항·호 연쇄 표기는 어절 경계를 보존한다', () => {
  assert.strictEqual(K.normalize('제3조제2항에 따라'), '제삼 조 제이 항에 따라');
  assert.strictEqual(K.normalize('제12조제3항제2호'), '제십이 조 제삼 항 제이 호');
});

test('법령 원문 항 번호 기호도 음성용 제N항으로 보존한다', () => {
  assert.strictEqual(K.normalize('제1조(목적) ① 이 법은 목적을 정한다. ② 국가는 지원하여야 한다.'),
    '제일 조 목적 제일 항 이 법은 목적을 정한다. 제이 항 국가는 지원하여야 한다.');
  assert.strictEqual(K.normalize('① 첫째 항목 ⑳ 스무 번째 항목'), '제일 항 첫째 항목 제이십 항 스무 번째 항목');
});

test('법령 목 표기의 점은 문장 끝으로 오인하지 않는다', () => {
  assert.strictEqual(K.normalize('가. 교육 대상자 나. 훈련 장소 다. 소집 일자'),
    '가목 교육 대상자 나목 훈련 장소 다목 소집 일자');
  assert.strictEqual(K.normalize('제1호가목. 교육 대상자'), '제일 호 가목 교육 대상자');
  const p = K.prepare('가. 교육 대상자 나. 훈련 장소', { emotion: 'neutral' });
  assert.strictEqual(p.sentences.length, 1, '목 표지 때문에 가짜 문장을 만들지 않는다');
});

test('군 장비명 K1·K2·K9는 일반 숫자 읽기와 분리한다', () => {
  const cases = [
    ['K1 전차', '케이원 전차'], ['K-1 전차', '케이원 전차'],
    ['K2 전차', '케이투 전차'], ['K-2 전차', '케이투 전차'],
    ['K9 자주포', '케이나인 자주포'], ['K-9 자주포', '케이나인 자주포'],
  ];
  for (const [input, expected] of cases) assert.strictEqual(K.normalize(input), expected, input);
});

test('군 장비명 K1A1·K2A1·K9A1은 기본 모델과 개량형을 분리한다', () => {
  const cases = [
    ['K1A1 전차', '케이원 에이원 전차'], ['K-1A1 전차', '케이원 에이원 전차'],
    ['K2A1 전차', '케이투 에이원 전차'], ['K-2A1 전차', '케이투 에이원 전차'],
    ['K9A1 자주포', '케이나인 에이원 자주포'], ['K-9A1 자주포', '케이나인 에이원 자주포'],
  ];
  for (const [input, expected] of cases) assert.strictEqual(K.normalize(input), expected, input);
});

test('신경망 전달 문자열은 chunk 경계에서 문장부호를 중복하지 않는다', () => {
  const p = K.prepare('훈련을 받아야 하며, 훈련에 참석합니다.', { emotion: 'neutral' });
  const spoken = K.joinSpokenChunks(p.sentences[0].chunks);
  assert.ok(!spoken.includes(',,'), spoken);
  assert.ok(spoken.includes('하며, 훈련'), spoken);
});

test('긴 구 자동 호흡은 주격·목적격·관형격 뒤에서 의미 단위를 끊지 않는다', () => {
  const text = '예비군 대원은 해마다 정해진 날수의 훈련을 받아야 하며, 훈련 소집 통지서를 받은 사람이 정당한 사유 없이 훈련에 참석하지 않으면 고발 대상이 될 수 있습니다. 다만 질병이나 재해처럼 불가피한 사정이 있으면 미리 연기를 신청할 수 있습니다.';
  const p = K.prepare(text, { emotion: 'neutral' });
  const spoken = p.sentences.map((s) => K.joinSpokenChunks(s.chunks)).join(' ');
  assert.ok(!spoken.includes('날수의, 훈련을'), spoken);
  assert.ok(!spoken.includes('사정이, 있으면'), spoken);
});

test('기호·영문 약어 정규화', () => {
  assert.strictEqual(K.normalize('KATUSA·ROTC'), '카투사, 알오티씨');
  assert.strictEqual(K.normalize('A/B'), '에이, 비');
  assert.strictEqual(K.normalize('TOEIC 700점, JLPT N2'), '토익 칠백 점, 제이엘피티 엔이');
  assert.strictEqual(K.normalize('자세히: https://allmymil.com/x'), '자세히: 링크');
  assert.strictEqual(K.normalize('**중요** 마감 😀'), '중요 마감');
});

test('음운 변동 — 연음·비음화·유음화·경음화·구개음화·ㅎ', () => {
  const cases = [
    ['같이', '가치'], ['굳이', '구지'], ['놓고', '노코'], ['않고', '안코'], ['많아', '마나'], ['좋아', '조아'], ['축하', '추카'], ['못해', '모태'],
    ['꽃이', '꼬치'], ['옷이', '오시'], ['값이', '갑씨'], ['앉아', '안자'], ['읽어', '일거'], ['앞에', '아페'], ['부엌에', '부어케'], ['있어요', '이써요'],
    ['국물', '궁물'], ['밥맛', '밤맏'], ['독립', '동닙'], ['협력', '혐녁'], ['종로', '종노'], ['신라', '실라'], ['칼날', '칼랄'],
    ['학교', '학꾜'], ['읽고', '일꼬'], ['읽다', '익따'], ['넓게', '널께'], ['밟다', '밥따'], ['희망', '히망'],
    ['강아지', '강아지'], ['병장', '병장'], ['입영', '이병'],
  ];
  for (const [i, e] of cases) assert.strictEqual(K.pronounce(i), e, i);
  assert.strictEqual(K.pronounce('같이 국물'), '가치 궁물', '어절마다 독립 적용');
});

test('문장 나누기·유형', () => {
  const s = K.splitSentences('접수는 마감입니다 궁금한 점 있으세요? 좋네요! 안내드려요');
  assert.deepStrictEqual(s, ['접수는 마감입니다', '궁금한 점 있으세요?', '좋네요!', '안내드려요']);
  assert.strictEqual(K.sentenceType('있으세요?'), 'question');
  assert.strictEqual(K.sentenceType('궁금하신가요'), 'question');
  assert.strictEqual(K.sentenceType('좋네요!'), 'exclaim');
  assert.strictEqual(K.sentenceType('접수하세요.'), 'request');
  assert.strictEqual(K.sentenceType('마감입니다.'), 'statement');
});

test('운율 계획 — 쉼·하강조·의문문 상승·강조', () => {
  const p = K.prepare('육군은 18개월, 해군 20개월 복무하며 병장 봉급은 150만원이에요. 반드시 기한 안에 접수하세요. 궁금한 점 있으세요?', { emotion: 'neutral' });
  assert.strictEqual(p.emotion, 'neutral');
  const [s1, s2, s3] = p.sentences;
  assert.ok(s1.chunks.length >= 3, '쉼표·연결어미에서 나뉜다');
  assert.strictEqual(s1.chunks[0].pause, K.PAUSE.comma);
  assert.strictEqual(s1.chunks[s1.chunks.length - 1].pause, K.PAUSE.ip, '문장 끝은 긴 쉼');
  assert.ok(s1.chunks[0].pitch > s1.chunks[s1.chunks.length - 1].pitch, '하강조');
  assert.ok(s1.chunks[s1.chunks.length - 1].rate < s1.chunks[0].rate, '말끝 늘림');
  assert.strictEqual(s2.chunks[0].emph, true, '반드시 = 강조');
  assert.strictEqual(s3.type, 'question');
  assert.ok(s3.chunks[s3.chunks.length - 1].pitch > 1.05, '의문문 끝은 올린다');
  assert.strictEqual(s3.chunks[s3.chunks.length - 1].pause, K.PAUSE.question);
});

test('감정 — 감지와 프리셋 반영', () => {
  assert.strictEqual(K.detectEmotion('합격을 축하드려요!'), 'joy');
  assert.strictEqual(K.detectEmotion('아쉽게 탈락했지만 괜찮아요'), 'sad');
  assert.strictEqual(K.detectEmotion('접수 절차 안내'), 'calm');
  assert.strictEqual(K.detectEmotion('병장 봉급은 백오십만 원이에요'), 'neutral');
  const sad = K.prepare('힘들죠.', { emotion: 'sad' }).sentences[0].chunks[0];
  const joy = K.prepare('힘들죠.', { emotion: 'joy' }).sentences[0].chunks[0];
  assert.ok(sad.rate < joy.rate && sad.pitch < joy.pitch && sad.volume < joy.volume, '슬픔=느리고 낮고 작게, 기쁨=빠르고 높게');
});

test('SSML — 잘 짜인 마크업과 이스케이프', () => {
  const x = K.toSSML('2명 <참고> 있어요?', { emotion: 'neutral' });
  assert.ok(x.startsWith('<speak xml:lang="ko-KR">'));
  assert.ok(x.includes('<break time="'));
  assert.ok(x.includes('두 명'));
  assert.ok(!x.includes('<참고>'), '괄호 기호는 정규화에서 제거');
  assert.ok(x.includes('<prosody rate="'));
});

test('긴 글은 Web Speech 끊김 한도(170자) 아래로 쪼갠다', () => {
  const long = Array(40).fill('아주 긴 문장이 이어집니다').join(' ') + '.';
  const p = K.prepare(long, { emotion: 'neutral' });
  for (const s of p.sentences) for (const c of s.chunks) assert.ok(c.text.length <= 200, '조각 길이 ' + c.text.length);
});

test('화자 프로필 — 쉼·하강·속도가 측정값으로 바뀌고 되돌아간다', () => {
  const fs = require('fs'); const path = require('path');
  const prof = JSON.parse(fs.readFileSync(path.join(__dirname, '../public/profiles/owner.json'), 'utf8'));
  const before = K.prepare('첫째 구는 이렇고, 둘째 구는 저렇고, 셋째 구로 끝납니다.', { emotion: 'neutral' }).sentences[0].chunks;
  const r = K.applyProfile(prof);
  assert.strictEqual(r.pause.ip, prof.engine.pause.ip);
  const after = K.prepare('첫째 구는 이렇고, 둘째 구는 저렇고, 셋째 구로 끝납니다.', { emotion: 'neutral' }).sentences[0].chunks;
  assert.strictEqual(after[after.length - 1].pause, prof.engine.pause.ip, '문장 끝 쉼 = 측정 중앙값');
  assert.strictEqual(after[0].pause, prof.engine.pause.comma);
  assert.ok(Math.abs((after[0].pitch - after[after.length - 1].pitch) - (before[0].pitch - before[before.length - 1].pitch)) > 0.005, '하강 폭이 달라진다');
  assert.ok(after[0].rate < before[0].rate, '측정 말속도(4.95음절/초)가 평균보다 느려 rate가 내려간다');
  K.applyProfile(null);
  const back = K.prepare('첫째 구는 이렇고, 둘째 구는 저렇고, 셋째 구로 끝납니다.', { emotion: 'neutral' }).sentences[0].chunks;
  assert.strictEqual(back[back.length - 1].pause, K.PAUSE.ip);
  assert.strictEqual(back[0].rate, before[0].rate);
});

test('쉼 흔들림 — 실측 로그정규로 문장 끝 쉼이 매번 달라지고, 기본값은 그대로다', () => {
  const fs = require('fs'); const path = require('path');
  const prof = JSON.parse(fs.readFileSync(path.join(__dirname, '../public/profiles/korean-corpus.json'), 'utf8'));
  const t = '첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다. 넷째 문장입니다. 다섯째 문장입니다.';
  const ends = (p) => p.sentences.map((s) => s.chunks[s.chunks.length - 1].pause);

  // 흔들림 없이(기본) 문장 끝 쉼은 전부 같다 — 이 평평함이 기계처럼 들리는 원인이었다
  K.applyProfile(null);
  const flat = ends(K.prepare(t, { emotion: 'neutral' }));
  assert.ok(new Set(flat).size === 1, '기본값은 고정: ' + flat.join(','));

  // 실측 프로필을 걸면 흔들린다
  K.applyProfile(prof);
  K.seedJitter(7);
  const varied = ends(K.prepare(t, { emotion: 'neutral' }));
  assert.ok(new Set(varied).size > 1, '실측 분포로 흔들려야 한다: ' + varied.join(','));
  for (const v of varied) assert.ok(v >= 50 && v <= 900, '쉼이 상식 범위 안: ' + v);

  // 같은 씨앗이면 같은 결과 — 캐시 키가 흔들리지 않는다
  K.seedJitter(7);
  assert.deepStrictEqual(ends(K.prepare(t, { emotion: 'neutral' })), varied, '씨앗이 같으면 재현된다');

  // 프로필을 벗기면 다시 고정으로 돌아간다
  K.applyProfile(null);
  assert.deepStrictEqual(ends(K.prepare(t, { emotion: 'neutral' })), flat);
});

test('억양 궤적 — 실측 5점 곡선이 조각마다 실리고, 프로필을 벗기면 사라진다', () => {
  const fs = require('fs'); const path = require('path');
  const prof = JSON.parse(fs.readFileSync(path.join(__dirname, '../public/profiles/korean-corpus.json'), 'utf8'));
  const t = '오늘은 예비군 훈련 준비물을 정리해 보겠습니다.';

  K.applyProfile(null);
  const plain = K.prepare(t, { emotion: 'neutral' }).sentences[0].chunks;
  assert.ok(plain.every((c) => c.pitchPoints === undefined), '프로필 없으면 궤적도 없다(옛 동작 유지)');

  K.applyProfile(prof);
  const s = K.prepare(t, { emotion: 'neutral' }).sentences[0];
  const cs = s.chunks.filter((c) => c.text);
  // 어절 표가 실리면 격자가 촘촘해진다(5점 → 16점). 점 수가 아니라 '내려가는가'를 본다.
  assert.ok(cs.every((c) => Array.isArray(c.pitchPoints) && c.pitchPoints.length >= 5), '조각마다 궤적');
  // 조각 안이 반드시 내려가야 하는 건 낭독체 얘기다. 유튜브·대화체는 거의 평평하고,
  // 조각 안 하강을 통째로 반복하면 한 조각이 1.9반음 급락한다(사용자 판정 「갑자기 호러처럼」).
  // 그래서 여기서는 조각 안이 아니라 **문장 전체가** 내려가는지만 본다.
  const first = cs[0].pitchPoints, last = cs[cs.length - 1].pitchPoints;
  assert.ok(first[0] > last[last.length - 1], '문장 전체로도 내려간다');
  // 하강 폭은 **문체가 정한다** — 낭독 -4.31 · 대화 -1.53 · 유튜브 -0.76반음(실측).
  // 프로필이 유튜브 문체를 쓰면 1~2반음이 정상이고, 3반음을 요구하면 낭독을 강요하는 테스트가 된다.
  const fallSemi = 12 * Math.log2(first[0] / last[last.length - 1]);
  assert.ok(fallSemi > 0.5, '문장 전체로는 내려간다: ' + fallSemi.toFixed(2));

  assert.strictEqual(K.endingOf('보겠습니다.'), '다');
  assert.strictEqual(K.endingOf('가능할까요?'), '까');
  assert.strictEqual(K.endingOf('돼요.'), '요');
  K.applyProfile(null);
});

test('어절 층 — 첫 자음과 어미가 어절 높이를 가른다(실측 5.2만 어절)', () => {
  const fs = require('fs'); const path = require('path');
  const prof = JSON.parse(fs.readFileSync(path.join(__dirname, '../public/profiles/korean-corpus.json'), 'utf8'));

  assert.strictEqual(K.onsetOf('카투사'), 'H_격음');
  assert.strictEqual(K.onsetOf('나라'), 'L_비음유음');
  assert.strictEqual(K.onsetOf('사람'), 'H_ㅅ');
  assert.strictEqual(K.onsetOf('부대'), 'L_평음');
  assert.strictEqual(K.tailOf('합니다'), '어미_다');   // 서버 분석과 같은 순서로 판정해야 표가 맞는다
  assert.strictEqual(K.tailOf('지원은'), '조사_주격');
  assert.strictEqual(K.tailOf('나라를'), '조사_목적');

  K.applyProfile(prof);
  assert.ok(K.getWord(), '프로필의 어절 표가 실린다');
  // 같은 위치·같은 음절수인데 첫 자음만 다르면 높이가 달라야 한다
  const g = K.wordGrid('카투사 나라를');
  assert.ok(g[0] > g[g.length - 1], '격음으로 시작한 어절이 비음·유음 어절보다 높다: ' + g[0] + ' vs ' + g[g.length - 1]);
  // 경계에서 순간 도약하면 「갑자기 뚝」 하고 들린다 — 평활 뒤에는 인접 점 도약이 작아야 한다
  let jump = 0;
  for (let i = 1; i < g.length; i++) jump = Math.max(jump, Math.abs(g[i] - g[i - 1]));
  assert.ok(jump < 0.5, '어절 경계가 미끄럽다(계단 아님): 최대 도약 ' + jump.toFixed(3));

  const withWord = K.prepare('카투사 지원은 신중하게 결정해야 합니다.', {}).sentences[0].chunks[0].pitchPoints;
  const p2 = JSON.parse(JSON.stringify(prof)); delete p2.engine.word;
  K.applyProfile(p2);
  const noWord = K.prepare('카투사 지원은 신중하게 결정해야 합니다.', {}).sentences[0].chunks[0].pitchPoints;
  assert.strictEqual(noWord.length, 5, '어절 표를 빼면 옛 5점으로 돌아간다(호환)');
  assert.ok(withWord.length >= 16 && withWord.length % 8 === 0,
    '격자는 어절 수에 비례한다(16점 고정은 긴 조각에서 앨리어싱): ' + withWord.length);
  K.applyProfile(null);
  assert.strictEqual(K.getWord(), null, '프로필을 벗기면 어절 표도 사라진다');
});

test('어절 간 도약 상한 — 사람 성대가 못 하는 점프를 막는다', () => {
  const fs = require('fs'); const path = require('path');
  const prof = JSON.parse(fs.readFileSync(path.join(__dirname, '../public/profiles/korean-corpus.json'), 'utf8'));
  K.applyProfile(prof);
  // 「때에도」(경음+부사 +0.82) 다음에 「정해진」(평음+무표지 -0.26) 이 오면 1.08반음이 쉼을 건너 떨어졌다.
  const g = K.wordGrid('때에도 정해진 날수만큼');
  let jump = 0;
  for (let i = 1; i < g.length; i++) jump = Math.max(jump, Math.abs(g[i] - g[i - 1]));
  const span = Math.max(...g) - Math.min(...g);
  assert.ok(span <= (prof.engine.word.maxStep || 0.7) * 3 + 0.01, '어절 간 총 변화가 상한 안에 든다: ' + span.toFixed(3));
  assert.ok(jump < 0.4, '격자 위에서도 미끄럽다: ' + jump.toFixed(3));
  K.applyProfile(null);
});
