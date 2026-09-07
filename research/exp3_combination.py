"""전체 조합 시험 — ko-voice(정규화+문장분리+쉼계획) × 1/f 요동 × CosyVoice2
  ① 통째로 합성 (CosyVoice2 기본)
  ② 문장별 합성 + 우리 쉼 계획 (고정값)
  ③ 문장별 합성 + 우리 쉼 계획 + 1/f 요동
"""
import sys, os, time, json, subprocess, resource
R='/tmp/claude-0/-home-user-militaryapplyhelper/3cdcc1ab-2560-5ce9-87a4-c23e74e7dd5a'
sys.path.insert(0, R); sys.path.insert(0, R+'/cosy'); sys.path.insert(0, R+'/cosy/third_party/Matcha-TTS')
import torch, torchaudio, librosa, numpy as np, soundfile as sf
torch.set_num_threads(4)
from cosyvoice.cli.cosyvoice import CosyVoice2
import pink

RAW = ("예비군 훈련, 매년 받으면서도 통합방위법이 뭔지 정확히 아는 사람은 드뭅니다. "
       "오늘은 그걸 30초 만에 정리해 드릴게요. 핵심은 딱 세 가지입니다. "
       "첫째, 통합방위사태는 갑종, 을종, 병종으로 나뉩니다. "
       "둘째, 선포권자가 사태별로 다릅니다. 셋째, 이 두 가지만 알면 문제의 절반은 풀립니다.")

ENG='/home/user/korean-voice/public/ko-voice.js'
js = ("const K=require(process.argv[1]);const p=K.prepare(process.argv[2],{});"
      "process.stdout.write(JSON.stringify(p));")
plan = json.loads(subprocess.run(['node','-e',js,ENG,RAW],capture_output=True,text=True).stdout)
NORM = plan['normalized']
# 문장 단위 + 계획된 쉼
units=[]
for s in plan['sentences']:
    txt=' '.join(c['text'] for c in s['chunks'] if c['text'])
    if not txt.strip(): continue
    pause=max((c['pause'] for c in s['chunks']), default=500)
    units.append({'text':txt,'pause_ms':int(pause),'rate':1.0})
print(f'[문장 {len(units)}개] 고정 쉼: {[u["pause_ms"] for u in units]}', flush=True)

speech,_ = librosa.load('/home/user/korean-voice/out/sexy/S1.wav', sr=16000)
P=R+'/prompt16k.wav'; sf.write(P, speech, 16000)
PT="자, 편하게 기대 봐. 오늘은 조금 천천히 가도 괜찮아. 그거 알아? 서두르는 사람이 제일 늦게 도착해."
m = CosyVoice2(R+'/models/CosyVoice2-0.5B', load_jit=False, load_trt=False, fp16=False)
SR = m.sample_rate

def one(text):
    ch=[]
    for o in m.inference_zero_shot(text, PT, P, stream=False): ch.append(o['tts_speech'])
    return torch.cat(ch,dim=1)

def joined(us, tag, out):
    t=time.time(); parts=[]
    for u in us:
        parts.append(one(u['text']))
        parts.append(torch.zeros(1, int(SR*u['pause_ms']/1000)))
    w=torch.cat(parts,dim=1); dt=time.time()-t; dur=w.shape[1]/SR
    torchaudio.save(out, w, SR)
    print(f'RESULT|{tag}|{dt:.1f}|{dur:.1f}|{dt/dur:.2f}', flush=True)

t=time.time(); w=one(NORM); dur=w.shape[1]/SR
torchaudio.save(R+'/f1_whole.wav', w, SR)
print(f'RESULT|①통째|{time.time()-t:.1f}|{dur:.1f}|{(time.time()-t)/dur:.2f}', flush=True)

import copy
joined(copy.deepcopy(units), '②문장별_고정쉼', R+'/f2_fixed.wav')
u3 = pink.apply(copy.deepcopy(units), pause_pct=0.22, rate_pct=0.0, seed=7)
print(f'[1/f 요동 쉼] {[u["pause_ms"] for u in u3]}', flush=True)
joined(u3, '③문장별_1f요동', R+'/f3_pink.wav')
