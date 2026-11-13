# CPU 통합강의 제작: 재조합 가능한 도구와 실험

기준일 2026-11-13. 목적은 GPU 구독 없이 제작 시간을 줄이면서 사용자가
선택한 김보라의 자연스러운 음색·리듬과 원문 정확성을 지키는 것이다.
목록은 실제 구현 확인, 저자 측정, 우리 실측, 미검증 가설을 구분한다.
운영 303강 완료·새 엔진 채택·음질 동등성의 기록이 아니다.

## 가장 중요한 새 발견: C의 설정값과 공식 생성 규칙은 달랐다

조사한 C 소스는 ef339be58a778b062e1c14382347964552eae007이다.

1. `qwen_tts_sampling.c`는 이전 토큰 목록을 순회하면서 같은 토큰이 여러 번
   나오면 반복 페널티를 누적 적용한다. Transformers 4.57.3의 해당 처리는
   토큰별로 한 번 적용한다.
2. C는 전체 확률을 계산한 뒤 top-k를 제거하고, 남은 확률을 재정규화하지
   않은 상태에서 top-p의 누적 확률을 계산한다. 공식 경로의 top-k 마스킹
   이후 softmax를 기준으로 한 top-p와 필터 결과가 다를 수 있다.
3. C의 CPU single code predictor는 15개 head에서 argmax를 사용한다.
   기존 Python recipe는 subtalker temperature .9 / top-k 50 / top-p 1로
   확률 추출한다. ctx의 CP sampling 필드가 있다는 것과 실제 사용은 다르다.

따라서 같은 .8/.9/50/1.05를 넣은 것만으로 같은 샘플링이라고 할 수 없다.
이 세 차이가 이전 첫 25초의 어색한 말투를 일으켰는지는 인과 검증 전이다.
별도 환경 옵션을 가진 패치로 원본, Talker 규칙 정렬, Talker+CP 정렬을
비교한다. C RNG·수치 연산·입력 구성까지 공식 Python과 같아지는 것은 아니다.
CP sampling을 복원하면 기존의 fused argmax보다 느려질 수도 있다.

## 이번 실제 시험 결과

### 선택적 MLP INT8: 계산은 줄었지만 이번 설정은 품질 기준 탈락

동일 AMD EPYC7763 / 4vCPU에서 공식 Python 생성기를 유지하고 FP32와
84개 MLP Linear만 dynamic INT8로 바꾼 설정을 순차 비교했다.

| 지표 | FP32 | 선택적 INT8 |
| --- | ---: | ---: |
| 합성 시간 | 107.397595초 | 69.230320초 |
| 실제 음성 길이 | 11.12초 | 9.12초 |
| 음성1초당 합성 시간 | 9.658057초 | 7.591044초 |
| 로드/변환 직후 현재 RSS | 8,662,264KiB | 6,050,036KiB |
| 프로세스 최고 RSS | 11,880,036KiB | 11,879,864KiB |
| 두 공개 문장의 canonical ASR 문자 오류 | 0 / 0 | 5 / 6 |

음성 길이로 정규화한 합성 시간은 약21.4%, 변환 직후 현재 RSS는 약30.2%
줄었지만 두 문장 모두 기존 엄격 기준을 통과하지 못했다. 실제 발화와
ASR 오류를 최종 구분하지 않았더라도 현재 기준의 채택 근거는 없다.
INT8은 FP32 상태를 먼저 로드하므로 최고 RSS는 줄지 않았다. 원본 Linear를
잡던 Python 참조를 제거하고 GC한 뒤 현재 메모리를 측정했다.
고정 순서·짧은 두 문장·한 번의 탐색 시험이며 반복 실험의 확정 배율이 아니다.
예전 BF16 시험과 CPU 기종도 달라 INT8/BF16 순위 비교로 사용하지 않는다.

- 실행: https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43384406020
- 수치: [evidence/cpu-quant-43384406020.json](evidence/cpu-quant-43384406020.json)
- ASR: [evidence/cpu-quant-43384406020-asr.json](evidence/cpu-quant-43384406020-asr.json)

### C의 선택 규칙 정렬: 짧은 문장 시험은 통과, 긴 말투는 미검증

같은 AMD EPYC9V74 / 4vCPU에서 원본, Talker 규칙만 정렬, Talker와
CP를 함께 정렬하는 세 조건을 두 공개 문장으로 비교했다. 두 번째
문장은 실행 순서를 거꾸로 했다. 실제 패치 C의 확률 테스트8개도 통과했다.

| 조건 | 생성 시간(모델 로드 포함) | 음성 길이 | 생성초/음성초 | ASR 문자 오류 |
| --- | ---: | ---: | ---: | --- |
| 원본 C | 40.212040초 | 13.28초 | 3.02802 | 0 / 0 |
| Talker 규칙 정렬 | 38.592204초 | 12.72초 | 3.03398 | 0 / 0 |
| Talker+CP 정렬 | 35.693858초 | 11.84초 | 3.01468 | 0 / 0 |

두 문장의 길이는 원본보다 약10.8% 줄었지만 프레임당 계산 속도의 큰
향상은 아니다. 짧은 표본에서 발생한 출력 길이 차이이며, 이전25초 구간의
말투가 고쳐졌거나 동일 음색이 보장됐다는 결론을 내리지 않는다.
원본 C도 이 두 문장은 정확 일치하므로 ASR 정확도가 개선됐다는 주장도 없다.
이 실험은 손실 양자화 없이 선택 규칙을 정렬할 재료를 확보했다는 의미다.

- 실행: https://github.com/quddnr0107-lgtm/korean-voice/actions/runs/43385051639
- 수치: [evidence/cpu-sampling-43385051639.json](evidence/cpu-sampling-43385051639.json)
- ASR: [evidence/cpu-sampling-43385051639-asr.json](evidence/cpu-sampling-43385051639-asr.json)

### 하은 발화 + 보라 참조 음색 변환: 실제 두 단계 CPU 시험

공식 OpenVoice V2의 FP32 ONNX 변환물을 별도 조사해 실제 파일·그래프
입력과 STFT를 확인했다. 변환물은 TigreGotico/voiceclonnx-openvoice-v2,
revision `34d010c192c97f763207f488f6057fd07fee42ad`의128MB converter와
3.26MB reference encoder다. MIT 원본/변환물 provenance를 확인했다.
처음에는 기존 하은 음성8.76초와 사용자가 좋아한 보라 GPU 음성의 첫12초로
실행했으며, 변환만4.0265초·출력8.7539초·신호 정상 결과를 얻었다.

그 다음 같은 호스트 AMD EPYC9V74, ORT CPU2스레드에서 하은을 새로
합성하는 전체 조합을1회 측정했다. Supertonic3 F2,16steps, 기존 seed와
문장별 속도·trim·fade·휴지를 사용했다. 참조 특징은 별도0.1428초에
준비한 캐시를 사용했다.

| 단계 | 실제 시간 |
| --- | ---: |
| 새 하은 합성 순수 추론 | 5.0294초 |
| 하은 합성+trim/fade/정규화/저장 | 5.0445초 |
| 음색 변환 forward | 3.2037초 |
| 모델 로딩과 캐시 로딩 | 2.1647초 |
| 로딩 포함, 결과 WAV 저장까지 | 10.4457초 |
| 로딩 제외, 결과 WAV 저장까지 | 8.2776초 |
| 결과 음성 | 8.7539초 |

다운로드·Python import·ASR 시간은 이 타이머에서 제외했다. 호스트는
9개 논리 CPU가 보이는 공유 환경이며 ORT 각 session을2스레드로 설정했다.
GitHub4vCPU의 다른 시험과 동일한 자원 제한의 성능 비교가 아니다.
한 번의 짧은 실행이고 파일시스템 캐시 상태도 통제하지 않았다.

실제 파일 크기로 하은의4개ONNX 가중치는 약398MB, FP32 OpenVoice의 두
가중치는 약131MB로 합계 약530MB다(ASR·설정 제외). 기존 Qwen의 모델과
codec 가중치 약4.5GB보다 다운로드·디스크 재료가 작다. 이 디스크 비율을
실제 RAM이나 합성 속도 비율로 그대로 바꾸지는 않는다.

원음 crop과 처음 변환본을 각각 검사했을 때 canonical 오류는1/0이었다.
새 합성 원음과 새 변환본은 둘 다1이었다. 차이는 모두 법령명
`통합방위법`을 ASR가 `통합방입법`으로 옮긴 부분이다. 따라서 음색 변환이
발음 오류를 고쳤다거나 전체 원고 정확도가 합격했다고 하지 않는다.
원음/출력의 전체 음량 정규화 범위 차이도 별도 기록했다.

이번 실험은 Torch 없이 이미 설치된 ORT·NumPy·SciPy로 실행했다.
공식과 같은 linear magnitude STFT1024/hop256/reflect384/periodic Hann을
구현했고 독립 SciPy STFT 결과와 최대3.81e-6 차이였다. 다만 공식의
librosa 리샘플러 대신 scipy.polyphase를 사용했고 PyTorch와 독립 수치
대조를 재현하지 않았다. ONNX exporter의 parity 보고를 우리 검증으로
대신하지 않는다. 보라 음색·자연스러움은 사용자 청취 전이며 운영 채택 전이다.

- 구현 재료: https://huggingface.co/TigreGotico/voiceclonnx-openvoice-v2
- 첫 변환: [evidence/openvoice-cpu-pilot-20261113.json](evidence/openvoice-cpu-pilot-20261113.json)
- 전체 조합: [evidence/openvoice-cpu-pipeline-20261113.json](evidence/openvoice-cpu-pipeline-20261113.json)
- ASR: [evidence/openvoice-cpu-asr-20261113.json](evidence/openvoice-cpu-asr-20261113.json)
- 재현 코드: [cpu-lab-openvoice/README.md](cpu-lab-openvoice/README.md)

## 후보 비교

| 우선 | 재료·방법 | 확인한 가능성 | 남는 한계·판정 |
| --- | --- | --- | --- |
| 보류 | 공식 Python + 선택적 MLP INT8 | 84개만 양자화한 실측에서 음성초당 합성21.4%·현재RSS30.2% 감소 | 공개 두 문장 ASR 모두 불일치. 이번 설정 채택하지 않음 |
| A | C 선택 규칙 정렬 | 위 3개 차이를 격리 옵션으로 시험 가능 | 모델의 정확한 수치·RNG 동등성은 없음. 품질과 프레임당 비용을 같이 판단 |
| A | 공개 모델의 검증된 캐시 | worker마다 약 5GB+설정 파일 다운로드를 줄일 후보 | 캐시 키나 캐시 안 HF metadata만으로 인증하면 안 됨. 저장소에 고정한 파일별 SHA256 필요 |
| A | TTS와 QA 키 분리 | 남아 있는31개 review WAV는 합성 없이 재검수 가능 | 원래 영수증을 덮어쓰거나 ASR 차이를 자동 승인하면 안 됨. 없는 후보는 복구 불가 |
| B | CPU 1/2/4스레드·affinity | 작은 decode 행렬의 스레드 조율 비용을 줄일 후보 | 코어 수만큼 성능이 증가하지 않음. 같은 CPU에서 측정 필요 |
| B | BF16·oneDNN·MLP compile | 같은 CPU에서 BF16은 이전 공개 시험의 음성초당 합성 시간을 약14.8% 줄임 | CPU 지원·PyTorch 버전 의존. 전체 강의 품질·속도 아님 |
| A | 하은/Supertonic 또는 Melo → OpenVoice V2 | 실제 하은+보라 참조 조합이 CPU2스레드에서 로딩 포함10.45초에8.75초 음성 | 짧은1회 실측. 보라 닮음 미판정·법령명ASR차이 남음. 기존1.7B와 동일음질/동일CPU 비교 아님 |
| B | Qwen 0.6B Base + 보라 참조 음성 | 공식 한국어 음성 복제와 참조 prompt 재사용, Apache-2.0 | 작은 CustomVoice는 실제 코드가 instruct를 무시. 기존 보라 지시문 이식과 구분해야 함 |
| C | KittenTTS 2 + C++ + 참조 준비 재사용 | 한국어 포함, 약1.66GB 기본 native 자산, 별도 참조 특징 준비 경로 | 저자 CPU RTF0.892는 8스레드·warm 영어 측정. 우리 한국어·4CPU 실측 아님. 가중치 상용 등록·표시 등 조건 확인 필요 |
| C | 새 KittenTTS 2 mini ONNX | 오늘 공개된 코드·문서는 82M/약82MB, 한국어 포함16언어를 명시 | 현재 가중치·config·license 접근을 모두 확인하지 못함. 문서상 고정 style이며 보라 복제 경로 없음 |
| C | 문장·절 계획과 짧은 지시문 | 불필요한 머뭇거림·호흡·긴 pause를 줄이는 가설 | 숫자·부정·의무·조건/예외절을 보존하며 한 조건씩 실험해야 함 |

F5-TTS 공식 가중치의 비상업 조건과 Kokoro 확인 언어 목록의 한국어 부재는
우선순위를 낮춘 이유다. 이름이 유명하거나 모델이 작다는 이유만으로
한국어·보라 음색·상용 배포가 모두 된다고 묶지 않는다.

## 논리·물리·청각을 연결하는 방법

### 논리: 한 줄의 속도 대신 전체 비용을 분해한다

전체 시간은 준비 + 모델 로드 + TTS + ASR + 후처리 + 저장 + 재시도로 나눈다.
ASR 부분의6.9% 개선을 전체 제작6.9%라고 하지 않는다. 병렬 worker 수40은
실제 shard가2개면 작업2개이며, shard를 늘리면 준비 비용도 늘어난다.
production은 이미 shard 안에서 모델을 한 번 로드한다. 매 문장 모델을
로드한다는 진단은 잘못이며 C CLI 실험과 운영 Python 경로를 구분한다.

### 물리: 파라미터 수보다 실제 데이터 이동과 직렬 단계를 본다

Qwen의12.5Hz 코덱은 음성1초당 약12.5프레임을 만든다. 16개 코드북과
CodePredictor의 연속 계산이 있으므로 큰 GPU/CPU 숫자만으로 가속을
예측하지 않는다. INT8은 가중치 이동과 행렬 연산 비용을 줄일 수 있지만
변환·packing·activation 양자화 비용과 실제 커널 지원을 함께 측정해야 한다.
84개 MLP만 바꾸는 실험은 codec·임베딩·attention·출력 head·CP를 보존한다.

현재 PyTorch2.8의 `set_float32_matmul_precision('high')`는 CPU 가속법이 아니다.
`mkldnn.matmul.fp32_precision='bf16'`은 확인한 문서상2.9 이후 API다.
Qwen은 `_supports_static_cache=False`이므로 일반 LLM의 static-KV+compile
설명을 그대로 적용하지 않는다. 다음 compile 후보는 MLP 부분만 묶고
compile 비용과 warm 이후 비용을 나눠 보는 것이다.

### 청각·언어: 느리게 읽는 것과 명료하게 읽는 것을 분리한다

한국어 읽기 연구는 긴 무음이 없어도 다른 운율 단서로 경계가 지각될 수
있음을 보여 준다. 영어 명료발화 연구도 정상 속도에서의 명료성 이점을
보고한다. 이 결과를 한국어 법령의 고정 최적 음절/초로 바꾸지는 않는다.

분석할 값은 전체 말속도, 휴지를 제외한 발화속도, 절 내부·문장 경계 휴지,
호흡, 어말 길이와 F0이다. RMS가 작은 모든 프레임을 무음이라고 부르지
않는다. 자음·약한 발성을 지울 수 있으므로 일괄 silence 절단도 피한다.

이전 같은 설명문 C64.96초/GPU53.05초의 차이11.91초가 모두 불필요한
길이라고 가정하면 약149코덱 프레임에 해당한다. 실제로 불필요한지는
내용·반복·경계·운율을 먼저 확인해야 한다. 이149프레임이 전체 연산을
같은 비율로 줄인다는 뜻은 아니다.

각 구간의 끝/앞 여유120ms와 추가 gap180/350ms가 합쳐질 가능성도 있다.
실제 경계 무음을 측정한 뒤 목표 총 pause에서 이미 있는 pause만 빼는
예산 방식이 후보다. 현재 C 샘플의 내부 늘어짐을 조립 gap 탓으로 단정하지 않는다.

## 재조합 실험 순서

1. 선택적 INT8 비교는 완료했으며 현재 설정은 품질 기준 탈락으로 보류한다.
2. C의 위3개 선택 규칙 정렬은 짧은 공개 원고 비교까지 완료했다.
   다음에는 이전 첫25초 원고의 말투를 통제해서 확인해야 한다.
3. 하은+OpenVoice의 변환 및 전체 조합 실측은 완료했다. 이 조합의
   음색 청취·독립 ASR·숫자/부정/의무 표현 검증을 우선한다.
4. 0.6B Base 참조 복제와 검증된 모델 캐시를 별도 버전에서 비교한다.
5. 이긴 경로에만 의미 단위 원고 계획·작업 분배·검수 분리를 결합한다.

이 목록의 가속 배율들을 곱해 전체 예상 속도라고 하지 않는다. 실제로
합성된 문장, 음성 길이, 정상 EOS, 신호, critical 표현, 독립 ASR 결과,
사용자가 판단하는 음색·자연스러움까지 통과해야 다음 단계로 간다.
원고 강제 정렬은 위치 찾기이며 발음 일치 증거가 아니다.

기존27개 통과 구간과31개 차단 영수증은 보존한다. 실험은 공개 문장과
별도 코드·출력에서 수행하고, 제작 설정·검수 기준·최종 조립은 변경하지 않는다.

## 원문

- C 소스: https://github.com/gabriele-mastrapasqua/qwen3-tts/tree/ef339be58a778b062e1c14382347964552eae007
- 공식 sampling: https://github.com/huggingface/transformers/blob/v4.57.3/src/transformers/generation/logits_process.py
- PyTorch dynamic INT8: https://docs.pytorch.org/docs/2.8/generated/torch.ao.quantization.quantize_dynamic.html
- Qwen 모델/참조 재사용: https://github.com/QwenLM/Qwen3-TTS
- Qwen 보고서: https://arxiv.org/abs/2601.15621
- 작은 Base: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base
- ONNX 후보: https://huggingface.co/wavekat/Qwen3-TTS-0.6B-Base-ONNX
- OpenVoice: https://github.com/myshell-ai/OpenVoice
- Melo 한국어: https://huggingface.co/myshell-ai/MeloTTS-Korean
- Kitten: https://github.com/KittenML/KittenTTS
- Kitten C++: https://github.com/KittenML/kitten-tts-2-cpp
- Kitten 가중치 조건: https://huggingface.co/KittenML/kitten-tts-2/raw/main/LICENSE.md
- 한국어 운율: https://www.isca-archive.org/interspeech_2007/cho07_interspeech.html
- 명료발화: https://pubmed.ncbi.nlm.nih.gov/12430828/
- GitHub cache: https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching
