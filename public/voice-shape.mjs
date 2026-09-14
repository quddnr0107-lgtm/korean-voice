/* 합성 뒤 다듬기 — server/voice_shape.py 의 U5 조합을 브라우저로 옮긴 것. 외부 의존 0.
   서버 순서: trim(앞여유 50ms) → 페이드 → 첫 음절 보강 → **Praat PSOLA 억양** → 꼬리 트림 → 피크 0.89
   여기 없는 것: PSOLA(praat_shape). Praat 가 필요해 브라우저에서 돌 수 없다 —
   그래서 브라우저 소리는 서버와 **억양 곡선만** 다르다(음량·길이·앞뒤 여유는 같다).
   값은 server/voice_shape.py 의 RECIPE 와 같아야 한다(test/voice-shape.test.mjs 가 두 파일을 맞춰 본다). */
export const RECIPE = {
  leadPadMs: 50,      // trim 이 앞에 남기는 여유
  trimThreshDb: -45,  // 최댓값 대비 이 아래는 무음으로 본다
  fadeMs: 10,         // 앞뒤 페이드(딸깍 방지)
  onsetBoost: 2.0,    // 첫 음절 이득 — 150ms 에 걸쳐 1.0 으로
  onsetMs: 150,
  tailDropDb: 40,     // 꼬리 트림 문턱(최댓값 대비)
  tailKeepMs: 60,
  peak: 0.89,         // 조각 피크
};

const frameRms = (w, frame) => {
  const n = Math.floor(w.length / frame);
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let j = i * frame; j < (i + 1) * frame; j++) s += w[j] * w[j];
    out[i] = Math.sqrt(s / frame + 1e-12);
  }
  return out;
};
const db = (x) => 20 * Math.log10(x + 1e-9);

/** 앞뒤 무음 제거 — 최댓값 대비 -45dB 위를 말로 본다(앞에 50ms 여유를 남긴다). */
export function trim(w, sr, threshDb = RECIPE.trimThreshDb, padMs = RECIPE.leadPadMs) {
  const frame = Math.floor(sr * 0.01);
  const n = Math.floor(w.length / frame);
  if (n < 3) return w;
  const rms = frameRms(w, frame);
  let max = -Infinity;
  for (let i = 0; i < n; i++) max = Math.max(max, db(rms[i]));
  let first = -1, last = -1;
  for (let i = 0; i < n; i++) if (db(rms[i]) > max + threshDb) { if (first < 0) first = i; last = i; }
  if (first < 0) return w;
  const pad = Math.floor(sr * padMs / 1000);
  return w.subarray(Math.max(0, first * frame - pad), Math.min(w.length, (last + 1) * frame + pad));
}

/** 앞뒤 짧은 페이드 — 자른 자리에서 딸깍 소리가 나지 않게. */
export function fade(w, sr, ms = RECIPE.fadeMs) {
  const k = Math.min(Math.floor(w.length / 2), Math.floor(sr * ms / 1000));
  if (k <= 0) return w;
  const out = Float32Array.from(w);
  for (let i = 0; i < k; i++) { const g = i / k; out[i] *= g; out[out.length - 1 - i] *= g; }
  return out;
}

/** 첫 음절 보강 — 첫 150ms 를 2.0배에서 1.0배로 내리며 곱한다(말 시작이 묻히지 않게). */
export function onsetBoost(w, sr, gain = RECIPE.onsetBoost, ms = RECIPE.onsetMs) {
  if (gain === 1 || !w.length) return w;
  const n = Math.min(Math.floor(sr * ms / 1000), w.length);
  const out = Float32Array.from(w);
  for (let i = 0; i < n; i++) out[i] *= gain + (1 - gain) * (i / Math.max(1, n - 1));
  return out;
}

/** 말이 끝난 뒤 남은 죽은 공백을 자른다 — 모델이 길이를 길게 잡아 무음이 붙는 일이 있다. */
export function tailTrim(w, sr, dropDb = RECIPE.tailDropDb, keepMs = RECIPE.tailKeepMs) {
  const frame = Math.floor(sr * 0.02);
  const n = Math.floor(w.length / frame);
  if (n < 5) return w;
  const rms = frameRms(w, frame);
  let max = -Infinity;
  for (let i = 0; i < n; i++) max = Math.max(max, db(rms[i]));
  let last = -1;
  for (let i = 0; i < n; i++) if (db(rms[i]) > max - dropDb) last = i;
  if (last < 0) return w;
  const end = Math.min(w.length, (last + 1) * frame + Math.floor(sr * keepMs / 1000));
  return end < w.length ? w.subarray(0, end) : w;
}

/** 조각 하나 다듬기 — 서버와 같은 순서(PSOLA 만 빠진다). */
export function shape(wav, sr) {
  let w = wav instanceof Float32Array ? wav : Float32Array.from(wav);
  w = trim(w, sr);
  w = fade(w, sr);
  w = onsetBoost(w, sr);
  w = tailTrim(w, sr);
  let peak = 0;
  for (let i = 0; i < w.length; i++) peak = Math.max(peak, Math.abs(w[i]));
  if (peak > 0) { const g = RECIPE.peak / peak; const o = Float32Array.from(w); for (let i = 0; i < o.length; i++) o[i] *= g; return o; }
  return Float32Array.from(w);
}
