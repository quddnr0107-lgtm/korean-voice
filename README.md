# korean-voice — 한국어 음성 자연화

대형 음성 서비스(일레븐랩스·구글·네이버 클로바)와 **같은 구조**를, **파일 0개·저장소 0개·서버는 Cloudflare
Worker 하나**로 만든 것. (2026-09-03 militaryapplyhelper 저장소의 `korean-voice/` 폴더에서 이력째 옮겨 왔다.)

```
텍스트 ──▶ ① 정규화(normalize) ──▶ ② 운율 계획(prepare) ──▶ ③ 음성 합성 ──▶ ④ 재생
            숫자·단위·약어·기호        문장/구 나누기·쉼·높낮이      a. 신경망(서버, Workers AI)
            "2명"→"두 명"              감정·문장 유형·강조          b. 브라우저(Web Speech) 폴백
                                                                  c. SSML(다른 클라우드 TTS용)
```

| 파일 | 역할 |
|---|---|
| `public/ko-voice.js` | 엔진. 브라우저(`window.KoVoice`)·Node·Worker에서 같은 파일. 외부 의존 0 |
| `public/index.html` · `public/lab.js` | 실험실 — 원문 vs 자연화 A/B, 정규화 diff, 운율 표, 발음 표기, SSML |
| `worker.mjs` | Cloudflare Worker: 정적 실험실 + `POST /api/tts`(신경망 음성, 엣지 캐시) |
| `wrangler.jsonc` | Worker `korean-voice` 설정. AI 바인딩만 있고 KV·R2·D1 없음 |
| `test/` | 정규화·음운 변동·운율·태그·API·프로필 회귀 테스트 (`npm test`) |
| `tools/analyze-voice.py` | 본인 녹음 → 운율 프로필(`public/profiles/owner.json`, 파생 수치만). 녹음 원본은 저장소에 없다 |
| `tools/make-prompt-kit.py` | 본인 녹음 → 제로샷 복제용 참조 음성 3~5개(`prompt-kit/`, gitignore) |
| `docs/VOICE_CLONING.md` | GPU 학습 없이 목소리를 얻는 방법 — 한·영·일·중 조사와 설계 |

## 왜 "음성 수집"이 아니라 이 구조인가

- 목소리는 생체정보다. 웹에 올라온 사람들의 음성을 동의 없이 모으는 것은 개인정보보호법상 민감정보 처리다.
  "공익"이라도 **당사자가 명시적으로 허용한 라이선스**가 있을 때만 쓸 수 있다(아래 데이터셋 목록).
- 이 환경엔 GPU·학습 파이프라인이 없고, 학습한 모델을 두는 순간 "파일·저장소 최소"가 깨진다.
- 대형 서비스의 자연스러움은 절반이 **글을 읽는 방식**(정규화·운율)에서, 나머지가 **신경망 음향 모델**에서 온다.
  전자는 공개 언어학 규칙으로 직접 만들었고, 후자는 이미 공개 허용 데이터로 학습된 모델을 **호출**한다.
  이게 "그들과 같은 형식으로 우리가 하되 뺏어오지 않는" 방법이다.

## 적용한 방법 — 엔진 함수 ↔ 근거

| # | 방법 | 함수 | 근거 |
|---|------|------|------|
| 1 | 수 체계 분기: 고유어(명·개·살·시·시간·번…) vs 한자어(년·월·일·분·원·개월…), 관형형(한·두·세·네·스무) | `readWithUnit` | 한국어 이중 수 체계. TTS 오독 1순위 |
| 2 | 달 이름 예외 6월→유월, 10월→시월, `제6회`→제육 회 | `readWithUnit` | 표준 발음·표기 |
| 3 | 큰 수 환산 150만원→백오십만 원, 1만→만, 1억→일억, 콤마 제거 | `readSino` | 관용 읽기 |
| 4 | 날짜·시각·전화·범위(~)·소수·퍼센트·영하·분수/월일 판별 | `normalize` | 숫자 음역 모호성 처리 연구 |
| 5 | 영문 약어 사전(KATUSA·ROTC·TOEIC…) + 대문자 철자 읽기 폴백 | `ABBR`/`spellLetters` | 엔진별 영문 처리 편차 제거 |
| 6 | 기호를 쉼·낱말로: `·`→쉼표, `/`→쉼표, `&`→그리고, 괄호→쉼표, 이모지·마크다운·URL 제거 | `normalize` | 소리로 낼 수 없는 것을 미리 치움 |
| 7 | 문장 경계 검출(문장부호 없이 이어진 `-습니다/-요/-죠`도 끊음) | `splitSentences` | IP(억양구) 단위 |
| 8 | 강세구(AP) 나누기: 쉼표·연결어미(-고/-며/-면/-서/-는데/-지만…)·긴 구의 조사 뒤 | `phraseSentence` | K-ToBI: IP·AP 두 층위, 운율구 경계 예측 연구 |
| 9 | 쉼 길이: 문장 끝 480ms, 의문 520, 쉼표 240, 연결어미 200, 약한 경계 110 | `PAUSE` | IP 경계 = 긴 쉼 + 말끝 늘림 |
| 10 | 하강조(declination): 문장 첫 구 +3%, 끝으로 갈수록 −6%까지 | `prepare` | 억양구 안 F0 하강 |
| 11 | 말끝 늘림: 마지막 구 속도 ×0.93 | `prepare` | final lengthening |
| 12 | 경계성조: 의문문 끝 높이 +8%(H%), 감탄 +5%·조금 크게, 요청은 조금 느리게 | `sentenceType` | K-ToBI 경계성조(L%·H%·LH%…) |
| 13 | 초점·강조: 반드시·절대·마감·필수 등 앞에 130ms 쉼, 속도 ×0.9, 높이 +4% | `EMPH` | 한국어 초점은 AP 첫머리 상승 + 앞 쉼 |
| 14 | 감정 프리셋: 기쁨(빠르고 높게)·슬픔(느리고 낮고 작게)·단호(가장 높고 빠르게)·차분·따뜻·긴급 | `EMOTIONS` | 한국어 감정 음성 연구: F0·강도·속도 순서(화남>기쁨>슬픔) |
| 15 | 감정 자동 감지(낱말 사전, 보수적) + 수동 지정 | `detectEmotion` | 축하/합격→기쁨, 탈락/걱정→위로 |
| 16 | 입력 표기 호환: `<break time="0.5s"/>`, `...`, `[기쁨]`/`[joy]` 같은 감정 태그(구간별 전환) | `parseTags` | 일레븐랩스 v2 break·v3 audio tags, SSML |
| 17 | 음운 변동(선택): 연음·비음화·유음화·경음화·구개음화·ㅎ 축약/탈락·겹받침·ㅢ | `pronounce` | 국립국어원 표준 발음법 |
| 18 | 신경망 음성: 문장 단위 요청 → 첫 소리 빠름 + 엣지 캐시 적중, 다음 문장 미리 받기, Web Audio 재생(blob URL 불필요) | `speakNeural` | 스트리밍형 TTS 서비스의 청크 방식 |
| 19 | 자동 폴백: 서버 없음/실패 → 브라우저 음성. 목소리 순위(Google·Natural·Yuna·SunHi), 170자 이하 조각, 조각 사이 쉼 | `speakAuto`/`speak` | Chrome 200자·15초 끊김 회피 |
| 20 | SSML 출력: `<s>`·`<break>`·`<prosody rate/pitch/volume>`·`<emphasis>` | `toSSML` | Google/Azure/Clova 공통 부분집합 |

## 쓰는 법

```js
// 브라우저 — 서버가 있으면 신경망, 없으면 브라우저 음성
KoVoice.speakAuto('[따뜻] 입영 전날이네요... 긴장되죠? <break time="0.6s"/> 준비물은 신분증 2부예요.');
// 분석만
const plan = KoVoice.prepare(text); // { emotion, normalized, sentences[{type, emotion, chunks[{text,pause,rate,pitch,volume}]}] }
// 다른 클라우드 TTS
const ssml = KoVoice.toSSML(text, { emotion: 'calm' });
```

```bash
npm test                 # 회귀 테스트 (외부 의존 없음)
npm run dev              # 로컬: http://localhost:8787  (AI 바인딩은 --remote 일 때만 실제 호출)
npm run deploy           # Cloudflare Worker "korean-voice" 배포 → https://korean-voice.<account>.workers.dev
```

## 배포 후 확인 절차 (머지 = 배포가 아니다)

1. `GET /api/tts` → `{"available":true}` 인가. false면 AI 바인딩이 안 붙은 것.
2. `POST /api/tts {"text":"2명이 6월 10일에 왔어요"}` → `Content-Type: audio/mpeg` 인가.
   502 `synthesis_failed`면 Workers AI MeloTTS가 한국어 코드('kr'·'ko')를 안 받는 것 — 그때는
   아래 "다음 단계"의 Supertonic 경로로 간다. 실험실은 자동으로 브라우저 음성으로 폴백하니 화면은 안 깨진다.
3. 실험실에서 "숫자 지옥" 예시를 **원문 그대로** vs **자연스럽게** 들어 본다. 오독이 0건이어야 한다.

## 내 목소리 — 측정해서 넣은 것

본인 녹음 15개(368초)를 `tools/analyze-voice.py`로 재서 얻은 말투 수치(`public/profiles/owner.json`):
음높이 중앙 222Hz(155~308), 4.95음절/초, 구 사이 쉼 300ms, 문장 사이 855ms, 문장 안 하강 −7.7%, 문장 끝 상승 57%(대화체).
실험실의 "내 말투 프로필 적용"을 켜면 엔진의 쉼·하강·속도가 이 값으로 바뀐다(`KoVoice.applyProfile`).
음색(목소리 자체)은 `docs/VOICE_CLONING.md`의 제로샷 경로로 — 학습 없이 참조 음성 10초로.

## 공개 허용 한국어 음성 데이터 (직접 학습할 때만 필요)

"사람들이 공개적으로 내놓은 음성"은 이런 형태로 존재한다. 라이선스가 곧 동의다 — 목록 밖의 음성은 쓰지 않는다.

| 데이터 | 규모 | 라이선스 | 비고 |
|---|---|---|---|
| KSS (Korean Single Speaker) | 12시간, 여성 1인 | CC BY-NC-SA 4.0 | 비상업. 한국어 TTS 연구의 기준선 |
| Zeroth-Korean | 51시간, 105명 | CC BY 4.0 | 상업 가능. 인식용이라 낭독 품질 편차 |
| Mozilla Common Voice (ko) | 수십 시간, 다수 화자 | CC0 | 상업 가능. 화자 다양성 최고, 품질 편차 |
| AI Hub 감성·다화자·낭독 음성 | 수천 시간 | AI Hub 이용약관(국내·가입·목적 신고) | 국비 구축. 학습엔 좋지만 재배포 불가 |
| FLEURS / MLS 등 다국어 | 소량 | CC BY | 검증용 |

이걸로 직접 학습하려면 GPU와 저장소가 필요해 이 프로젝트의 제약(최소 파일·최소 저장소)과 충돌한다.
그래서 지금은 **이미 이런 공개 데이터로 학습돼 공개된 모델**을 호출한다.

## 다음 단계 — 신경망 목소리 품질을 올리는 길 (검토 순서대로)

1. **Cloudflare Workers AI MeloTTS**(현재) — 파일 0, 저장소 0, 분당 과금. 한국어 코드 지원 여부는 배포 후 1번 확인.
2. **Supertonic 3**(슈퍼톤, 2026-01 공개) — 한국어 포함 31개 언어, 약 99M 파라미터, ONNX로 브라우저(onnxruntime-web)에서
   실행, 정규화·G2P 내장, 44.1kHz. 라이선스 OpenRAIL-M(사용 제한 조항 확인 필요). 모델을 우리 서버에 두지 않고
   Hugging Face CDN에서 브라우저가 직접 받아 브라우저 캐시에 두면 "저장소 0"은 지킬 수 있지만 첫 다운로드 수십~수백 MB.
   → 실험실에 "고품질(다운로드)" 옵션으로 붙이는 것이 다음 작업.
3. **직접 학습** — 위 데이터셋 + VITS/F5-TTS/CosyVoice 계열. GPU·저장소가 생겼을 때만.

## 한계 (정직하게)

- 파형 자체는 이 코드가 만들지 않는다. 브라우저 폴백 품질은 OS에 설치된 목소리를 따른다.
- Web Speech의 pitch/rate는 조각 단위로만 먹는다. 음절 단위 F0 곡선(LHLH)은 SSML을 받는 엔진에서만 근사된다.
- 음운 변동은 형태소 정보 없이 어절 안에서만 적용한다. ㄴ 첨가(솜이불)·한자어 ㄹ 경음화(발전)·사잇소리는 뺐다.
- 감정 감지는 낱말 사전이다. 반어·문맥은 못 읽는다. 중요한 문장은 태그나 `emotion`으로 직접 지정한다.

## 참고 자료

- 표준 발음법 정리: https://namu.wiki/w/%ED%95%9C%EA%B5%AD%EC%96%B4/%EC%9D%8C%EC%9A%B4%20%EB%B3%80%EB%8F%99
- K-ToBI(Sun-Ah Jun): https://sunahjun.humspace.ucla.edu/ktobi/K-tobi.html · https://linguistics.ucla.edu/people/jun/JUN-JKL14-FINAL.pdf
- 한국어 TTS 운율구 모델: https://www.sciencedirect.com/science/article/abs/pii/S0885230805000021 · 강세구 경계 검출: https://link.springer.com/chapter/10.1007/978-3-540-28651-6_35
- 숫자 음역 모호성: https://koreascience.kr/article/CFKO201832073078857.pdf · 숫자 정규화: https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002716499
- 한국어 감정 음성 지각: https://www.sciencedirect.com/science/article/abs/pii/S0024384125002086 · https://koreascience.kr/article/JAKO202431643638911.page
- 영어 TTS가 한국어를 못하는 이유: https://humelo.com/insights/why-korean-tts-ai-voice-answer-humelo
- 일레븐랩스 모범 사례(break·태그·정규화): https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices
- 최신 합성 구조: F5-TTS https://arxiv.org/html/2410.06885v1 · CosyVoice 3 https://arxiv.org/pdf/2505.17589 · IndexTTS 2.5 https://arxiv.org/pdf/2601.03888
- Cloudflare Workers AI MeloTTS: https://developers.cloudflare.com/workers-ai/models/melotts/ · 언어 목록 미문서 이슈: https://github.com/cloudflare/cloudflare-docs/issues/23308
- Supertonic: https://github.com/supertone-inc/supertonic · https://huggingface.co/Supertone/supertonic-3
- 경량 대안: Piper KSS 한국어 ONNX https://huggingface.co/neurlang/piper-onnx-kss-korean · sherpa-onnx https://k2-fsa.github.io/sherpa/onnx/tts/index.html
- Web Speech API: https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis · 추천 목소리: https://github.com/HadrienGardeur/web-speech-recommended-voices
