/* public/ 을 워커와 **같은 헤더**로 내주는 작은 서버 — 교차출처 격리(COOP/COEP)와 CSP 를 그대로 재현한다.
   /model/* 은 이미 내려받은 Supertonic 자산을 내준다(테스트에서 384MB 를 다시 받지 않는다). */
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { join, extname } from 'node:path';
const PUB = process.argv[2], MODEL = process.argv[3], PORT = +(process.argv[4] || 8123);
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.onnx': 'application/octet-stream', '.wasm': 'application/wasm' };
const CSP = ["default-src 'self'", "script-src 'self' 'wasm-unsafe-eval' blob: https://cdn.jsdelivr.net", "worker-src 'self' blob:", "style-src 'self' 'unsafe-inline'", "img-src 'self' data:", "connect-src 'self' blob: https://cdn.jsdelivr.net https://huggingface.co https://*.huggingface.co https://*.hf.co", "media-src 'self' blob:", "object-src 'none'", "base-uri 'self'", "frame-ancestors 'none'"].join('; ');
createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  console.log(new Date().toISOString().slice(11,19), req.method, path);
  const file = path.startsWith('/model/') ? join(MODEL, path.slice(7)) : join(PUB, path === '/' ? 'index.html' : path);
  try {
    await stat(file);
    const body = await readFile(file);
    res.writeHead(200, {
      'Content-Type': TYPES[extname(file)] || 'application/octet-stream',
      'Content-Length': body.length,
      'Content-Security-Policy': CSP,
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'credentialless',
      'Cross-Origin-Resource-Policy': 'cross-origin',
      'Access-Control-Allow-Origin': '*',
    });
    res.end(body);
  } catch (_) { res.writeHead(404); res.end('no'); }
}).listen(PORT, () => console.log('serving ' + PORT));
