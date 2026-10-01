/* 따라읽기(원문회독·눈회독·타이핑) 줄 → 굽을 글. yebijun 화면과 **같은 함수**(SpeechText.줄글)를 쓴다 — 한 글자라도 다르면 굽은 조각이 안 맞는다.
   SpeechText.줄글(b, 근거): 표 조각을 숨긴 줄 → '' · 별표 사진 줄 → 앞말 + 글로 푼 표 + 사진 밖 글 · 그 밖 → 원문 (yebijun 2026-10-01)
   🔵 사이트가 아직 옛 speech-text.js 면(줄글 없음) 원문을 쓴다 — 지금과 같다. 순서(사이트 배포 → 이 저장소 → 굽기)를 어겨도 틀린 조각을 굽지 않는다.
   🔴 줄글이 **있는데** 근거(숨김 줄·글로 푼 표)를 못 받았으면 멈춘다 — 조용히 원문으로 넘어가면 깨진 표 조각을 굽고,
      따로 돌리는 prune 이 맞는 조각을 지운다(Codex 리뷰 #73 P1). 옛 사이트 호환은 줄글이 **없을 때만**이다. */
export function 회독글들(sd, S, 근거) {
  if (S && typeof S.줄글 === 'function' && !(근거 && 근거.studySpeech && 근거.studyHideLineIds)) {
    throw new Error('사이트에 SpeechText.줄글 이 있는데 /appendix-speech.json·/appendix_data.json 을 못 받았다 — 깨진 표 글자를 굽지 않으려 멈춘다');
  }
  const 줄글 = S && typeof S.줄글 === 'function' ? (b) => S.줄글(b, 근거 || null) : (b) => String((b && b.original) || '').trim();
  const 읽기용 = S && typeof S.읽기용 === 'function' ? S.읽기용 : (t) => t;
  const 본줄 = new Set(); const out = [];
  for (const b of (sd && sd.blanks) || []) {
    const t = 줄글(b);
    if (!t || t.length < 2 || 본줄.has(t)) continue;
    본줄.add(t);
    out.push({ t: 읽기용(t) || t, law: b.law });
  }
  return out;
}
/* 근거 — 공개 파일 둘에서. 없으면(옛 사이트) null 칸으로 — 줄글이 원문으로 넘어간다 */
export function 근거만들기(apxSrc, spSrc) {
  let apx = null, sp = null;
  try { apx = apxSrc ? JSON.parse(apxSrc) : null; } catch (_) { apx = null; }
  try { sp = spSrc ? JSON.parse(spSrc) : null; } catch (_) { sp = null; }
  return { studySpeech: (sp && sp.studySpeech) || null, studyHideLineIds: (apx && apx.studyHideLineIds) || null };
}
