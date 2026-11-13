# 하은 → 보라 참조 음색: CPU 재현 재료

이 폴더는2026-11-13 실제 로컬 실험의 코드를 바꾸지 않고 보존한 것이다.
운영 renderer나 자동 제작 요청이 아니다. `run_pilot.py`와 `run_pipeline.py`의
SHA256은 `../evidence/openvoice-cpu-*-20261113.json`에 기록돼 있다.
이미 수행한 짧은 실험 결과를 재현하거나 새 실험을 만드는 재료다.

## 필요한 작업 폴더

두 실행 파일은 자신의 폴더를 모델/출력 디렉터리로, 그 부모를 입력
디렉터리로 사용한다. 저장소 안에서 바로 실행하지 말고 별도 작업 폴더로
복사해 다음처럼 배치한다. 원본 모델이나 음성은 이 저장소에 포함하지 않는다.

```text
work/
  sample-raw.wav                 기존 하은9문장 원본,44.1kHz
  bora-gpu-demo.wav              사용자가 고른 보라 GPU 원본,24kHz
  render.py                     기존 하은 샘플 생성 코드(영수증용)
  assets/py/helper.py            supertonic-model-pins.json의 고정 코드
  assets/voice_styles/F2.json
  assets/onnx/                   고정된6개 ONNX/설정 파일
  openvoice-cpu-pilot/
    run_pilot.py
    run_pipeline.py
    prepare_embeddings.py
    tone_ref_encoder.onnx
    tone_converter.onnx
    model-metadata.json          {"sha":"34d010c192c97f763207f488f6057fd07fee42ad"}
```

필요한 공개 OpenVoice 파일은 `TigreGotico/voiceclonnx-openvoice-v2`의 위
revision에서 받는다. FP32 converter SHA256은
`7d7ee834c230037ead5cd6b64d44fcd842fbab5cd4cf6fe3ab381fad763325e9`,
encoder는 `3dd4918cab90e1acf7fa5c6f7539c27710e7a3cdfba550468c5ea49399178bf7`다.
기존 Supertonic 파일은 동봉한 pin 목록을 따른다. 코드와 모델 가중치의
라이선스는 각각 확인한다. 이 실험은 공개 모델 다운로드 외에 음성을
외부 서비스에 올리지 않았으며 GPU를 사용하지 않았다.

환경은 Python3.12.14, ORT1.31.1, NumPy2.5.4, SciPy1.18.1,
soundfile0.14.0이었다. 실험 당시 이미 설치된 환경을 사용했다.

## 실행 순서

1. 새 작업 폴더에서 `run_pilot.py`를 실행해 기존 두 음성을 자르고
   전처리·음색 변환의 실제 입출력·신호·시간을 기록한다.
2. `prepare_embeddings.py --pilot-dir ... --output ...`로 별도 새 디렉터리에
   source/target 특징과 캐시 영수증을 만든다. 기존 파일 덮어쓰기는 차단한다.
3. 새 실험 폴더에서 이 세 캐시 파일을 사용하도록 배치한 뒤
   `run_pipeline.py`로 하은 두 문장을 새로 생성하고 음색 변환한다.
4. 원본 WAV 해시·전사·숫자/부정 표현과 사용자의 청취 평가를 별도로 확인한다.

모델 로딩, TTS 추론, 후처리, 변환, 한 번의 참조 특징 추출 시간을
구분해 기록한다. 저장된 결과 WAV도 strict 운영 합격으로 자동 승격하지 않는다.
짧은 표본·공유 호스트·리샘플러 차이와 미실시한 PyTorch parity는 원본
영수증에 명시돼 있다. 최고 메모리와 장문 안정성은 아직 평가하지 않았다.
