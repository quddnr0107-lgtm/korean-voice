# korean-voice 즉시 합성 서버 — CPU 하나로 돈다 (x86_64 · arm64 모두). Cloudflare Containers(Workers Builds)가 이 파일로 이미지를 만든다.
#   docker build -t korean-voice-tts .
#   docker run -d --name tts -p 8790:8790 -v tts-cache:/app/cache --restart unless-stopped korean-voice-tts
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir onnxruntime numpy soundfile PyYAML imageio-ffmpeg praat-parselmouth && \
    apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
# Supertonic 3 자산(384MB) — 이미지에 굽는다(서버가 켜지자마자 바로 답한다)
RUN mkdir -p supertonic3/onnx supertonic3/voice_styles supertonic3/py && \
    B=https://huggingface.co/Supertone/supertonic-3/resolve/main && \
    for f in config.json onnx/duration_predictor.onnx onnx/text_encoder.onnx onnx/tts.json onnx/unicode_indexer.json onnx/vector_estimator.onnx onnx/vocoder.onnx; do curl -sL -o supertonic3/$f "$B/$f"; done && \
    for s in F1 F2 F3 F4 F5 M1 M2 M3 M4 M5; do curl -sL -o supertonic3/voice_styles/$s.json "$B/voice_styles/$s.json"; done && \
    for f in helper.py example_onnx.py; do curl -sL -o supertonic3/py/$f https://raw.githubusercontent.com/supertone-inc/supertonic/main/py/$f; done
# 🔴 server.py 가 import 하는 우리 모듈을 **전부** 넣는다. 2026-09-04 이후 recipes.py·voice_shape_k2.py(#19)와
#    singleflight.py(#27) 가 생겼는데 이 줄이 따라오지 않아, server/ 를 고친 첫 커밋의 이미지 재빌드에서
#    컨테이너가 `ModuleNotFoundError: No module named 'singleflight'` 로 기동 실패했다(2026-09-13).
#    test/container-image.test.cjs 가 import 와 이 줄이 어긋나면 잡는다.
COPY server.py voice_shape.py voice_shape_k2.py recipes.py singleflight.py ./
ENV SUPERTONIC_DIR=/app/supertonic3 CACHE_DIR=/app/cache PORT=8790 STEPS=16 ALLOW_ORIGIN=*
EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:8790/health || exit 1
CMD ["python3", "server.py"]
