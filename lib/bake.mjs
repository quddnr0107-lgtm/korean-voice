// 굽기 대기열 — 컨테이너가 전편을 스스로 굽고 R2 에 넣는다. 바깥 드라이버가 없다(2026-09-04).
//
//   왜 — /warm 은 컨테이너 **디스크**에만 남고 잠들면 사라진다. R2 에는 /tts 를 거쳐야 들어간다.
//        전편(약 3만 조각 · 80시간)을 밖에서 GET 으로 돌리려면 그 시간 내내 살아 있는 프로세스가 필요했다.
//        그래서 Durable Object 의 알람이 드라이버 노릇을 한다: 알람마다 조각 몇 개를 /tts 로 받아 R2 에 넣고 다음 알람을 건다.
//        알람의 /tts 요청이 곧 컨테이너를 깨워 두는 신호라 3분 잠들기에도 안 걸린다.
//
//   상태는 DO 저장소(KV 꼴)에 둔다: q:<seq>  대기 조각 {v,s,r,t,tries} · k:<키> 대기 중인 키 색인(같은 조각을 두 번 안 넣는다 · 재실행이 멱등)
//   · e:<seq> 못 만든 조각 · stat 집계 · seq 번호 · stopped 깃발
//   🔴 표식 대조 — 컨테이너의 X-TTS-Recipe 가 워커 표식과 다르면 **넣지 않고 60초 뒤 다시** 본다(옛 이미지가 남아 있는 창).
//
// 순수 논리만 여기 둔다(테스트가 가짜 저장소·가짜 컨테이너로 돈다). DO 껍데기는 worker.mjs 의 BakeQueue.
import { cacheKey, parseR } from './tts-key.mjs';

export const BATCH = 4;        // 알람 한 번에 R2 로 옮기는 조각 수(조각당 합성 3~4초 · 알람 벽시계 20초 안)
export const WARM_AHEAD = 60;  // 컨테이너 대기열에 앞서 넣어 둘 조각 수(알람이 받을 때 이미 디스크에 있게)
export const MAX_TRIES = 3;
export const MISMATCH_WAIT_MS = 60000;
export const TICK_MS = 250;
const pad = (n) => String(n).padStart(9, '0');

export function makeBaker({ storage, r2, fetchContainer, recipeTag, now = () => Date.now(), setAlarm, batch = BATCH, warmAhead = WARM_AHEAD }) {
  const cleanText = (t) => String(t == null ? '' : t).replace(/\s+/g, ' ').trim().slice(0, 400);
  async function stat() { return (await storage.get('stat')) || { queued: 0, skipped: 0, done: 0, error: 0, mismatch: 0, pending: 0, started_at: null, last_at: null, finished_at: null }; }
  async function saveStat(s) { await storage.put('stat', s); }
  // 🔴 읽고-바로-쓴다 — 사이에 await(R2·컨테이너)가 있으면 넣는 쪽과 굽는 쪽이 서로 덮는다(3회차 실측: done 24 → 0)
  async function bump(fn) { const st = await stat(); fn(st); await saveStat(st); return st; }

  async function enqueue({ v = 'female', s = 16, items = [] }) {
    let seq = (await storage.get('seq')) || 0;
    let queued = 0, skipped = 0;
    const seen = new Set();
    const cand = [];
    for (const it of items) {
      const t = cleanText(typeof it === 'string' ? it : it && it.t);
      if (!t) continue;
      const r = parseR(typeof it === 'object' && it && it.r != null ? it.r : 1);
      const key = await cacheKey(v, s, r, t);
      if (seen.has(key)) continue;
      seen.add(key); cand.push({ v, s, r, t, key, tries: 0 });
    }
    // R2 에 이미 있나 — 하나씩 기다리면 400개에 20초가 넘는다(3회차 실측) → 50개씩 한꺼번에 묻는다
    const have = new Array(cand.length).fill(false);
    if (r2) for (let i = 0; i < cand.length; i += 50) {
      const part = cand.slice(i, i + 50);
      const res = await Promise.all(part.map((c) => r2.head(c.key).catch(() => null)));
      res.forEach((h, j) => { have[i + j] = !!h; });
    }
    // 이미 대기열에 있나 — 보내는 쪽이 다시 돌면(타임아웃·재실행) 같은 조각이 또 온다. 두 번 구우면 컨테이너 시간이 두 배다
    const inq = new Set();
    for (let i = 0; i < cand.length; i += 100) { const m = await storage.get(cand.slice(i, i + 100).map((c) => 'k:' + c.key)); for (const [k, v] of m) if (v) inq.add(k.slice(2)); }
    const puts = {};
    for (let i = 0; i < cand.length; i++) {
      if (have[i] || inq.has(cand[i].key)) { skipped++; continue; }
      seq++; puts['q:' + pad(seq)] = cand[i]; puts['k:' + cand[i].key] = pad(seq); queued++;
    }
    // 묶어 쓴다 — DO storage 의 여러 키 put 은 한 번에 128개까지다
    const ks = Object.keys(puts);
    for (let i = 0; i < ks.length; i += 100) { const part = {}; for (const k of ks.slice(i, i + 100)) part[k] = puts[k]; await storage.put(part); }
    await storage.put('seq', seq);
    const st = await bump((st) => { st.queued += queued; st.skipped += skipped; st.pending += queued; if (!st.started_at && queued) st.started_at = now(); if (queued) st.finished_at = null; });
    if (queued && !(await storage.get('stopped'))) await setAlarm(now() + TICK_MS);
    return { queued, skipped, pending: st.pending };
  }

  async function pendingItems(limit) {
    const m = await storage.list({ prefix: 'q:', limit });
    return [...m.entries()].map(([k, v]) => ({ ...v, k }));   // 🔴 k 는 뒤에 — 저장값에 k:undefined 가 남아 있어도 덮이지 않게
  }

  async function tick() {
    if (await storage.get('stopped')) return { stopped: true };
    const items = await pendingItems(Math.max(batch, warmAhead));
    if (!items.length) { await bump((st) => { st.pending = 0; st.finished_at = st.finished_at || now(); }); return { idle: true }; }
    // 앞서 굽기 — 같은 (v,s,r) 끼리 묶어 컨테이너 대기열에 넣는다(디스크 캐시에 이미 있으면 서버가 스스로 건너뛴다)
    const groups = new Map();
    for (const it of items) { const g = `${it.v}|${it.s}|${it.r}`; if (!groups.has(g)) groups.set(g, { v: it.v, s: it.s, r: it.r, texts: [] }); groups.get(g).texts.push(it.t); }
    for (const g of groups.values()) {
      try { await fetchContainer('/warm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(g) }); } catch (_) { /* 깨우는 중일 수 있다 — 아래 /tts 가 다시 시도한다 */ }
    }
    let done = 0, mismatch = false, error = 0, mism = 0;
    for (const it of items.slice(0, batch)) {
      let res;
      try { res = await fetchContainer('/tts?v=' + encodeURIComponent(it.v) + '&t=' + encodeURIComponent(it.t) + '&s=' + it.s + '&r=' + it.r); } catch (e) { res = null; }
      if (res && res.ok && (res.headers.get('Content-Type') || '').startsWith('audio/')) {
        if ((res.headers.get('X-TTS-Recipe') || '') !== recipeTag) { mismatch = true; mism++; break; }
        const bytes = new Uint8Array(await res.arrayBuffer());
        await r2.put(it.key, bytes, { httpMetadata: { contentType: 'audio/mpeg', cacheControl: 'public, max-age=31536000, immutable' } });
        await storage.delete([it.k, 'k:' + it.key]); done++;
      } else {
        const tries = (it.tries || 0) + 1;
        if (tries >= MAX_TRIES) { await storage.delete([it.k, 'k:' + it.key]); await storage.put('e:' + it.k.slice(2), { ...it, k: undefined, tries, status: res ? res.status : 'fetch_failed' }); error++; }
        else await storage.put(it.k, { ...it, k: undefined, tries });
      }
    }
    await bump((st) => { st.done += done; st.error += error; st.mismatch += mism; st.pending = Math.max(0, st.pending - done - error); st.last_at = now(); });
    await setAlarm(now() + (mismatch ? MISMATCH_WAIT_MS : TICK_MS));
    return { done, mismatch };
  }

  async function status() {
    const st = await stat();
    const errs = await storage.list({ prefix: 'e:', limit: 5 });
    return { ...st, stopped: !!(await storage.get('stopped')), recipe: recipeTag, error_sample: [...errs.values()].map((e) => ({ t: e.t.slice(0, 40), status: e.status })) };
  }
  async function stop() { await storage.put('stopped', true); return status(); }
  async function resume() { await storage.delete('stopped'); if ((await stat()).pending > 0) await setAlarm(now() + TICK_MS); return status(); }
  async function clear() {
    for (const prefix of ['q:', 'k:', 'e:']) for (;;) { const m = await storage.list({ prefix, limit: 1000 }); if (!m.size) break; await storage.delete([...m.keys()].slice(0, 128)); }
    await bump((st) => { st.pending = 0; });
    return status();
  }
  /* pending 을 저장소에서 다시 센다 — 집계가 어긋났을 때(3회차처럼) 한 번 맞춘다 */
  async function recount() {
    let n = 0, start;
    for (;;) { const m = await storage.list({ prefix: 'q:', limit: 1000, ...(start ? { startAfter: start } : {}) }); if (!m.size) break; n += m.size; start = [...m.keys()].pop(); if (m.size < 1000) break; }
    await bump((st) => { st.pending = n; });
    return status();
  }
  return { enqueue, tick, status, stop, resume, clear, recount };
}

/* 테스트용 가짜 저장소 — DO storage 의 get/put/delete/list 흉내(정렬된 키) */
export function memoryStorage() {
  const m = new Map();
  return {
    async get(k) { if (Array.isArray(k)) { const out = new Map(); for (const x of k) if (m.has(x)) out.set(x, m.get(x)); return out; } return m.get(k); },
    async put(k, v) { if (typeof k === 'object' && k) { for (const [a, b] of Object.entries(k)) m.set(a, b); } else m.set(k, v); },
    async delete(k) { for (const x of (Array.isArray(k) ? k : [k])) m.delete(x); },
    async list({ prefix = '', limit = Infinity, startAfter } = {}) {
      const out = new Map();
      for (const k of [...m.keys()].sort()) { if (k.startsWith(prefix) && (!startAfter || k > startAfter)) { out.set(k, m.get(k)); if (out.size >= limit) break; } }
      return out;
    },
    _map: m,
  };
}
