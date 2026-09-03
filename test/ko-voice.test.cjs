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
    ['10:30', '열 시 삼십 분'], ['24시', '이십사 시'], ['18~21개월', '십팔 개월에서 이십일 개월'],
    ['12.5%', '십이 점 오 퍼센트'], ['0.5배', '영 점 오 배'], ['5km', '오 킬로미터'], ['-3도', '영하 삼 도'],
    ['제6회', '제육 회'], ['3번째', '세 번째'], ['경쟁률 2.5:1', '경쟁률 이 점 오 대 일'], ['육군 18, 해군 20, 공군 21.', '육군 십팔, 해군 이십, 공군 이십일.'], ['1,234명', '천이백삼십사 명'], ['09:00~18:00', '아홉 시 영 분에서 십팔 시 영 분'], ['만 19세', '만 십구 세'], ['010-1234-5678', '공일공 일이삼사 오육칠팔'],
  ];
  for (const [i, e] of cases) assert.strictEqual(K.normalize(i), e, i);
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
