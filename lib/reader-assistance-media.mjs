// F1 clips are immutable byte ranges of public, content-addressed release packs.
// This route never synthesizes audio and never accesses R2 or a Container.
import reviewedSubsetPolicy from '../reader-assistance/reviewed-subset-policy.json' with { type: 'json' };
const PREFIX = '/reader-assistance/f1/v1/';
const HEX = /^[a-f0-9]{64}$/;
const SUBSET_SCHEMA = 'reader-assistance-reviewed-subset-audio.v1';
const SEALED_RECIPE_SHA256 = '325696fa16298826933979aa72f34cebf1aca3c1e473bb5035cd10265097f0b8';
const PACK_ROOT = 'https://github.com/quddnr0107-lgtm/korean-voice/releases/download/reader-assistance-f1-v1-packs/';
const MAX_PACK = 24 * 1024 * 1024;
const MAX_CLIP = 5 * 1024 * 1024;
const indices = new WeakMap();
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Allow-Headers': 'Range, If-Range, If-None-Match',
  'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, ETag, X-Reader-Assistance-Voice',
};
const positive = value => Number.isSafeInteger(value) && value > 0;
const plain = value => value && typeof value === 'object' && !Array.isArray(value);
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const hash = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
const canonical = value => Array.isArray(value) ? '[' + value.map(canonical).join(',') + ']' : plain(value)
  ? '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}' : JSON.stringify(value);

function reviewedSubset(manifest) {
  const policy = reviewedSubsetPolicy;
  return manifest.schema === SUBSET_SCHEMA && manifest.complete === false && manifest.reviewedScopeReady === true &&
    policy.schema === 'reader-assistance-reviewed-subset-policy.v1' && policy.kind === 'reviewed-subset' &&
    policy.bodyRows === policy.approvedEntries + 1 && policy.excluded?.length === 1 &&
    HEX.test(policy.sourceCorpusSha256 || '') && HEX.test(policy.sourceRowsSha256 || '') &&
    manifest.expectedEntries === policy.approvedEntries && manifest.recipeSha256 === SEALED_RECIPE_SHA256 &&
    canonical(manifest.releaseScope) === canonical(policy);
}

export function validateAssistanceManifest(manifest) {
  const subset = plain(manifest) && reviewedSubset(manifest);
  const complete = plain(manifest) && manifest.schema === 'reader-assistance-audio-pack.v1' && manifest.complete === true;
  if (!plain(manifest) || (!complete && !subset) ||
      manifest.voice !== 'f1' || manifest.recipe !== 'reader-assistance-f1-v1' ||
      !HEX.test(manifest.sourceDataSha256 || '') || !HEX.test(manifest.recipeSha256 || '') ||
      !positive(manifest.expectedEntries) || manifest.readyEntries !== manifest.expectedEntries ||
      !Array.isArray(manifest.entries) || manifest.entries.length !== manifest.expectedEntries ||
      !plain(manifest.packs) || !plain(manifest.clips)) throw new Error('incomplete_audio_manifest');
  for (const [key, pack] of Object.entries(manifest.packs)) {
    if (!HEX.test(key) || !plain(pack) || pack.sha256 !== key || !positive(pack.size) || pack.size > MAX_PACK ||
        pack.url !== PACK_ROOT + key + '.bin') throw new Error('invalid_audio_pack');
  }
  for (const [key, clip] of Object.entries(manifest.clips)) {
    const pack = plain(clip) && own(manifest.packs, clip.pack) ? manifest.packs[clip.pack] : null;
    if (!HEX.test(key) || !pack || !HEX.test(clip.sha256 || '') || !HEX.test(clip.textSha256 || '') ||
        !Number.isSafeInteger(clip.offset) || clip.offset < 0 || !positive(clip.length) || clip.length > MAX_CLIP ||
        !Number.isSafeInteger(clip.offset + clip.length) || clip.offset + clip.length > pack.size ||
        !positive(clip.durationMs) || clip.durationMs > 600000) throw new Error('invalid_audio_clip');
  }
  const ids = new Set(), referenced = new Set(), sourceIndices = new Set();
  const excluded = subset && reviewedSubsetPolicy.excluded[0];
  for (const entry of manifest.entries) {
    if (!plain(entry) || typeof entry.id !== 'string' || !entry.id || ids.has(entry.id) ||
        !own(manifest.clips, entry.audioKey)) throw new Error('invalid_audio_entry');
    if (subset && (!HEX.test(entry.id) || entry.id === excluded.id ||
        !Number.isSafeInteger(entry.sourceIndex) || entry.sourceIndex < 0 || entry.sourceIndex === excluded.sourceIndex ||
        sourceIndices.has(entry.sourceIndex) || !HEX.test(entry.originalSha256 || ''))) throw new Error('invalid_subset_source_entry');
    ids.add(entry.id); referenced.add(entry.audioKey);
    if (subset) sourceIndices.add(entry.sourceIndex);
  }
  if (referenced.size !== Object.keys(manifest.clips).length) throw new Error('unreferenced_audio_clip');
  return manifest;
}

export async function validateReviewedSubsetLedger(manifest) {
  if (manifest.schema !== SUBSET_SCHEMA) return manifest;
  if (!reviewedSubset(manifest)) throw new Error('unapproved_subset_scope');
  const excluded = reviewedSubsetPolicy.excluded[0];
  const rows = manifest.entries.map(entry => [entry.sourceIndex, entry.id, entry.originalSha256]);
  rows.push([excluded.sourceIndex, excluded.id, excluded.originalSha256]);
  rows.sort((a, b) => a[0] - b[0]);
  if (rows.length !== reviewedSubsetPolicy.bodyRows ||
      await hash(new TextEncoder().encode(JSON.stringify(rows))) !== reviewedSubsetPolicy.sourceRowsSha256)
    throw new Error('subset_source_ledger_mismatch');
  return manifest;
}

async function checked(manifest) {
  if (!plain(manifest)) throw new Error('missing_audio_manifest');
  if (!indices.has(manifest)) indices.set(manifest, validateReviewedSubsetLedger(validateAssistanceManifest(manifest)));
  return indices.get(manifest);
}

function fail(status, error, extra = {}) {
  return new Response(JSON.stringify({ok:false, error}), {status, headers:{...CORS, 'Content-Type':'application/json; charset=utf-8', 'Cache-Control':'no-store', ...extra}});
}

export function clientByteRange(header, size) {
  if (!header) return null;
  const match = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!match || (!match[1] && !match[2])) throw new Error('invalid_range');
  let start, end;
  if (!match[1]) {
    const suffix = Number(match[2]);
    if (!positive(suffix)) throw new Error('invalid_range');
    start = Math.max(0, size - suffix); end = size - 1;
  } else {
    start = Number(match[1]); end = match[2] ? Number(match[2]) : size - 1;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start >= size || end < start) throw new Error('invalid_range');
    end = Math.min(end, size - 1);
  }
  return {start, end};
}

async function readExactly(response, expected) {
  const supplied = response.headers.get('Content-Length');
  if (supplied !== null && supplied !== String(expected)) throw new Error('upstream_length_mismatch');
  if (!response.body) throw new Error('upstream_empty_body');
  const reader = response.body.getReader(), chunks = [];
  let total = 0;
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > expected) throw new Error('upstream_body_too_large');
      chunks.push(value);
    }
    if (total !== expected) throw new Error('upstream_body_truncated');
  } catch (error) { await reader.cancel().catch(() => {}); throw error; }
  const result = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result;
}

async function fetchClip(pack, clip, fetchImpl) {
  let target = pack.url;
  const range = `bytes=${clip.offset}-${clip.offset + clip.length - 1}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    for (let redirects = 0; redirects <= 2; redirects++) {
      const response = await fetchImpl(target, {method:'GET', redirect:'manual', signal:controller.signal,
        headers:{Range:range, Accept:'application/octet-stream', 'Accept-Encoding':'identity'}});
      if ([301, 302, 303, 307, 308].includes(response.status)) {
        const location = response.headers.get('Location');
        await response.body?.cancel().catch(() => {});
        if (!location || redirects === 2) throw new Error('upstream_redirect');
        const next = new URL(location, target);
        if (next.protocol !== 'https:' || next.username || next.password || next.hash ||
            next.hostname !== 'release-assets.githubusercontent.com' ||
            !next.pathname.startsWith('/github-production-release-asset/')) throw new Error('upstream_redirect_host');
        target = next.href; continue;
      }
      const expected = `bytes ${clip.offset}-${clip.offset + clip.length - 1}/${pack.size}`;
      if (response.status !== 206 || response.headers.get('Content-Range') !== expected ||
          ![null, 'identity'].includes(response.headers.get('Content-Encoding'))) {
        await response.body?.cancel().catch(() => {});
        throw new Error('upstream_range_mismatch');
      }
      const bytes = await readExactly(response, clip.length);
      if (await hash(bytes) !== clip.sha256) throw new Error('upstream_audio_hash_mismatch');
      return bytes;
    }
    throw new Error('upstream_redirect_limit');
  } finally { clearTimeout(timer); }
}

/** All optional dependencies are injectable for adversarial tests; production uses native fetch and Cache API. */
export async function handleReaderAssistanceMedia(request, ctx, manifest, dependencies = {}) {
  const url = new URL(request.url);
  const match = /^\/reader-assistance\/f1\/v1\/([a-f0-9]{64})\.mp3$/.exec(url.pathname);
  if (!match || url.search) return fail(404, 'unknown_assistance_audio');
  if (request.method === 'OPTIONS') return new Response(null, {status:204, headers:{...CORS, 'Access-Control-Max-Age':'86400'}});
  if (!['GET', 'HEAD'].includes(request.method)) return fail(405, 'method', {Allow:'GET, HEAD, OPTIONS'});
  let data;
  try { data = await checked(manifest); } catch (_) { return fail(503, 'assistance_audio_not_ready'); }
  const key = match[1];
  if (!own(data.clips, key)) return fail(404, 'unknown_assistance_audio');
  const clip = data.clips[key], pack = data.packs[clip.pack];
  const etag = '"' + clip.sha256 + '"';
  const headers = {...CORS, 'Content-Type':'audio/mpeg', 'Accept-Ranges':'bytes',
    'Cache-Control':'public, max-age=31536000, immutable', ETag:etag,
    'X-Content-Type-Options':'nosniff', 'X-Reader-Assistance-Voice':'f1',
    'X-Reader-Assistance-SHA256':clip.sha256};
  const condition = request.headers.get('If-None-Match');
  if (condition && condition.split(',').map(value => value.trim()).some(value => value === etag || value === 'W/' + etag || value === '*'))
    return new Response(null, {status:304, headers});
  let range;
  try {
    const ifRange = request.headers.get('If-Range');
    range = clientByteRange(!ifRange || ifRange === etag ? request.headers.get('Range') : null, clip.length);
  } catch (_) { return fail(416, 'invalid_range', {'Content-Range':`bytes */${clip.length}`}); }
  const status = range ? 206 : 200;
  const length = range ? range.end - range.start + 1 : clip.length;
  const resultHeaders = {...headers, 'Content-Length':String(length), ...(range ? {'Content-Range':`bytes ${range.start}-${range.end}/${clip.length}`} : {})};
  if (request.method === 'HEAD') return new Response(null, {status, headers:resultHeaders});
  const fetchImpl = dependencies.fetchImpl || fetch;
  const cache = own(dependencies, 'cache') ? dependencies.cache : globalThis.caches?.default;
  const cacheKey = new Request(url.origin + PREFIX + key + '.mp3');
  try {
    let bytes;
    try {
      const hit = cache && await cache.match(cacheKey);
      if (hit && hit.status === 200 && hit.headers.get('X-Reader-Assistance-SHA256') === clip.sha256 &&
          hit.headers.get('Content-Length') === String(clip.length)) bytes = await readExactly(hit, clip.length);
    } catch (_) { bytes = null; }
    if (!bytes) {
      bytes = await fetchClip(pack, clip, fetchImpl);
      if (cache) {
        try {
          const store = Promise.resolve(cache.put(cacheKey, new Response(bytes, {headers:{...headers, 'Content-Length':String(clip.length)}}))).catch(() => {});
          if (ctx && typeof ctx.waitUntil === 'function') ctx.waitUntil(store); else await store;
        } catch (_) {} // A cache quota/failure must not discard a verified audio response.
      }
    }
    return new Response(range ? bytes.slice(range.start, range.end + 1) : bytes, {status, headers:resultHeaders});
  } catch (_) { return fail(502, 'assistance_audio_unavailable'); }
}
