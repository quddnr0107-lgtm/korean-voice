"""Read-only numeric-notation comparison of authenticated saved receipts.

This projection cannot approve, write, synthesize or publish anything. All output
keys are fixed counters; manuscript and recognition text never leave it.
"""
import math

from pilot_render import canonical
from tts_normalize import normalize


def require(ok):
    if not ok:
        raise ValueError('saved_receipt_comparison_invalid')


def _bucket():
    return dict(units=0, passed_units=0, blocked_units=0, candidates=0,
        raw_exact_candidates=0, normalized_exact_candidates=0,
        newly_exact_candidates=0, normalization_flagged_candidates=0,
        normalization_unavailable_candidates=0, blocked_candidates=0,
        blocked_newly_exact_candidates=0, blocked_units_with_any_normalized_exact=0,
        blocked_units_with_all_normalized_exact=0,
        blocked_units_with_no_normalized_exact=0, tts_seconds=0.0,
        audio_seconds=0.0, measured_tts_candidates=0, measured_audio_candidates=0)


def _measurement(candidate, key, nested=None):
    value = candidate.get(nested, {}).get(key) if nested else candidate.get(key)
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def diagnostic_projection(rows, plan):
    require(isinstance(rows, dict) and rows and isinstance(plan, dict)
        and isinstance(plan.get('units'), list))
    modes = {key: _bucket() for key in ('reading', 'explain')}
    totals = _bucket()
    for index, row in rows.items():
        require(type(index) is int and 0 <= index < len(plan['units'])
            and isinstance(row, dict) and isinstance(row.get('receipt'), dict))
        unit, receipt = plan['units'][index], row['receipt']
        require(isinstance(unit, dict) and unit.get('mode') in modes
            and isinstance(unit.get('spoken'), str) and bool(canonical(unit['spoken']))
            and isinstance(unit.get('critical_terms', []), list)
            and all(isinstance(x, str) for x in unit.get('critical_terms', []))
            and receipt.get('status') in ('passed', 'blocked_requires_review')
            and isinstance(receipt.get('candidates'), list)
            and 0 < len(receipt['candidates']) <= 2)
        blocked = receipt['status'] == 'blocked_requires_review'
        buckets = (totals, modes[unit['mode']])
        for bucket in buckets:
            bucket['units'] += 1
            bucket['blocked_units' if blocked else 'passed_units'] += 1
        matches = []
        expected = canonical(unit['spoken'])
        critical = [canonical(x) for x in unit.get('critical_terms', [])]
        for candidate in receipt['candidates']:
            require(isinstance(candidate, dict) and isinstance(candidate.get('asr', {}), dict)
                and isinstance(candidate.get('signal', {}), dict))
            recognized = candidate.get('asr', {}).get('recognized')
            raw_exact = normalized_exact = newly_exact = flagged = unavailable = False
            if not isinstance(recognized, str) or len(recognized) > 10000:
                unavailable = True
            else:
                raw = canonical(recognized)
                raw_exact = raw == expected and all(x in raw for x in critical)
                try:
                    normalized, _, flags = normalize(recognized)
                    flagged = bool(flags)
                    key = canonical(normalized)
                    normalized_exact = not flagged and key == expected and all(x in key for x in critical)
                    newly_exact = normalized_exact and not raw_exact
                except (ValueError, OverflowError):
                    unavailable = True
            matches.append(normalized_exact)
            for bucket in buckets:
                bucket['candidates'] += 1
                bucket['blocked_candidates'] += int(blocked)
                bucket['raw_exact_candidates'] += int(raw_exact)
                bucket['normalized_exact_candidates'] += int(normalized_exact)
                bucket['newly_exact_candidates'] += int(newly_exact)
                bucket['normalization_flagged_candidates'] += int(flagged)
                bucket['normalization_unavailable_candidates'] += int(unavailable)
                bucket['blocked_newly_exact_candidates'] += int(blocked and newly_exact)
                tts = _measurement(candidate, 'tts_seconds')
                audio = _measurement(candidate, 'seconds', 'signal')
                if tts is not None:
                    bucket['tts_seconds'] += tts
                    bucket['measured_tts_candidates'] += 1
                if audio is not None:
                    bucket['audio_seconds'] += audio
                    bucket['measured_audio_candidates'] += 1
        if blocked:
            for bucket in buckets:
                bucket['blocked_units_with_any_normalized_exact'] += int(any(matches))
                bucket['blocked_units_with_all_normalized_exact'] += int(all(matches))
                bucket['blocked_units_with_no_normalized_exact'] += int(not any(matches))
    for bucket in (totals, *modes.values()):
        for key in ('tts_seconds', 'audio_seconds'):
            require(math.isfinite(bucket[key]))
            bucket[key] = round(bucket[key], 3)
    return dict(schema='bora-saved-receipt-comparison/v1',
        status='diagnostic_only_no_promotion', totals=totals, modes=modes,
        production_status_changed=False, checkpoint_written=False,
        synthesis_started=False, production_eligible=False,
        human_listening_verified=False,
        scope='Exact comparison after existing pronunciation normalization; not proof of speech correctness.')
