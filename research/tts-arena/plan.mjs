#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '../..');
const KoVoice = require(resolve(root, 'public/ko-voice.js'));
const providersDoc = JSON.parse(readFileSync(resolve(here, 'providers.json'), 'utf8'));
const casesDoc = JSON.parse(readFileSync(resolve(here, 'cases.json'), 'utf8'));

const args = process.argv.slice(2);
const value = (name) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : null;
};
const has = (name) => args.includes(name);
const providerFilter = value('--provider');
const domainFilter = value('--domain');
const gateOnly = has('--gate-only');
const allowNetwork = process.env.ARENA_ALLOW_NETWORK === '1';
const allowPaid = process.env.ARENA_ALLOW_PAID === '1';

if (allowPaid) {
  throw new Error('ARENA_ALLOW_PAID=1 is intentionally unsupported. This arena never authorizes paid usage.');
}

let providers = providersDoc.providers.slice();
if (providerFilter) providers = providers.filter((p) => p.id === providerFilter);
if (!providers.length) throw new Error('No provider matched selection.');

let cases = casesDoc.cases.slice();
if (domainFilter) cases = cases.filter((c) => c.domain === domainFilter);
if (gateOnly) cases = cases.filter((c) => c.gate);
if (!cases.length) throw new Error('No case matched selection.');

const variants = [];
for (const c of cases) {
  const canonical = KoVoice.normalize(c.source);
  variants.push({ case_id: c.id, domain: c.domain, gate: c.gate, variant: 'raw', text: c.source, target: c.target });
  variants.push({ case_id: c.id, domain: c.domain, gate: c.gate, variant: 'ko-voice', text: canonical, target: c.target });
}

const charCount = variants.reduce((n, x) => n + x.text.length, 0);
const gateCount = cases.filter((c) => c.gate).length;
const researchCount = cases.length - gateCount;
const providerPlans = providers.map((p) => {
  const network = p.kind === 'api' || p.kind === 'api-or-web';
  const runnable = network ? allowNetwork : p.default_enabled === true;
  let blocked_reason = null;
  if (network && !allowNetwork) blocked_reason = 'network-default-deny';
  if (network && !p.credential_env) blocked_reason = blocked_reason || 'no-credential-contract';
  if (network && p.hard_char_budget != null && charCount > p.hard_char_budget) blocked_reason = blocked_reason || 'hard-char-budget-exceeded';
  return {
    id: p.id,
    kind: p.kind,
    tier: p.tier,
    korean: p.korean,
    runnable: runnable && !blocked_reason,
    blocked_reason,
    hard_char_budget: p.hard_char_budget ?? null,
    credential_env: p.credential_env ?? null
  };
});

const output = {
  policy: providersDoc.policy,
  selected: { providers: providerPlans.length, cases: cases.length, gate_cases: gateCount, research_cases: researchCount, variants: variants.length, characters: charCount },
  providers: providerPlans,
  jobs: variants
};

if (has('--json')) {
  process.stdout.write(JSON.stringify(output, null, 2) + '\n');
} else {
  console.log('Korean TTS Arena — zero-cost battle plan');
  console.log(`cases=${cases.length} gate=${gateCount} research=${researchCount} variants=${variants.length} chars=${charCount}`);
  for (const p of providerPlans) console.log(`${p.runnable ? 'READY' : 'HOLD '} ${p.id} ${p.blocked_reason || ''}`.trim());
  console.log('Network calls are denied unless ARENA_ALLOW_NETWORK=1; paid authorization is not implemented.');
}
