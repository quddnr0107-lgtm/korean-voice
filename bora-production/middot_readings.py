"""Reviewed pronunciation of exact Korean legal enumeration lexemes.

Only the closed inventory below is approved. Whole Hangul lexemes include the
reviewed particles/inflections; new words, new inflections, numbers, Latin text
and spaced separators remain unchanged and require review. The three separator
glyphs have the same lookup meaning, but source spelling is kept in each edit.
No original manuscript or delivery prompt is read or changed by this module.
"""
from __future__ import annotations

import re

RULESET = 'bora-reviewed-middot-readings/v1'
SEPARATORS = '·ㆍᆞ'
# Each complete lexeme was checked as an enumeration/coordination. This is a
# finite pronunciation dictionary, not a rule to split arbitrary middle dots.
REVIEWED_LEXEMES = (
    '거짓보고·지연보고',
    '검토·분석하여',
    '경비·보안',
    '경비·보안활동에',
    '경비·보안활동의',
    '경찰·군과',
    '경찰청·해양경찰청',
    '공항·항만',
    '교통·산업시설로',
    '교통·통신',
    '교통·통신시설을',
    '교통·통신시설이',
    '교통·통신이',
    '국가정보원·군·경찰',
    '국가정보원·군·경찰·지방자치단체',
    '군·경',
    '군·경찰·예비군',
    '군·경합동상황실',
    '군·경합동상황실도',
    '군·경합동상황실로',
    '군·경합동상황실은',
    '군·경합동상황실을',
    '군·경합동상황실이',
    '군·경합동상황실입니다',
    '금지·제한',
    '금지·제한·퇴거명령의',
    '금지·제한하거나',
    '기준·절차',
    '기준·절차·공고',
    '민·관·경·군',
    '민·관·군',
    '발령·해제',
    '방송·확성기·벽보',
    '보급·급식',
    '산업·수송·장비',
    '상황실·육성·지원·주민신고면',
    '설치·운영',
    '설치·운영입니다',
    '설치·운영한다',
    '설치·운영할',
    '설치·운용하는',
    '설치·운용할',
    '수립·조정',
    '수립·조정을',
    '수립·조정이라는',
    '수립·조정입니다',
    '수송·장비',
    '시·군',
    '시·군·구',
    '시·군·구·읍·면·동',
    '시·군·구까지',
    '시·군·구까지만',
    '시·군·구까지입니다',
    '시·군·구는',
    '시·군·구도',
    '시·군·구라는',
    '시·군·구를',
    '시·군·구에는',
    '시·군·구에도',
    '시·군·구에서',
    '시·군·구와',
    '시·군·구의',
    '시·군·구처럼',
    '시·군·자치구·읍·면·동의',
    '시·군·자치구·읍·면·동이',
    '시·군·자치구에서는',
    '시·군을',
    '시·도',
    '시·도가',
    '시·도경찰청에',
    '시·도경찰청장',
    '시·도경찰청장과',
    '시·도경찰청장도',
    '시·도경찰청장등',
    '시·도경찰청장등으로',
    '시·도경찰청장등은',
    '시·도경찰청장등이',
    '시·도경찰청장등이라',
    '시·도경찰청장등이라고',
    '시·도경찰청장등입니다',
    '시·도경찰청장뿐입니다',
    '시·도경찰청장에게는',
    '시·도경찰청장으로',
    '시·도경찰청장으로부터',
    '시·도경찰청장은',
    '시·도경찰청장을',
    '시·도경찰청장이',
    '시·도경찰청장이나',
    '시·도경찰청장이면',
    '시·도경찰청장입니다',
    '시·도까지만',
    '시·도나',
    '시·도는',
    '시·도를',
    '시·도마다',
    '시·도만',
    '시·도부터',
    '시·도에',
    '시·도에는',
    '시·도에도',
    '시·도에서',
    '시·도와',
    '시·도의',
    '시·도의회',
    '시·도의회가',
    '시·도의회는',
    '시·도의회도',
    '시·도의회로',
    '시·도의회를',
    '시·도의회에',
    '시·도의회에서',
    '시·도의회와',
    '시·도의회의',
    '시·도의회입니다',
    '시·도지사',
    '시·도지사가',
    '시·도지사나',
    '시·도지사는',
    '시·도지사도',
    '시·도지사등',
    '시·도지사등에게',
    '시·도지사등은',
    '시·도지사등이',
    '시·도지사등이라',
    '시·도지사등이라고',
    '시·도지사등이었지요',
    '시·도지사라',
    '시·도지사라고',
    '시·도지사로',
    '시·도지사를',
    '시·도지사에게',
    '시·도지사에게도',
    '시·도지사에게를',
    '시·도지사였습니다',
    '시·도지사와',
    '시·도지사의',
    '시·도지사이고',
    '시·도지사입니다',
    '시·도협의회에',
    '시·도협의회의',
    '시장·군수·구청장',
    '시장·군수·구청장·읍장·면장·동장',
    '시장·군수·구청장과',
    '시장·군수·구청장으로',
    '시장·군수·구청장은',
    '시장·군수·구청장의',
    '시장·군수·구청장이',
    '시장·군수·구청장이고',
    '시장·군수·구청장이었습니다',
    '시장·군수·구청장이었지요',
    '시장·군수·구청장입니다',
    '시장·군수로',
    '시장·군수만',
    '신문·방송에',
    '실시방법·절차',
    '심의·의결한',
    '요건·절차',
    '운영·육성',
    '육성·운용',
    '육성·지원',
    '육성·지원과',
    '육성·지원으로',
    '육성·지원은',
    '육성·지원이',
    '육성·지원입니다',
    '을종·병종',
    '을종·병종사태는',
    '읍·면·동',
    '읍·면·동까지',
    '읍·면·동에는',
    '읍·면·동에도',
    '읍·면·동에서는',
    '읍·면·동은',
    '읍·면·동의',
    '읍·면·동이',
    '읍·면·동장',
    '읍·면·동장은',
    '읍장·면장·동장',
    '읍장·면장·동장은',
    '읍장·면장·동장이',
    '읍장·면장·동장입니다',
    '의료·구호',
    '이동·배치·성능이나',
    '인력·재정',
    '인명·신체에',
    '인원·시설',
    '입항·출항',
    '작전·훈련',
    '작전·훈련의',
    '작전·훈련지침',
    '전·후에',
    '정책·분석·조정이면',
    '제십사조제일항·제사항에',
    '제십이조제삼항·제오항',
    '조사·분석하고',
    '조정·분담하고',
    '조정·통제',
    '조정·통제면',
    '좌·우측',
    '지도·감독',
    '지도·감독은',
    '지도·감독을',
    '지연·변경',
    '지휘·통신망의',
    '지휘·통신체계',
    '지휘·통신체계와',
    '지휘·통제',
    '지휘·통제하는',
    '지휘·협조체계',
    '지휘·협조체계는',
    '지휘·협조체계로',
    '지휘·협조체계를',
    '지휘·협조체계입니다',
    '차량·선박',
    '차량·선박·시설',
    '체육·문화시설',
    '텔레비전·라디오',
    '통로·시간·방법',
    '통신·전산',
    '통합·운영',
    '통합·운영을',
    '통합·운영이라는',
    '통합·운영할',
    '통합방위작전·훈련',
    '통합방위작전·훈련에',
    '통합방위작전·훈련의',
    '통합방위작전·훈련이',
    '특별시·광역시·특별자치시·도·특별자치도',
    '특별시·광역시·특별자치시·도·특별자치도·시·군·자치구·읍·면·동의',
    '특별시·광역시·특별자치시·도·특별자치도·시·군·자치구의',
    '특별시장·광역시장·특별자치시장·도지사·특별자치도지사',
    '특별시장·광역시장·특별자치시장·도지사·특별자치도지사·시장·군수',
    '해군·공군은',
    '해군·공군의',
    '협의·조정',
    '홍보·계몽',
    '홍보·계몽에도',
    '확보·유지',
    '확보·유지가',
    '확인·감독',
    '확인·감독은',
    '확인·감독을',
    '확인·감독이면',

)
READINGS = {term: term.replace('·', ', ') for term in REVIEWED_LEXEMES}
assert len(READINGS) == len(REVIEWED_LEXEMES) == 243
_CLUSTER = re.compile(r'(?<![\w·ㆍᆞ])([가-힣]+(?:[·ㆍᆞ][가-힣]+)+)(?![\w·ㆍᆞ])')


def normalize(spoken):
    """Return (spoken, reversible_edits, unresolved_review_flags).

    Offsets are Python code points in the INPUT spoken string, not in the raw
    manuscript. The caller retains the raw manuscript and its separate mapping.
    Unknown notation is retained verbatim. No review flag is silently cleared.
    """
    if not isinstance(spoken, str):
        raise ValueError('middle_dot_input_type')
    parts, edits, flags = [], [], []
    cursor = delta = 0
    covered = set()
    for match in _CLUSTER.finditer(spoken):
        original = match[0]
        covered.update(i for i in range(match.start(), match.end()) if spoken[i] in SEPARATORS)
        key = original.translate(str.maketrans({'ㆍ': '·', 'ᆞ': '·'}))
        replacement = READINGS.get(key)
        if replacement is None:
            flags.append(dict(source_start=match.start(), source_end=match.end(),
                              original=original, reason='unreviewed_middle_dot_lexeme'))
            continue
        parts.extend((spoken[cursor:match.start()], replacement))
        edits.append(dict(source_start=match.start(), source_end=match.end(),
                          spoken_start=match.start() + delta,
                          spoken_end=match.start() + delta + len(replacement),
                          original=original, replacement=replacement,
                          rule=RULESET, reviewed_lexeme=key))
        delta += len(replacement) - len(original)
        cursor = match.end()
    parts.append(spoken[cursor:])
    result = ''.join(parts)
    for index, char in enumerate(spoken):
        if char in SEPARATORS and index not in covered:
            flags.append(dict(source_start=index, source_end=index + 1,
                              original=char, reason='unreviewed_middle_dot_notation'))
    flags.sort(key=lambda flag: (flag['source_start'], flag['source_end']))
    # Every edit must preserve exactly the lexical/alphanumeric content.
    lexical = lambda text: ''.join(c for c in text if c.isalnum() and c not in SEPARATORS)
    if lexical(spoken) != lexical(result):
        raise ValueError('middle_dot_lexical_content_changed')
    reverse = result
    for edit in reversed(edits):
        a, b = edit['spoken_start'], edit['spoken_end']
        if reverse[a:b] != edit['replacement']:
            raise ValueError('middle_dot_replacement_span')
        reverse = reverse[:a] + edit['original'] + reverse[b:]
    if reverse != spoken:
        raise ValueError('middle_dot_reverse_reconstruction')
    return result, edits, flags


apply_reviewed_readings = normalize
