"""Resolve only reviewed pronunciation in authenticated Defense Directive plans."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re

from production_plan import digest

RULESET = 'bora-dr-pronunciation/v1'
RULES_FILE = Path(__file__).with_name('dr-pronunciation.json')
LESSON_ID = re.compile(r'DR-(?:00[1-9]|0[1-6][0-9]|07[0-8])')
HASH = re.compile(r'[0-9a-f]{64}')
SEPARATORS = '·ㆍᆞ'
SEPARATOR_MAP = str.maketrans({'ㆍ': '·', 'ᆞ': '·'})
LEXEME_REASON = 'unreviewed_middle_dot_lexeme'
NOTATION_REASON = 'unreviewed_middle_dot_notation'
ENGLISH_REASON = 'english-word-review'
REMAINING_FLAG = 'review_remaining_middot_do_not_globally_split'
LONG_FLAG = 'review_long_segment_at_semantic_boundary'
SCOPE_FIELDS = {'id', 'unit_index', 'source_sha256', 'input_sha256', 'spoken_sha256'}


def _check(condition, code):
    if not condition:
        raise ValueError(code)


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _pin_scope(pin, fields):
    _check(isinstance(pin, dict) and set(pin) == SCOPE_FIELDS | fields
           and _matches(LESSON_ID, pin['id'])
           and type(pin['unit_index']) is int and 0 <= pin['unit_index'] <= 9999
           and all(_matches(HASH, pin[name]) for name in SCOPE_FIELDS if name.endswith('sha256')),
           'dr_pronunciation_pin_scope')
    return (pin['id'], pin['unit_index'], pin['source_sha256'],
            pin['input_sha256'], pin['spoken_sha256'])


def _english_edits(pin):
    edits, tokens = pin['edits'], pin['source_flag_tokens']
    _check(isinstance(edits, list) and 0 < len(edits) <= 10
           and isinstance(tokens, list) and 0 < len(tokens) <= 32
           and all(_matches('[A-Za-z]+', token) for token in tokens),
           'dr_pronunciation_english_rule')
    previous = 0
    originals = []
    for edit in edits:
        _check(isinstance(edit, dict) and set(edit) ==
               {'source_start', 'source_end', 'original', 'replacement'},
               'dr_pronunciation_english_edit')
        start, end = edit['source_start'], edit['source_end']
        _check(type(start) is int and type(end) is int and previous <= start < end <= 1000
               and _matches(r'[A-Za-z]+(?:[ -][A-Za-z]+)*', edit['original'])
               and end - start == len(edit['original'])
               and _matches(r'[가-힣]+(?: [가-힣]+)*', edit['replacement']),
               'dr_pronunciation_english_edit_scope')
        previous = end
        originals.extend(re.findall('[A-Za-z]+', edit['original']))
    _check(originals == tokens, 'dr_pronunciation_english_token_coverage')
    return tuple(tokens), tuple((e['source_start'], e['source_end'], e['original'], e['replacement'])
                               for e in edits)


@lru_cache(maxsize=1)
def _rules():
    _check(not RULES_FILE.is_symlink() and RULES_FILE.is_file(), 'dr_pronunciation_rules_file')
    raw = RULES_FILE.read_bytes()
    _check(0 < len(raw) <= 65536, 'dr_pronunciation_rules_size')
    data = json.loads(raw)
    _check(isinstance(data, dict) and set(data) ==
           {'schema', 'reference_revision', 'lexemes', 'context_pins', 'english_alias_pins'}
           and data['schema'] == RULESET and _matches('[0-9a-f]{40}', data['reference_revision']),
           'dr_pronunciation_rules_schema')
    lexemes = data['lexemes']
    _check(isinstance(lexemes, list) and 0 < len(lexemes) <= 512
           and all(_matches(r'[가-힣]+(?:·[가-힣]+)+', value) for value in lexemes)
           and lexemes == sorted(set(lexemes)), 'dr_pronunciation_lexemes')
    context_pins = data['context_pins']
    english_pins = data['english_alias_pins']
    _check(isinstance(context_pins, list) and len(context_pins) <= 16
           and isinstance(english_pins, list) and len(english_pins) <= 16,
           'dr_pronunciation_pin_count')
    contexts, aliases = {}, {}
    for pin in context_pins:
        scope = _pin_scope(pin, {'offset', 'glyph', 'replacement'})
        _check(type(pin['offset']) is int and 0 <= pin['offset'] <= 1000
               and isinstance(pin['glyph'], str) and len(pin['glyph']) == 1
               and pin['glyph'] in SEPARATORS and pin['replacement'] in (',', ', '),
               'dr_pronunciation_context_rule')
        key = (*scope, pin['offset'], pin['glyph'])
        _check(key not in contexts, 'dr_pronunciation_context_duplicate')
        contexts[key] = pin['replacement']
    for pin in english_pins:
        scope = _pin_scope(pin, {'edits', 'source_flag_tokens'})
        _check(scope not in aliases, 'dr_pronunciation_english_duplicate')
        aliases[scope] = _english_edits(pin)
    return frozenset(lexemes), contexts, aliases, hashlib.sha256(raw).hexdigest()


def rules_sha256():
    return _rules()[3]


def _middot_edits(old, base, scope, lexemes, contexts):
    details = base['middot_review_details']
    if not details:
        return [], []
    raw = old
    base_edits = base['middot_edits']
    for edit in reversed(base_edits):
        start, end = edit['spoken_start'], edit['spoken_end']
        _check(0 <= start < end <= len(raw) and raw[start:end] == edit['replacement'],
               'dr_pronunciation_base_edit_span')
        raw = raw[:start] + edit['original'] + raw[end:]
    _check(digest(raw) == base['middot_input_sha256'], 'dr_pronunciation_middot_input_hash')
    edits, unresolved = [], []
    previous = 0
    for item in details:
        start, end, original = item['source_start'], item['source_end'], item['original']
        _check(previous <= start < end <= len(raw) and raw[start:end] == original,
               'dr_pronunciation_middot_flag_span')
        previous = end
        lexeme = original.translate(SEPARATOR_MAP)
        reason = item['reason']
        if reason == LEXEME_REASON and lexeme in lexemes:
            replacement = lexeme.replace('·', ', ')
        elif reason == NOTATION_REASON and end == start + 1:
            replacement = contexts.get((*scope, start, original))
        else:
            replacement = None
        if replacement is None:
            unresolved.append(item)
            continue
        _check(all(edit['source_end'] <= start or end <= edit['source_start'] for edit in base_edits),
               'dr_pronunciation_middot_overlap')
        shift = sum((edit['spoken_end'] - edit['spoken_start']) -
                    (edit['source_end'] - edit['source_start'])
                    for edit in base_edits if edit['source_end'] <= start)
        edits.append((start + shift, end + shift, original, replacement, reason))
    return edits, unresolved


def _apply_edits(old, edits):
    parts, records = [], []
    cursor = delta = 0
    for start, end, original, replacement, reason in sorted(edits):
        _check(cursor <= start < end <= len(old) and old[start:end] == original,
               'dr_pronunciation_spoken_edit_span')
        parts.extend((old[cursor:start], replacement))
        records.append(dict(input_start=start, input_end=end, spoken_start=start + delta,
                            spoken_end=start + delta + len(replacement), original=original,
                            replacement=replacement, rule=reason))
        cursor = end
        delta += len(replacement) - len(original)
    parts.append(old[cursor:])
    spoken = ''.join(parts)
    reverse = spoken
    for edit in reversed(records):
        start, end = edit['spoken_start'], edit['spoken_end']
        _check(reverse[start:end] == edit['replacement'], 'dr_pronunciation_reverse_span')
        reverse = reverse[:start] + edit['original'] + reverse[end:]
    _check(reverse == old, 'dr_pronunciation_reverse_reconstruction')
    return spoken, records


def _unit_overlay(unit, lesson_id, source_hash, index, lexemes, contexts, aliases, config_hash):
    base = unit['normalization']
    if base['failed'] or 'dr_overlay' in base:
        return unit
    if not base['middot_review_details'] and not base['review_details']:
        return unit
    old = unit['spoken']
    old_hash = digest(old)
    _check(old_hash == unit['spoken_sha256'] == base['spoken_sha256'],
           'dr_pronunciation_spoken_hash')
    scope = (lesson_id, index, source_hash)
    edits, remaining_middot = _middot_edits(
        old, base, (*scope, base['middot_input_sha256'], old_hash), lexemes, contexts)
    remaining_other = base['review_details']
    english = aliases.get((*scope, base['input_sha256'], old_hash))
    if english is not None:
        tokens = tuple(flag['original'] for flag in remaining_other if flag['reason'] == ENGLISH_REASON)
        if tokens == english[0]:
            edits.extend((*edit, ENGLISH_REASON) for edit in english[1])
            remaining_other = [flag for flag in remaining_other if flag['reason'] != ENGLISH_REASON]
    if not edits:
        return unit
    spoken, records = _apply_edits(old, edits)
    review = set(unit['review'])
    reasons_left = {item['reason'] for item in (*remaining_middot, *remaining_other)}
    resolved = {record['rule'] for record in records} - reasons_left
    for reason in resolved:
        review.discard('normalization_' + reason)
    if not any(c in SEPARATORS for c in spoken):
        review.discard(REMAINING_FLAG)
    if len(spoken) > 240:
        review.add(LONG_FLAG)
    spoken_hash = digest(spoken)
    overlay = dict(schema=RULESET, rules_sha256=config_hash, before_spoken_sha256=old_hash,
                   spoken_sha256=spoken_hash, edits=records,
                   remaining_middot_review_details=remaining_middot,
                   remaining_normalization_review_details=remaining_other)
    return dict(unit, spoken=spoken, spoken_sha256=spoken_hash, review=sorted(review),
                normalization=dict(base, spoken_sha256=spoken_hash, dr_overlay=overlay))


def apply_to_plan(plan):
    """Return a DR plan with unchanged source, offsets, modes and other review flags."""
    _check(isinstance(plan, dict) and isinstance(plan.get('lesson_id'), str),
           'dr_pronunciation_plan_schema')
    lesson_id = plan['lesson_id']
    if not LESSON_ID.fullmatch(lesson_id):
        return plan
    source_hash = plan['source_sha256']
    _check(digest(plan['source']) == source_hash, 'dr_pronunciation_source_hash')
    rules = _rules()
    units = [_unit_overlay(unit, lesson_id, source_hash, index, *rules)
             for index, unit in enumerate(plan['units'])]
    if all(old is new for old, new in zip(plan['units'], units)):
        return plan
    return dict(plan, units=units, review_unit_count=sum(bool(unit['review']) for unit in units))
