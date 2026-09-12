'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const cp = require('node:child_process');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const providers = JSON.parse(fs.readFileSync(path.join(ROOT, 'research/tts-arena/providers.json'), 'utf8'));
const cases = JSON.parse(fs.readFileSync(path.join(ROOT, 'research/tts-arena/cases.json'), 'utf8'));
const mossK2 = JSON.parse(fs.readFileSync(path.join(ROOT, 'research/tts-arena/results/2026-09-12-moss-nano-k2-smoke.json'), 'utf8'));

test('TTS arena는 유료·네트워크 호출을 기본 허용하지 않는다', () => {
  assert.equal(providers.policy.network_default, 'deny');
  assert.equal(providers.policy.paid_default, 'deny');
  assert.equal(providers.policy.audio_git_commit, 'deny');
  for (const p of providers.providers) {
    if (p.kind === 'api' || p.kind === 'api-or-web') assert.equal(p.default_enabled, false, p.id);
  }
});

test('TTS arena provider/case id는 중복되지 않는다', () => {
  const pids = providers.providers.map((p) => p.id);
  const cids = cases.cases.map((c) => c.id);
  assert.equal(new Set(pids).size, pids.length);
  assert.equal(new Set(cids).size, cids.length);
});

test('gate 문장은 모두 독립 목표 발음을 가진다', () => {
  const gates = cases.cases.filter((c) => c.gate);
  assert.ok(gates.length >= 40, `gate cases=${gates.length}`);
  for (const c of gates) {
    assert.equal(typeof c.target, 'string', c.id);
    assert.ok(c.target.trim().length > 0, c.id);
  }
});

test('공식 근거가 확보된 KF-21·FA-50은 research가 아니라 gate다', () => {
  const kf = cases.cases.find((c) => c.id === 'mil-kf21');
  const fa = cases.cases.find((c) => c.id === 'mil-fa50');
  assert.ok(kf && kf.gate);
  assert.ok(fa && fa.gate);
  assert.match(kf.target, /케이에프 이십일/);
  assert.match(fa.target, /에프에이 오십/);
  assert.equal(cases.cases.some((c) => c.id === 'research-kf21'), false);
  assert.equal(cases.cases.some((c) => c.id === 'research-fa50'), false);
});

test('research 문장은 미확정 발음을 gate로 오인하지 않는다', () => {
  const research = cases.cases.filter((c) => !c.gate);
  assert.ok(research.length > 0);
  for (const c of research) {
    assert.equal(c.target, null, c.id);
    assert.ok(c.research_question, c.id);
  }
});

test('MOSS Nano는 공식 한국어 지원과 CPU 실측을 분리 기록한다', () => {
  const p = providers.providers.find((x) => x.id === 'moss-tts-nano');
  assert.ok(p);
  assert.equal(p.korean, 'official-and-verified');
  assert.match(p.repo, /OpenMOSS\/MOSS-TTS-Nano/);
  assert.match(p.notes, /Verified 2026-09-12/);
});

test('MOSS Nano K2 smoke 증거는 무비용 canonical 승리를 보존한다', () => {
  assert.equal(mossK2.execution.cloudflare_r2_calls, 0);
  assert.equal(mossK2.execution.cloudflare_do_calls, 0);
  assert.equal(mossK2.execution.cloudflare_container_calls, 0);
  assert.equal(mossK2.execution.paid_tts_api_calls, 0);
  assert.equal(mossK2.raw.hit_max_new_frames, true);
  assert.equal(mossK2.raw.literal_cer, 1.0);
  assert.equal(mossK2.ko_voice.hit_max_new_frames, false);
  assert.equal(mossK2.ko_voice.canonical_cer, 0.0);
  assert.ok(mossK2.derived.synthesis_speedup_raw_over_canonical > 8);
  assert.ok(mossK2.raw.audio_seconds > mossK2.ko_voice.audio_seconds * 10);
});

test('arena plan은 raw와 ko-voice를 같은 수로 만든다', () => {
  const out = cp.execFileSync(process.execPath, ['research/tts-arena/plan.mjs', '--gate-only', '--json'], { cwd: ROOT, encoding: 'utf8' });
  const plan = JSON.parse(out);
  assert.equal(plan.selected.research_cases, 0);
  assert.equal(plan.selected.variants, plan.selected.cases * 2);
  const raw = plan.jobs.filter((x) => x.variant === 'raw');
  const canonical = plan.jobs.filter((x) => x.variant === 'ko-voice');
  assert.equal(raw.length, canonical.length);
  assert.ok(plan.providers.some((p) => p.id === 'ko-voice-supertonic-current' && p.runnable));
  assert.ok(plan.providers.filter((p) => p.kind.includes('api')).every((p) => !p.runnable));
});

test('ARENA_ALLOW_PAID는 설계상 거부된다', () => {
  const r = cp.spawnSync(process.execPath, ['research/tts-arena/plan.mjs', '--json'], {
    cwd: ROOT,
    encoding: 'utf8',
    env: { ...process.env, ARENA_ALLOW_PAID: '1' }
  });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /intentionally unsupported/);
});
