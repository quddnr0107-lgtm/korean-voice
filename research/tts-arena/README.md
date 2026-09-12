# Korean TTS Arena — 200% doctrine

목표는 특정 TTS 한 개를 숭배하는 것이 아니라, **군사·법률 한국어를 가장 정확하고 자연스럽게 읽는 조합을 지속적으로 찾아 교체 가능한 구조**로 만드는 것이다.

## 학익진 구조

- **중앙**: `public/ko-voice.js` — 숫자·법령·군사용어 정규화, 구 분절, 운율 계획.
- **좌익**: 무료/오픈소스 backend — 현재 Supertonic, Qwen3-TTS, CosyVoice3, MOSS-TTS 계열.
- **우익**: 공식 상용 backend — Google, Azure, AWS, ElevenLabs, Fish Audio, Typecast. 네트워크와 유료 호출은 기본 금지.
- **후군**: 자동 판정 — ASR 되받아쓰기 CER + 운율거리 + 클리핑/무음 + 사람이 확정한 gate 문장.
- **정찰대**: 발음 관례가 불확실한 군 장비명. `gate:false`로 두고 공개 군 자료와 실제 청취 근거가 쌓이기 전까지 정답으로 박지 않는다.

## 가장 중요한 실험

모든 backend에 같은 문장을 두 버전으로 보낸다.

1. `raw`: 원문 그대로
2. `ko-voice`: 우리 frontend로 정규화한 canonical text

따라서 모델 A와 B만 비교하는 것이 아니라 **우리 frontend가 어떤 backend에서도 오류율을 얼마나 줄이는지** 측정한다. 세계 최고 TTS가 바뀌어도 frontend 자산은 남는다.

## 비용 원칙

- `ARENA_ALLOW_NETWORK` 기본 false.
- 유료 실행을 허가하는 `ARENA_ALLOW_PAID`는 아예 구현하지 않는다. 값을 1로 주면 실패한다.
- API provider는 `hard_char_budget`를 가진다.
- 무료 quota가 있더라도 billing 계정 자동과금 가능성이 있으면 사람의 별도 zero-cost 확인 전까지 실행하지 않는다.
- 음성 결과는 git에 커밋하지 않는다. 실험 중 필요하면 임시 디렉터리/Actions artifact만 쓴다.
- 음성복제는 본인·명시 동의·공개적으로 재사용 허용된 reference만 쓴다.

## 현재 전열

### 무료/로컬 우선

- 현재 Supertonic — production champion.
- Qwen3-TTS 0.6B — 공식 한국어 지원, Korean preset `Sohee`, Apache-2.0.
- Fun-CosyVoice3 0.5B — 공식 한국어 포함 9개 언어, Apache-2.0.
- MOSS-TTS family — 공식 한국어 지원, Apache-2.0. Nano는 CPU/browser 후보라 우선 실험하되 Nano 자체 한국어 품질은 실측 후 승격한다.

### 공식 상용 정찰군

- Google Cloud TTS: ko-KR Chirp3-HD 다수, Neural2 A/B/C.
- Azure Speech: ko-KR DragonHD, MAI-Voice-2, Neural 계열.
- Amazon Polly: Korean Seoyeon/Jihye, Seoyeon generative.
- ElevenLabs Korean.
- Fish Audio multilingual/Korean.
- Typecast Korean.

`providers.json`의 상용 provider는 모두 disabled by default다.

## gate와 research

`cases.json`에서:

- `gate:true`: 발음 확신도가 높고 regression 실패로 취급할 문장.
- `gate:false`: `K239`, `KM21`, `KF-21` 등 현장 낭독 관례를 더 검증해야 하는 문장. 결과를 모으되 품질 점수에 넣지 않는다.

## 실행

정적 계획만 본다. 네트워크 요청은 발생하지 않는다.

```bash
node research/tts-arena/plan.mjs
node research/tts-arena/plan.mjs --gate-only --json
node research/tts-arena/plan.mjs --domain military --json
```

상용 adapter가 추가되더라도 `ARENA_ALLOW_NETWORK=1` 없이는 호출하지 않는다. 그 경우에도 `hard_char_budget` 초과는 차단한다.

## 승리 조건

단일 숫자 `100%`가 아니라 다음을 동시에 만족해야 한다.

1. 고신뢰 군사·법률 gate의 의미 오독 0.
2. 정규화 멱등성 회귀 0.
3. raw 대비 `ko-voice` 적용 시 모든 주요 backend에서 CER 악화 없음, 대부분 개선.
4. 의미를 깨는 chunk 경계 0.
5. 새 결함은 golden case로 추가되어 재발 불가.
6. production backend가 바뀌어도 동일 arena로 즉시 비교 가능.

이 상태를 이 프로젝트에서 말하는 **200%**로 정의한다.
