"""이어붙이기 방식 비교 — 무엇이 낭독의 길이와 음량을 지키는가 (research/toolkit/2026-09-13-audio-pipeline.json).

    python3 research/toolkit/bench_concat_loudnorm.py --cache <조각 mp3 가 있는 폴더> [--out <작업 폴더>]

`-c copy` 는 조각마다 mp3 프레임 1개(1152샘플 = 24kHz 에서 48ms) 패딩을 남겨 길이가 밀린다 — 이 자가 그걸 잡는다.
필요한 것: numpy · soundfile · pyloudnorm · imageio-ffmpeg (전부 pip · 상업 이용 가능 라이선스).
"""
import argparse, glob, json, os, re, subprocess, time
import numpy as np, soundfile as sf, pyloudnorm as pyln, imageio_ffmpeg

ap = argparse.ArgumentParser()
ap.add_argument('--cache', required=True, help='문장별 mp3 가 있는 폴더(server.py 의 CACHE_DIR/<voice>)')
ap.add_argument('--out', default=None, help='작업 폴더(기본: --cache 아래 bench/)')
ap.add_argument('--pauses', default='480,240,480,200,480')
A = ap.parse_args()
SC = A.out or os.path.join(A.cache, 'bench'); os.makedirs(SC, exist_ok=True)
FF = imageio_ffmpeg.get_ffmpeg_exe()
pieces = sorted(glob.glob(os.path.join(A.cache, '*.mp3')))[:5]
assert len(pieces) >= 2, '조각 mp3 가 없다: ' + A.cache
PAUSES = [int(x) for x in A.pauses.split(',')][:len(pieces)]
SR = 24000

def dec(p):
    w = p + '.d.wav'
    subprocess.run([FF, '-v', 'error', '-y', '-i', p, '-ar', str(SR), '-ac', '1', w], check=True)
    x, sr = sf.read(w); return x.astype(np.float32), sr

def lufs(x, sr): return round(float(pyln.Meter(sr).integrated_loudness(x)), 2)
def peak(x): return round(float(20*np.log10(max(1e-9, np.abs(x).max()))), 2)

# 기준: 조각 오디오 + 정확한 쉼
ref, expect = [], 0.0
for p, ms in zip(pieces, PAUSES):
    x, _ = dec(p); ref.append(x); expect += len(x)/SR
    ref.append(np.zeros(int(SR*ms/1000), dtype=np.float32)); expect += ms/1000
ref = np.concatenate(ref)
out = {'expect_s': round(expect, 3), 'ref_lufs': lufs(ref, SR)}

def sil(ms):
    o = os.path.join(SC, f's{ms}.mp3')
    if not os.path.exists(o):
        subprocess.run([FF,'-v','error','-y','-f','lavfi','-i',f'anullsrc=r={SR}:cl=mono','-t',f'{ms/1000:.3f}',
                        '-codec:a','libmp3lame','-b:a','48k','-f','mp3',o], check=True)
    return o
parts = []
for p, ms in zip(pieces, PAUSES): parts += [p, sil(ms)]
lst = os.path.join(SC,'l2.txt'); open(lst,'w').write(''.join("file '%s'\n" % p for p in parts))

def run(name, args, target):
    t = time.time(); subprocess.run(args, check=True); el = time.time()-t
    x, sr = dec(target)
    out[name] = {'s': round(len(x)/sr,3), 'drift_ms': round((len(x)/sr-expect)*1000,1),
                 'lufs': lufs(x,sr), 'peak_db': peak(x), 'cpu_s': round(el,2), 'bytes': os.path.getsize(target)}

A = os.path.join(SC,'a.mp3'); run('A_copy', [FF,'-v','error','-y','-f','concat','-safe','0','-i',lst,'-c','copy','-f','mp3',A], A)
B = os.path.join(SC,'b.mp3'); run('B_reencode', [FF,'-v','error','-y','-f','concat','-safe','0','-i',lst,'-ar',str(SR),'-ac','1','-codec:a','libmp3lame','-b:a','48k','-f','mp3',B], B)
C = os.path.join(SC,'c.mp3'); run('C_reencode_loudnorm', [FF,'-v','error','-y','-f','concat','-safe','0','-i',lst,
      '-af','loudnorm=I=-16:TP=-1.5:LRA=11','-ar',str(SR),'-ac','1','-codec:a','libmp3lame','-b:a','48k','-f','mp3',C], C)
# D: 우리가 PCM 을 직접 잇고(쉼은 정확한 샘플 수) 한 번만 인코딩
d_wav = os.path.join(SC,'d.wav'); sf.write(d_wav, ref, SR)
D = os.path.join(SC,'d.mp3'); run('D_pcm_join', [FF,'-v','error','-y','-i',d_wav,'-ar',str(SR),'-ac','1','-codec:a','libmp3lame','-b:a','48k','-f','mp3',D], D)
print(json.dumps(out, ensure_ascii=False))
