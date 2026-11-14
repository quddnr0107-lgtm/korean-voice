"""Source-bound reading and pronunciation refinements for Military Service lessons."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from production_plan import digest

RULESET = 'bora-ms-pronunciation/v1'
RULES_FILE = Path(__file__).with_name('ms-pronunciation.json')
SEPARATORS = '·ㆍᆞ'
CANONICAL_SEPARATOR = str.maketrans(dict.fromkeys(SEPARATORS, '·'))
LEXEME = 'unreviewed_middle_dot_lexeme'
NOTATION = 'unreviewed_middle_dot_notation'
REMAINING = 'review_remaining_middot_do_not_globally_split'
LONG = 'review_long_segment_at_semantic_boundary'
FRAGMENT = 'reading_mode_ambiguous_legal_fragment'
HAN = 'han-reading-review'
_SHA256 = re.compile('[0-9a-f]{64}')
_DAY_NAME = digest('육·이십오')


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _scope(lesson_id):
    return isinstance(lesson_id, str) and re.fullmatch(r'MS-[0-9]{3}', lesson_id) and 1 <= int(lesson_id[3:]) <= 166


@lru_cache(maxsize=1)
def _rules():
    _require(RULES_FILE.is_file() and not RULES_FILE.is_symlink(), 'ms_rules_file')
    raw = RULES_FILE.read_bytes()
    _require(0 < len(raw) < 65536, 'ms_rules_size')
    value = json.loads(raw)
    required = {'schema', 'reference_revision', 'middot_lexeme_sha256', 'middot_context_pins',
                'reading_fragment_pins', 'han_duplicate_pins'}
    _require(isinstance(value, dict) and set(value) == required and value['schema'] == RULESET
             and re.fullmatch('[0-9a-f]{40}', value['reference_revision']), 'ms_rules_schema')
    lexemes = value['middot_lexeme_sha256']
    _require(isinstance(lexemes, list) and 0 < len(lexemes) <= 1024
             and all(isinstance(x, str) and _SHA256.fullmatch(x) for x in lexemes)
             and lexemes == sorted(set(lexemes)) and _DAY_NAME not in lexemes, 'ms_rules_lexemes')
    contexts, fragments, han = set(), set(), set()
    for key, fields, cap in (
        ('middot_context_pins', ('id', 'input_sha256', 'offset', 'glyph'), 128),
        ('reading_fragment_pins', ('id', 'source_sha256', 'paragraph_id', 'paragraph_sha256'), 64),
        ('han_duplicate_pins', ('id', 'unit_id', 'spoken_sha256'), 4),
    ):
        rows = value[key]
        _require(isinstance(rows, list) and 0 < len(rows) <= cap, 'ms_rules_pin_count')
        for row in rows:
            _require(isinstance(row, dict) and set(row) == set(fields) and _scope(row['id'])
                     and all(isinstance(row[f], str) and _SHA256.fullmatch(row[f])
                             for f in fields if f.endswith('sha256')), 'ms_rules_pin_scope')
            item = tuple(row[f] for f in fields)
            ident = row['id']
            if key == 'middot_context_pins':
                _require(type(row['offset']) is int and 0 <= row['offset'] <= 1000
                         and isinstance(row['glyph'], str) and len(row['glyph']) == 1
                         and row['glyph'] in SEPARATORS, 'ms_rules_middot_pin')
                contexts.add(item)
            elif key == 'reading_fragment_pins':
                _require(isinstance(row['paragraph_id'], str)
                         and re.fullmatch(ident + r':p[0-9]{4,8}', row['paragraph_id']),
                         'ms_rules_fragment_pin')
                fragments.add(item)
            else:
                _require(isinstance(row['unit_id'], str)
                         and re.fullmatch(ident + r':p[0-9]{4,8}:u[0-9]{3,8}', row['unit_id']),
                         'ms_rules_han_pin')
                han.add(item)
    _require(len(contexts) == len(value['middot_context_pins'])
             and len(fragments) == len(value['reading_fragment_pins'])
             and len(han) == len(value['han_duplicate_pins']), 'ms_rules_duplicate_pin')
    return frozenset(lexemes), frozenset(contexts), frozenset(fragments), frozenset(han), hashlib.sha256(raw).hexdigest()


def rules_sha256():
    return _rules()[4]


def _reverse_edits(spoken, edits):
    restored = spoken
    for edit in reversed(edits):
        start, end = edit['spoken_start'], edit['spoken_end']
        _require(type(start) is int and type(end) is int and 0 <= start <= end <= len(restored)
                 and restored[start:end] == edit['replacement'], 'ms_reverse_edit_span')
        restored = restored[:start] + edit['original'] + restored[end:]
    return restored


def _collect_middot_edits(unit, ident, lexemes, contexts):
    base = unit['normalization']
    details = base['middot_review_details']
    if not details:
        return [], []
    old = unit['spoken']
    input_sha = base['middot_input_sha256']
    base_edits = base['middot_edits']
    raw = _reverse_edits(old, base_edits)
    _require(digest(raw) == input_sha, 'ms_pronunciation_input_hash')
    replacements, unresolved = [], []
    previous = 0
    for detail in details:
        start, end, original = detail['source_start'], detail['source_end'], detail['original']
        _require(type(start) is int and type(end) is int and previous <= start < end <= len(raw)
                 and raw[start:end] == original, 'ms_pronunciation_flag_span')
        previous = end
        reason = detail['reason']
        canon = original.translate(CANONICAL_SEPARATOR)
        replacement = None
        if reason == LEXEME:
            lexeme_sha = digest(canon)
            if lexeme_sha in lexemes:
                replacement = canon.replace('·', ', ')
            elif ident == 'MS-138' and lexeme_sha == _DAY_NAME and raw[end:].startswith(' 전쟁'):
                replacement = canon.replace('·', ' ')
        elif reason == NOTATION and end == start + 1 and (ident, input_sha, start, original) in contexts:
            replacement = ','
        if replacement is None:
            unresolved.append(detail)
            continue
        _require(all(edit['source_end'] <= start or end <= edit['source_start']
                     for edit in base_edits), 'ms_pronunciation_overlaps_base_edit')
        shift = sum((edit['spoken_end'] - edit['spoken_start']) -
                    (edit['source_end'] - edit['source_start'])
                    for edit in base_edits if edit['source_end'] <= start)
        replacements.append((start + shift, end + shift, original, replacement, reason))
    return replacements, unresolved


def _han_duplicate(unit, ident, pins, old_sha, other):
    old = unit['spoken']
    token = '연한(年限)'
    if (ident, unit['id'], old_sha) not in pins:
        return None
    _require(len(other) == 1 and other[0]['reason'] == HAN and other[0]['original'] == '年限'
             and old.count(token) == 1 and old.count('年限') == 1, 'ms_han_duplicate_scope')
    start = old.index(token) + len('연한')
    return start, start + len('(年限)'), '(年限)', '', 'explicit_hangul_gloss'


def _pronunciation(unit, ident, lexemes, contexts, han, config_sha):
    base = unit['normalization']
    if base['failed'] or 'ms_overlay' in base:
        return unit
    if not (base['middot_review_details'] or base['review_details']):
        return unit
    old = unit['spoken']
    old_sha = digest(old)
    _require(old_sha == unit['spoken_sha256'] == base['spoken_sha256'], 'ms_pronunciation_spoken_hash')
    replacements, unresolved_dots = _collect_middot_edits(unit, ident, lexemes, contexts)
    other = base['review_details']
    gloss = _han_duplicate(unit, ident, han, old_sha, other)
    if gloss:
        replacements.append(gloss)
        other = []
    if not replacements:
        return unit
    cursor = delta = 0
    parts, edits = [], []
    for a, b, original, replacement, reason in sorted(replacements):
        _require(cursor <= a < b <= len(old) and old[a:b] == original, 'ms_replacement_span')
        parts.extend((old[cursor:a], replacement))
        edits.append(dict(input_start=a, input_end=b, spoken_start=a + delta,
                          spoken_end=a + delta + len(replacement), original=original,
                          replacement=replacement, rule=reason))
        cursor = b
        delta += len(replacement) - len(original)
    parts.append(old[cursor:])
    spoken = ''.join(parts)
    lexical = lambda value: ''.join(c for c in value if c.isalnum() and c not in SEPARATORS)
    # An in-line Hanja gloss duplicates the immediately preceding Hangul word.
    expected = old if gloss is None else old[:gloss[0]] + old[gloss[1]:]
    _require(lexical(expected) == lexical(spoken) and _reverse_edits(spoken, edits) == old,
             'ms_pronunciation_content_changed')

    review = set(unit['review'])
    reasons = {detail['reason'] for detail in [*unresolved_dots, *other]}
    for reason in (LEXEME, NOTATION, HAN):
        if reason not in reasons:
            review.discard('normalization_' + reason)
    if not any(char in SEPARATORS for char in spoken):
        review.discard(REMAINING)
    if len(spoken) > 240:
        review.add(LONG)
    spoken_sha = digest(spoken)
    overlay = dict(schema=RULESET, rules_sha256=config_sha, before_spoken_sha256=old_sha,
                   spoken_sha256=spoken_sha, edits=edits,
                   unresolved_middot_details=unresolved_dots, unresolved_normalization_details=other)
    return dict(unit, spoken=spoken, spoken_sha256=spoken_sha, review=sorted(review),
                normalization=dict(base, spoken_sha256=spoken_sha, ms_overlay=overlay))


def apply_to_plan(plan):
    """Use exact review candidates for MS only, preserving source and segment offsets."""
    ident = plan['lesson_id']
    if not _scope(ident):
        return plan
    lexemes, contexts, fragments, han, config_sha = _rules()
    source_sha = plan['source_sha256']
    paragraphs = {p['id']: p for p in plan['paragraphs']}
    verified_spans = set()
    units = []
    for unit in plan['units']:
        result = _pronunciation(unit, ident, lexemes, contexts, han, config_sha)
        if FRAGMENT in result['review']:
            paragraph = paragraphs[result['paragraph_id']]
            key = (ident, source_sha, paragraph['id'], digest(paragraph['source']))
            if key in fragments:
                _require(source_sha == digest(plan['source']) and result['mode'] == 'explain'
                         and result['reading_mode_verified'] is False, 'ms_reading_fragment_evidence')
                verified_spans.add((paragraph['visible_start_utf16'], paragraph['visible_end_utf16']))
                evidence = dict(result['reading_mode_verification'], method='exact_numbered_legal_fragment',
                                ms_rules_sha256=config_sha)
                review = [flag for flag in result['review'] if flag != FRAGMENT]
                result = dict(result, mode='reading', review=review,
                              reading_mode_verified=not any(f.startswith('reading_mode_') for f in review),
                              reading_mode_verification=evidence)
        units.append(result)
    if all(before is after for before, after in zip(plan['units'], units)):
        return plan
    spans = set(map(tuple, plan['reading_spans'])) | verified_spans
    return dict(plan, units=units, reading_spans=[list(span) for span in sorted(spans)],
                review_unit_count=sum(bool(unit['review']) for unit in units))
