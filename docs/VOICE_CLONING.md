# GPU 학습 없이 "내 목소리"를 얻는 방법 — 조사와 설계

질문: "GPU로 음성 복제 모델을 학습하는 대신, 최소 연산·최소 용량으로 같은 결과(또는 더 나은 결과)를 내는 방법은?"

한·영·일·중 자료를 모아 읽은 결론은 하나다. **2026년의 답은 '학습'이 아니라 '조건 부여(conditioning)'다.**
수십만 시간으로 이미 학습된 공개 제로샷 모델에 **3~12초의 참조 음성**을 넣으면, 개인이 GPU로 며칠 학습한
모델보다 대개 더 자연스럽다. 학습이 필요 없으니 GPU도, 학습 데이터 저장소도 없다.

## 1. 사실 확인 (출처는 맨 아래)

| 사실 | 근거 |
|---|---|
| 제로샷 복제는 3~10초 참조 음성으로 즉시 된다 | GPT-SoVITS(5초), Fish Speech(5~10초), Voxtral TTS(3초), Qwen3-TTS(3초) |
| 한국어를 지원하는 제로샷 모델이 여럿 있다 | CosyVoice 2/3(ko 포함), GPT-SoVITS v2+(ko), Fish Speech(ko), IndexTTS-2(ja/ko 권장), XTTS-v2(ko), X-Voice(30개 언어) |
| 1분 미세조정(few-shot)은 "유사도 향상"용이지 필수가 아니다 | GPT-SoVITS 문서: 5초 제로샷은 거칠고, 1분이면 설득력 있음 |
| 무료 GPU가 있다 | Colab 주 15~30시간, Kaggle 주 30시간(T4/P100), HF ZeroGPU 하루 몇 분 |
| 브라우저에서도 제로샷 복제가 돈다 | Irodori-TTS(일본어, WebGPU 완전 브라우저), F5-TTS ONNX, OuteTTS-WebGPU, Pocket TTS |
| 목소리는 벡터 하나로 요약된다 | WeSpeaker ECAPA-TDNN 화자 임베딩(192~512차원, 수 KB), CosyVoice·XTTS가 임베딩+프롬프트로 조건 부여 |
| CPU 추론은 되지만 느리다 | Kokoro-82M 4코어 CPU에서 실시간의 2~3배; 제로샷 대형 모델은 문장당 수 초(GPU) |
| 서비스형 무료 API는 "커스텀 모델 실행"이 아니다 | Workers AI 등은 정해진 모델만 — 복제 모델을 올릴 수 없다 |

## 2. 설계 — "학습 0 · 저장 1KB + 10초"

```
본인 녹음 15개(368초)
   │  tools/analyze-voice.py      → 운율 프로필(JSON, 수백 바이트)  ← 쉼·하강·말속도 = "말투"
   │  tools/make-prompt-kit.py    → 참조 음성 3~5개(각 4~12초 WAV) ← "음색"   (저장소 밖)
   ▼
[텍스트] → ko-voice.js (정규화 + 운율 계획 + 태그) → 제로샷 모델(참조 음성 조건) → 파형
                                                     ├ 브라우저 WebGPU (F5-TTS/Irodori류 ONNX)
                                                     ├ 무료 GPU 세션 (Colab/Kaggle/ZeroGPU) — 배치로 미리 만들어 둠
                                                     └ Cloudflare Workers AI(MeloTTS) — 복제 없음, 폴백
```

핵심 아이디어 세 가지(자료들을 합쳐 만든 것):

1. **말투와 음색을 분리해서 저장한다.** 음색(누구 목소리인가)은 참조 음성 10초 또는 임베딩 1KB.
   말투(어떻게 읽는가)는 우리가 측정한 프로필(쉼 855/300ms, 하강 −7.7%, 4.95음절/초). 제로샷 모델은 음색은
   잘 베끼지만 말투는 참조 음성의 그 문장 분위기에 끌려간다 — 그래서 말투는 **텍스트 쪽에서**(쉼 표기·문장 나누기·속도)
   우리가 넣어 준다. IndexTTS-2가 "음색과 감정을 따로 제어"하는 것과 같은 방향이다.
2. **참조 음성은 '가장 좋은 10초'를 기계가 고른다.** 제로샷 품질의 절반은 참조 음성 품질이다(잡음·끊김·클리핑·음높이 요동).
   `make-prompt-kit.py`가 SNR 20dB 이상·끊김 없음·음높이 안정 구간을 점수로 고른다.
3. **연산은 '미리' 쓴다.** 실시간 GPU가 없으니, 자주 쓰는 문장(안내문·FAQ)은 무료 GPU 세션에서 배치로 만들어
   MP3로 두고(문장당 수십 KB), 나머지는 브라우저 WebGPU 또는 폴백. 큰 모델 파일은 우리 서버가 아니라
   Hugging Face CDN에서 브라우저가 직접 받아 브라우저 캐시에 둔다 — "저장소 0"이 지켜진다.

## 3. 후보 모델 — 한국어 관점 비교

| 모델 | 한국어 | 참조 길이 | 크기 | 라이선스 | 비고 |
|---|---|---|---|---|---|
| CosyVoice 2 / Fun-CosyVoice3 (알리바바) | ✓ | 3~10초 | 0.5B | Apache-2.0 | 제로샷 검증에 가장 무난, 교차언어 |
| GPT-SoVITS v2Pro (RVC-Boss) | ✓ | 5초(제로샷)/1분(미세조정) | ~1GB | MIT | 한국 커뮤니티 자료 많음, WebUI |
| Fish Speech 1.5 | ✓ | 5~10초 | ~1GB | CC BY-NC-SA | 빠름, 비상업 |
| IndexTTS-2 (빌리빌리) | 중/일/한 권장 | 5초 | 1.5B | Apache-2.0(모델 별도 확인) | 감정·길이 제어, 음색/감정 분리 |
| XTTS-v2 (Coqui) | ✓ | 6~12초 | ~1.8GB | CPML(비상업) | CPU에서도 느리지만 됨 |
| Voxtral TTS (Mistral) | 다국어 | 3초 | 공개 가중치 | 확인 필요 | ElevenLabs Flash 대비 68% 승률(자체 평가) |
| Qwen3-TTS | 10개 언어 | 3초 | — | 확인 필요 | Colab T4 무료로 동작 보고 |
| Supertonic 3 (슈퍼톤, 한국) | ✓ | 복제 없음(프리셋 12개) | 99M | OpenRAIL-M | 브라우저 ONNX, 한국어 품질 좋음 — 음색 복제가 필요 없다면 1순위 |

## 4. 이 저장소에서 지금 되는 것 / 다음 것

- 지금: 운율 프로필 측정(`public/profiles/owner.json`)과 엔진 적용(`KoVoice.applyProfile`), 참조 음성 키트 생성 도구.
- 다음(무료 GPU 세션 한 번이면 됨): Colab에서 CosyVoice2 또는 GPT-SoVITS를 열고 `prompt-kit/prompt-01.wav`와
  받아쓰기를 넣어 안내문 30문장을 배치 합성 → MP3를 이 폴더 `public/audio/`에 두고 실험실에서 재생.
  이때 텍스트는 반드시 `KoVoice.normalize` 결과를 넣는다(제로샷 모델도 숫자 오독은 그대로 낸다).
- 그 다음: 브라우저 WebGPU 경로(F5-TTS ONNX 계열)에 참조 음성을 붙여 서버 없이 실시간.

## 5. 하지 않는 것

- 타인 음성 수집·복제. 본인 음성(동의 = 본인)만 참조로 쓴다. 참조 음성 파일은 저장소에 넣지 않는다.
- "학습이 더 낫다"는 가정. 제로샷 대형 모델 + 좋은 참조 음성이 개인 미세조정보다 나은 경우가 대부분이다.
  1분 미세조정은 제로샷 결과를 들어 본 뒤 유사도가 부족할 때만.

## 출처

- SiliconFlow, Best open-source voice cloning 2026: https://www.siliconflow.com/articles/best-open-source-models-for-voice-cloning
- RBS, 6 tested (2026): https://rarebuildsoftware.com/blog/best-open-source-voice-cloning-2026
- X-Voice (30개 언어 제로샷 교차언어): https://arxiv.org/pdf/2605.05611 · IndexTTS: https://arxiv.org/html/2502.05512v1
- GPT-SoVITS 한국어 문서: https://github.com/RVC-Boss/GPT-SoVITS/blob/main/docs/ko/README.md · 코딩애플 가이드: https://codingapple.com/blog/gpt-sovits-tts-my-voice/
- 日本語: 音声クローン 作り方 2026 https://crystal-method.com/blog/voice-clone-how-to/ · Voxtral TTS(3초 복제) https://www.techno-edge.net/article/2026/03/30/4961.html · オープンソースTTS動向 https://nobdata.co.jp/report/creative_ai/11/
- 中文: 声网 2026 TTS 评测 https://www.shengwang.cn/blog/blogdetail/2026-TTS-evaluation/ · 音色克隆方案对比 https://liudon.com/posts/voice-cloning-solution-comparison/ · IndexTTS-2 https://zhuanlan.zhihu.com/p/2003799994726102039 · 5款模型对比 https://zhuanlan.zhihu.com/p/8603402649
- 브라우저: Irodori-TTS WebGPU https://github.com/ngc-shj/irodori-tts-webgpu · F5-TTS 브라우저 https://github.com/nsarang/voice-cloning-f5-tts · OuteTTS-WebGPU https://huggingface.co/spaces/webml-community/OuteTTS-WebGPU · Pocket TTS https://offlinetts.com/blog/pocket-tts-browser-voice-cloning-guide/
- 화자 임베딩: WeSpeaker https://github.com/wenet-e2e/wespeaker · ECAPA-TDNN 모델 https://huggingface.co/Wespeaker/wespeaker-ecapa-tdnn512-LM
- 무료 GPU: https://aimultiple.com/free-cloud-gpu · Colab vs HF 2026 https://lalatenduswain.medium.com/google-colab-vs-hugging-face-the-definitive-ai-platform-showdown-in-2026-90725f875c55 · Qwen3-TTS Colab https://github.com/Myuniqous/qwen3-tts-voice-cloning-google-collab · VoxCPM2 https://huggingface.co/openbmb/VoxCPM2
- Supertonic: https://github.com/supertone-inc/supertonic
