"""Bounded, metadata-only reconnaissance for the residual protected MS source review."""
from collections import Counter
import hashlib
import re

REQUEST_ID = 'ms-source-review-digests-20261121'
IDS = tuple(f'MS-{i:03d}' for i in (5, 6, 21, 28, 31, 53, 54, 71, 73, 75, 94, 97, 103, 115, 141, 144))
SEPARATORS = str.maketrans(dict.fromkeys('·ㆍᆞ', '·'))
MIDDOT_REASONS = ('unreviewed_middle_dot_lexeme', 'unreviewed_middle_dot_notation')
HASH = re.compile(r'[0-9a-f]{64}')
KNOWN_FLAGS = frozenset((
    'reading_mode_ambiguous_legal_fragment',
    'review_remaining_middot_do_not_globally_split',
    'normalization_unreviewed_middle_dot_lexeme',
    'normalization_unreviewed_middle_dot_notation',
    'normalization_han-reading-review',
))


def enabled(request, ids, profile):
    if request.get('request_id') != REQUEST_ID:
        return False
    if (profile != 'single_pass' or request.get('course') != 'MS' or tuple(ids) != IDS
            or request.get('retry_blocked') is not False
            or request.get('assembly_only') is not None
            or request.get('selected_shards') is not None):
        raise ValueError('ms_source_review_diagnostic_scope')
    return True


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def record(plan, lesson_id, pins):
    source, pinned = plan['source'], plan['source_sha256']
    if lesson_id not in IDS or plan['lesson_id'] != lesson_id or sha(source) != pinned:
        raise ValueError('ms_source_review_diagnostic_identity')
    lexemes, contexts, fragments, _, _ = pins
    source_options = {pinned, sha(source if source.endswith('\n') else source+'\n')}
    own_pins = {(a,b,c) for ident,a,b,c in fragments if ident == lesson_id}
    known_sources = {a for a,_,_ in own_pins}
    paragraphs = {p['id']: p for p in plan['paragraphs']}
    classes = Counter()
    count = 0
    for unit in plan['units']:
        flags = set(unit.get('review', ())) | set(unit.get('review_flags', ()))
        if not flags:
            continue
        count += 1
        classes.update(f if f in KNOWN_FLAGS else 'other_review' for f in flags)
        if 'reading_mode_ambiguous_legal_fragment' in flags:
            p = paragraphs[unit['paragraph_id']]
            pdigest = sha(p['source'])
            if any(a in source_options and b == p['id'] and c == pdigest for a,b,c in own_pins):
                classes['fragment_exact_match'] += 1
            elif any(b == p['id'] and c == pdigest for _,b,c in own_pins):
                classes['fragment_same_paragraph_different_source'] += 1
            elif any(c == pdigest for _,_,c in own_pins):
                classes['fragment_same_text_other_paragraph'] += 1
            else:
                classes['fragment_unknown_text'] += 1
        norm = unit.get('normalization') or {}
        overlay = norm.get('ms_overlay')
        details = (overlay.get('unresolved_middot_details', ()) if overlay is not None
                   else norm.get('middot_review_details', ()))
        for detail in details:
            reason, original = detail['reason'], detail['original']
            if reason == MIDDOT_REASONS[0]:
                known = sha(original.translate(SEPARATORS)) in lexemes
                classes['middot_known_lexeme' if known else 'middot_unknown_lexeme'] += 1
            elif reason == MIDDOT_REASONS[1]:
                known = (lesson_id, norm.get('middot_input_sha256'),
                         detail['source_start'], original) in contexts
                classes['middot_known_context' if known else 'middot_unknown_context'] += 1
            else:
                classes['middot_other_reason'] += 1
    ref = ('known_reference_match' if source_options & known_sources else
           'known_reference_differs' if known_sources else 'no_known_reference')
    return dict(lesson_id=lesson_id, reference=ref, review_unit_count=count,
                classes=dict(sorted(classes.items())))
