/* 단계별로 즉시 찍는다 — 어디서 막히는지 보기 위해. THREADS·STEPS 를 환경변수로 받는다. */
import { chromium } from 'playwright';
import { readFile } from 'node:fs/promises';
import { join, extname } from 'node:path';
const THREADS = +(process.env.THREADS || 1);
const say = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const proxy = process.env.HTTPS_PROXY;
const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox', '--ignore-certificate-errors'].concat(proxy ? ['--proxy-server=' + proxy] : []) });
const page = await browser.newPage();
const ORT_DIR = process.env.ORT_DIR;
const MIME = { '.js': 'text/javascript', '.wasm': 'application/wasm' };
await page.route('https://cdn.jsdelivr.net/**', async (route) => {
  const u = new URL(route.request().url());
  const name = u.pathname.startsWith('/npm/onnxruntime-web@1.17.0/dist/esm/') ? 'esm-' + u.pathname.split('/').pop() : u.pathname.split('/').pop();
  try {
    const body = await readFile(join(ORT_DIR, name));
    say('  [stub]', name, body.length);
    await route.fulfill({ status: 200, headers: { 'Content-Type': MIME[extname(name)] || 'application/octet-stream', 'Access-Control-Allow-Origin': '*', 'Cross-Origin-Resource-Policy': 'cross-origin' }, body });
  } catch (_) { say('  [stub 404]', name); await route.fulfill({ status: 404, body: 'x' }); }
});
page.on('console', (m) => say('  [page]', m.type(), m.text().slice(0, 160)));
page.on('pageerror', (e) => say('  [pageerror]', String(e).slice(0, 200)));
page.on('requestfailed', (r) => say('  [reqfail]', r.url().slice(0, 80), (r.failure() || {}).errorText));
await page.addInitScript(() => { window.KO_MODEL_BASE = 'http://localhost:8123/model'; });
say('goto'); await page.goto('http://localhost:8123/app.html', { waitUntil: 'domcontentloaded' });
say('env', JSON.stringify(await page.evaluate(() => ({ iso: self.crossOriginIsolated, sab: typeof SharedArrayBuffer !== 'undefined', cores: navigator.hardwareConcurrency }))));
async function step(name, fn, arg, ms) {
  say('>>', name);
  const t = Date.now();
  try {
    const r = await Promise.race([page.evaluate(fn, arg), new Promise((_, rej) => setTimeout(() => rej(new Error('시간초과 ' + ms + 'ms')), ms))]);
    say('OK', name, ((Date.now() - t) / 1000).toFixed(1) + 's', JSON.stringify(r).slice(0, 300));
    return r;
  } catch (e) { say('FAIL', name, ((Date.now() - t) / 1000).toFixed(1) + 's', String(e.message).slice(0, 200)); throw e; }
}
try {
  await step('module import', async () => { window.L = await import('/local-tts.js'); return { ok: !!window.L, base: window.L.MODEL_BASE }; }, null, 60000);
  await step('ort ready', async (threads) => {
    const ort = await import('/vendor/ort-cdn.js');
    ort.env.wasm.numThreads = threads; ort.env.wasm.simd = true; ort.env.wasm.wasmPaths = ort.ORT_WASM_PATHS;
    return { threads: ort.env.wasm.numThreads };
  }, THREADS, 60000);
  await step('load models', async () => { const t = performance.now(); await window.L.load({ voice: 'female' }); return { loadSec: +((performance.now() - t) / 1000).toFixed(1), info: window.L.info() }; }, null, 420000);
  await step('synth one', async () => {
    const K = window.KoVoice; const plan = K.prepare('2026년 9월 3일 접수 인원은 1,234명입니다.');
    const t = K.joinSpokenChunks(plan.sentences[0].chunks);
    const r = await window.L.synth([{ t, pause: 0, r: 1 }]);
    let peak = 0; for (let i = 0; i < r.pcm.length; i++) peak = Math.max(peak, Math.abs(r.pcm[i]));
    return { text: t.slice(0, 30), audioSec: +r.audioSeconds.toFixed(2), synthSec: +r.synthSeconds.toFixed(2), rtf: +r.rtf.toFixed(3), peak: +peak.toFixed(3), samples: r.pcm.length };
  }, null, 300000);
} catch (_) { /* 이미 찍었다 */ }
await browser.close();
