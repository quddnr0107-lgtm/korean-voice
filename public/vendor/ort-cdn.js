/* onnxruntime-web 을 CDN 에서 잇는 자리 — **여기 한 줄이 버전의 정본**이다.
   왜 import map 이 아니라 이 파일인가: import map 은 인라인 <script> 라서 CSP 에 'unsafe-inline' 을
   넣어야 실행된다(2026-09-13 헤드리스 실측에서 막혔다). 그걸 켜면 XSS 방어가 무너지므로,
   바 스펙 대신 상대 경로 모듈 하나로 잇는다 — CSP 는 script-src 에 CDN 만 허용하면 된다.
   1.17.0 으로 못 박은 이유: research/tts-arena/results/supertonic-browser-fp16-wasm-*.json 의
   실측이 같은 버전이라 숫자를 비교할 수 있다. 올릴 때는 그 실측을 다시 돌린다. */
export * from 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.17.0/dist/esm/ort.min.js';
/** ORT 가 .wasm 을 찾는 곳 — 페이지 기준 상대경로로 찾으면 404 가 난다(실측). */
export const ORT_WASM_PATHS = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.17.0/dist/';
