"""tools/hum.py — 우웅 다시 굽기·다시 재기 (모델 없이 잰다 · 2026-10-01).

  python3 test/hum_test.py
  🔴 mp3 대조는 굽기와 **같은 꼴**(24kHz · 48kbps · libmp3lame)로 인코딩한 뒤 다시 풀어 같은 자(hum_tail)에 댄다 —
     「wav 에서 잡히는 우웅이 mp3 를 거쳐도 잡히나」(다시 재기 모드의 전제)를 양성·음성 둘 다로 잰다.
"""
import os, sys, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', 'server'))
import numpy as np  # noqa: E402
import hum as HUM  # noqa: E402

ok = bad = 0
def t(이름, 참):
    global ok, bad
    if 참: ok += 1
    else: bad += 1; print('  ✗', 이름)

# ── 다시뽑기 ──
뽑힌 = []
def 뽑기목록(*값):
    it = iter(값)
    def f():
        v = next(it); 뽑힌.append(v); return v
    return f
우웅 = lambda y: y == '우웅'
y, n, 남음 = HUM.다시뽑기('깨끗', 뽑기목록(), 우웅, 4)
t('처음부터 깨끗하면 다시 안 뽑는다', (y, n, 남음) == ('깨끗', 0, False))
y, n, 남음 = HUM.다시뽑기('우웅', 뽑기목록('우웅', '깨끗', '안뽑힘'), 우웅, 4)
t('우웅이면 깨끗한 것이 나올 때까지만 뽑는다(2번째에 나옴)', (y, n, 남음) == ('깨끗', 2, False))
y, n, 남음 = HUM.다시뽑기('우웅', 뽑기목록('우웅', '우웅', '우웅', '우웅', '안뽑힘'), 우웅, 4)
t('횟수를 다 써도 남으면 남았다고 돌려준다(횟수만큼만 뽑는다)', (y, n, 남음) == ('우웅', 4, True))
y, n, 남음 = HUM.다시뽑기('?', 뽑기목록(), lambda _y: None, 4)
t('못 잰 것(None)은 우웅으로 세지 않는다 — 다시 뽑지도 않는다', (y, n, 남음) == ('?', 0, False))
t('조각이름 — sha1 앞 10자 · 글을 안 드러낸다', HUM.조각이름('비공개 원고 글') == HUM.조각이름('비공개 원고 글') and len(HUM.조각이름('가')) == 10 and '원고' not in HUM.조각이름('비공개 원고 글'))

# ── mp3 를 거쳐도 우웅이 잡히나(양성) · 말소리만이면 안 잡히나(음성) ──
try:
    import parselmouth  # noqa: F401
    import soundfile as sf
    import imageio_ffmpeg
    import voice_shape_k2 as VS
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    sr = 24000; rng = np.random.default_rng(3); tt = np.arange(sr) / sr
    speech = (0.4 * np.sin(2 * np.pi * 200 * tt) * (1 + 0.3 * np.sin(2 * np.pi * 3 * tt)) + 0.15 * rng.standard_normal(sr)).astype(np.float32)
    humw = (0.1 * np.sin(2 * np.pi * 120 * np.arange(sr // 2) / sr)).astype(np.float32)
    def mp3(w):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'x.wav'); sf.write(p, w, sr)
            return subprocess.run([ff, '-v', 'error', '-i', p, '-ar', '24000', '-codec:a', 'libmp3lame', '-b:a', '48k', '-f', 'mp3', 'pipe:1'], check=True, capture_output=True).stdout
    꼬리 = np.concatenate([speech, np.zeros(sr // 20, dtype=np.float32), humw])
    t('[양성] wav 에서 잡히는 우웅 꼬리', VS.hum_tail(꼬리, sr) is True)
    t('[양성] mp3(굽기와 같은 꼴)를 거쳐 다시 풀어도 잡힌다', VS.hum_tail(HUM.mp3_소리(ff, mp3(꼬리), sr), sr) is True)
    t('[음성] 말소리만이면 mp3 를 거쳐도 안 잡힌다', VS.hum_tail(HUM.mp3_소리(ff, mp3(speech), sr), sr) is False)
    try:
        HUM.mp3_소리(ff, b'not mp3', sr); t('깨진 mp3 는 예외(조용히 빈 소리로 안 넘어간다)', False)
    except subprocess.CalledProcessError:
        t('깨진 mp3 는 예외(조용히 빈 소리로 안 넘어간다)', True)
except ImportError as e:
    print(f'  🔴 mp3 대조를 못 쟀다 — {e}. 굽는 러너엔 있다(pip install praat-parselmouth soundfile imageio-ffmpeg)'); bad += 1

print(f"{'✅' if not bad else '🔴'} 우웅 다시 굽기·다시 재기 {ok}/{ok + bad}")
sys.exit(1 if bad else 0)
