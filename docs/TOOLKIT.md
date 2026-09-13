# 고정 무기 (TOOLKIT) — 2026-09-13

후보를 전수 조사한 뒤 **이 저장소의 4코어 CPU 환경에서 실제로 돌려 보고** 통과한 것만 적는다.
돌릴 수 없는 자리(결제·인증)는 그렇다고 밝히고 문서 근거로만 판정한다.
측정값은 `research/toolkit/*.json`, 재현 스크립트는 `research/toolkit/bench_*`.

원칙: **비상업 라이선스는 무조건 탈락** · 상시 서버가 필요한 것은 탈락(우리는 Workers + 컨테이너) · 무료 한도로 시작할 수 있어야 한다.

---

## ③ 오디오 후처리 — 실측 채택 ✅

| 무기 | 라이선스 | 왜 |
|---|---|---|
| **ffmpeg concat 디먹서 + 디코드 후 1회 인코딩** | LGPL-2.1+ | `-c copy` 는 조각마다 mp3 프레임 1개(1152샘플 = 24kHz 에서 **48ms**) 패딩을 남긴다. 조각 10개 26초에서 **+500ms** 밀렸다 |
| **loudnorm 2-pass `linear=true`** (I=-16 · TP=-1.5 · LRA=11) | LGPL-2.1+ | 전체에 단일 게인. 25.5초당 0.88초 |
| **pyloudnorm** | MIT | 결과 LUFS 검증(BS.1770-4). numpy 만 필요 |

실측(4코어 · Supertonic u5 steps 8 · 여성):

| 방식 | 길이 오차 | CPU(25.5초) | 결과 LUFS |
|---|---|---|---|
| `-c copy` | **+500ms** | 0.02s | −17.85 |
| 재인코딩 1회 | 0ms | 0.20s | −18.31 |
| + loudnorm 1-pass | 0ms | 0.62s | −16.71 |
| **+ loudnorm 2-pass linear** | **0ms** | 0.88s | **−16.48** |

- 합성 자체는 **RTF 0.521** (23.66초 오디오를 12.3초에) — 실시간의 2배.
- 조각 간 LUFS 편차가 **1.1 LU** 밖에 안 되므로 **문장별 정규화는 하지 않는다**(강조·감정의 셈여림을 뭉갠다).
- 1-pass vs 2-pass 의 구간 게인 편차는 0.85 vs 0.91 LU 로 **이 표본에서는 가릴 수 없었다.** 비용 차이가 0.26초뿐이니
  구조적으로 단일 게인이 보장되는 2-pass 를 쓴다(측정이 결론을 못 내면 이론이 이긴다).
- **탈락**: `sox`(GPL 전염) · `librosa`(numba/scipy 수백MB · 분석용 과잉) · `pydub`(3.13 에서 audioop 제거) · `-c copy`.
- 코드 반영: `server/server.py` 의 `render()`. 불변식은 `test/render-audio-invariants.test.cjs` 가 지킨다.

## ⓪ 우리 비용 0 — 합성을 사용자 브라우저로 (2026-09-13)

컨테이너가 유일한 변동비였다. 합성을 브라우저로 옮기면 **그 경로의 우리 비용이 0**이 되고, 오늘 겪은
이미지 재빌드 사고(컨테이너가 기동 못 하던 것)도 구조적으로 사라진다.

| 층 | 무기 | 무료 근거 |
|---|---|---|
| 오리진 | Cloudflare Workers 무료 | 요청 10만/일 + **정적 자산 요청은 무제한·무과금** |
| 모델 배달(384MB) | **R2 무료 10GB + 커스텀 도메인 + Cache Rules** | 저장 10GB·egress 영구 무료·Class B 월 1,000만 무료. 2023 ToS 개편으로 **CF 호스팅 대용량 파일의 CDN 배달은 허용**된다 |
| 합성 | 사용자 브라우저 (onnxruntime-web 1.17.0 + Supertonic 3 ONNX) | 우리 CPU 0 |
| 속도 | `COOP: same-origin` + `COEP: credentialless` → SharedArrayBuffer → WASM 멀티스레드 + SIMD, 있으면 WebGPU | 헤드리스 실측에서 `crossOriginIsolated: true` 확인 |
| 모델 캐시 | Cache API + `navigator.storage.persist()` | 한 번 받으면 재다운로드 0 |
| 부가 | D1(5GB) · KV · Web Analytics · Sentry(5천/월) · `*.workers.dev` | 전부 무료 |

### 🔴 약관 위반이라 못 쓰는 꼼수

| 수단 | 조항 |
|---|---|
| **Vercel Hobby** | "personal or **non-commercial** use only" — 수익화하면 무통보 삭제 |
| **GitHub Pages** | "free web hosting to run your online business…or SaaS" 명시 금지 |
| **jsDelivr 로 384MB 배달** | GitHub 단일 파일 20MB 상한 · "general-purpose file hosting = abuse" |
| **raw.githubusercontent** | CDN 용도 아님 + 2025-05 비인증 rate limit 강화 |
| **Colab·Kaggle 을 백엔드로** | "web service offerings not related to interactive compute" 금지 |
| **GitHub Actions 를 요청 기반 합성 백엔드로** | "serverless computing"·"소프트웨어 프로젝트와 무관한 활동" 금지. 지금 `bake.yml` 처럼 **우리 레포 산출물**을 굽는 것은 허용 |
| **Workers AI** | 한국어 TTS 모델이 없다(MeloTTS 는 EN/ZH) |

### 구현에서 실제로 걸린 것 (헤드리스 Chromium 실측)

1. **CSP 가 import map 을 막는다** — import map 은 인라인 `<script>` 라 `'unsafe-inline'` 이 필요하다.
   그걸 켜면 XSS 방어가 무너지므로 **모듈 한 개(`public/vendor/ort-cdn.js`)로 CDN 을 잇는다**.
2. **ORT 의 `.wasm` 404** — `ort.env.wasm.wasmPaths` 를 CDN 으로 지정해야 한다(기본은 페이지 상대경로).
3. **`writeWavFile` 이중 변환** — −1~1 실수를 받아 안에서 int16 으로 바꾼다. 미리 바꿔 넘기면 소리가 깨진다.
4. **`loadVoiceStyle` 은 가중 평균을 하지 않는다** — 스타일을 배치로 쌓기만 한다. 서버(F4:0.6+F2:0.4)와
   같은 목소리를 내려면 블렌딩을 우리가 해야 한다(`public/local-tts.js`).
5. **엔진 전처리가 영어를 심는다** — `@`→" at ", `e.g.,`→"for example, ". 서버·브라우저 두 경로가 같은
   전처리를 지나므로 `ko-voice.js` 정규화에서 먼저 한국어로 바꿔 막았다(골뱅이·예를 들어·즉).
6. **다듬기가 빠져 소리가 달랐다** — `server/voice_shape.py` 의 U5 중 4단계를 `public/voice-shape.mjs` 로
   옮겼다(트림·페이드·첫 음절 보강·꼬리 트림·피크 0.89). Praat PSOLA 억양만 옮기지 못했다.

## ④ 오독 자동 검증 — STT 왕복은 탈락 ❌ / 골든 텍스트로 대체 ✅

| 모델 | RTF(4코어) | canonical CER 평균 |
|---|---|---|
| faster-whisper `small` int8 | 0.812 | 0.1357 |
| faster-whisper `large-v3-turbo` int8 | 1.821 | **0.1424** (더 나쁨) |

**왜 탈락인가** — 전화번호·시각 문장에서 CER 0.52~0.55 가 났는데, 원인은 우리 TTS 가 아니었다:

```
우리 읽기 : 문의는 일오팔팔 구공구공, 접수는 매일 아홉 시부터 십팔 시까지입니다.   ← 정확
whisper   : 문의는 천우보 88 구정구신, 접수는 매일 연구웨어 언행팔입니다.          ← 못 알아들음
```

두 모델이 똑같이 숫자열에서 무너진다. **우리가 파는 바로 그 지점(숫자·단위·법령)에서 오탐이 나는 게이트는 해롭다.**

- **채택(제한)**: 산문 낭독 스크리닝에만. 산문 문장 CER 은 0.00~0.04 로 쓸 만하다. `small` 이 `turbo` 보다 싸고 정확하다.
- **대체**: 오독 검증의 정본은 **골든 텍스트 회귀**다 — 원문 → 기대 읽기를 고정해 두고 `node --test` 로 잰다
  (`test/ko-voice.test.cjs` 가 이미 그 방식이고 115개가 돌고 있다). 결정적이고 공짜이며 오탐이 없다.
- **canonical CER 자체는 유효**: `시월 육일` ↔ `10월 6일` 을 raw 0.13 → **canon 0.00** 으로 정확히 흡수했다
  (`research/judge.py` 의 자를 계속 쓴다).
- **탈락**: sherpa-onnx 한국어 zipformer(가중치가 AI Hub KsponSpeech — 상업 이용 별도 협의) · Kanana Nano(CC-BY-NC) ·
  NeMo Parakeet/Canary(한국어 미지원) · ETRI 오픈API(2025-06-30 종료) · openai-whisper(torch 2GB).

## ⑤ 대본 입력 — 실측 채택 ✅

| 무기 | 라이선스 | 실측 |
|---|---|---|
| **mammoth** (docx) | BSD-2-Clause | 한국어 3줄 **3/3 정확** · 237ms |
| **unpdf** (pdf) | MIT | 한국어 3줄 **3/3 정확** · 131ms · Workers 에서도 동작 |
| txt | — | 브라우저 기본 |

- `pdfjs-dist` 를 직접 import 하면 API/Worker 버전이 어긋난다(6.3.289 vs 6.1.200). **unpdf 가 감싼 경로를 쓴다.**
- **미검증**: hwpx(지방자치단체 공문서 HWPX 의무화 2026-05-18 — 한국 공공·교육 수요가 있다. JSZip + XML 직접 파싱이 후보) ·
  tesseract.js(스캔 PDF OCR · 브라우저 전용 · kor 데이터 5~15MB → 사용자 동의 후 지연 로딩).

## ① 결제 — 실행 테스트 불가(계정·사업자 정보 필요) · 문서 판정

🔴 **국내 PG(토스페이먼츠·포트원·페이플·이니시스·KCP·나이스)는 예외 없이 사업자등록증이 필수다.** 비사업자는 계약 자체가 안 된다.

| 상황 | 무기 | 수수료 |
|---|---|---|
| 사업자등록 있음 (한국 고객·원화) | **토스페이먼츠** — REST + Basic 인증, 웹훅 HMAC-SHA256 → Workers 완전 호환 | 카드 3.4% + VAT |
| 사업자등록 없음 (지금 당장) | **Polar** 또는 **Creem** — MoR(해외 정산), 개인 가능 | Polar 5% + $0.50 · Creem 3.9% + $0.40 |

- Polar 는 지급 국가에 South Korea 가 명시돼 있고, Creem 이 수수료가 더 싸다. 둘 다 Standard Webhooks/HMAC 로 Workers 호환.
- **탈락**: Stripe(한국 법인·사업자 계정 개설 불가) · PayPal(한국↔한국 국내거래 불가) · Gumroad(실효 ≈13%).
- 정기결제(구독)는 국내 PG 기준 **별도 심사**가 붙는다 — 첫 출발은 단건 결제 + 크레딧이 빠르다.

## ② 인증 — 문서 판정 · 1순위 명확

| 무기 | 라이선스 | 왜 |
|---|---|---|
| **Better Auth** (+ D1/KV) | MIT | **카카오·네이버 provider 내장** · Cloudflare 공식 연동 패키지 · 엣지 네이티브 · 무료 |
| 자체 JWT + D1/DO | — | 락인 0. WebCrypto 만으로 완결되고 카카오/네이버 OAuth 는 REST 직접 호출 |

- **탈락**: Lucia(2025-03 deprecated) · Hanko·Stytch(카카오·네이버 없음) · Cloudflare Access(사내 도구용, B2C 회원 부적합).
- 후보: Supabase Auth(무료 50k MAU · 카카오 공식, 네이버는 직접) · Clerk(무료 50k MRU이나 카카오·네이버는 상위 플랜 custom OIDC).

## ⑥ 운영 — 전부 무료로 성립

| 자리 | 무기 | 무료 한도 |
|---|---|---|
| 로그·에러 | **Workers Logs** (플랫폼 내장) | Free 플랜 3일 보존 |
| 사용량 집계 | **Workers Analytics Engine** | 10만 writes/일 |
| 방문 분석 | **Cloudflare Web Analytics** | 무제한 · **IP 미저장** |
| 예외 추적 | **Sentry** `@sentry/cloudflare` | 5,000 이벤트/월 |
| 업타임 | **Cronitor** | 모니터 5개 |

- 🔴 **UptimeRobot 무료 플랜은 2024-12부터 상업적 이용 금지** — 우리 용도로 쓰면 계정 정지 위험. 탈락.
- **탈락**: Baselime(Cloudflare 인수 후 단독 운영 중단) · Highlight.io(2026-02-28 서비스 종료) · Workers Logpush(유료 플랜 전용).

---

## 지금 바로 붙일 순서

1. **③ 는 이미 코드에 들어갔다** — `render()` 가 2-pass loudnorm 으로 잇는다.
2. **⑤ mammoth + unpdf** — `/app.html` 에 파일 올리기를 붙인다. 브라우저에서 뽑아 서버로 **텍스트만** 보낸다.
3. **① 결제** — 사업자등록 여부가 갈림길이다. 없으면 Creem/Polar 로 오늘 시작할 수 있다.
4. **② Better Auth** — 무료 한도를 IP 에서 계정으로 옮기는 순간 필요해진다(지금은 `Meter` DO 가 sha1(IP) 로 센다).
5. **⑥** — 결제를 붙이는 날 Sentry + Analytics Engine 을 같이 켠다.
