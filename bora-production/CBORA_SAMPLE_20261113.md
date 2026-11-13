# C 엔진 김보라 공개 청취 샘플

2026-11-13. 기본 소희 시험에서 한 단계 진행해, 기존 김보라의 설명형·법령
낭독형 지시문과 음향 보정을 핀 고정 C 엔진에 적용하는 공개 샘플이다.
운영 통과·강의 완성·전체 배포 기록이 아니다.

## 입력과 구현

- 모델: Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice / Sohee.
- 모델 리비전: `0c0e3051f131929182e2c023b9537f8b1c68adfe`.
- C 구현: https://github.com/gabriele-mastrapasqua/qwen3-tts
- C 소스 리비전: `ef339be58a778b062e1c14382347964552eae007`.
- 실험 코드: `cbackend_bora_sample.py`, 별도 신규 테스트 6개 통과.
- 공개 입력: `cbackend-bora-fixtures.json`, 총 425자·4구간.
- 첫 3구간은 이전 GPU 김보라 데모와 줄바꿈을 제외하고 같은 설명문이다.
  이전 데모의 한 번 생성과 달리 이번에는 세 문단을 별도로 생성한다.
- 마지막 구간은 통합방위법 제2조제1호의 정의를 읽는 낭독형이다.
  음성용으로 인용부호를 생략하고 `침투ㆍ도발`을 `침투와 도발`로 읽는다.
  원문: https://law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=900254891

## 옮긴 설정과 남는 차이

| 항목 | 이번 C 샘플 |
| --- | --- |
| 음성·언어 | Sohee·Korean |
| 모드 지시문 | `pilot_render.STYLES` 설명/낭독 원문 그대로 |
| 음향 보정 | `pilot_render.FILTERS` 모드별 원문 그대로 |
| 후처리 | 24k mono float → 모드 필터 → 양끝 120ms 유지 trim·5ms fade |
| 재생 속도·피치 | tempo 1, pitch shift 0 |
| Talker 샘플링 | production의 temperature .8, top_p .9, top_k 50, repetition penalty 1.05 |
| 시드·종료 | 5192, 자연 EOS 필수, EOS 보조 off, cap 1100 |
| Code predictor | C는 greedy argmax, Python production의 subtalker sampling과 다름 |
| 수치·RNG | C BF16 체크포인트/커널·C RNG이며 Python FP32와 다름 |
| 추론 방식 | C 내부 chunk decode와 Python non_streaming 옵션의 동등성 주장 없음 |

같은 목소리 이름·지시문·음향 보정을 사용해도 동일한 WAV나 동일한 음색·
자연스러움이 보장되지는 않는다. 전체 품질·성능 동등성을 주장하지 않는다.

## 검증 범위

운영 원고·모델·합성·검수·배포 설정과 기존 통과 구간을 변경하지 않는다.
CPU 4스레드만 사용하며 공개 모델 이외의 키·원고를 받지 않는다.
후보 원본·중간 보정본·최종 WAV와 원시 로그를 보존한다. 자연 EOS,
프레임 수, 디코딩 PCM 범위, 비무음·클리핑·유한값을 확인한다.
별도 받아쓰기는 기존 고정 ASR 리비전을 사용하고 원고 프롬프트를 넣지 않는다.
ASR가 일치해도 청취 검증이나 운영 통과로 승격하지 않는다.

실행: https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43379876730

## 실제 결과

- CPU: AMD EPYC 9V74 / GitHub 표준 4 vCPU, 스레드 4.
- 네 구간 자연 EOS·PCM 프레임 범위·원본/후처리 신호 검사 완료.
- 원본/후처리 음성 84.16초, 문단 간 350ms 무음을 더한 연결 PCM 85.21초.
- TTS 프로세스 합계 265.366916초, 음성 1초당 3.15312초.
  각 프로세스 모델 로드를 포함하고, 설치·다운로드·DSP·ASR·MP3 저장은 별도다.
- MP3 약 1.71MB, 24kHz mono 160kbps. 재생 속도·피치 변경 없음.
- 이전 짧은 C 시험은 Intel CPU였고 이번은 AMD CPU다. 이전 GPU 데모와도
  문단 분할·추가 법령·후처리 조건이 달라 정확한 엔진별 배율을 주장하지 않는다.

고정된 faster-whisper-small 리비전 `536b0662742c02347bc0e980a01041f333bce120`으로
후처리 WAV 해시를 확인한 뒤 전사했다. 한국어, INT8, CPU 1스레드,
beam 5, temperature 0, 이전 문맥·VAD·정답 프롬프트 없음이다.

| 구간 | canonical 문자 오류 | 주요 불일치 |
| --- | ---: | --- |
| explain-1 | 1 | 통합방위법 → 통합방입법 |
| explain-2 | 0 | 없음 |
| explain-3 | 0 | 없음 |
| definition | 4 | 일원화하여 → 일어나와요 |

불일치가 실제 발화 오류인지 ASR 오류인지 아직 확정하지 않았다.
특히 법령 낭독의 핵심 표현이 달라 엄격한 운영 기준으로는 차단 대상이다.
자동 기준을 완화하거나 통과로 승격하지 않았고, 재합성도 하지 않았다.
이 파일은 음색·말투의 청취 비교용이며 낭독 품질 합격본이 아니다.

- 생성 보고서: [evidence/cbora-43379876730.json](evidence/cbora-43379876730.json)
- ASR 기록: [evidence/cbora-43379876730-asr-check.json](evidence/cbora-43379876730-asr-check.json)
- 연결 기록: [evidence/cbora-43379876730-assembled-sample.json](evidence/cbora-43379876730-assembled-sample.json)
