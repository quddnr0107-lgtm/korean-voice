# 즉시 합성 서버 (korean-voice/server)

굽지 않는다. 사이트에서 재생을 누르면 문장 단위로 이 서버가 합성해 돌려주고 캐시한다.
첫 청취자만 첫 문장 1.5~2초를 기다리고, 그 뒤 문장은 재생 중에 앞서 만들어진다. 같은 문장은 두 번 만들지 않는다.

```
브라우저(yebijun)                        서버(이 폴더)
  ko-voice.js  정규화·문장 나누기·쉼   →  GET /tts?v=female&t=문장[&r=속도배수]   → Supertonic 3 (CPU) → 다듬기(voice_shape.py) → mp3 → 디스크 캐시 → CDN 캐시
  live-tts.js  조각 이어 재생·미리받기 →  POST /warm {texts:[…]}     → 나머지 문장 백그라운드 합성
```

## 실측 (이 저장소 개발 세션 · 4코어 CPU · 디노이징 16단계)
| 항목 | 값 |
|---|---|
| 한 문장(30~50자) 첫 합성 | 2.1~4.2초 (음성 3~6초 분량) |
| 캐시 적중 | 10ms |
| 8단계 | 2.1초 (품질 약간 낮음) |
| 실시간 대비 속도 | 약 1.5배(16단계) — 재생을 앞지른다 |

## 올리기 — 서버 한 대면 된다
1. 서버: 4코어 이상 x86_64 또는 arm64(Oracle Cloud 무료 ARM 4코어도 됨). Docker 설치.
2. DNS: `tts.yebijun.drillstudy.com` A 레코드 → 서버 IP. (Cloudflare 프록시를 켜면 CDN 캐시까지 붙는다.)
3. 이 폴더에서 `docker compose up -d --build` (모델 384MB를 이미지에 넣는다 · 첫 빌드 3~5분).
4. 확인: `curl https://tts.yebijun.drillstudy.com/health` → `{"ok":true,…}`.
5. 사이트: `_deploy/index.html` 의 `window.LIVE_TTS_BASE = 'https://tts.yebijun.drillstudy.com'` 로 바꿔 배포.
   비어 있으면 기능이 잠겨 있어 아무 영향이 없다.

Docker 없이: `pip install onnxruntime numpy soundfile librosa PyYAML imageio-ffmpeg praat-parselmouth` 후 Supertonic 자산을 받아
`SUPERTONIC_DIR=… PORT=8790 python3 server.py` (systemd 로 상시 실행).

## API
- `GET /health` → `{ok, voices, cached, steps, recipe, queue, stats}`
- `GET /tts?v=female|male&t=<문장>[&s=단계][&r=속도배수]` → `audio/mpeg` · `Cache-Control: immutable` · CORS `*` · `Range` 지원(iOS)
  · `r` = 합성 속도 배수(0.7~1.6 · 조각별 완급 · 기본 1.0). 캐시 키 = `sha1(voice|steps|r|조합표식|text)` (`lib/tts-key.mjs` · 워커와 같다)
- `POST /warm` `{v, texts:[…][, r]}` → 대기열에 넣고 바로 `{queued}`; 백그라운드가 순서대로 굽는다
- 목소리·다듬기는 `voice_shape.py` 가 정본이다(여성 F4:0.6+F2:0.4 = 사람이 표본을 듣고 정한 「U4」 · 남성 M1:0.7+M3:0.3).
  합성 뒤 **다듬기**가 붙는다: trim(앞여유 50ms) → 첫 음절 보강 → Praat PSOLA 억양(문장 끝 +6반음 · 물음 +8 · 위로만 1.8배 · +1반음).
  문장 끝(hard)은 글자로 판정한다(`is_sentence_end` — 문장부호·「다/요/까」로 끝나면 끝, 쉼표·낱말로 끝나면 중간). 조합을 바꾸면 `RECIPE_TAG` 를 올려라 — 캐시 키가 갈린다.
  `python3 server/voice_shape.py --selftest` · `node --test test/tts-key.test.mjs`(워커·서버 키가 같은가).
  🔴 서버는 `X-TTS-Recipe` 헤더로 자기 표식을 보내고, 워커는 **그 값이 자기 표식과 같을 때만** R2 에 넣는다 — Workers Builds 가 워커와
  컨테이너 이미지를 따로 올려 워커만 새 판인 창이 생기기 때문(그때 옛 소리를 새 키로 넣으면 영영 안 지워진다). 워커 `/health` 의 `recipe_match` 로 본다.

## 왜 굽기 대신 이것인가
전체 강의(예비군 사이트 기준 240만 자 ≈ 음성 117시간)를 두 목소리로 미리 구우면 CPU 160시간이 든다.
학생이 실제로 듣는 편만, 들을 때 만들면 총량은 같아도 **기다림이 분산**되고 새 원고·새 목소리가 바로 반영된다.
