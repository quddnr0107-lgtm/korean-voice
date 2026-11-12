"""Conservative sentence cues from ASR of final PCM, never estimated durations.

This does not run ASR or force speech to an expected transcript. The caller must
reanalyze the exact final decoded/trimmed/resampled unit PCM and bind its SHA-256
to both the measurement and recognition row. pcm_sha256 is the hash of the
exact final PCM WAV artifact, including its container. Candidate ASR is not acceptable.
Unresolved sentences are omitted and flagged; partial output must not replace a
complete production timing map. Exact text matches do not verify naturalness or
the perceptual accuracy of ASR timestamps.
"""
import math
import re
import unicodedata

from paragraph_plan import JS_SPACE, digest, measured_timeline, utf16
from quality import number, prepare

SCHEMA = 'kim-bora-sentence-cues/v1'
_SPACE = '[' + re.escape(JS_SPACE) + ']+'
_SPLIT = re.compile(r'(?<=[다요죠네까습음]\.)' + _SPACE + r'|(?<=[?!])' +
                    _SPACE + r'|(?<=다\.)(?=[가-힣])|(?<=요\.)(?=[가-힣])')
_HASH = re.compile(r'[a-f0-9]{64}')


def canonical(text):
    """Same lexical tolerance as the pilot ASR scorer; no word substitutions."""
    text = unicodedata.normalize('NFKC', text)
    # Legal source files also encode the middle-dot separator as araea letters.
    # Treat their punctuation role like ·, rather than requiring ASR to say it.
    text = re.sub('[·ㆍᆞ]', '', text)
    text = re.sub(r'(?<!\d)\d{1,4}(?!\d)', lambda m: number(m[0]), text)
    return ''.join(c for c in text if c.isalnum())


def sentence_spans(plan):
    """Mirror examPrep's nonempty-line paragraphs and Korean sentence split.

    Offsets address the unchanged raw source, before the frontend's trim offset.
    Display text is not normalized. A sentence may span multiple synthesis units.
    """
    source = plan['source']
    positions = {utf16(source[:i]): i for i in range(len(source) + 1)}
    sentences = []
    for paragraph in plan['paragraphs']:
        lo = positions[paragraph['visible_start_utf16']]
        hi = positions[paragraph['visible_end_utf16']]
        text = source[lo:hi]
        cursor = 0
        cuts = [(m.start(), m.end()) for m in _SPLIT.finditer(text)] + [(len(text), len(text))]
        for end, next_start in cuts:
            fragment = text[cursor:end]
            left = len(fragment) - len(fragment.lstrip(JS_SPACE))
            right = len(fragment.rstrip(JS_SPACE))
            if left < right:
                a, b = lo + cursor + left, lo + cursor + right
                sentences.append(dict(index=len(sentences), paragraph_id=paragraph['id'],
                    start_utf16=utf16(source[:a]), end_utf16=utf16(source[:b]),
                    source=source[a:b]))
            cursor = next_start
    return sentences


def _validate_recognition(unit, measured, row):
    if row.get('timing_basis') != 'final_decoded_pcm':
        raise ValueError('sentence timestamps require final PCM ASR')
    if row.get('source_sha256') != unit['source_sha256']:
        raise ValueError('stale ASR source')
    if row.get('spoken_sha256') != digest(unit['spoken']):
        raise ValueError('stale ASR spoken text')
    pcm_hash = measured.get('pcm_sha256', '')
    if not isinstance(pcm_hash, str) or not _HASH.fullmatch(pcm_hash) or row.get('pcm_sha256') != pcm_hash:
        raise ValueError('ASR must match measured final PCM hash')
    for key in ('sample_rate', 'sample_count'):
        if type(row.get(key)) != int or row[key] != measured[key]:
            raise ValueError('ASR sample measurement mismatch')
    if not isinstance(row.get('words'), list):
        raise ValueError('ASR words required')


def _word_anchors(row, clock_start, min_probability):
    """Word edges are observed anchors. Never divide a word's time by chars."""
    text, words, previous_end = '', [], 0
    rate, count = row['sample_rate'], row['sample_count']
    for index, word in enumerate(row['words']):
        token = word.get('text')
        if not isinstance(token, str):
            raise ValueError('ASR word text required')
        start, end, probability = word.get('start_s'), word.get('end_s'), word.get('probability')
        if (type(start) not in (int, float) or type(end) not in (int, float) or
                not math.isfinite(start) or not math.isfinite(end) or
                start < 0 or end <= start or end > count / rate + 1 / rate or start < previous_end):
            raise ValueError('non-monotonic/out-of-range ASR word timing')
        if (type(probability) not in (int, float) or not math.isfinite(probability) or
                not 0 <= probability <= 1):
            raise ValueError('ASR word probability required')
        previous_end = end
        normalized = canonical(token)
        if not normalized:
            continue
        a, b = len(text), len(text) + len(normalized)
        text += normalized
        start_sample = clock_start + round(start * rate)
        end_sample = clock_start + min(count, round(end * rate))
        if end_sample <= start_sample:
            raise ValueError('word timestamp has no sample extent')
        words.append(dict(index=index, start=a, end=b,
            start_sample=start_sample, end_sample=end_sample,
            low_probability=probability < min_probability))
    return text, words


def _occurrences(text, needle):
    start = 0
    while needle:
        index = text.find(needle, start)
        if index < 0:
            return
        yield index
        start = index + 1


def build_sentence_cues(plan, measurements, recognitions, min_probability=.5):
    """Return only exact, unique, monotonic sentence-fragment word anchors.

    measurements is paragraph_plan.measured_timeline input, with pcm_sha256 on
    each row. recognitions = {source_sha256, units:[{id, source_sha256,
    spoken_sha256, pcm_sha256, timing_basis:'final_decoded_pcm', sample_rate,
    sample_count, words:[{text,start_s,end_s,probability}]}]}. Word seconds are
    local to each final unit, excluding gap_before_samples. Full-unit exact
    transcript matches permit repeated sentences by their known order. Otherwise
    a fragment must occur exactly once in the unit's recognized transcript.
    """
    if type(min_probability) not in (int, float) or not 0 <= min_probability <= 1:
        raise ValueError('probability threshold')
    timeline = measured_timeline(plan, measurements)
    if recognitions.get('source_sha256') != plan['source_sha256']:
        raise ValueError('stale recognition plan')
    rows = recognitions.get('units', [])
    if (not isinstance(rows, list) or len(rows) != len(plan['units']) or
            len({r.get('id') for r in rows}) != len(rows)):
        raise ValueError('missing/duplicate ASR units')
    recognized = {r['id']: r for r in rows}
    if set(recognized) != {u['id'] for u in plan['units']}:
        raise ValueError('ASR unit identity')
    measured = {r['id']: r for r in measurements['units']}
    source = plan['source']
    cp = {utf16(source[:i]): i for i in range(len(source) + 1)}
    sentences = sentence_spans(plan)
    flags, fragments = [], {s['index']: [] for s in sentences}
    expected_parts = {s['index']: 0 for s in sentences}

    def flag(reason, unit_id=None, sentence_index=None):
        item = dict(reason=reason)
        if unit_id is not None:
            item['unit_id'] = unit_id
        if sentence_index is not None:
            item['sentence_index'] = sentence_index
        flags.append(item)

    for unit, timing in zip(plan['units'], timeline['segments']):
        row = recognized[unit['id']]
        _validate_recognition(unit, measured[unit['id']], row)
        parts = []
        for sentence in sentences:
            a = max(sentence['start_utf16'], unit['start_utf16'])
            b = min(sentence['end_utf16'], unit['end_utf16'])
            if a >= b:
                continue
            raw = source[cp[a]:cp[b]].strip(JS_SPACE)
            normalized = canonical(prepare(raw)['spoken'])
            if not normalized:
                # A quote mark can sit on the other side of a reviewed reading
                # span/unit boundary. It stays in the source sentence's range,
                # but it supplies no speech timestamp and requires no inference.
                continue
            expected_parts[sentence['index']] += 1
            parts.append((sentence['index'], normalized))
        expected = canonical(unit['spoken'])
        if ''.join(text for _, text in parts) != expected:
            flag('source_to_spoken_mapping_requires_review', unit['id'])
            continue
        try:
            actual, words = _word_anchors(row, timing['start_sample'], min_probability)
        except ValueError:
            flag('invalid_word_timestamps', unit['id'])
            continue
        exact = actual == expected
        if not exact:
            flag('unit_transcript_disagreement', unit['id'])
        starts = {w['start']: w for w in words}
        ends = {w['end']: w for w in words}
        cursor = 0
        previous_actual_end = 0
        for sentence_index, text in parts:
            locations = [cursor] if exact else list(_occurrences(actual, text))
            cursor += len(text)
            if len(locations) != 1:
                flag('missing_fragment' if not locations else 'ambiguous_repeated_fragment', unit['id'], sentence_index)
                continue
            a, b = locations[0], locations[0] + len(text)
            if a < previous_actual_end:
                flag('non_monotonic_fragment', unit['id'], sentence_index)
                continue
            previous_actual_end = b
            if a not in starts or b not in ends:
                flag('sentence_boundary_inside_asr_word', unit['id'], sentence_index)
                continue
            selected = [w for w in words if a <= w['start'] and w['end'] <= b]
            if any(w['low_probability'] for w in selected):
                flag('low_probability_fragment', unit['id'], sentence_index)
                continue
            fragments[sentence_index].append(dict(unit_id=unit['id'],
                start_sample=starts[a]['start_sample'], end_sample=ends[b]['end_sample']))

    cues, previous_end = [], 0
    for sentence in sentences:
        index = sentence['index']
        aligned = fragments[index]
        if not expected_parts[index] or len(aligned) != expected_parts[index]:
            flag('sentence_incomplete', sentence_index=index)
            continue
        start, end = aligned[0]['start_sample'], aligned[-1]['end_sample']
        if start < previous_end or end <= start:
            flag('non_monotonic_sentence', sentence_index=index)
            continue
        previous_end = end
        cues.append(dict(sentence_index=index, paragraph_id=sentence['paragraph_id'],
            start_utf16=sentence['start_utf16'], end_utf16=sentence['end_utf16'],
            start_sample=start, end_sample=end, start_s=start/timeline['sample_rate'],
            end_s=end/timeline['sample_rate'], unit_ids=[p['unit_id'] for p in aligned],
            anchor_basis='exact_spoken_fragment_at_final_pcm_asr_word_edges'))
    return dict(schema=SCHEMA, lesson_id=plan['lesson_id'], source_sha256=plan['source_sha256'],
        sample_rate=timeline['sample_rate'], total_samples=timeline['total_samples'],
        sentence_count=len(sentences), aligned_sentence_count=len(cues), segments=cues,
        flags=flags, complete=len(cues) == len(sentences), perceptual_review_required=True,
        production_ready=False, status='review_required' if flags else 'sentence_cues_prepared_for_review')
