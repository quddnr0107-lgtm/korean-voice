# 브라우저 합성 실측 (우리 비용 0 경로)

`public/local-tts.js` 가 실제 브라우저에서 끝까지 도는지, 얼마나 빠른지 잰다.
결과: `research/toolkit/2026-09-13-browser-synthesis.json`

```bash
npm i playwright                      # 저장소 의존성 아님 — 재는 사람만 설치한다
# Supertonic 자산(384MB)을 받아 둔다: huggingface.co/Supertone/supertonic-3 의 onnx/·voice_styles/
node serve.mjs <저장소>/public <자산폴더> 8123 > serve.log &   # 워커와 같은 헤더(COOP/COEP·CSP)로 내준다
CHROME=<chrome 실행파일> ORT_DIR=<ort dist 를 받아 둔 폴더> node measure.mjs
```

- `serve.mjs` 는 **워커와 같은 보안 헤더**를 보낸다 — 교차출처 격리가 안 되면 숫자가 달라진다.
- `measure.mjs` 는 CDN 요청을 `ORT_DIR` 의 파일로 채운다(샌드박스에서 CDN 에 못 나갈 때).
  실제 배포에서는 그대로 CDN 에서 받는다.
- 단계마다 즉시 찍는다. 어디서 막히는지 보려고 그렇게 만들었다 — 실제로 막힌 곳 5군데를 이걸로 찾았다.
