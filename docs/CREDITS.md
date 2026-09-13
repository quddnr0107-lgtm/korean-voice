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

## 3. 목소리 정책

- `public/profiles/owner.json`은 **저작권자 본인 녹음**의 파생 수치다(원본 녹음은 저장소에 없다).
- 타인의 목소리는 라이선스로 동의가 확인된 공개 코퍼스 밖에서 쓰지 않는다.
  통화 녹음처럼 상대방 음성이 섞인 파일은 쓰지 않는다.
- 화자 역할(`research/tts-arena/roles.json`)은 **직업·말투**이고 실존 인물의 정체성이 아니다
  (`real_person_imitation: deny`). 이 정책은 OpenRAIL-M의 impersonation 제한과 같은 방향이다.
