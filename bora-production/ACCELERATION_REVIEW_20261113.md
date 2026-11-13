# 소희·보라 통합강의 제작 속도 개선 조사와 실측

기준일: 2026-11-13 KST. 대상: Qwen3-TTS 12Hz 1.7B CustomVoice / Sohee.
이 문서는 공개 문장 시험과 운영 코드 검토를 구분한다. 운영 303강을
완료하거나 새 엔진을 배포했다는 기록이 아니다.

## 확인한 문제

1. 기존 Python FP32 / CPU 4스레드 경로의 합성 계산이 가장 크다.
   공개 기본 소희 시험은 9.6초 음성을 만드는 데 89.479초가 걸렸다.
   기존 말투·샘플링 시험은 11.12초에 107.919초였다. 음성 길이로
   정규화하면 9.32068 대 9.70499초/음성초이다. 말투 지시만 빼는
   변경은 이 짧은 시험에서 약 4% 차이였으며 주된 해결책이 아니다.
2. 운영 TB-001은 58구간에서 후보 95개를 생성했다. 첫 후보 통과는
   21구간, 두 번째 후보 통과는 6구간, 두 후보 모두 차단은 31구간이다.
   두 번째 합성 37회 중 추가 통과 6회(16.2%)였다. 후보 길이가 달라
   이 개수 비율을 시간 절감률이라고 부르지 않는다.
3. ASR 불일치는 발음 오류의 확정 증거가 아니다. 원고-받아쓰기 숫자
   정규화 확대 진단으로 일치한 차단 구간도 31개 중 1개뿐이었다.
   나머지를 정상·불량 어느 쪽으로도 자동 단정할 수 없다.
4. 최대 병렬 작업 수 40과 실제 작업 수는 다르다. 현재 TB-001 요청은
   두 큰 작업으로 나뉘어 있었다. 이미 구현된 execution_shard_chars는
   다음 유효한 제작 요청에 적용해야 한다. 차단 원인 없이 동일 후보를
   다시 만드는 요청은 시작하지 않았다.

## 이번에 반영한 변경

`runtime.restore(..., unit_range=(begin,end))`를 추가했다.

- probe/render는 전체 강의의 음성을 매번 모두 복호화하는 대신,
  자신에게 필요한 구간만 복호화·압축 해제·PCM 기록한다.
- 선택 전에 전체 공개 번들의 구조·범위·핀·암호문 해시를 검증한다.
- 선택된 구간은 기존 AES 인증과 open_unit 검증을 그대로 통과해야 한다.
- 최종 조립은 계속 전체 강의를 복원·검증한다.
- 원고·모델·음성 설정 지문·통과 기준은 바꾸지 않았다.

신규 8개 암호화 범위 테스트와 GitHub 전체 회귀 173개가 통과했다.
전체/범위 복원의 영수증·음성 바이트 일치, 범위 밖 공개 암호문 변조,
범위 안 AES 태그 변조, 잘못된 범위, 최종 조립 전체 복원을 검사했다.
이 변경은 중복 복호화와 디스크 작업을 줄인다. 모델 합성이나 전체
암호문 다운로드·전역 해시 계산을 없애는 변경은 아니다.

- 변경: https://github.com/quddnr0107-lgtm/korean-voice/commit/f83743ba9a7e3e6d497c161a2d3ace58bdacf42f
- 회귀: https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43377162601

## 실제 실행한 추가 실험

### 1. 단어별 시간 계산 제거: 작은 개선

기존 공개 WAV 4개를 같은 faster-whisper-small 리비전, INT8, CPU 1스레드,
beam 5, VAD 없음, 정답 프롬프트 없음으로 비교했다. 단어 시각 옵션만
바꿨고 실행 순서를 교대했다.

| 설정 | ASR 합계 |
| --- | ---: |
| word_timestamps=True | 20.732890초 |
| word_timestamps=False | 19.296878초 |

네 파일 모두 canonical 전사·오류 수·필수용어·정확 일치 판정이 같았다.
이 시험의 ASR 부분은 6.926% 짧아졌다. 전체 합성이 6.926% 빨라졌다는
의미가 아니고, 운영 법령 모든 문장에서 같은 결과라는 증명도 아니다.
현재 renderer를 바꾸면 기존 음성 지문이 달라지므로 이 옵션을 운영에
성급히 적용하지 않았다.

- 실행: https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43377183096
- 수치: [evidence/asr-timestamps-43377183096.json](evidence/asr-timestamps-43377183096.json)

### 2. C 기반 CPU 엔진: 더 큰 개선 후보

커뮤니티 C/BLAS 구현을 소스 커밋 ef339be58a778b062e1c14382347964552eae007로
고정해 빌드했다. 같은 공식 1.7B 모델 리비전과 Sohee·한국어·4스레드,
공개된 기본 소희 시험의 두 문장을 사용했다. INT8/INT4 변환은 하지 않았다.
기본 엔진에 있는 EOS 보조 기능은 명시적으로 껐다.

| 지표 | 결과 |
| --- | ---: |
| 생성한 음성 합계 | 9.68초 |
| 프로세스 시간 합계(매번 모델 로드 포함) | 29.109초 |
| 프로세스초 / 음성초 | 3.0071 |
| 실제 CPU | Intel Xeon 6975P-C / 4 vCPU |
| 정상 종료 | 두 후보 모두 원시 로그·프레임·PCM 범위로 확인 |
| 기존 ASR로 공개 원고 대조 | 두 문장 모두 canonical 오류 0 |

기존 Python FP32 공개 시험의 9.6초/89.479초보다 짧은 결과를 관찰했다.
그러나 서로 다른 실행의 CPU 기종과 BF16/C 대 FP32/Python 계산 경로가
달라 정확한 동등품질 3배 향상이라는 결론을 내리지는 않는다. 짧은
일반 문장 두 개의 ASR 일치가 법령 전체의 발음·음색·호흡 검증은 아니다.

처음 보고서의 EOS 필드는 null이었다. 실제 로그에는 진행 표시 뒤에
`EOS at frame`이 같은 줄에 붙어 있어 파서가 놓쳤다. 원본 보고서를
덮어쓰지 않고 별도 재분석을 만들었다. 현재 파서는 유일한 EOS·유일한
Generated 프레임 수·EOS 보조 off·WAV 샘플 수가 모두 맞아야 인정한다.
원본 WAV/로그/보고서 해시와 기존 실패 판정을 새 분석에 보존했다.
음성을 다시 생성하거나 운영 통과로 승격하지 않았다.

- 실행: https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43377295943
- 재분석: [evidence/cbackend-43377295943-reanalysis.json](evidence/cbackend-43377295943-reanalysis.json)
- ASR 확인: [evidence/cbackend-43377295943-asr-check.json](evidence/cbackend-43377295943-asr-check.json)
- 구현: https://github.com/gabriele-mastrapasqua/qwen3-tts

### 3. Python CPU FP32/BF16 비교

같은 GitHub 실행의 Intel Xeon 6975P-C / 4 vCPU에서 Python 경로의
FP32와 BF16을 별도 프로세스로 순차 실행했다. 모델 리비전·공개 문장
두 개·화자·시드·4스레드·기본 생성 옵션을 고정하고 정밀도만 바꿨다.
AVX512_BF16/AMX_BF16 명령 지원과 실제 모델 파라미터 dtype을 확인했다.

| 정밀도 | 음성 합계 | 합성 합계 | 합성초 / 음성초 |
| --- | ---: | ---: | ---: |
| FP32 | 9.60초 | 52.574956초 | 5.476558 |
| BF16 | 8.88초 | 41.441873초 | 4.666878 |

실제 생성 길이로 정규화한 BF16 처리량은 약 1.1735배이며,
음성 1초당 합성 시간은 약 14.8% 줄었다. 길이가 다른 출력의
52.57→41.44초 감소를 그대로 전체 제작 시간 절감률이라고 하지 않는다.
두 설정 모두 자연 EOS·신호 검사를 통과했고, 저장된 WAV 해시를 확인한
별도 ASR 검사도 네 파일 모두 canonical 오류 0이었다.

FP32→BF16 고정 순서로 한 번씩 측정했으며 워밍업·실행 순서 효과를
통제하지 않았다. 모델 로드·검수·후처리·재시도 시간은 합성 시간에
포함하지 않았다. 두 짧은 일반 문장의 ASR 일치는 음색·자연스러움·
법령 전체 품질의 동등성을 증명하지 않는다. 운영 FP32 설정은 유지했다.

이 시험의 FP32는 이전 공개 시험의 89.479초보다 빠른 52.575초였다.
따라서 앞의 C 29.109초와 옛 Python 89.479초의 단순 비교를
"엔진 교체만으로 3배"라고 해석해서는 안 된다. 새 Python과 C는
표시된 CPU 기종은 같지만 별도 실행·다른 수치 경로이며 C 측정에는
매번 모델 로드도 포함돼, C의 인과적 개선 폭은 추가 통제가 필요하다.

- 실행: https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43378254194
- 수치: [evidence/cpu-precision-43378254194.json](evidence/cpu-precision-43378254194.json)
- ASR 확인: [evidence/cpu-precision-43378254194-asr-check.json](evidence/cpu-precision-43378254194-asr-check.json)

## 공식 자료와 오픈소스 비교

| 후보 | 검증한 내용 | 적용 판단 |
| --- | --- | --- |
| Qwen 공식 배치 합성 | CustomVoice API가 문장 목록을 받는다 | 길이가 비슷한 문장 묶음과 메모리 한도를 실측할 후보 |
| C/BLAS 엔진 | 1.7B CustomVoice·Sohee 직접 지원, 원본 safetensors 사용 | 위 공개 CPU 시험 완료. 법령·음색·같은 하드웨어 비교 필요 |
| vLLM-Omni | 같은 1.7B CustomVoice에 배치·CUDA Graph·Code Predictor 최적화 구현 | GPU 대량 제작의 유력 후보. 외부 H200 수치를 우리 환경에 대입 금지 |
| faster-qwen3-tts | GPU CUDA Graph·스트리밍 구현 | GPU 샘플·라이선스·원고 정확도·안정성 실측 뒤 후보 선정 |
| OpenVINO GenAI | 개발 문서에 정확한 CustomVoice 모델 지원 등록 | 현재 안정판 패키지와 개발 문서가 다름. 즉시 교체 후보에서 보류 |
| llama.cpp TTS | 확인한 공식 경로는 Base·참조 음성 중심 | 기존 Sohee를 그대로 쓰는 우선 경로에서 제외 |
| faster-whisper 배치 | 공식 배치 인식 제공 | VAD 등 기본값과 전사 차이를 통제한 시험 필요 |
| SenseVoiceSmall | 한국어 지원 비 Whisper 계열 독립 ASR | 의심 음성 재검증 후보. 단독 자동 승인 근거로 사용하지 않음 |
| WhisperX | 단어 시각 정렬·검수 위치 찾기 | 주어진 원고의 강제 정렬을 발음 정확성 증거로 사용하지 않음 |

OpenVINO는 최신 문서에 모델 이름이 보인다는 이유로 바로 실행하지
않았다. 안정 wheel과 소스를 대조해 Qwen3-TTS 지원이 안정 배포 이후
개발 브랜치에 추가된 점을 확인했다. 미지원 stable 조합으로 변환을
반복하거나 검증 없이 운영 의존성을 nightly로 바꾸지 않았다.

## 제작 구조 변경의 다음 순서

1. 합성 한 번이 끝나면 원본 후보를 즉시 암호화·보존한다.
2. TTS·검수·후처리 캐시 지문을 분리한다.
   - 합성 키: 발음 원고, 모델 리비전, 화자, 정밀도, 샘플링, 시드.
   - 검수 키: 원본 WAV 해시, ASR 리비전/옵션, 정규화와 판정 정책.
   - 조립 키: 최종 PCM, DSP, 구간 순서, 원문 범위, 출력 형식.
3. 받아쓰기 불일치가 나면 먼저 같은 음성을 독립 ASR/청취로 재검증한다.
4. 실제 발화 결함이 확인된 구간만 새로운 후보를 생성한다.
5. 새 엔진은 숫자·기한·부정·의무·약어를 포함한 공개 법령형 문장과
   실제 인증된 원고에서 검수한 뒤 별도 버전으로 제작한다.
6. 기존 통과 27구간은 원래 지문 그대로 보존한다. 새 설정의 지문으로
   바꿔 적거나 검수 기록을 합성 기록처럼 위장하지 않는다.

현재는 이 구조 전체를 운영에 구현한 상태가 아니다. 단순 CER 허용치
완화, 강제 정렬 성공을 이용한 승인, 한도 우회, 부분 과목 배포를 하지 않았다.
기존의 하루 40분 구독으로 수개월 걸린다는 계산은 미최적 구현의
가정별 계획치였다. 최적화 후 실제 GPU 계산시간을 측정해 다시 산정해야 한다.

그 밖에 workflow별 공개 모델·패키지 반복 다운로드도 줄일 수 있다.
공개 자산만 revision별로 캐시하고 복원 후 핀·파일 검증을 유지하며,
한 작업에서 모델을 한 번 올린 뒤 적정 수의 구간을 처리하는 구성이
후보다. 작은 작업을 무조건 늘리면 모델 설치·다운로드·로딩 횟수도
늘 수 있으므로, 실제 합성 시간과 준비 시간을 나눠 측정해야 한다.

## 추가 원문

- Qwen 공식 API: https://github.com/QwenLM/Qwen3-TTS
- vLLM 최적화 구현·측정: https://github.com/vllm-project/vllm-omni/blob/main/docs/design/qwen3_omni_tts_performance_optimization.md
- GPU CUDA Graph 구현: https://github.com/remominor/faster-qwen3-tts
- OpenVINO 지원 목록: https://openvinotoolkit.github.io/openvino.genai/docs/supported-models/
- OpenVINO Qwen3-TTS 추가: https://github.com/openvinotoolkit/openvino.genai/commit/d9f966d4133a148e00d1b8ff4aa72ef441a99249
- llama.cpp 공식 TTS: https://github.com/ggml-org/llama.cpp/blob/master/tools/tts/README.md
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- WhisperX: https://github.com/m-bain/whisperX
- SenseVoice: https://github.com/QwenAudio/SenseVoice
