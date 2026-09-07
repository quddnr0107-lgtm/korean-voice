import sys, os, time, resource
sys.path.insert(0, '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a/cosy')
sys.path.insert(0, '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a/cosy/third_party/Matcha-TTS')
import torch, torchaudio, librosa
torch.set_num_threads(int(os.environ.get('NT', '4')))
from cosyvoice.cli.cosyvoice import CosyVoice2

MODEL = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a/models/CosyVoice2-0.5B'
PROMPT_WAV = sys.argv[1]                  # 참조 음성 (16kHz 로 변환됨)
PROMPT_TXT = sys.argv[2]                  # 그 음성이 말한 내용
TEXT = sys.argv[3]
OUT = sys.argv[4]

t0 = time.time()
m = CosyVoice2(MODEL, load_jit=False, load_trt=False, fp16=False)
load_s = time.time() - t0
print(f'[load] {load_s:.1f}s', flush=True)

import soundfile as sf
speech, _ = librosa.load(PROMPT_WAV, sr=16000)
prompt = '/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a/prompt16k.wav'
sf.write(prompt, speech, 16000)

t1 = time.time()
chunks = []
for out in m.inference_zero_shot(TEXT, PROMPT_TXT, prompt, stream=False):
    chunks.append(out['tts_speech'])
wav = torch.cat(chunks, dim=1)
synth_s = time.time() - t1
dur = wav.shape[1] / m.sample_rate
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
torchaudio.save(OUT, wav, m.sample_rate)
print(f'[synth] {synth_s:.1f}s · 음성 {dur:.1f}s · RTF {synth_s/dur:.2f} · 최대메모리 {rss:.1f}GB · threads {torch.get_num_threads()}')
