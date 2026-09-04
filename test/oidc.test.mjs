// GitHub OIDC 검증(lib/oidc.mjs) — 우리가 만든 RSA 키로 서명한 JWT 와 가짜 JWKS 로 흐름을 잰다.
import test from 'node:test';
import assert from 'node:assert';
import { verifyGithubOidc, GITHUB_ISS, _resetJwks } from '../lib/oidc.mjs';

const b64url = (buf) => Buffer.from(buf).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
async function makeSigner() {
  const kp = await crypto.subtle.generateKey({ name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' }, true, ['sign', 'verify']);
  const jwk = await crypto.subtle.exportKey('jwk', kp.publicKey);
  const keys = [{ kty: 'RSA', kid: 'k1', n: jwk.n, e: jwk.e, alg: 'RS256', use: 'sig' }];
  const sign = async (claims, kid = 'k1') => {
    const h = b64url(JSON.stringify({ alg: 'RS256', kid, typ: 'JWT' })); const p = b64url(JSON.stringify(claims));
    const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', kp.privateKey, new TextEncoder().encode(h + '.' + p));
    return h + '.' + p + '.' + b64url(sig);
  };
  return { keys, sign };
}
const NOW = 1_800_000_000_000;
const allow = { aud: 'korean-voice-bake', repository: 'quddnr0107-lgtm/korean-voice-bake', ref: 'refs/heads/main' };
const good = () => ({ iss: GITHUB_ISS, aud: 'korean-voice-bake', exp: NOW / 1000 + 300, nbf: NOW / 1000 - 10, repository: 'quddnr0107-lgtm/korean-voice-bake', ref: 'refs/heads/main', job_workflow_ref: 'x' });

test('[양성] 제대로 서명된 우리 저장소 main 토큰은 통과한다', async () => {
  const { keys, sign } = await makeSigner();
  const r = await verifyGithubOidc(await sign(good()), allow, { keys, now: () => NOW });
  assert.ok(r.claims && r.claims.repository === allow.repository, JSON.stringify(r));
});

test('[음성] 다른 저장소 · 다른 브랜치 · 만료 · 다른 aud · 다른 키로 서명 · 변조 — 전부 막는다', async () => {
  const { keys, sign } = await makeSigner();
  const other = await makeSigner();
  const cases = [
    ['repository', await sign({ ...good(), repository: 'someone/else' })],
    ['ref', await sign({ ...good(), ref: 'refs/heads/feature' })],
    ['exp', await sign({ ...good(), exp: NOW / 1000 - 3600 })],
    ['aud', await sign({ ...good(), aud: 'other' })],
    ['iss', await sign({ ...good(), iss: 'https://evil' })],
    ['signature', await other.sign(good())],   // 남의 키로 서명(kid 는 같다)
  ];
  for (const [want, tok] of cases) {
    const r = await verifyGithubOidc(tok, allow, { keys, now: () => NOW });
    assert.strictEqual(r.error, want, `${want} 를 막아야 한다 → ${JSON.stringify(r)}`);
  }
  const t = await sign(good()); const parts = t.split('.'); const tampered = parts[0] + '.' + b64url(JSON.stringify({ ...good(), repository: 'someone/else' })) + '.' + parts[2];
  assert.strictEqual((await verifyGithubOidc(tampered, allow, { keys, now: () => NOW })).error, 'signature');
  assert.strictEqual((await verifyGithubOidc('abc', allow, { keys })).error, 'malformed');
});

test('JWKS 를 받아 6시간 캐시한다', async () => {
  _resetJwks();
  const { keys, sign } = await makeSigner();
  let n = 0; const fetchFn = async () => { n++; return { ok: true, json: async () => ({ keys }) }; };
  const tok = await sign(good());
  assert.ok((await verifyGithubOidc(tok, allow, { fetchFn, now: () => NOW })).claims);
  assert.ok((await verifyGithubOidc(tok, allow, { fetchFn, now: () => NOW + 1000 })).claims);
  assert.strictEqual(n, 1);
  _resetJwks();
});
