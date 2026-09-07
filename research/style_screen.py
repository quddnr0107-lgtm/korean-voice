"""1단계 선별 — 스타일 조합 공간을 짧은 구절로 대량 훑는다. 후처리 0(음정이동·EQ·압축 전부 없음).
기계음의 원인이 후처리이므로, 원하는 음색을 '스타일 벡터 자체'에서 찾는다."""
import os, sys, itertools, time, importlib.util
import numpy as np, soundfile as sf, librosa

KV='/home/user/korean-voice'
spec=importlib.util.spec_from_file_location('synth', os.path.join(KV,'tools','synth-supertonic.py'))
synth=importlib.util.module_from_spec(spec); spec.loader.exec_module(synth)
sys.path.insert(0, os.path.join(KV,'st3','py')); import helper

PHRASE="자, 집중하세요. 오늘 핵심은 딱 하나입니다."
F=['F1','F2','F3','F4','F5']
specs=[]
for a in F: specs.append(f'{a}:1.0')
for a,b in itertools.combinations(F,2):
    for w in (0.7,0.5,0.3): specs.append(f'{a}:{w},{b}:{round(1-w,2)}')
for a,b,c in itertools.combinations(F,3):
    specs.append(f'{a}:0.5,{b}:0.3,{c}:0.2'); specs.append(f'{a}:0.34,{b}:0.33,{c}:0.33')
print(f'[선별] 조합 {len(specs)}개 · 후처리 없음', flush=True)

tts=helper.load_text_to_speech(os.path.join(KV,'st3','onnx'), False); sr=tts.sample_rate
t0=time.time()
for sp in specs:
    try:
        style,_=synth.load_blend(helper, os.path.join(KV,'st3','voice_styles'), sp)
        wav,_=tts._infer([PHRASE],['ko'],style,16,1.05)
        y=synth.trim(np.asarray(wav,dtype=np.float32).reshape(-1), sr)
        f0,vo,_=librosa.pyin(y,fmin=80,fmax=400,sr=sr,frame_length=1024)
        v=f0[~np.isnan(f0)]
        if len(v)<10: continue
        cen=float(np.mean(librosa.feature.spectral_centroid(y=y,sr=sr)))
        # 음높이 변동 폭(반음) — 일타강사는 이게 커야 한다
        rng=float(np.percentile(v,90)/np.percentile(v,10))
        semis=12*np.log2(rng)
        # 배음 대 잡음 — 깨끗함
        S=np.abs(librosa.stft(y)); hnr=float(np.mean(S.max(0)/(S.mean(0)+1e-9)))
        print(f'SCR|{sp}|{np.median(v):.1f}|{cen:.0f}|{semis:.2f}|{hnr:.1f}', flush=True)
    except Exception as e:
        print(f'SCR|{sp}|ERR|{str(e)[:40]}', flush=True)
print(f'[끝] {time.time()-t0:.0f}s', flush=True)
