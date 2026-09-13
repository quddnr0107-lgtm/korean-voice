/* 브라우저에서 낭독 만들기 — **우리 비용 0**. 합성이 사용자 기기에서 돌아 서버·컨테이너를 쓰지 않는다.
   읽는 방식(정규화·쉼·감정)은 ko-voice.js 가 정하고, 소리는 Supertonic 3 ONNX 가 만든다.

   같은 목소리를 내기 위해 서버(server/server.py · voice_shape u5)와 **같은 값**을 쓴다:
     u5(기본·스텝 8)  여성 F4:0.6+F2:0.4 · 남성 M1:0.7+M3:0.3 · speed 1.05
   🔴 vendor 의 loadVoiceStyle 은 스타일을 **배치로 쌓기만** 한다(가중 평균이 아니다). 그래서 섞는 것은
      여기서 직접 한다(blendStyle) — 안 그러면 서버와 다른 목소리가 난다.

   모델은 384MB 다. 한 번 받으면 Cache API 에 두고 다시 받지 않는다(navigator.storage.persist()).
   기기가 느리면(첫 문장 RTF 가 한계를 넘으면) 서버 경로로 물러난다 — speedGate 를 보라. */
import * as ort from './vendor/ort-cdn.js';
import { ORT_WASM_PATHS } from './vendor/ort-cdn.js';
import { TextToSpeech, UnicodeProcessor, Style, loadOnnx, writeWavFile } from './vendor/supertonic-helper.js';

/** 모델 출처. R2 에 올리면 window.KO_MODEL_BASE 로 갈아탄다(egress 무료 · 우리 도메인).
 *  그때까지는 Hugging Face CDN 을 쓴다(IP 당 5분 3,000 요청 제한이 있다). */
export const MODEL_BASE = (typeof window !== 'undefined' && window.KO_MODEL_BASE) ||
  'https://huggingface.co/Supertone/supertonic-3/resolve/main';
const CACHE_NAME = 'ko-voice-model-v1';
const RECIPES = {
  u5: { steps: 8, female: 'F4:0.6,F2:0.4', male: 'M1:0.7,M3:0.3' },
  k2: { steps: 16, female: 'F2', male: 'M1:0.7,M3:0.3' },
};
const SPEED = 1.05;                 // server.py VOICES[*].speed 와 같다
export const SPEED_GATE_RTF = 3.0;  // 첫 문장이 이보다 느리면 서버로 물러난다(레포 실측: 단일 스레드 1.65~2.28)

/* ── 받아 두기 — Cache API 에 두고 두 번째부터는 네트워크를 쓰지 않는다 ── */
async function cached(url, onProgress) {
  let cache = null;
  try { cache = await caches.open(CACHE_NAME); } catch (_) { /* 사생활 보호 모드 등 */ }
  if (cache) {
    const hit = await cache.match(url).catch(() => null);
    if (hit) return await hit.arrayBuffer();
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error('모델을 받지 못했습니다: ' + url.split('/').pop() + ' (' + res.status + ')');
  const total = +(res.headers.get('Content-Length') || 0);
  if (!res.body || !onProgress) {
    const buf = await res.arrayBuffer();
    if (cache) await cache.put(url, new Response(buf.slice(0))).catch(() => {});
    return buf;
  }
  const reader = res.body.getReader(); const parts = []; let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value); got += value.length; onProgress(got, total);
  }
  const buf = new Uint8Array(got); let at = 0;
  for (const p of parts) { buf.set(p, at); at += p.length; }
  if (cache) await cache.put(url, new Response(buf.slice(0))).catch(() => {});
  return buf.buffer;
}

/** 'F4:0.6,F2:0.4' → 가중 평균한 Style 하나(bsz=1). 가중치 합은 1 로 맞춘다(server.py 와 같다). */
async function blendStyle(spec) {
  const parts = spec.split(',').map((s) => { const [n, w] = s.split(':'); return { n: n.trim(), w: w == null || w === '' ? 1 : parseFloat(w) }; });
  const sum = parts.reduce((a, p) => a + p.w, 0) || 1;
  let ttl = null, dp = null, ttlDims = null, dpDims = null;
  for (const p of parts) {
    const json = await (await fetch(`${MODEL_BASE}/voice_styles/${p.n}.json`)).json();
    const w = p.w / sum;
    const t = Float32Array.from(json.style_ttl.data.flat(Infinity));
    const d = Float32Array.from(json.style_dp.data.flat(Infinity));
    if (!ttl) {
      ttl = new Float32Array(t.length); dp = new Float32Array(d.length);
      ttlDims = json.style_ttl.dims; dpDims = json.style_dp.dims;
    }
    for (let i = 0; i < t.length; i++) ttl[i] += t[i] * w;
    for (let i = 0; i < d.length; i++) dp[i] += d[i] * w;
  }
  return new Style(new ort.Tensor('float32', ttl, [1, ttlDims[1], ttlDims[2]]),
                   new ort.Tensor('float32', dp, [1, dpDims[1], dpDims[2]]));
}

/* ── 엔진 ── */
let engine = null;
export function ready() { return !!engine; }

export async function load({ tag = 'u5', voice = 'female', onStatus = () => {} } = {}) {
  if (engine && engine.tag === tag && engine.voice === voice) return engine;
  const threads = Math.max(1, Math.min(4, (navigator.hardwareConcurrency || 1)));
  // SharedArrayBuffer 가 없으면(교차출처 격리 안 됨) 멀티스레드가 불가능하다 — 그때는 1 스레드로 돈다
  ort.env.wasm.numThreads = (typeof SharedArrayBuffer === 'undefined' || !self.crossOriginIsolated) ? 1 : threads;
  ort.env.wasm.simd = true;
  ort.env.wasm.wasmPaths = ORT_WASM_PATHS;   // 페이지 기준 상대경로로 찾으면 404 (실측 2026-09-13)
  const providers = [];
  if (navigator.gpu) providers.push('webgpu');        // 있으면 먼저 쓴다(ORT 의 WebGPU EP 는 아직 실험적이라 폴백 필수)
  providers.push('wasm');
  onStatus('모델 받는 중', 0);
  const names = ['duration_predictor', 'text_encoder', 'vector_estimator', 'vocoder'];
  const files = {};
  for (let i = 0; i < names.length; i++) {
    files[names[i]] = await cached(`${MODEL_BASE}/onnx/${names[i]}.onnx`,
      (got, total) => onStatus(`모델 받는 중 (${i + 1}/4)`, total ? got / total : 0));
  }
  for (const f of ['tts.json', 'unicode_indexer.json']) {
    files[f] = await cached(`${MODEL_BASE}/onnx/${f}`);
  }
  onStatus('엔진 준비 중', 1);
  /* vendor 의 loadTextToSpeech 는 경로를 받아 스스로 fetch 한다 — 우리는 이미 캐시에서 바이트를 들고 있으므로
     그 함수를 쓰지 않고 **부품으로 직접 조립**한다(같은 384MB 를 두 번 받지 않는다).
     조립 순서는 vendor 의 loadTextToSpeech 와 같다: 설정 → 모델 4개 → 글자 처리기. */
  const opt = { executionProviders: providers };
  const [dpOrt, textEncOrt, vectorEstOrt, vocoderOrt] = await Promise.all(
    names.map((n) => loadOnnx(new Uint8Array(files[n]), opt)));
  const text = (k) => JSON.parse(new TextDecoder().decode(new Uint8Array(files[k])));
  const cfgs = text('tts.json');
  const tts = new TextToSpeech(cfgs, new UnicodeProcessor(text('unicode_indexer.json')),
                               dpOrt, textEncOrt, vectorEstOrt, vocoderOrt);
  const recipe = RECIPES[tag] || RECIPES.u5;
  const style = await blendStyle(recipe[voice] || recipe.female);
  try { if (navigator.storage && navigator.storage.persist) await navigator.storage.persist(); } catch (_) { /* */ }
  engine = { tts, style, tag, voice, steps: recipe.steps, sampleRate: tts.sampleRate,
             threads: ort.env.wasm.numThreads, providers };
  onStatus('준비 끝', 1);
  return engine;
}

/** 조각(ko-voice 의 buildItems 결과)을 합성해 계획된 쉼과 함께 잇는다 → Float32Array PCM.
 *  쉼은 **정확한 샘플 수**로 넣는다(mp3 이어붙이기의 48ms 오차가 여기서는 없다). */
export async function synth(items, { onItem = () => {}, signal } = {}) {
  if (!engine) throw new Error('엔진이 준비되지 않았습니다');
  const sr = engine.sampleRate;
  const chunks = [];
  let audio = 0, spent = 0, firstRtf = null;
  for (let i = 0; i < items.length; i++) {
    if (signal && signal.aborted) throw new Error('멈췄습니다');
    const it = items[i];
    const t0 = performance.now();
    const { wav } = await engine.tts._infer([it.t], ['ko'], engine.style, engine.steps, SPEED * (it.r || 1));
    const took = (performance.now() - t0) / 1000;
    const pcm = Float32Array.from(wav);
    chunks.push(pcm); audio += pcm.length / sr; spent += took;
    if (it.pause) chunks.push(new Float32Array(Math.round(sr * it.pause / 1000)));
    if (firstRtf == null) firstRtf = took / (pcm.length / sr);
    onItem(i + 1, items.length, { rtf: took / (pcm.length / sr), firstRtf });
  }
  let n = 0; for (const c of chunks) n += c.length;
  const out = new Float32Array(n); let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.length; }
  // 전체에 단일 게인 — 조각별로 다르게 걸면 강조·감정의 셈여림이 뭉개진다(서버의 loudnorm linear 와 같은 취지)
  let peak = 0; for (let i = 0; i < out.length; i++) { const a = Math.abs(out[i]); if (a > peak) peak = a; }
  const target = 0.84;                       // 약 -1.5 dBFS
  if (peak > 0) { const g = target / peak; for (let i = 0; i < out.length; i++) out[i] *= g; }
  return { pcm: out, sampleRate: sr, audioSeconds: audio, synthSeconds: spent, rtf: spent / (audio || 1), firstRtf };
}

/** 첫 문장만 만들어 기기 속도를 잰다. 느리면 서버 경로를 권한다. */
export async function speedGate(item) {
  const t0 = performance.now();
  const { wav } = await engine.tts._infer([item.t], ['ko'], engine.style, engine.steps, SPEED);
  const rtf = ((performance.now() - t0) / 1000) / (wav.length / engine.sampleRate);
  return { rtf, ok: rtf <= SPEED_GATE_RTF };
}

/** writeWavFile 은 **−1~1 실수**를 받아 안에서 int16 으로 바꾼다 — 미리 바꿔 넘기면 두 번 변환된다. */
export function wavBlob(pcm, sampleRate) {
  return new Blob([writeWavFile(pcm, sampleRate)], { type: 'audio/wav' });
}
export const info = () => engine && { tag: engine.tag, voice: engine.voice, steps: engine.steps,
  sampleRate: engine.sampleRate, threads: engine.threads, providers: engine.providers };
