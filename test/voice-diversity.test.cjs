'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const voices = JSON.parse(fs.readFileSync(path.join(ROOT, 'research/tts-arena/voices.json'), 'utf8'));
const roles = JSON.parse(fs.readFileSync(path.join(ROOT, 'research/tts-arena/roles.json'), 'utf8'));
const allSpeakers = JSON.parse(fs.readFileSync(path.join(ROOT, 'research/tts-arena/results/qwen3-tts-06b-all-speakers-korean-screen-20260912.json'), 'utf8'));
const voiceDesign = JSON.parse(fs.readFileSync(path.join(ROOT, 'research/tts-arena/results/qwen3-tts-17b-voicedesign-korean-roles-20260912.json'), 'utf8'));
const sohee17 = JSON.parse(fs.readFileSync(path.join(ROOT, 'research/tts-arena/results/qwen3-tts-17b-sohee-pronunciation-20260912.json'), 'utf8'));

test('voice catalog keeps cloning and real-person imitation denied', () => {
  assert.equal(voices.policy.real_person_imitation, 'deny');
  assert.equal(voices.policy.voice_cloning_without_consent, 'deny');
  assert.equal(voices.policy.synthetic_voice_first, true);
});

test('Qwen fixed voice pool includes all nine official presets and both genders', () => {
  const q = voices.voices.filter((v) => v.model === 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice');
  const names = q.map((v) => v.speaker).sort();
  assert.deepEqual(names, ['Aiden','Dylan','Eric','Ono_Anna','Ryan','Serena','Sohee','Uncle_Fu','Vivian'].sort());
  assert.ok(q.some((v) => v.gender === 'female'));
  assert.ok(q.some((v) => v.gender === 'male'));
  assert.equal(q.find((v) => v.speaker === 'Sohee')?.native_language, 'Korean');
});

test('measured nine-speaker Korean screen keeps female and male leaders explicit', () => {
  assert.equal(allSpeakers.ranking.length, 9);
  assert.equal(allSpeakers.ranking[0].speaker, 'Sohee');
  assert.equal(allSpeakers.ranking[0].mean_semantic_cer, 0);
  const males = allSpeakers.ranking.filter((v) => v.gender === 'male');
  assert.equal(males[0].speaker, 'Dylan');
  assert.equal(males[0].mean_semantic_cer, 0.0454);
  assert.ok(allSpeakers.ranking.find((v) => v.speaker === 'Aiden').mean_semantic_cer > 0.2);
  assert.equal(allSpeakers.execution.paid_tts_api_calls, 0);
  assert.equal(allSpeakers.execution.cloudflare_container_calls, 0);
});

test('voice design and larger Sohee tracks are explicit Korean research lanes', () => {
  const design = voices.voices.find((v) => v.id === 'qwen17-voice-design');
  const q17 = voices.voices.find((v) => v.id === 'qwen17-sohee');
  assert.equal(design?.gender, 'designable');
  assert.equal(design?.occupation_flexibility, 'high');
  assert.match(design?.korean_status || '', /verified/);
  assert.equal(q17?.speaker, 'Sohee');
  assert.match(q17?.research_reason || '', /lower Korean WER/);
  assert.equal(q17?.universal_upgrade, false);
});

test('VoiceDesign CPU pilot proves occupation and gender voice generation without hiding a miss', () => {
  assert.equal(voiceDesign.summary.cpu_feasible, true);
  assert.equal(voiceDesign.summary.role_count, 3);
  assert.equal(voiceDesign.summary.perfect_semantic_cer, 2);
  assert.equal(voiceDesign.results.find((r) => r.role === 'female-news-anchor').semantic_cer, 0);
  assert.equal(voiceDesign.results.find((r) => r.role === 'female-teacher').semantic_cer, 0);
  assert.equal(voiceDesign.results.find((r) => r.role === 'male-military-law-instructor').semantic_cer, 0.0645);
  assert.equal(voiceDesign.execution.paid_tts_api_calls, 0);
  assert.equal(voiceDesign.execution.audio_git_commit, 0);
});

test('1.7B Sohee evidence remains mixed instead of being promoted as a universal upgrade', () => {
  assert.equal(sohee17.decision.universal_upgrade, false);
  assert.equal(sohee17.decision.legal_pronunciation_challenger, true);
  assert.ok(sohee17.mean_semantic_cer < sohee17.comparison_to_06b_same_three_cases.qwen06_mean_semantic_cer);
  assert.equal(sohee17.results.find((r) => r.id === 'legal-chain').semantic_cer, 0);
  assert.ok(sohee17.results.find((r) => r.id === 'mil-k2').semantic_cer > sohee17.results.find((r) => r.id === 'mil-k2').qwen06_semantic_cer);
  assert.equal(sohee17.execution.paid_tts_api_calls, 0);
});

test('occupation catalog has broad roles and male/female variants without role-gender locking', () => {
  assert.ok(roles.roles.length >= 9, `roles=${roles.roles.length}`);
  assert.deepEqual(roles.policy.gender_variants_required, ['female', 'male']);
  assert.equal(roles.policy.occupation_is_style_not_identity, true);
  const required = ['military-instructor','legal-instructor','news-anchor','teacher','civil-service-guide','counselor','navigation-system','study-narrator','emergency-announcement'];
  for (const id of required) {
    const role = roles.roles.find((r) => r.id === id);
    assert.ok(role, id);
    assert.ok(role.female_prompt?.includes('여성'), `${id}: female`);
    assert.ok(role.male_prompt?.includes('남성'), `${id}: male`);
    const sum = Object.values(role.weights).reduce((a, n) => a + n, 0);
    assert.equal(sum, 100, `${id}: weights=${sum}`);
  }
});

test('every role promotion requires accuracy, blind listening, and fatigue checks', () => {
  assert.deepEqual(roles.policy.promotion_requires, ['pronunciation_gate', 'blind_listening', 'fatigue_check']);
  for (const role of roles.roles) {
    assert.ok(role.goal.length > 0, role.id);
    assert.ok(role.weights.pronunciation >= 20, role.id);
  }
});
