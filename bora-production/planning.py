"""Deterministic PRIVATE production plans for all 303 authenticated manuscripts.

The caller authenticates/decrypts the source. No source, title, audio or key is
printed, uploaded or fetched here. Raw text and the UI's paragraph boundaries
remain exact; only the separately recorded spoken text is normalized.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
QUALITY_DIR = ROOT / 'research' / 'reading-quality'
if str(QUALITY_DIR) not in sys.path:
    sys.path.append(str(QUALITY_DIR))

import production_plan
import quality
import tts_normalize
import middot_readings
from lecture_crypto import namespace_for

SCHEMA = 'bora-production-plan/v1'
MODE_RULESET = 'bora-structural-reading-mode/v1'
NORMALIZATION_RULESET = 'bora-production-pronunciation/v1'
MAX_CHARS = 180
TB_CONFIG = ROOT / 'bora-production' / 'approved-tb-annotations.json'

# These exact substitutions already exist in the approved TB pilot recipe.
# They alter pronunciation only; unknown middle dots are never globally split.
PRONUNCIATION_REPLACEMENTS = (
    ('통합방위법', '통합 방위법'), ('국가방위요소', '국가 방위 요소'),
    ('침투ᆞ도발', '침투, 도발'), ('통합ᆞ운용', '통합 운용'),
    ('수립ᆞ시행', '수립 시행'), ('침투·도발', '침투, 도발'),
    ('통합·운용', '통합 운용'), ('수립·시행', '수립 시행'),
)
_ARTICLE = re.compile(r'^제\s*[0-9일이삼사오육칠팔구십백천]+\s*조(?:의\s*[0-9일이삼사오육칠팔구십백천]+)?(?=\s|[（(①-⑳]|$)')
_ITEM = re.compile(r'^(?:[①-⑳]|[1-9][0-9]{0,3}[.)][ \t]+|[가-하][.)][ \t]+)')
_LEGAL_PREDICATE = re.compile(r'(?:하여야\s*한다|따라야\s*한다|해서는\s*아니\s*된다|할\s*수\s*있다|목적으로\s*한다|아니한다|말한다|뜻한다|따른다|포함한다|제외한다|본다|한한다|적용한다|처한다|벌한다|정한다|금지한다|둔다)(?:[.!。]|\s|$)')
_LEGAL_CONTEXT = re.compile(r'(?:이\s*법|법률|대통령령|총리령|부령|규정|각\s*[호목]|조문|법문|원문|다음과\s*같다)')
_LIST_CONTEXT = re.compile(r'(?:다음\s*각\s*[호목]|다음과\s*같다)')
_SENTENCE_END = re.compile(r'(?:[.!?。]|다|라|요)[\s]*$')
_QUOTES = (('“', '”'), ('‘', '’'), ('「', '」'), ('『', '』'), ('"', '"'), ("'", "'"))


def identity(value):
    """Shared compact sorted ASCII JSON identity used by receipts/assembly."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                    separators=(',', ':')).encode('ascii')).hexdigest()


def _approved_annotations(lesson_id, source_hash):
    """Only an exact approved TB-001 source hash may inherit its old offsets."""
    if lesson_id != 'TB-001':
        return None
    if not TB_CONFIG.is_file() or TB_CONFIG.is_symlink():
        raise ValueError('approved_annotation_config_missing')
    raw = TB_CONFIG.read_bytes()
    config = json.loads(raw)
    if config.get('schema') != 'kim-bora-tb-pilot/v1' or config.get('lesson_id') != lesson_id:
        raise ValueError('approved_annotation_config_identity')
    records = [config] + config.get('accepted_sources', [])
    matches = [row for row in records if row.get('source_sha256') == source_hash]
    if not matches:
        return None
    spans = matches[0]['reading_spans']
    if any(row.get('reading_spans') != spans for row in matches):
        raise ValueError('approved_annotation_conflict')
    return dict(spans=spans, critical_terms=config.get('critical_terms', []),
                config_sha256=hashlib.sha256(raw).hexdigest(),
                method='existing_exact_source_annotations')


def _full_sentence_quote(body):
    # A quoted title/word is not a change of speaking style. Full sentences are.
    body = body.strip(production_plan.JS_SPACE)
    return len(re.sub(r'\s', '', body)) >= 10 and bool(_SENTENCE_END.search(body))


def _structural_modes(source):
    """Return paragraph-contained CP spans with honest structural evidence.

    This determines the selected reading/explanation prompt, not legal truth or
    audio quality. Unmarked ordinary narration uses the explanation prompt.
    """
    spans, paragraphs = [], []
    list_context = False
    for _, _, start, end in production_plan._paragraphs(source):
        text = source[start:end]
        article, item = _ARTICLE.match(text), _ITEM.match(text)
        flags, own = [], []
        reason = 'structural_explanation_default'
        legal = bool(_LEGAL_PREDICATE.search(text))
        # An explicit article with a legal predicate, or a numbered clause in
        # legal/list context, is sufficiently structural to select reading.
        if ((article and legal) or (item and (list_context or legal))):
            own = [(start, end, 'explicit_numbered_legal_clause')]
            reason = own[0][2]
        else:
            for opening, closing in _QUOTES:
                pattern = re.escape(opening) + '(.*?)' + re.escape(closing)
                for match in re.finditer(pattern, text):
                    if _full_sentence_quote(match[1]):
                        own.append((start + match.start(), start + match.end(), 'full_sentence_quotation'))
                unbalanced = (text.count(opening) % 2 != 0 if opening == closing
                              else text.count(opening) != text.count(closing))
                if unbalanced and legal and _LEGAL_CONTEXT.search(text):
                    flags.append('reading_mode_ambiguous_unbalanced_legal_quote')
            # A heading alone (제1조(목적), 1. 제목) is ordinary explanation.
            # Long numbered fragments with explicit legal context need review.
            if (article or item) and not legal and _LEGAL_CONTEXT.search(text) and not _SENTENCE_END.search(text):
                body = text[(article or item).end():].strip()
                if len(body) > 35:
                    flags.append('reading_mode_ambiguous_legal_fragment')
        # Nested sentence quotes share one reading span; disjoint quotes retain
        # explanation text between them. Never merge over paragraph boundaries.
        merged = []
        for lo, hi, method in sorted(own):
            if merged and lo <= merged[-1][1]:
                a, b, prior = merged[-1]
                merged[-1] = (a, max(b, hi), prior)
            else:
                merged.append((lo, hi, method))
        spans.extend(merged)
        paragraphs.append(dict(start_cp=start, end_cp=end, review=sorted(set(flags)), method=reason))
        list_context = bool(own and _LIST_CONTEXT.search(text)) or bool(item and list_context)
    return spans, paragraphs


def _pronunciation(raw):
    visible = raw.strip(production_plan.JS_SPACE)
    prepared = quality.prepare(visible)
    try:
        spoken, edits, flags = tts_normalize.normalize(prepared['spoken'])
        normalization_error = False
    except (ValueError, OverflowError):
        # Preserve the prepared content and explicitly block; a normalization
        # failure must never become permission to render an uncertain reading.
        spoken, edits, flags = prepared['spoken'], [], []
        normalization_error = True
    substitutions = []
    for old, new in PRONUNCIATION_REPLACEMENTS:
        count = spoken.count(old)
        if count:
            spoken = spoken.replace(old, new)
            substitutions.append(dict(original=old, replacement=new, occurrences=count))
    middot_input_sha256 = production_plan.digest(spoken)
    spoken, middot_edits, middot_flags = middot_readings.normalize(spoken)
    review = list(prepared['review'])
    resolved = []
    for flag, remains in (
        ('review_remaining_numeric_notation', bool(re.search(r'[0-9]', spoken))),
        ('review_remaining_middot_do_not_globally_split', bool(re.search('[·ㆍᆞ]', spoken))),
        ('review_long_segment_at_semantic_boundary', len(spoken) > 240),
    ):
        if flag in review and not remains and not normalization_error:
            review.remove(flag)
            resolved.append(flag)
        if remains and flag not in review:
            review.append(flag)
    review.extend('normalization_' + flag['reason'] for flag in flags)
    review.extend('normalization_' + flag['reason'] for flag in middot_flags)
    if normalization_error:
        review.append('normalization_error_requires_review')
    provenance = dict(
        schema=NORMALIZATION_RULESET, preparation_version=quality.VERSION,
        normalizer_version=tts_normalize.RULESET,
        input_sha256=production_plan.digest(visible),
        prepared_sha256=production_plan.digest(prepared['spoken']),
        spoken_sha256=production_plan.digest(spoken),
        edits_basis='quality.prepare_output_codepoints', edits=edits,
        review_details=flags, exact_substitutions=substitutions,
        middot_ruleset=middot_readings.RULESET, middot_input_sha256=middot_input_sha256,
        middot_edits=middot_edits, middot_review_details=middot_flags,
        resolved_preparation_flags=sorted(resolved), failed=normalization_error)
    return spoken, sorted(set(review)), provenance


def build_plan(lesson_id, source):
    """Build a private source-bound plan; no manual approval is invented.

    `reading_mode_verified` means exact prior annotations or a deterministic
    structural rule checked the prompt choice. It never means someone listened.
    Genuine ambiguity is carried into the unit's blocking `review` list.
    """
    namespace = namespace_for(lesson_id)
    if not isinstance(source, str) or not source.strip(production_plan.JS_SPACE):
        raise ValueError('empty manuscript')
    source_hash = production_plan.digest(source)
    annotations = _approved_annotations(lesson_id, source_hash)
    cp_to_utf16 = [0]
    for char in source:
        cp_to_utf16.append(cp_to_utf16[-1] + (2 if ord(char) > 0xffff else 1))
    utf16_to_cp = {offset: index for index, offset in enumerate(cp_to_utf16)}
    structural, paragraph_rules = _structural_modes(source)
    reading_spans = (annotations['spans'] if annotations else
                     [[cp_to_utf16[a], cp_to_utf16[b]] for a, b, _ in structural])
    plan = production_plan.build_plan(source, lesson_id, MAX_CHARS, reading_spans)
    paragraphs = {row['id']: row for row in plan['paragraphs']}
    paragraph_evidence = {}
    for paragraph, evidence in zip(plan['paragraphs'], paragraph_rules):
        paragraph.update(start_cp=utf16_to_cp[paragraph['start_utf16']],
                         end_cp=utf16_to_cp[paragraph['end_utf16']])
        paragraph_evidence[paragraph['id']] = evidence
    for unit in plan['units']:
        start, end = utf16_to_cp[unit['start_utf16']], utf16_to_cp[unit['end_utf16']]
        unit.update(start_cp=start, end_cp=end)
        spoken, review, normalization = _pronunciation(unit['source'])
        if 'long_unbroken_span_review' in unit['review']:
            review.append('long_unbroken_span_review')
        evidence = paragraph_evidence[unit['paragraph_id']]
        mode_flags = [] if annotations else evidence['review']
        review.extend(mode_flags)
        method = annotations['method'] if annotations else evidence['method']
        if not annotations and unit['mode'] == 'reading':
            methods = [m for a, b, m in structural if a < end and b > start]
            if not methods:
                raise ValueError('reading_mode_missing_evidence')
            method = sorted(set(methods))[0]
        unit.update(spoken=spoken, spoken_sha256=production_plan.digest(spoken),
                    review=sorted(set(review)), normalization=normalization,
                    reading_mode_verified=not mode_flags,
                    reading_mode_verification=dict(
                        ruleset=MODE_RULESET, method=method,
                        automated=True, human_review_claimed=False,
                        source_sha256=source_hash))
        critical = annotations.get('critical_terms', []) if annotations else []
        spoken_key = ''.join(c for c in spoken if c.isalnum())
        unit['critical_terms'] = [term for term in critical
                                  if ''.join(c for c in term if c.isalnum()) in spoken_key]
        owner = paragraphs[unit['paragraph_id']]
        if source[start:end] != unit['source'] or not owner['start_cp'] <= start < end <= owner['end_cp']:
            raise ValueError('raw_source_mapping_failed')
    plan.update(planner_schema=SCHEMA, namespace=namespace, max_chars=MAX_CHARS,
                reading_mode_ruleset=MODE_RULESET,
                normalization_ruleset=NORMALIZATION_RULESET,
                reading_spans=reading_spans,
                approved_annotation_match=annotations is not None,
                approved_annotation_config_sha256=annotations['config_sha256'] if annotations else None,
                automatic_structural_verification=True,
                human_reading_mode_review_claimed=False,
                review_unit_count=sum(bool(u['review']) for u in plan['units']))
    return plan
