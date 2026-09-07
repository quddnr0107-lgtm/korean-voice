// GitHub Actions OIDC 토큰 검증 — 비밀값 없이 「이 저장소의 main 워크플로가 보낸 것」을 믿는다 (2026-09-04).
//   러너가 ACTIONS_ID_TOKEN_REQUEST_URL 로 받은 JWT(RS256 · GitHub 이 서명)를 Bearer 로 보낸다.
//   워커는 GitHub 의 JWKS(https://token.actions.githubusercontent.com/.well-known/jwks)로 서명을 확인하고
//   iss · aud · exp · repository · ref 를 본다. 시크릿을 어디에도 심지 않는다 — 공개 저장소의 무료 러너가 R2 에 올릴 수 있다.
export const GITHUB_ISS = 'https://token.actions.githubusercontent.com';
export const GITHUB_JWKS = GITHUB_ISS + '/.well-known/jwks';

const b64url = (s) => { s = s.replace(/-/g, '+').replace(/_/g, '/'); while (s.length % 4) s += '='; return Uint8Array.from(atob(s), (c) => c.charCodeAt(0)); };
const dec = (u8) => new TextDecoder().decode(u8);

let jwksCache = { at: 0, keys: null };
export async function fetchJwks(fetchFn = fetch, now = Date.now) {
  if (jwksCache.keys && now() - jwksCache.at < 6 * 3600 * 1000) return jwksCache.keys;
  const r = await fetchFn(GITHUB_JWKS);
  if (!r.ok) throw new Error('jwks ' + r.status);
  const j = await r.json();
  jwksCache = { at: now(), keys: j.keys || [] };
  return jwksCache.keys;
}
export function _resetJwks() { jwksCache = { at: 0, keys: null }; }

/* 검증 — 통과하면 claims 를, 아니면 { error } 를 돌려준다. allow = { aud, repository, ref } */
export async function verifyGithubOidc(token, allow, { fetchFn = fetch, now = Date.now, keys = null } = {}) {
  const parts = String(token || '').split('.');
  if (parts.length !== 3) return { error: 'malformed' };
  let header, claims;
  try { header = JSON.parse(dec(b64url(parts[0]))); claims = JSON.parse(dec(b64url(parts[1]))); } catch (_) { return { error: 'malformed' }; }
  if (header.alg !== 'RS256' || !header.kid) return { error: 'alg' };
  const jwks = keys || await fetchJwks(fetchFn, now);
  const jwk = jwks.find((k) => k.kid === header.kid);
  if (!jwk) return { error: 'kid' };
  const key = await crypto.subtle.importKey('jwk', { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: 'RS256', ext: true }, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
  const ok = await crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, b64url(parts[2]), new TextEncoder().encode(parts[0] + '.' + parts[1]));
  if (!ok) return { error: 'signature' };
  const t = Math.floor(now() / 1000);
  if (claims.iss !== GITHUB_ISS) return { error: 'iss' };
  const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!aud.includes(allow.aud)) return { error: 'aud' };
  if (!(claims.exp > t - 60)) return { error: 'exp' };
  if (claims.nbf && claims.nbf > t + 60) return { error: 'nbf' };
  if (claims.repository !== allow.repository) return { error: 'repository' };
  if (allow.ref && claims.ref !== allow.ref) return { error: 'ref' };
  return { claims };
}
