"""Resolve reviewed Reserve Forces pronunciation only on the private plan.

Use unresolved spans returned by the base planner. Every replacement preserves
the lexical content, and uncertain or unrelated review flags stay blocking.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re

import production_plan

RULESET = 'bora-rl-pronunciation/v1'
RULES_FILE = Path(__file__).with_name('rl-pronunciation.json')
SEPARATORS = '·ㆍᆞ'
SEPARATOR_MAP = str.maketrans({'ㆍ': '·', 'ᆞ': '·'})
LEXEME_REASON = 'unreviewed_middle_dot_lexeme'
NOTATION_REASON = 'unreviewed_middle_dot_notation'
REMAINING_FLAG = 'review_remaining_middot_do_not_globally_split'
LONG_FLAG = 'review_long_segment_at_semantic_boundary'


def _check(condition, code):
    if not condition:
        raise ValueError(code)


@lru_cache(maxsize=1)
def _rules():
    _check(not RULES_FILE.is_symlink() and RULES_FILE.is_file(), 'rl_pronunciation_rules_file')
    raw = RULES_FILE.read_bytes()
    _check(len(raw) < 65536, 'rl_pronunciation_rules_size')
    data = json.loads(raw)
    _check(isinstance(data, dict) and set(data) ==
           {'schema', 'reference_revision', 'lexemes', 'context_pins'}
           and data['schema'] == RULESET
           and re.fullmatch('[0-9a-f]{40}', data['reference_revision']),
           'rl_pronunciation_rules_schema')
    lexemes = data['lexemes']
    _check(isinstance(lexemes, list) and 0 < len(lexemes) <= 512
           and all(isinstance(item, str)
                   and re.fullmatch(r'[가-힣]+(?:·[가-힣]+)+', item)
                   for item in lexemes)
           and lexemes == sorted(set(lexemes)), 'rl_pronunciation_lexeme_rules')
    pins = data['context_pins']
    _check(isinstance(pins, list) and len(pins) <= 16, 'rl_pronunciation_context_rules')
    contexts = set()
    for pin in pins:
        _check(isinstance(pin, dict) and set(pin) ==
               {'id', 'input_sha256', 'offset', 'glyph', 'replacement'}
               and re.fullmatch(r'RL-(?:00[1-9]|0[12][0-9]|03[0-4])', pin['id'])
               and re.fullmatch('[0-9a-f]{64}', pin['input_sha256'])
               and type(pin['offset']) is int and 0 <= pin['offset'] <= 1000
               and len(pin['glyph']) == 1 and pin['glyph'] in SEPARATORS
               and pin['replacement'] == ',', 'rl_pronunciation_context_rule')
        key = (pin['id'], pin['input_sha256'], pin['offset'], pin['glyph'])
        _check(key not in contexts, 'rl_pronunciation_duplicate_context')
        contexts.add(key)
    return frozenset(lexemes), frozenset(contexts), hashlib.sha256(raw).hexdigest()


def rules_sha256():
    return _rules()[2]


def _pre_middot_text(spoken, base_edits, input_sha):
    raw = spoken
    for edit in reversed(base_edits):
        start, end = edit['spoken_start'], edit['spoken_end']
        _check(type(start) is int and type(end) is int and 0 <= start < end <= len(raw)
               and raw[start:end] == edit['replacement'], 'rl_pronunciation_base_edit_span')
        raw = raw[:start] + edit['original'] + raw[end:]
    _check(production_plan.digest(raw) == input_sha, 'rl_pronunciation_input_hash')
    return raw


def _unit_overlay(unit, lesson_id, lexemes, contexts, config_hash):
    base = unit['normalization']
    details = base['middot_review_details']
    if not details or base['failed']:
        return unit
    old = unit['spoken']
    old_sha = production_plan.digest(old)
    _check(old_sha == unit['spoken_sha256'] == base['spoken_sha256'],
           'rl_pronunciation_base_spoken_hash')
    input_sha = base['middot_input_sha256']
    base_edits = base['middot_edits']
    raw = _pre_middot_text(old, base_edits, input_sha)
    parts, edits, unresolved = [], [], []
    cursor = delta = previous = 0
    for detail in details:
        start, end, original = detail['source_start'], detail['source_end'], detail['original']
        _check(type(start) is int and type(end) is int and previous <= start < end <= len(raw)
               and raw[start:end] == original, 'rl_pronunciation_flag_span')
        previous = end
        reason = detail['reason']
        lexeme = original.translate(SEPARATOR_MAP)
        if reason == LEXEME_REASON and lexeme in lexemes:
            replacement = lexeme.replace('·', ', ')
        elif reason == NOTATION_REASON and end == start + 1 and (
            lesson_id, input_sha, start, original
        ) in contexts:
            replacement = ','
        else:
            unresolved.append(detail)
            continue
        _check(all(edit['source_end'] <= start or end <= edit['source_start']
                   for edit in base_edits), 'rl_pronunciation_overlaps_base_edit')
        shift = sum((edit['spoken_end'] - edit['spoken_start']) -
                    (edit['source_end'] - edit['source_start'])
                    for edit in base_edits if edit['source_end'] <= start)
        a, b = start + shift, end + shift
        _check(cursor <= a < b <= len(old) and old[a:b] == original,
               'rl_pronunciation_replacement_span')
        parts.extend((old[cursor:a], replacement))
        edits.append(dict(input_start=a, input_end=b, spoken_start=a + delta,
                          spoken_end=a + delta + len(replacement), original=original,
                          replacement=replacement, rule=reason))
        cursor = b
        delta += len(replacement) - len(original)
    if not edits:
        return unit
    parts.append(old[cursor:])
    spoken = ''.join(parts)
    lexical = lambda value: ''.join(c for c in value if c.isalnum() and c not in SEPARATORS)
    _check(lexical(old) == lexical(spoken), 'rl_pronunciation_lexical_content_changed')
    reverse = spoken
    for edit in reversed(edits):
        start, end = edit['spoken_start'], edit['spoken_end']
        _check(reverse[start:end] == edit['replacement'], 'rl_pronunciation_reverse_span')
        reverse = reverse[:start] + edit['original'] + reverse[end:]
    _check(reverse == old, 'rl_pronunciation_reverse_reconstruction')

    review = set(unit['review'])
    remaining_reasons = {item['reason'] for item in unresolved}
    other_reasons = {item['reason'] for item in base['review_details']}
    for reason in (LEXEME_REASON, NOTATION_REASON):
        if reason not in remaining_reasons and reason not in other_reasons:
            review.discard('normalization_' + reason)
    if not any(c in SEPARATORS for c in spoken):
        review.discard(REMAINING_FLAG)
    if len(spoken) > 240:
        review.add(LONG_FLAG)
    spoken_sha = production_plan.digest(spoken)
    overlay = dict(schema=RULESET, rules_sha256=config_hash, before_spoken_sha256=old_sha,
                   spoken_sha256=spoken_sha, edits=edits,
                   remaining_middot_review_details=unresolved)
    return dict(unit, spoken=spoken, spoken_sha256=spoken_sha, review=sorted(review),
                normalization=dict(base, spoken_sha256=spoken_sha, rl_overlay=overlay))


def apply_to_plan(plan):
    """Return a new RL plan, changing only pronunciation and review evidence."""
    _check(isinstance(plan, dict) and isinstance(plan.get('lesson_id'), str),
           'rl_pronunciation_plan_schema')
    lesson_id = plan['lesson_id']
    if not lesson_id.startswith('RL-'):
        return plan
    lexemes, contexts, config_hash = _rules()
    units = [
        unit if 'rl_overlay' in unit['normalization']
        else _unit_overlay(unit, lesson_id, lexemes, contexts, config_hash)
        for unit in plan['units']
    ]
    if all(old is new for old, new in zip(plan['units'], units)):
        return plan
    return dict(plan, units=units, review_unit_count=sum(bool(u['review']) for u in units))
