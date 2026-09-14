// 문서에서 한국어 대본 뽑기 — 후보 비교 (research/toolkit/2026-09-13-doc-extract.json)
//   npm i mammoth unpdf 뒤 sample.docx · sample.pdf 를 같은 폴더에 두고: node bench_doc_extract.mjs
// 자리 ⑤ 실측 — 같은 한국어 대본을 docx/pdf 에서 뽑아 원문과 맞는지 본다.
import { readFile } from 'node:fs/promises';
const REF = ['2026년 9월 3일 기준 접수 인원은 1,234명입니다.',
             '근로기준법 제60조에 따라 15일의 유급휴가를 주어야 합니다.',
             '문의는 1588-9090, 접수는 09:00~18:00입니다.'];
const norm = (s) => s.replace(/\s+/g, ' ').trim();
const hit = (text) => REF.filter((r) => norm(text).includes(norm(r))).length;
const out = {};
const t0 = () => process.hrtime.bigint();
const ms = (a) => Number(process.hrtime.bigint() - a) / 1e6;

// 1) mammoth — docx
try {
  const mammoth = (await import('mammoth')).default;
  const a = t0();
  const { value } = await mammoth.extractRawText({ buffer: await readFile('sample.docx') });
  out.mammoth_docx = { ms: Math.round(ms(a)), hit: hit(value) + '/3', chars: value.length, head: norm(value).slice(0, 60) };
} catch (e) { out.mammoth_docx = { error: String(e.message).slice(0, 120) }; }

// 2) unpdf — Workers 에서도 돈다고 주장하는 경로
try {
  const { extractText, getDocumentProxy } = await import('unpdf');
  const a = t0();
  const pdf = await getDocumentProxy(new Uint8Array(await readFile('sample.pdf')));
  const { text } = await extractText(pdf, { mergePages: true });
  out.unpdf = { ms: Math.round(ms(a)), hit: hit(text) + '/3', chars: text.length, head: norm(text).slice(0, 60) };
} catch (e) { out.unpdf = { error: String(e.message).slice(0, 120) }; }

// 3) pdfjs-dist — 브라우저 표준 경로
try {
  const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs');
  const a = t0();
  const doc = await pdfjs.getDocument({ data: new Uint8Array(await readFile('sample.pdf')), useSystemFonts: false }).promise;
  let text = '';
  for (let i = 1; i <= doc.numPages; i++) {
    const c = await (await doc.getPage(i)).getTextContent();
    text += c.items.map((it) => it.str).join('') + '\n';
  }
  out.pdfjs = { ms: Math.round(ms(a)), hit: hit(text) + '/3', chars: text.length, head: norm(text).slice(0, 60) };
} catch (e) { out.pdfjs = { error: String(e.message).slice(0, 120) }; }

console.log(JSON.stringify(out, null, 1));
