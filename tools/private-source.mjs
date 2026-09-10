const API = 'https://yebijun-sync.quddnr0107.workers.dev/api/tts-source';
const KINDS = { '/exam_prep.json': 'exam', '/easy_explain.json': 'easy', '/study_data.json': 'study' };
export function makeSourceReader(site, { env = process.env, fetchFn = fetch } = {}) {
  let tokenPromise;
  async function token() {
    if (!tokenPromise) tokenPromise = (async () => {
      const endpoint = env.ACTIONS_ID_TOKEN_REQUEST_URL, credential = env.ACTIONS_ID_TOKEN_REQUEST_TOKEN;
      if (!endpoint || !credential) throw new Error('Private source requires an authorized main Actions workflow');
      const url = new URL(endpoint); url.searchParams.set('audience', 'yebijun-tts-source');
      const r = await fetchFn(url, { headers: { Authorization: 'Bearer ' + credential }, signal: AbortSignal.timeout(20000) });
      if (!r.ok) throw new Error('Source OIDC request failed: ' + r.status);
      const data = await r.json();
      if (!data.value) throw new Error('Source OIDC token missing');
      return data.value;
    })();
    return tokenPromise;
  }
  return async path => {
    const kind = KINDS[path];
    const r = kind ? await fetchFn(API, {
      method: 'POST', headers: { Authorization: 'Bearer ' + await token(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind }), signal: AbortSignal.timeout(60000)
    }) : await fetchFn(site + path + '?nocache=' + Date.now(), { headers: { 'Cache-Control': 'no-cache' }, signal: AbortSignal.timeout(30000) });
    if (!r.ok) throw new Error(path + ' ' + r.status);
    return r.text();
  };
}
