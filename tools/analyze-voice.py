#!/usr/bin/env python3
"""녹음(본인 음성) → 운율 프로필(JSON). 녹음 원본은 저장소에 넣지 않는다 — 파생 수치만 남긴다.

측정하는 것(모두 numpy만으로, 외부 모델 없음):
  - 음높이(F0): 자기상관 기반. 중앙값·p10·p90, 반음 범위
  - 쉼: 에너지 기반 발화 구간 검출 → 150~600ms(구 사이) / 600ms 초과(문장 사이) 길이 분포
  - 말속도: 에너지 포락선의 음절 핵 피크 수 / 발화 시간 (음절/초, 근사)
  - 하강조: 호흡 구간 앞 1/3 vs 뒤 1/3 F0 평균 차이(%)
  - 문장 끝 억양: 호흡 구간 마지막 300ms의 F0 기울기 → 하강 비율

쓰는 법:
  pip install numpy imageio-ffmpeg
  python3 tools/analyze-voice.py <녹음 폴더 또는 파일들...> --out profiles/owner.json
"""
import sys, os, json, subprocess, glob, argparse
import numpy as np

SR = 16000
FRAME = int(0.025 * SR)   # 25ms
HOP = int(0.010 * SR)     # 10ms


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 'ffmpeg'


def decode(path):
    cmd = [ffmpeg_exe(), '-v', 'error', '-i', path, '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-']
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def frames(x):
    n = 1 + max(0, (len(x) - FRAME) // HOP)
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    return x[idx]


def f0_autocorr(fr, fmin=60.0, fmax=400.0):
    """프레임별 F0(Hz). 무성이면 0."""
    fr = fr - fr.mean(axis=1, keepdims=True)
    win = np.hanning(FRAME)
    fr = fr * win
    lag_min, lag_max = int(SR / fmax), int(SR / fmin)
    out = np.zeros(len(fr))
    # FFT 자기상관
    nfft = 1 << (2 * FRAME - 1).bit_length()
    spec = np.fft.rfft(fr, nfft, axis=1)
    ac = np.fft.irfft(spec * np.conj(spec), nfft, axis=1)[:, :FRAME]
    ac0 = ac[:, 0] + 1e-9
    seg = ac[:, lag_min:lag_max + 1] / ac0[:, None]
    best = seg.argmax(axis=1)
    peak = seg[np.arange(len(seg)), best]
    voiced = peak > 0.45
    out[voiced] = SR / (lag_min + best[voiced])
    return out


def runs(mask):
    """True 구간 [(start, end)) 목록(프레임 단위)."""
    res, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            res.append((start, i)); start = None
    if start is not None:
        res.append((start, len(mask)))
    return res


def analyze(x):
    fr = frames(x)
    rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    floor = np.percentile(db, 10)
    ceil = np.percentile(db, 97)
    thr = floor + 0.35 * (ceil - floor)  # 잡음 바닥과 최대 사이 35% 지점
    speech = db > thr
    # 80ms 미만 구멍 메우기, 80ms 미만 발화 버리기
    for s, e in runs(~speech):
        if e - s < 8:
            speech[s:e] = True
    for s, e in runs(speech):
        if e - s < 8:
            speech[s:e] = False

    f0 = f0_autocorr(fr)
    f0[~speech] = 0
    # 옥타브 오류 억제: 중앙값의 0.5배 미만·2배 초과는 버린다
    v = f0[f0 > 0]
    if len(v) < 20:
        return None
    med = np.median(v)
    f0[(f0 > 0) & ((f0 < med * 0.55) | (f0 > med * 1.9))] = 0
    v = f0[f0 > 0]

    # 쉼
    pauses = [(e - s) * 10 for s, e in runs(~speech)]
    total_speech_s = speech.sum() * 0.01
    # 말속도(음절 핵): 발화 구간에서 포락선(저역 통과) 피크
    env = rms.copy()
    k = np.hanning(9); k /= k.sum()
    env = np.convolve(env, k, mode='same')
    peaks = 0
    for s, e in runs(speech):
        seg = env[s:e]
        if len(seg) < 5:
            continue
        loc = (seg[1:-1] > seg[:-2]) & (seg[1:-1] >= seg[2:]) & (seg[1:-1] > 0.25 * seg.max())
        # 60ms 안의 이중 피크 합치기
        idx = np.where(loc)[0]
        last = -100
        for i in idx:
            if i - last >= 6:
                peaks += 1; last = i
    # 하강조·문장 끝
    decl, finals = [], []
    for s, e in runs(speech):
        if e - s < 60:  # 0.6s 미만 구간은 제외
            continue
        seg = f0[s:e]
        vv = seg[seg > 0]
        if len(vv) < 20:
            continue
        third = len(seg) // 3
        a, b = seg[:third], seg[-third:]
        a, b = a[a > 0], b[b > 0]
        if len(a) > 5 and len(b) > 5:
            decl.append((b.mean() - a.mean()) / a.mean() * 100)
        tail = seg[-30:]
        t = np.where(tail > 0)[0]
        if len(t) >= 8:
            slope = np.polyfit(t * 0.01, tail[t], 1)[0]  # Hz/s
            finals.append(slope)
    st = lambda arr: 12 * np.log2(arr)
    return {
        'duration_s': round(len(x) / SR, 1),
        'speech_s': round(total_speech_s, 1),
        'f0_hz': {'median': round(float(med), 1), 'p10': round(float(np.percentile(v, 10)), 1), 'p90': round(float(np.percentile(v, 90)), 1)},
        'f0_semitone_range_p10_p90': round(float(st(np.percentile(v, 90)) - st(np.percentile(v, 10))), 1),
        'syllables_per_s': round(peaks / total_speech_s, 2) if total_speech_s else None,
        'pauses_ms': pauses,
        'declination_pct': decl,
        'final_slopes_hz_per_s': finals,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='+')
    ap.add_argument('--out', default='profiles/owner.json')
    args = ap.parse_args()
    files = []
    for p in args.inputs:
        files += sorted(glob.glob(os.path.join(p, '*'))) if os.path.isdir(p) else [p]
    files = [f for f in files if f.lower().endswith(('.m4a', '.mp3', '.wav', '.ogg', '.aac', '.webm'))]
    per, pauses, decl, finals, f0s, rates, dur = [], [], [], [], [], [], 0.0
    for f in files:
        try:
            r = analyze(decode(f))
        except Exception as e:
            print('skip', os.path.basename(f), e, file=sys.stderr); continue
        if not r:
            continue
        per.append({'file': os.path.basename(f)[:8], 'duration_s': r['duration_s'], 'f0_median': r['f0_hz']['median'], 'syl_per_s': r['syllables_per_s']})
        pauses += r['pauses_ms']; decl += r['declination_pct']; finals += r['final_slopes_hz_per_s']
        f0s.append(r['f0_hz']); rates.append(r['syllables_per_s'] or 0); dur += r['duration_s']
    if not per:
        sys.exit('분석할 수 있는 녹음이 없습니다')
    pauses = np.array(pauses)
    short = pauses[(pauses >= 150) & (pauses <= 600)]
    long_ = pauses[(pauses > 600) & (pauses <= 3000)]
    finals = np.array(finals)
    med_f0 = float(np.median([f['median'] for f in f0s]))
    profile = {
        'source': '본인 녹음 %d개, 총 %.0f초 (원본은 저장소에 없음)' % (len(per), dur),
        'f0_hz': {'median': round(med_f0, 1), 'p10': round(float(np.median([f['p10'] for f in f0s])), 1), 'p90': round(float(np.median([f['p90'] for f in f0s])), 1)},
        'syllables_per_s': round(float(np.median([r for r in rates if r])), 2),
        'pause_ms': {
            'phrase_median': int(np.median(short)) if len(short) else None,
            'phrase_p75': int(np.percentile(short, 75)) if len(short) else None,
            'sentence_median': int(np.median(long_)) if len(long_) else None,
            'per_minute': round(len(pauses[pauses >= 150]) / (dur / 60), 1) if dur else None,
        },
        'declination_pct': round(float(np.median(decl)), 1) if decl else None,
        'final_fall_ratio': round(float((finals < -20).mean()), 2) if len(finals) else None,
        'final_rise_ratio': round(float((finals > 20).mean()), 2) if len(finals) else None,
        'files': per,
    }
    # 엔진 파라미터로 번역 (ko-voice.js applyProfile이 읽는 부분)
    ref_rate = 5.5  # 한국어 낭독 평균 근사(음절/초)
    engine = {
        'rate': round(min(1.3, max(0.8, profile['syllables_per_s'] / ref_rate)), 2) if profile['syllables_per_s'] else 1.0,
        'pause': {
            'ip': int(np.clip(profile['pause_ms']['sentence_median'] or 480, 300, 900)),
            'comma': int(np.clip((profile['pause_ms']['phrase_median'] or 240), 150, 450)),
            'conj': int(np.clip((profile['pause_ms']['phrase_median'] or 200) * 0.85, 120, 400)),
            'weak': int(np.clip((profile['pause_ms']['phrase_median'] or 110) * 0.5, 80, 200)),
        },
        'declination': round(float(np.clip(-(profile['declination_pct'] or 6) / 100, 0.02, 0.15)), 3),
        'questionRise': 1.08,
    }
    profile['engine'] = engine
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fp:
        json.dump(profile, fp, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in profile.items() if k != 'files'}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
