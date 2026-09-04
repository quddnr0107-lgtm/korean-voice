#!/usr/bin/env node
/* 굽을 조각 목록 — 사이트가 공개로 내주는 파일 여섯(live-tts.js · ko-voice.js · speech-text.js · exam_prep.json · easy_explain.json · study_data.json)에서 만든다.
   🔴 여기서 새로 나누지 않는다 — 화면이 쓰는 그 함수(LiveTTS.segment)를 vm 으로 꺼내 쓴다. 원고 규칙은 yebijun app.js 의 셋을 베낀 것
   (출제핵심 (lectureScript||bodyText).trim() · 개념강의 빈 줄→문장→`**` 제거→' ' 이음 + _standalone · 따라읽기 SpeechText.읽기용(original)).
   yebijun 의 .github/scripts/bake-live-tts.mjs 와 같은 논리다 — 그쪽 check-bake-list-live · check-follow-live-tts-live 가 화면과 같은지 잰다.
   갈래 k — exam(출제핵심강의) · easy(개념강의) · study(따라읽기 · 원문회독·눈회독·타이핑 줄 · 2026-09-04). bake.py --kind 가 이 값으로 거른다.
   사용: node chunks.mjs --site https://yebijun.drillstudy.com --out chunks.json */
import fs from 'node:fs';
import vm from 'node:vm';
const 인자 = process.argv.slice(2);
const 값 = (k, d) => { const i = 인자.indexOf(k); return i >= 0 && 인자[i + 1] ? 인자[i + 1] : d; };
const SITE = 값('--site', 'https://yebijun.drillstudy.com').replace(/\/$/, '');
const OUT = 값('--out', 'chunks.json');
const get = async (p) => { const r = await fetch(SITE + p + '?nocache=' + Date.now(), { headers: { 'Cache-Control': 'no-cache' } }); if (!r.ok) throw new Error(p + ' ' + r.status); return r.text(); };

function require서로(src) { const m = { exports: {} }; const g = { module: m, exports: m.exports, window: undefined, console }; vm.createContext(g); vm.runInContext(src, g); return m.exports; }
function 조각기(liveSrc, koSrc) {
  const K = require서로(koSrc);
  const g = { window: { KoVoice: K }, document: { addEventListener() {} }, fetch: () => {}, Audio: function () {}, setTimeout, clearTimeout, console };
  g.window.window = g.window; g.self = g.window;
  vm.createContext(g); vm.runInContext(liveSrc, g);
  const L = g.window.LiveTTS; if (!L || typeof L.segment !== 'function') throw new Error('LiveTTS.segment 를 못 꺼냈다');
  return L.segment;
}
const 출제핵심글 = (item) => String(item.lectureScript || item.bodyText || '').trim();
function 개념글(content) {
  const paragraphs = String(content || '').split(/\n{2,}/).map((p) => p.trim()).filter(Boolean); const parts = [];
  for (const para of paragraphs) for (const s of para.split(/(?<=[\.\!\?。])\s+|(?<=[\.\!\?。])\n|(?<=다\.)(?=\s)|(?<=요\.)(?=\s)|\n+/).filter((s) => s.trim())) { const txt = s.replace(/\*\*/g, '').trim(); if (txt) parts.push(txt); }
  return parts.join(' ');
}
const [liveSrc, koSrc, stSrc, epSrc, eeSrc, sdSrc] = await Promise.all([get('/live-tts.js'), get('/ko-voice.js'), get('/speech-text.js'), get('/exam_prep.json'), get('/easy_explain.json'), get('/study_data.json')]);
const seg = 조각기(liveSrc, koSrc);
const 읽기용 = (() => { const S = require서로(stSrc); if (!S || typeof S.읽기용 !== 'function') throw new Error('SpeechText.읽기용 을 못 꺼냈다'); return S.읽기용; })();
const 글들 = [];
const ep = JSON.parse(epSrc);
for (const period of Object.keys(ep)) for (const it of (ep[period] || [])) { const t = 출제핵심글(it); if (t) 글들.push({ k: 'exam', t }); }
const ee = JSON.parse(eeSrc);
for (const period of Object.keys(ee)) { if (period.startsWith('_')) continue; for (const law of Object.keys(ee[period] || {})) { if (law.startsWith('_')) continue; for (const a of Object.keys(ee[period][law] || {})) { const t = 개념글(ee[period][law][a] && ee[period][law][a].content); if (t) 글들.push({ k: 'easy', t }); } } }
const st = ee._standalone || {};
for (const period of Object.keys(st)) for (const law of Object.keys(st[period] || {})) for (const type of Object.keys(st[period][law] || {})) for (const a of Object.keys(st[period][law][type] || {})) { const t = 개념글(st[period][law][type][a] && st[period][law][type][a].content); if (t) 글들.push({ k: 'easy', t }); }
/* 따라읽기 — 줄 original(머리줄 포함 · 2자 이상)을 읽기용에 통과시킨 것. original 은 안 건드린다(yebijun bake-live-tts.mjs 회독줄들 과 같다) */
const sd = JSON.parse(sdSrc); const 본줄 = new Set();
for (const b of (sd.blanks || [])) { const t = String(b.original || '').trim(); if (t.length < 2 || 본줄.has(t)) continue; 본줄.add(t); 글들.push({ k: 'study', t: 읽기용(t) || t }); }
const map = new Map();
for (const { k: 갈래, t: 글 } of 글들) for (const c of seg(글)) { const r = Math.round((c.r || 1) * 100) / 100; const k = c.text + '|' + r.toFixed(2); if (!map.has(k)) map.set(k, { t: c.text, r, k: 갈래 }); }
const 목록 = [...map.values()];
fs.writeFileSync(OUT, JSON.stringify(목록));
const 세기 = (갈래) => 목록.filter((c) => c.k === 갈래).length;
console.log(`원고 ${글들.length}편 · 고유 조각 ${목록.length}개(exam ${세기('exam')} · easy ${세기('easy')} · study ${세기('study')}) · ${목록.reduce((a, c) => a + c.t.length, 0).toLocaleString()}자 → ${OUT}`);
