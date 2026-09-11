from pathlib import Path

source = Path('public/ko-voice.js')
s = source.read_text(encoding='utf-8')
anchor = "    // 전화번호: 010-1234-5678 → 공일공 일이삼사 오육칠팔\n"
assert s.count(anchor) == 1, 'normalize anchor changed'
rule = """    // 법령 조 제목: 제1조(목적) · 제3조의2(적용 범위) → 괄호를 쉼표로 만들지 않고 의미 경계만 보존한다.
    t = t.replace(/(제\\s*\\d+\\s*조(?:\\s*의\\s*\\d+)?)\\s*\\(([^()\\n]{1,60})\\)/g, '$1 $2');
"""
s = s.replace(anchor, rule + anchor, 1)
source.write_text(s, encoding='utf-8')

tests = Path('test/frontend-invariants.test.cjs')
t = tests.read_text(encoding='utf-8')
corpus_anchor = "  '별표 1 제1호가목2)에 따른 가격을 적용한다.',\n];"
assert t.count(corpus_anchor) == 1, 'corpus anchor changed'
t = t.replace(corpus_anchor, "  '별표 1 제1호가목2)에 따른 가격을 적용한다.',\n  '제3조의2(적용 범위)에 따라 대상자를 정한다.',\n];", 1)

test_anchor = "test('production canonical은 중복 쉼표와 핵심 조사 뒤 오분절을 만들지 않는다', () => {"
assert t.count(test_anchor) == 1, 'test insertion anchor changed'
extra = """test('법령 조 제목 괄호는 불필요한 쉼표 없이 의미 경계를 보존한다', () => {
  const cases = [
    ['제1조(목적)', '제일 조 목적'],
    ['제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다.', '제이 조 정의 이 법에서 사용하는 용어의 뜻은 다음과 같다.'],
    ['제3조의2(적용 범위)', '제삼 조의 이 적용 범위'],
    ['제10조 (신청 및 처리)', '제십 조 신청 및 처리'],
    ['제3조의2(적용 범위)에 따라', '제삼 조의 이 적용 범위에 따라'],
  ];
  for (const [raw, expected] of cases) assert.strictEqual(K.normalize(raw), expected, raw);
});

"""
t = t.replace(test_anchor, extra + test_anchor, 1)
tests.write_text(t, encoding='utf-8')
