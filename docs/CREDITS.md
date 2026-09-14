# 출처와 표기 의무 (CREDITS)

이 저장소는 **음성 파일도 모델 가중치도 담지 않는다.** 제3자 모델은 실행 시에 받아 쓰고, 공개 코퍼스는
"받고 → 재고 → 지우는" 방식으로 **파생 통계만** 남겼다. 그래도 아래 표기·조건은 배포와 서비스에 따라붙는다.
유료 서비스를 열기 전 이 문서가 정본이다.

## 1. 모델 — 실행할 때 받는다

| 구성요소 | 라이선스 | 상업적 이용 | 지켜야 하는 것 |
|---|---|---|---|
| **Supertonic 3** — ONNX·`voice_styles/` (`server/Dockerfile`이 Hugging Face `Supertone/supertonic-3`에서 받는다) | BigScience **OpenRAIL-M** | 가능 (로열티 프리) | Attachment A의 **사용 제한을 서비스 약관에 승계**. 특히 ① 실존 인물의 동의 없는 음성 모사 금지 ② **AI 생성 사실 고지** ③ 불법·허위정보·차별·의료조언 용도 금지. 스타일 벡터 가중평균(`voice_shape.py`의 여성 F4:0.6+F2:0.4 · 남성 M1:0.7+M3:0.3)은 OpenRAIL-M의 파생물이며 같은 제한을 그대로 승계한다 |
| **Supertonic 샘플 코드** (`py/helper.py`, `example_onnx.py`) | MIT | 가능 | 저작권 표시 유지. 업스트림 GitHub 저장소는 **아카이브 상태(AS IS)** — 보안 패치가 없다 |
| **MeloTTS** (Cloudflare Workers AI `/api/tts` 폴백 경로) | MIT (MyShell.ai) | 가능 | 없음. Cloudflare 쪽은 모델을 Third-Party Service로 취급하므로 모델 라이선스를 직접 따른다 |
| Qwen3-TTS · Fun-CosyVoice3 · MOSS-TTS (비교·후보) | Apache-2.0 | 가능 | 라이선스 사본 포함 + 변경 고지 |
| **MOSS-TTS-Nano-100M** | 태그는 apache-2.0이나 카드에 **재배포 비허가 경고** | **불확실** | 프로덕션 후보에서 제외(`research/tts-arena/providers.json`). 루트 LICENSE 게시 확인 전에는 쓰지 않는다 |

`public/ko-voice.js`(정규화·운율 엔진)는 우리가 쓴 코드이고 외부 의존이 0이다 — Apache-2.0(`LICENSE`).

## 2. 코퍼스 — 파생 통계만 남았다

측정에 쓴 원본 음성은 이 저장소에 없고, 남은 것은 반음·쉼 길이 같은 **집계 수치**다.

| 코퍼스 | 라이선스 | 상업적 이용 | 표기 |
|---|---|---|---|
| **Zeroth-Korean** | CC BY 4.0 | 가능 | 저작자 표시 필요 — `public/profiles/korean-corpus.json`의 낭독 궤적·쉼 분포 출처 |
| **Mozilla Common Voice (ko)** | CC0 | 가능 | 표기 의무 없음 |
| **FLEURS** | CC BY 4.0 | 가능 | 저작자 표시 필요 |
| **YODAS / YODAS2** (ESPnet) | CC BY 3.0 | 가능 | 저작자 표시 필요. 원본이 YouTube의 CC 라이선스 영상이라 개별 영상 단위 검증은 데이터셋 카드의 진술에 의존한다(잔존 리스크) — `server/voice_shape_k2.py`의 유튜브 화자 궤적 출처 |
| **KsponSpeech (AI Hub)** | **AI Hub 이용약관** (CC BY 4.0 아님) | **별도 협의 필요** | 연구·개발 활용은 되지만 상업적 이용·제3자 제공은 수행기관 협의가 필요하다. 이 저장소에서는 `research/absorb_kspon.py`와 `research/FINDINGS.md`의 **조사 기록에만** 남아 있고, 배포되는 코드(`server/`·`public/`·`lib/`)에는 유래 수치가 없다 — 대화체 궤적의 정본은 YODAS 표다 |
| KSS (Korean Single Speaker) | CC BY-NC-SA 4.0 | **불가(비상업)** | 이 저장소는 쓰지 않는다. 참고 문헌으로만 언급된다 |

## 2-1. 조항 단위로 확인한 것 (2026-09-13 · 원문 대조)

BigScience Open RAIL-M(2022-08-18 무수정본, Supertone 이 덧붙인 조항 없음) 원문을 읽고 정리한다.

| 질문 | 조항 | 답 |
|---|---|---|
| 돈을 받아도 되나 | §2 "no-charge, royalty-free, **irrevocable**" · §11 "may choose to offer, and **charge a fee**" · Attachment A 13개 중 상업 금지 0개 | **된다** |
| 소스를 공개해야 하나 | §4 "may provide **additional or different license terms**" | **아니다**(카피레프트 아님). 전파되는 건 사용 제한 조항뿐 |
| 만든 음성의 권리는 | §6 "Licensor claims **no rights in the Output**" | **우리 것**. 단 §6 은 산출물도 사용 제한을 어길 수 없다고 못 박는다 |
| 브라우저로 모델을 내려주는 것은 | §1(g) "Distribution … including providing the Model as a **hosted service** … e.g. API-based or **web access**" | **배포다**(사용이 아니다) |
| 그래서 생기는 의무 | §4(b) "must give any Third Party recipients … **a copy of this License**" · §4(a) 사용 제한을 "**enforceable provision**" 으로 두고 "give notice to subsequent users" | 라이선스 사본 제공 + 약관 편입 + 내려받기 전 고지 |
| 아카이브되면 | §2 "**irrevocable**" | 권리는 유지된다. 다만 소스가 사라질 수 있으니 **미러를 우리가 갖고 있어야** 한다 |
| 스타일 벡터 평균은 | §1(f) "any other model created or initialized by **transfer of patterns of the weights**" | 파생물로 보는 게 안전하다. **파일로 내보내지 않으면 배포가 아니다** — 그래서 브라우저 메모리에서만 섞는다 |

이행한 것: `public/licenses.html` + `public/licenses/*.txt`(원문 사본) · `public/terms.html` 제1조(편입) ·
내려받기 전 1회 확인(`public/app.js`) · 만든 WAV 의 LIST/INFO 청크에 AI 생성 고지.
`test/license-obligations.test.cjs` 5개가 이 의무들이 사라지지 않게 잰다.

### 코퍼스 — 통계 수치만 쓸 때

- CC BY 4.0 §4(c): 표시 의무는 "**Share all or a substantial portion of the contents of the database**" 할 때 발생한다.
  측정치는 상당 부분이 아니다. 한국 저작권법 제93조② 도 "개별 소재는 상당한 부분으로 보지 않는다"고 한다.
- 🔴 다만 제93조② 단서: "**반복적이거나 특정한 목적을 위하여 체계적으로**" 복제하면 상당 부분으로 본다 →
  코퍼스를 상시 재다운로드하지 않는다. **한 번 받고 → 수치만 남기고 → 지운다**(research/absorb_*.py 가 그 방식이다).
- 🔴 Mozilla Common Voice 는 2025-10 부터 Mozilla Data Collective 전용이고 **미러 재배포를 금지**한다.
  CC0 라 저작권상 강제력은 없지만 계정 약관은 구속이다 — 우리는 수치만 쓰고 원본을 두지 않는다.

## 3. 목소리 정책

- `public/profiles/owner.json`은 **저작권자 본인 녹음**의 파생 수치다(원본 녹음은 저장소에 없다).
- 타인의 목소리는 라이선스로 동의가 확인된 공개 코퍼스 밖에서 쓰지 않는다.
  통화 녹음처럼 상대방 음성이 섞인 파일은 쓰지 않는다.
- 화자 역할(`research/tts-arena/roles.json`)은 **직업·말투**이고 실존 인물의 정체성이 아니다
  (`real_person_imitation: deny`). 이 정책은 OpenRAIL-M의 impersonation 제한과 같은 방향이다.
