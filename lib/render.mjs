/* 대본 낭독 만들기 — 조각을 순서대로 잇고 **계획된 쉼**을 넣어 하나의 mp3 로 돌려준다.
   조각 합성·이어붙이기는 컨테이너(server/server.py 의 /render · ffmpeg concat)가 하고,
   여기 있는 것은 워커가 쓰는 순수 함수뿐이다 — 한도 검사, 결과 캐시 키, 무료 한도 계산.

   🔴 왜 조각을 미리 나누어 보내나: 문장을 나누고 쉼을 정하는 것은 브라우저의 ko-voice.js 다(정본이 하나뿐이어야 한다).
      서버는 받은 문장을 그대로 읽고 받은 쉼을 그대로 넣는다 — /tts 와 같은 규칙이라 **조각 캐시가 그대로 재사용된다**.
      대본을 고쳐 다시 만들면 바뀐 문장만 새로 합성된다. */
import { sha1, fmtR, parseR, RECIPE_TAG } from './tts-key.mjs';

export const LIMITS = {
  items: 400,          // 조각 수 (server.py 의 /warm 과 같은 상한)
  chars: 20000,        // 한 번에 만들 수 있는 글자 수 — 약 20분 낭독
  itemChars: 400,      // 조각 하나 (server.py MAX_CHARS 와 같다)
  pauseMs: 3000,       // 조각 뒤 쉼 상한
};
export const FREE_DAILY_CHARS = 3000;   // 로그인 없이 하루에 만들 수 있는 글자 수(약 3분)

/** 밖에서 온 items 를 서버가 받는 꼴로 다듬는다. 반환: { ok, items, chars, error } */
export function normalizeItems(raw) {
  if (!Array.isArray(raw) || !raw.length) return { ok: false, error: 'empty_items' };
  if (raw.length > LIMITS.items) return { ok: false, error: 'too_many_items' };
  const items = [];
  let chars = 0;
  for (const it of raw) {
    const t = String((it && it.t) == null ? '' : it.t).replace(/\s+/g, ' ').trim().slice(0, LIMITS.itemChars);
    const pause = Math.max(0, Math.min(LIMITS.pauseMs, Math.round(Number((it && it.pause) || 0)) || 0));
    // 🔴 글 없이 쉼만 있는 조각(운율 계획의 쉼 줄)은 버리지 않고 **앞 조각의 쉼에 더한다** — 버리면 쉼이 사라진다
    if (!t) {
      if (items.length && pause) items[items.length - 1].pause = Math.min(LIMITS.pauseMs, items[items.length - 1].pause + pause);
      continue;
    }
    items.push({ t, r: parseR(it && it.r == null ? 1 : it.r), pause });
    chars += t.length;
    if (chars > LIMITS.chars) return { ok: false, error: 'too_many_chars', chars };
  }
  if (!items.length) return { ok: false, error: 'empty_items' };
  return { ok: true, items, chars };
}

/** 같은 대본·같은 목소리면 같은 키 — 두 번째 요청은 합성 없이 R2 에서 나간다. */
export async function renderKey(v, steps, tag, items) {
  const body = items.map((it) => `${fmtR(it.r)}:${it.pause}:${it.t}`).join('\n');
  return `render/${await sha1(`${v}|${steps}|${tag || RECIPE_TAG}|${body}`)}.mp3`;
}

/** UTC 날짜 도장 — 무료 한도를 하루 단위로 끊는다. */
export const dayStamp = (now = Date.now()) => new Date(now).toISOString().slice(0, 10);

/** 무료 한도 판정. used = 오늘 이미 쓴 글자 수. */
export function quota(used, chars, limit = FREE_DAILY_CHARS) {
  const after = used + chars;
  return { ok: after <= limit, used, after, limit, remaining: Math.max(0, limit - used) };
}
