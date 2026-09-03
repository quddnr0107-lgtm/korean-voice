#!/usr/bin/env python3
"""녹음(본인 음성) → 제로샷 음성 복제용 '프롬프트 키트'.

학습(GPU)을 하지 않는 대신, 제로샷 모델(CosyVoice·GPT-SoVITS·F5-TTS·Fish-Speech·IndexTTS2·Qwen3-TTS…)이
요구하는 것은 단 하나 — **깨끗한 3~12초 참조 음성 + 그 받아쓰기**다. 이 스크립트는 녹음에서
가장 좋은 구간을 고르고(잡음 대비 20dB 이상·끊김 없음·음높이 안정), 24kHz 모노 WAV로 잘라 낸다.

산출물은 prompt-kit/ (gitignore) — 목소리 원본이므로 저장소에 넣지 않는다.
  prompt-kit/prompt-01.wav … + manifest.json (파일·길이·SNR·받아쓰기 칸)

쓰는 법:
  pip install numpy imageio-ffmpeg
  python3 tools/make-prompt-kit.py <녹음 폴더> --out prompt-kit --top 5
받아쓰기(transcript)는 manifest.json에 직접 적거나 Whisper로 채운다 — 제로샷 모델 대부분이 요구한다.
"""
import os, sys, json, glob, argparse, subprocess, importlib.util
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('av', os.path.join(HERE, 'analyze-voice.py'))
av = importlib.util.module_from_spec(spec); spec.loader.exec_module(av)


def candidates(x, min_s=4.0, max_s=12.0, gap_ms=400):
    fr = av.frames(x)
    rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    floor, ceil = np.percentile(db, 10), np.percentile(db, 97)
    speech = db > floor + 0.35 * (ceil - floor)
    for s, e in av.runs(~speech):
        if e - s < 8: speech[s:e] = True
    for s, e in av.runs(speech):
        if e - s < 8: speech[s:e] = False
    f0 = av.f0_autocorr(fr); f0[~speech] = 0
    # 짧은 쉼(gap_ms 미만)으로 이어진 발화를 합쳐 max_s 이하 덩어리로
    groups, cur = [], None
    for s, e in av.runs(speech):
        if cur and (s - cur[1]) * 10 < gap_ms and (e - cur[0]) * 10 <= max_s * 1000:
            cur = (cur[0], e)
        else:
            if cur: groups.append(cur)
            cur = (s, e)
    if cur: groups.append(cur)
    out = []
    for s, e in groups:
        dur = (e - s) * 0.01
        if dur < min_s or dur > max_s: continue
        seg_db = db[s:e]; seg_sp = speech[s:e]
        snr = float(seg_db[seg_sp].mean() - floor)
        vf = f0[s:e]; vv = vf[vf > 0]
        if len(vv) < 30: continue
        f0_cv = float(vv.std() / vv.mean())
        voiced_ratio = float(len(vv) / max(1, seg_sp.sum()))
        clip = float((np.abs(x[s * av.HOP:(e * av.HOP) + av.FRAME]) > 0.99).mean())
        score = snr + 8 * voiced_ratio - 3 * abs(dur - 8) - 20 * f0_cv - 200 * clip
        out.append({'start_s': round(s * 0.01, 2), 'dur_s': round(dur, 2), 'snr_db': round(snr, 1), 'voiced_ratio': round(voiced_ratio, 2),
                    'f0_median': round(float(np.median(vv)), 1), 'f0_cv': round(f0_cv, 3), 'clipping': round(clip, 4), 'score': round(score, 1)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='+'); ap.add_argument('--out', default='prompt-kit'); ap.add_argument('--top', type=int, default=5)
    a = ap.parse_args()
    files = []
    for p in a.inputs:
        files += sorted(glob.glob(os.path.join(p, '*'))) if os.path.isdir(p) else [p]
    files = [f for f in files if f.lower().endswith(('.m4a', '.mp3', '.wav', '.ogg', '.aac', '.webm'))]
    allc = []
    for f in files:
        try: x = av.decode(f)
        except Exception as e: print('skip', f, e, file=sys.stderr); continue
        for c in candidates(x):
            c['file'] = f; allc.append(c)
    if not allc: sys.exit('후보 구간이 없습니다 (4~12초짜리 깨끗한 발화가 필요)')
    allc.sort(key=lambda c: -c['score'])
    picked, per_file = [], {}
    for c in allc:  # 파일당 2개까지 — 다양성
        if per_file.get(c['file'], 0) >= 2: continue
        picked.append(c); per_file[c['file']] = per_file.get(c['file'], 0) + 1
        if len(picked) >= a.top: break
    os.makedirs(a.out, exist_ok=True)
    manifest = []
    for i, c in enumerate(picked, 1):
        name = 'prompt-%02d.wav' % i
        subprocess.run([av.ffmpeg_exe(), '-v', 'error', '-y', '-ss', str(max(0, c['start_s'] - 0.15)), '-t', str(c['dur_s'] + 0.3), '-i', c['file'],
                        '-ac', '1', '-ar', '24000', '-af', 'afade=t=in:d=0.05,afade=t=out:st=%.2f:d=0.08' % (c['dur_s'] + 0.2), os.path.join(a.out, name)], check=True)
        manifest.append({'file': name, 'source': os.path.basename(c['file']), 'start_s': c['start_s'], 'dur_s': c['dur_s'], 'snr_db': c['snr_db'],
                         'f0_median': c['f0_median'], 'score': c['score'], 'transcript': ''})
    json.dump({'note': '제로샷 TTS의 참조 음성. transcript를 채워야 하는 모델(GPT-SoVITS·CosyVoice·F5-TTS)이 많다. 저장소에 넣지 말 것.',
               'prompts': manifest}, open(os.path.join(a.out, 'manifest.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    for m in manifest: print('%s  %5.1fs  SNR %4.1fdB  F0 %5.1fHz  score %5.1f  ← %s @%ss' % (m['file'], m['dur_s'], m['snr_db'], m['f0_median'], m['score'], m['source'][:8], m['start_s']))


if __name__ == '__main__':
    main()
