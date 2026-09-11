from pathlib import Path

source = Path('public/ko-voice.js')
s = source.read_text(encoding='utf-8')

old = """    // 가지조문: 제3조의2 · 제3조의2제1항 → 제삼 조의 이 · 제삼 조의 이 제일 항
    // 일반 제N조 처리보다 먼저 잡아 '의2'를 독립된 가지 번호로 읽는다.
    t = t.replace(/제\\s*(\\d+)\\s*조\\s*의\\s*(\\d+)(?=\\s*제\\s*\\d)/g, (m, n, sub) => '제' + readSino(n) + ' 조의 ' + readSino(sub) + ' ');
    t = t.replace(/제\\s*(\\d+)\\s*조\\s*의\\s*(\\d+)/g, (m, n, sub) => '제' + readSino(n) + ' 조의 ' + readSino(sub));
    // 제6회 · 제3조제2항제1호 → 제육 회 · 제삼 조 제이 항 제일 호
"""
new = """    // 가지번호: 제3조의2 · 제2호의2서식 → 제삼 조의 이 · 제이 호의 이 서식
    // 일반 제N단위 처리보다 먼저 잡아 '의2'를 독립된 가지 번호로 읽는다.
    t = t.replace(/제\\s*(\\d+)\\s*(조|호)\\s*의\\s*(\\d+)(?=\\s*(?:제\\s*\\d|서식|(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)목))/g, (m, n, u, sub) => '제' + readSino(n) + ' ' + u + '의 ' + readSino(sub) + ' ');
    t = t.replace(/제\\s*(\\d+)\\s*(조|호)\\s*의\\s*(\\d+)/g, (m, n, u, sub) => '제' + readSino(n) + ' ' + u + '의 ' + readSino(sub));
    // 제1호가목 · 별지 제1호서식처럼 호 뒤의 세부 단위를 붙여 읽지 않는다.
    t = t.replace(/제\\s*(\\d+)\\s*호(?=\\s*(?:(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)목|서식))/g, (m, n) => '제' + readSino(n) + ' 호 ');
    // 제6회 · 제3조제2항제1호 → 제육 회 · 제삼 조 제이 항 제일 호
"""
assert s.count(old) == 1, 'legal branch anchor changed'
s = s.replace(old, new, 1)
source.write_text(s, encoding='utf-8')

tests = Path('test/frontend-invariants.test.cjs')
t = tests.read_text(encoding='utf-8')
corpus_anchor = "  '제3조의2제1항제4호에 따라 2026. 9. 11.부터 시행한다.',\n];"
assert t.count(corpus_anchor) == 1, 'corpus anchor changed'
t = t.replace(corpus_anchor, "  '제3조의2제1항제4호에 따라 2026. 9. 11.부터 시행한다.',\n  '별지 제2호의2서식과 별표 1 제1호가목을 확인한다.',\n];", 1)

test_anchor = "test('production canonical은 중복 쉼표와 핵심 조사 뒤 오분절을 만들지 않는다', () => {"
assert t.count(test_anchor) == 1, 'test insertion anchor changed'
extra = """test('법령 호 가지번호와 목·서식 경계를 보존한다', () => {
  const cases = [
    ['제1호의2', '제일 호의 이'],
    ['별지 제2호의2서식', '별지 제이 호의 이 서식'],
    ['제1호가목', '제일 호 가목'],
    ['제1조제2항제3호나목', '제일 조 제이 항 제삼 호 나목'],
    ['별지 제1호서식', '별지 제일 호 서식'],
    ['별표 1', '별표 일'],
    ['제1항부터 제3항까지', '제일 항부터 제삼 항까지'],
  ];
  for (const [raw, expected] of cases) assert.strictEqual(K.normalize(raw), expected, raw);
});

"""
t = t.replace(test_anchor, extra + test_anchor, 1)
tests.write_text(t, encoding='utf-8')
