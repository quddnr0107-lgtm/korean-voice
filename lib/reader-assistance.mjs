/* Shared content identity for reviewed F1 assistance audio. Keep in sync with tools/reader_assistance.py. */
import { createHash } from 'node:crypto';

export const RECIPE = 'reader-assistance-f1-v1';
export const VOICE = 'f1';
export const MEDIA_BASE = 'https://korean-voice.quddnr0107.workers.dev/reader-assistance/f1/v1/';

export function canonicalJson(value) {
  function sorted(item) {
    if (Array.isArray(item)) return item.map(sorted);
    if (item && typeof item === 'object') return Object.fromEntries(Object.keys(item).sort().map(key => [key, sorted(item[key])]));
    if (typeof item === 'number' && !Number.isFinite(item)) throw new TypeError('Non-finite recipe number');
    if (item === undefined || typeof item === 'function' || typeof item === 'bigint') throw new TypeError('Non-JSON recipe value');
    return item;
  }
  return JSON.stringify(sorted(value));
}

export const sha256 = text => createHash('sha256').update(text, 'utf8').digest('hex');
export const textSha256 = text => {
  if (typeof text !== 'string' || !text.trim()) throw new TypeError('Assistance text is required');
  return sha256(text);
};
export const recipeSha256 = recipe => sha256(canonicalJson(recipe));
export const audioKey = (recipe, text) => sha256(canonicalJson(recipe) + '\n' + textSha256(text));

export function audioMetadata(key, clip) {
  if (!/^[a-f0-9]{64}$/.test(key) || !clip || !/^[a-f0-9]{64}$/.test(clip.sha256) || !/^[a-f0-9]{64}$/.test(clip.textSha256) || !Number.isSafeInteger(clip.durationMs) || clip.durationMs <= 0) throw new TypeError('Invalid assistance audio metadata');
  return { voice: VOICE, recipe: RECIPE, url: MEDIA_BASE + key + '.mp3', durationMs: clip.durationMs, sha256: clip.sha256, audioKey: key, textSha256: clip.textSha256 };
}
