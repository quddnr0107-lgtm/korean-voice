#!/usr/bin/env python3
"""재조립 합성기 — 우리 운율 엔진(ko-voice.js) × Supertonic 3(슈퍼톤, 한국어 ONNX, CPU).

무엇을 하나:
  1. 텍스트 → ko-voice.js `prepare()`: 정규화(숫자·단위·약어) + 문장/구 나누기 + 쉼 길이 + 감정별 속도 + 태그
  2. 문장(또는 <break>로 자른 조각)마다 Supertonic으로 합성 — 문장 안 억양은 모델이, 문장 사이 쉼·속도는 우리가
  3. 목소리 = 스타일 벡터의 가중 평균 (F1:0.6,F3:0.4 처럼) → 누구의 목소리도 아닌 새 목소리. --save-style로 저장해 재사용
  4. 조각 앞뒤 무음 다듬기 + 10ms 페이드 + 계획된 쉼 삽입 + 음량 정규화 → WAV(44.1kHz) / MP3

표현 태그: [웃음]→<laugh> [한숨]→<sigh> [숨]→<breath>  (Supertonic 인라인 태그), <break time="0.5s"/>·[기쁨] 등은 엔진 태그.

준비:
  pip install onnxruntime numpy soundfile librosa PyYAML imageio-ffmpeg
  Supertonic 자산: https://huggingface.co/Supertone/supertonic-3 (onnx/, voice_styles/)  +  예제 helper.py (github supertone-inc/supertonic py/)
쓰는 법:
  python3 tools/synth-supertonic.py --assets /path/supertonic3 --helper /path/supertonic3/py \
      --style F1:0.6,F3:0.4 --emotion warm --profile public/profiles/owner.json \
      --text "2026년 9월 접수는 10월 6일 마감입니다. [기쁨] 합격을 축하드려요!" --out out/작품.wav --mp3
"""
import argparse, json, os, subprocess, sys, time
import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, '..', 'public', 'ko-voice.js')
EXPR = {'[웃음]': ' ⌁laugh ', '[한숨]': ' ⌁sigh ', '[숨]': ' ⌁breath ', '<laugh>': ' ⌁laugh ', '<sigh>': ' ⌁sigh ', '<breath>': ' ⌁breath '}


def plan(text, emotion=None, profile=None, rate=1.0):
    """ko-voice.js prepare()를 Node로 호출해 운율 계획을 받는다."""
    for k, v in EXPR.items():
        text = text.replace(k, v)
    js = """
const K = require(process.argv[1]);
const a = JSON.parse(process.argv[2]);
if (a.profile) K.applyProfile(a.profile);
const p = K.prepare(a.text, { emotion: a.emotion || undefined, rate: a.rate || 1 });
process.stdout.write(JSON.stringify(p));
"""
    arg = json.dumps({'text': text, 'emotion': emotion, 'profile': profile, 'rate': rate}, ensure_ascii=False)
    r = subprocess.run(['node', '-e', js, os.path.abspath(ENGINE), arg], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('엔진 오류: ' + r.stderr[-500:])
    return json.loads(r.stdout)


def units_from_plan(p):
    """문장 단위(억양 연속성 유지). 명시적 쉼(⏸ → 빈 조각)이 있으면 거기서 자른다.
    반환: [{text, pause_ms, rate, emph}]"""
    out = []
    for s in p['sentences']:
        buf, rate_acc, emph = [], [], False
        for c in s['chunks']:
            if not c['text']:
                if buf:
                    out.append({'text': ' '.join(buf), 'pause_ms': c['pause'], 'rate': float(np.mean(rate_acc)), 'emph': emph, 'hard': True}); buf, rate_acc, emph = [], [], False
                elif out:
                    out[-1]['pause_ms'] = max(out[-1]['pause_ms'], c['pause']); out[-1]['hard'] = True
                continue
            buf.append(c['text']); rate_acc.append(c['rate']); emph = emph or c.get('emph', False)
        if buf:
            last = s['chunks'][-1]
            out.append({'text': ' '.join(buf), 'pause_ms': last['pause'], 'rate': float(np.mean(rate_acc)), 'emph': emph})
    for u in out:
        t = u['text'].replace('⌁laugh', '<laugh>').replace('⌁sigh', '<sigh>').replace('⌁breath', '<breath>')
        u['text'] = ' '.join(t.split())
    return [u for u in out if u['text'].strip(' ,.')]


def group_units(units, max_chars=90):
    """짧은 문장들을 호흡 묶음으로 합친다 — 문장마다 따로 합성하면 '자! / 집중. / 오늘…'처럼 뚝뚝 끊긴다.
    묶음 안의 문장 사이 쉼은 모델이 문장부호를 보고 자연스럽게 내고, 묶음 사이 쉼만 우리가 넣는다.
    긴 쉼(<break>·문단, 600ms 이상)이나 글자 수 초과에서만 끊는다."""
    out = []
    for u in units:
        if out and not out[-1].get('hard') and len(out[-1]['text']) + len(u['text']) + 1 <= max_chars and out[-1]['pause_ms'] < 600:
            prev = out[-1]
            prev['text'] = prev['text'].rstrip() + ' ' + u['text']
            prev['pause_ms'] = u['pause_ms']
            prev['rate'] = (prev['rate'] + u['rate']) / 2
            prev['emph'] = prev['emph'] or u['emph']
            prev['hard'] = u.get('hard', False)
        else:
            out.append(dict(u))
    return out


def load_blend(helper, styles_dir, spec):
    """'F1:0.6,F3:0.4' → 가중 평균 스타일. 가중치 합은 1로 정규화."""
    parts = []
    for item in spec.split(','):
        name, _, w = item.partition(':')
        parts.append((name.strip(), float(w) if w else 1.0))
    total = sum(w for _, w in parts)
    ttl = dp = None
    for name, w in parts:
        path = name if name.endswith('.json') else os.path.join(styles_dir, name + '.json')
        st = helper.load_voice_style([path])
        ttl = st.ttl * (w / total) if ttl is None else ttl + st.ttl * (w / total)
        dp = st.dp * (w / total) if dp is None else dp + st.dp * (w / total)
    return helper.Style(ttl.astype(np.float32), dp.astype(np.float32)), parts


def save_style(style, path, parts):
    ref = json.load(open(os.path.join(os.path.dirname(path) if os.path.dirname(path) else '.', 'F1.json'))) if False else None
    obj = {
        'style_ttl': {'data': style.ttl.tolist(), 'dims': list(style.ttl.shape), 'type': 'float32'},
        'style_dp': {'data': style.dp.tolist(), 'dims': list(style.dp.shape), 'type': 'float32'},
        'metadata': {'source_file': 'blend:' + ','.join(f'{n}:{w}' for n, w in parts), 'source_sample_rate': 44100, 'target_sample_rate': 44100, 'extracted_at': time.strftime('%Y-%m-%dT%H:%M:%S')},
    }
    json.dump(obj, open(path, 'w'), ensure_ascii=False)


def trim(wav, sr, thresh_db=-45.0, pad_ms=30):
    """앞뒤 무음 제거(에너지 기준) + 짧은 여유."""
    if wav.ndim > 1:
        wav = wav[0]
    frame = int(sr * 0.01)
    n = len(wav) // frame
    if n < 3:
        return wav
    rms = np.sqrt((wav[:n * frame].reshape(n, frame) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    idx = np.where(db > db.max() + thresh_db)[0]
    if not len(idx):
        return wav
    pad = int(sr * pad_ms / 1000)
    s = max(0, idx[0] * frame - pad); e = min(len(wav), (idx[-1] + 1) * frame + pad)
    return wav[s:e]


def fade(wav, sr, ms=10):
    k = min(len(wav) // 2, int(sr * ms / 1000))
    if k > 0:
        ramp = np.linspace(0, 1, k, dtype=np.float32)
        wav[:k] *= ramp; wav[-k:] *= ramp[::-1]
    return wav


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--assets', required=True, help='supertonic-3 폴더 (onnx/, voice_styles/)')
    ap.add_argument('--helper', required=True, help='supertonic 예제 helper.py 가 있는 폴더')
    ap.add_argument('--text'); ap.add_argument('--text-file')
    ap.add_argument('--style', default='F1', help='F1 또는 F1:0.6,F3:0.4 (스타일 섞기) 또는 저장한 json 경로')
    ap.add_argument('--save-style', help='섞은 스타일을 Supertonic 호환 json으로 저장')
    ap.add_argument('--emotion')
    ap.add_argument('--profile', default=os.path.join(HERE, '..', 'public', 'profiles', 'owner.json'), help='화자 프로필 json (쉼·속도 측정값). 기본: public/profiles/owner.json')
    ap.add_argument('--no-profile', action='store_true', help='프로필 없이 엔진 기본 쉼·속도')
    ap.add_argument('--rate', type=float, default=1.0); ap.add_argument('--base-speed', type=float, default=1.05)
    ap.add_argument('--steps', type=int, default=8); ap.add_argument('--out', default='out/synth.wav'); ap.add_argument('--mp3', action='store_true')
    ap.add_argument('--gain-db', type=float, default=-1.0, help='피크 정규화 목표(dBFS)')
    ap.add_argument('--group-chars', type=int, default=90, help='짧은 문장을 이 글자 수까지 한 호흡으로 합쳐 합성 (0=문장마다)')
    ap.add_argument('--bright-db', type=float, default=0.0, help='3kHz 위 고역을 올려 밝게 (예 4)')
    ap.add_argument('--punch', type=float, default=0.0, help='0~1. 소프트 압축으로 또렷·강하게 (예 0.5)')
    ap.add_argument('--pitch-st', type=float, default=0.0, help='반음 단위 미세 음정 조정 (−2~+2 권장; 크게 주면 인위적)')
    a = ap.parse_args()
    text = a.text or open(a.text_file, encoding='utf-8').read()
    sys.path.insert(0, a.helper)
    import helper  # noqa: E402
    tts = helper.load_text_to_speech(os.path.join(a.assets, 'onnx'), False)
    sr = tts.sample_rate
    style, parts = load_blend(helper, os.path.join(a.assets, 'voice_styles'), a.style)
    if a.save_style:
        save_style(style, a.save_style, parts)
    profile = json.load(open(a.profile, encoding='utf-8')) if (a.profile and not a.no_profile and os.path.exists(a.profile)) else None
    # 문단(줄) 단위로 계획: 문단 경계는 항상 호흡을 끊는다. 태그 없는 문단은 앞 문단의 감정을 잇는다.
    units, emotions, last_emotion = [], [], a.emotion
    for par in [l for l in text.split('\n') if l.strip()]:
        p = plan(par, a.emotion or (None if '[' in par else last_emotion), profile, a.rate)
        last_emotion = p['emotion']; emotions.append(p['emotion'])
        us = units_from_plan(p)
        if us:
            us[-1]['hard'] = True; us[-1]['pause_ms'] = max(us[-1]['pause_ms'], 600)
        units += us
    p = {'emotion': '→'.join(emotions)}
    n_sent = len(units)
    if a.group_chars > 0:
        units = group_units(units, a.group_chars)
    print(f'감정 {p["emotion"]} · 문장 {n_sent}개 → 호흡 묶음 {len(units)}개 · 목소리 {parts}', flush=True)
    pieces, t0 = [], time.time()
    for i, u in enumerate(units, 1):
        speed = a.base_speed * u['rate'] * (0.95 if u['emph'] else 1.0)
        wav, _ = tts._infer([u['text']], ['ko'], style, a.steps, float(np.clip(speed, 0.7, 1.6)))
        w = fade(trim(np.asarray(wav, dtype=np.float32).reshape(-1), sr), sr)
        pieces.append(w)
        pieces.append(np.zeros(int(sr * u['pause_ms'] / 1000), dtype=np.float32))
        print(f'  [{i}/{len(units)}] {len(w)/sr:4.1f}s  속도 {speed:.2f}  쉼 {u["pause_ms"]}ms  {u["text"][:38]}', flush=True)
    out = np.concatenate(pieces)
    if a.pitch_st:
        import librosa
        out = librosa.effects.pitch_shift(out, sr=sr, n_steps=float(np.clip(a.pitch_st, -3, 3)), bins_per_octave=12).astype(np.float32)
    if a.bright_db:
        # 고역 선반(high-shelf): 3kHz 1차 저역통과를 빼서 고역 성분을 뽑고 더한다
        alpha = float(np.exp(-2 * np.pi * 3000 / sr)); lp = np.empty_like(out); acc = 0.0
        for i in range(len(out)):
            acc = alpha * acc + (1 - alpha) * out[i]; lp[i] = acc
        out = out + (10 ** (a.bright_db / 20) - 1) * (out - lp)
    if a.punch:
        # 소프트 압축: 큰 소리는 눌러 주고 전체를 올려 또렷하게
        k = 1 + 4 * a.punch
        out = np.tanh(k * out / (np.abs(out).max() or 1.0)) / np.tanh(k)
    peak = np.abs(out).max() or 1.0
    out = out / peak * (10 ** (a.gain_db / 20))
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    sf.write(a.out, out, sr)
    print(f'완료: {a.out}  {len(out)/sr:.1f}s  합성 {time.time()-t0:.1f}s', flush=True)
    if a.mp3:
        try:
            import imageio_ffmpeg
            mp3 = os.path.splitext(a.out)[0] + '.mp3'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-y', '-i', a.out, '-codec:a', 'libmp3lame', '-q:a', '2', mp3], check=True)
            print('mp3:', mp3)
        except Exception as e:
            print('mp3 변환 실패:', e)


if __name__ == '__main__':
    main()
