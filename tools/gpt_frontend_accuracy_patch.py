from pathlib import Path

source = Path('public/ko-voice.js')
s = source.read_text(encoding='utf-8')

# `2026. 9. 11.` must not be mistaken for an ordered Markdown list item (`2026. `).
old_markdown = r"t = t.replace(/^[ \t]*(#{1,6}|[-*•]|\d+[.)])\s+/gm, '');"
new_markdown = r"t = t.replace(/^[ \t]*(#{1,6}|[-*•]|(?!\d{4}\.\s+\d{1,2}\.)\d+[.)])\s+/gm, '');"
assert s.count(old_markdown) == 1, 'markdown normalization anchor changed'
s = s.replace(old_markdown, new_markdown, 1)

old_date = r"t = t.replace(/\b(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\.?(?!\d)/g,"
new_date = r"t = t.replace(/\b(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})\.?(?!\d)/g,"
assert s.count(old_date) == 1, 'date normalization anchor changed'
s = s.replace(old_date, new_date, 1)

anchor = "    // 제6회 · 제3조제2항제1호 → 제육 회 · 제삼 조 제이 항 제일 호\n"
assert s.count(anchor) == 1, 'legal normalization anchor changed'
legal = """    // 가지조문: 제3조의2 · 제3조의2제1항 → 제삼 조의 이 · 제삼 조의 이 제일 항
    // 일반 제N조 처리보다 먼저 잡아 '의2'를 독립된 가지 번호로 읽는다.
    t = t.replace(/제\\s*(\\d+)\\s*조\\s*의\\s*(\\d+)(?=\\s*제\\s*\\d)/g, (m, n, sub) => '제' + readSino(n) + ' 조의 ' + readSino(sub) + ' ');
    t = t.replace(/제\\s*(\\d+)\\s*조\\s*의\\s*(\\d+)/g, (m, n, sub) => '제' + readSino(n) + ' 조의 ' + readSino(sub));
"""
s = s.replace(anchor, legal + anchor, 1)
source.write_text(s, encoding='utf-8')

tests = Path('test/frontend-invariants.test.cjs')
t = tests.read_text(encoding='utf-8')
corpus_anchor = "  '제12조제3항제2호에 따른 대상자는 21명입니다.',\n];"
assert t.count(corpus_anchor) == 1, 'corpus anchor changed'
t = t.replace(corpus_anchor, "  '제12조제3항제2호에 따른 대상자는 21명입니다.',\n  '제3조의2제1항제4호에 따라 2026. 9. 11.부터 시행한다.',\n];", 1)

test_anchor = "test('production canonical은 중복 쉼표와 핵심 조사 뒤 오분절을 만들지 않는다', () => {"
assert t.count(test_anchor) == 1, 'test anchor changed'
extra = """test('가지조문과 공포문 점 표기 날짜를 의미 경계대로 읽는다', () => {
  const cases = [
    ['제3조의2', '제삼 조의 이'],
    ['제3조의2에 따라', '제삼 조의 이에 따라'],
    ['제3조의2제1항제4호', '제삼 조의 이 제일 항 제사 호'],
    ['2026. 9. 11.', '이천이십육 년 구 월 십일 일'],
    ['2026 . 9 . 11', '이천이십육 년 구 월 십일 일'],
  ];
  for (const [raw, expected] of cases) assert.strictEqual(K.normalize(raw), expected, raw);
});

"""
t = t.replace(test_anchor, extra + test_anchor, 1)
tests.write_text(t, encoding='utf-8')
