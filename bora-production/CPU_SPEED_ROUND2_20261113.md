# 김보라 원본 CPU 속도 개선 — 2차 실험 (2026-11-13)

원본 Qwen3-TTS 12Hz 1.7B CustomVoice·Sohee·FP32·기존 설명/낭독 지시문·공식 샘플러를 유지했다. 다섯 가지 접근을 오류 수정과 재확인을 포함해 열 차례 실행했다. GPU나 대체 목소리를 사용하지 않았다.

## 확인한 결과

| 실험 | 실제 측정 | 판단 |
|---|---|---|
| AMD EPYC 7763의 대기 방식 | 137.992→109.465초, 음성 11.12초 동일. 20.67% 단축 | 해당 CPU에만 조건부 반영 |
| Intel 8573C에서 순서·문장 변경 재확인 | 기본 93.355초, 새 방식 95.363초. 2.15% 느림 | 기본 유지 |
| ARM Neoverse-N2, 4스레드 | 11.12초 음성에 52.721초. ARM 2스레드는 72.133초 | ARM에서는 4스레드가 유망 |
| ARM과 새 대기 방식 결합 | 기본 51.018초, 새 방식 55.149초. 8.10% 느림 | ARM은 기본 유지 |
| oneDNN 연산 기능 끄기 | ON 105.136초, OFF 106.534초. OFF가 1.33% 느림 | 기존 ON 유지 |
| 원본 predictor 일부 컴파일 | 새 컴파일 없는 둘째 문장 61.740→55.451초. 10.19% 단축 | 반복 합성 후보, 운영 미적용 |

서로 다른 실험의 단축률을 합산하지 않는다. ARM 52.721초는 앞선 AMD 4스레드 107.697초보다 관측상 51.05% 짧았지만 별도 CPU·runner 실행이다. 일반적인 ARM 우위나 303강 전체의 확정 단축률을 뜻하지 않는다. 모든 수치는 짧은 공개 문장 시험이다.

## 실제 제작 설정에 반영한 부분

[제작 설정 변경](https://github.com/quddnr0107-lgtm/korean-voice/commit/8324b0de0cb445498009ade733fe7299da7ff705)은 정확히 측정된 AMD EPYC 7763 모델에서만 PASSIVE/GOMP_SPINCOUNT=0/KMP_BLOCKTIME=0 힌트를 선택한다. Intel·ARM·미확인 CPU는 기본 환경을 유지한다. 이미 설정된 대기 변수가 있으면 덮어쓰지 않는다.

선택기는 native 라이브러리를 불러오기 전에 키 없는 단계에서 실행한다. 실제 격리된 preflight/render Python에도 세 변수가 전달되도록 sudo 보존 목록을 연결했다. 모델·renderer·recipe·실제 4스레드·샘플러·QA·생산 request는 바꾸지 않았다. 기존 27개 통과 구간을 새 설정으로 바꿔 적거나 재합성하지 않았다. 이번 변경으로 새 제작 실행이나 부분 과목 배포를 시작하지 않았다.

[첫 CI](https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43404209991)와 [후속 CI](https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43404775055)가 모두 성공했다. 각각 272개 실행 중 271개 통과, 기존 1개 건너뜀이다. 실제 sudo→network namespace→일반 사용자 Python에서 환경변수 전달 성공과 보존을 뺐을 때의 실패를 확인했다. 기존 핵심 함수와 음성 지문 불변 검사도 통과했다. [반영 검증 기록](evidence/cpu-wait-adoption-20261113.json).

## 음성 및 검수

대기 방식 비교의 네 문장, ARM 결합 시험의 두 문장, 최종 컴파일 시험의 두 문장은 각각 기본 방식과 decoded PCM의 모든 샘플이 완전히 같았다. 파일 컨테이너의 timestamp 때문에 WAV 전체 해시는 달라질 수 있어 실제 PCM을 따로 검사했다.

ARM↔AMD 비교는 작은 FP32 수치 차이가 있었으며 완전 동일하다고 표현하지 않는다. ARM 원본·ARM 대기 방식·최종 컴파일·oneDNN 시험의 ASR 결과는 모두 canonical 문자 오류 0이었다.

Intel 재확인의 낭독 시험에서는 기본/새 방식 모두 같은 ASR 1자 차이가 있었다. PCM이 같은 공통 인식 차이이므로 새 대기 방식이 발음을 바꾼 증거는 아니다. 엄격한 기존 QA에서는 두 음성 모두 계속 차단 대상이다. 검수 기준을 완화하지 않았다.

## 컴파일에서 해결한 문제와 남은 적용 조건

세 번의 실패를 분리했다. 초기 Inductor 오류는 재시험에서 생성 C++ 커널 오류로 좁혔고, 그다음 실패는 우리가 둔 그래프 수 상한이었다. 계측 wrapper가 컴파일러 내부 속성을 복사해 실행 카운터를 우회할 수 있는 오류도 수정했다. 과거 카운터 0은 관측값으로 보존하되 미실행의 확정 증거로 해석하지 않도록 기록을 보정했다.

마지막에는 원래 마스크 계산을 eager로 유지하고, 15개 출력 헤드 선택과 샘플러는 원본 Python 그대로 둔 채 inner predictor.model.forward만 컴파일했다. 실제 그래프 3개와 실행 2,085회를 관측했고, outer 함수 및 마스크 복구를 확인했다.

첫 문장은 원본 46.862초 대비 컴파일 88.783초였다. 둘째는 새 그래프 없이 61.740→55.451초로 줄었다. 두 문장 합계는 108.602→144.234초로 32.81% 느렸다. 초기 비용의 장기 상쇄는 측정하지 않았으므로 전체 제작이 빨라졌다고 주장하지 않는다.

ARM용 torchaudio는 2.8.0, 현재 x86 제작 pin은 2.8.0+cpu다. ARM과 컴파일을 운영에 넣으려면 별도 실제 실행 지문을 등록해야 한다. 기존 TB-001은 원래 프로필로 보존하고 새 강의부터 다른 프로필을 쓰는 경로가 가능하지만, 실행별 완성 암호화 파일을 모으는 기능도 필요하다. 한 강의 안에서 서로 다른 receipt를 몰래 섞는 변경은 하지 않는다.

## 재현 가능한 실행 기록

- [AMD 대기 방식](https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43400117671), [Intel 재확인](https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43401975416)
- [ARM 원본](https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43400425292), [ARM 결합 시험](https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43403967601)
- [oneDNN 재시험](https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43400980403)
- [성공한 inner 컴파일](https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43405549864)
- ZIP SHA256·추출 경로/크기·모델 SHA·WAV/PCM SHA·ASR 설정과 결과는 각 run ID의 evidence JSON에 보존했다.

