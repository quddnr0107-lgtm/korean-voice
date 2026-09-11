from pathlib import Path

source = Path('public/ko-voice.js')
s = source.read_text(encoding='utf-8')
anchor = "    // 범위: 18~21개월 → 18개월에서 21개월\n"
assert s.count(anchor) == 1, 'range anchor changed'
rule = """    // 법령 목 안의 숫자 하위번호: 제1호가목2)에 → 제일 호 가목 이에 (닫는 괄호는 쉼으로 읽지 않는다.)
    t = t.replace(/((?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)목)\\s*(\\d+)\\)/g, (m, mok, n) => mok + ' ' + readSino(n));
"""
s = s.replace(anchor, rule + anchor, 1)
source.write_text(s, encoding='utf-8')

tests = Path('test/frontend-invariants.test.cjs')
t = tests.read_text(encoding='utf-8')
corpus_anchor = "  '별지 제2호의2서식과 별표 1 제1호가목을 확인한다.',\n];"
assert t.count(corpus_anchor) == 1, 'corpus anchor changed'
t = t.replace(corpus_anchor, "  '별지 제2호의2서식과 별표 1 제1호가목을 확인한다.',\n  '별표 1 제1호가목2)에 따른 가격을 적용한다.',\n];", 1)

test_anchor = "test('production canonical은 중복 쉼표와 핵심 조사 뒤 오분절을 만들지 않는다', () => {"
assert t.count(test_anchor) == 1, 'test insertion anchor changed'
extra = """test('법령 목의 숫자 하위번호에서 닫는 괄호를 불필요한 쉼으로 만들지 않는다', () => {
  const cases = [
    ['별표 1 제1호가목2)에 따른 가격', '별표 일 제일 호 가목 이에 따른 가격'],
    ['제1호나목3)을 적용한다.', '제일 호 나목 삼을 적용한다.'],
    ['나목12)까지', '나목 십이까지'],
    ['제2호다목4)', '제이 호 다목 사'],
  ];
  for (const [raw, expected] of cases) assert.strictEqual(K.normalize(raw), expected, raw);
});

"""
t = t.replace(test_anchor, extra + test_anchor, 1)
tests.write_text(t, encoding='utf-8')
