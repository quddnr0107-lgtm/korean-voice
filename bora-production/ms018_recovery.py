"""One reviewed MS-018 recovery candidate; retain all other authenticated units.

A different seed is a different render settings fingerprint. The original
single-pass settings are never rewritten, and mixed receipts are accepted
only for the pinned failed unit after authenticating both generations.
"""
from __future__ import annotations
import copy
import hashlib
import json

REQUEST_ID = 'ms018-unit50-seed-recovery-20261126'
LESSON = 'MS-018'
INDEX = 50
SHARD = 2
OLD_SEED = 5192
NEW_SEED = 7319
PLAN = 'eb16ff7b35451180b4afb5ba717fb85b497558ec9856e756469d4b6a8ea94b35'
RECIPE = 'd3d35666712d276c0fa60bb5c5b1f7ab7d11c7042d490c95bb6fb8846682502d'
SETTINGS = '5597ef328c8f1381e634e45a00b11dd556a8423597d7dfc80771f3085649dd32'


def identity(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('ascii')).hexdigest()


def require(ok, code):
    if not ok:
        raise ValueError(code)


def enabled(request, profile):
    return profile == 'single_pass' and request.get('request_id') == REQUEST_ID


def validate_request_scope(request):
    if request.get('request_id') != REQUEST_ID:
        return
    expected = dict(schema='bora-fp32-request/v1',
        campaign_sha256='60e9763ab344e8a59264ec634a47ee60321667bc426108100f2973a04cfdca76',
        course='MS', execution_profile='bora-python-fp32-single-pass/v1',
        execution_shard_chars=2200, retry_blocked=False, request_id=REQUEST_ID,
        lesson_ids=[LESSON], selected_shards=[dict(lesson_id=LESSON, shard=SHARD)],
        render_workers=1)
    require(request == expected and type(request['render_workers']) is int
            and type(request['selected_shards'][0]['shard']) is int
            and type(request['execution_shard_chars']) is int
            and request['retry_blocked'] is False, 'ms018_recovery_request_scope')


def scope(plan, recipe_hash, settings_hash):
    require(plan['lesson_id'] == LESSON and identity(plan) == PLAN
            and recipe_hash == RECIPE and settings_hash == SETTINGS
            and len(plan['units']) > INDEX, 'ms018_recovery_identity')


def replacement_settings(original):
    require(isinstance(original, dict) and original.get('seeds') == [OLD_SEED]
            and identity(original) == SETTINGS, 'ms018_original_settings_pin')
    return dict(copy.deepcopy(original), seeds=[NEW_SEED])


def is_replacement(settings, fingerprint):
    if not isinstance(settings, dict) or settings.get('seeds') != [NEW_SEED]:
        return False
    return (identity(settings) == fingerprint
            and identity(dict(settings, seeds=[OLD_SEED])) == SETTINGS)


def resolve(plan, recipe_hash, settings_hash, original_rows, load_replacement):
    """Return accepted/reusable rows and the replacement settings, if needed."""
    scope(plan, recipe_hash, settings_hash)
    require(set(original_rows) == set(range(len(plan['units']))),
            'ms018_original_coverage')
    require(all(row['receipt'].get('status') == 'passed'
                for i, row in original_rows.items() if i != INDEX),
            'ms018_other_unit_not_passed')
    receipt = original_rows[INDEX]['receipt']
    if receipt.get('status') == 'passed':
        return dict(original_rows), None
    candidates = receipt.get('candidates')
    require(receipt.get('status') == 'blocked_requires_review'
            and receipt.get('review_flags') == []
            and receipt.get('natural_eos') is True
            and isinstance(candidates, list) and len(candidates) == 1,
            'ms018_original_receipt_scope')
    candidate = candidates[0]
    signal = candidate.get('signal', {})
    require(candidate.get('seed') == OLD_SEED and candidate.get('natural_eos') is True
            and signal.get('blocked') is True and signal.get('reasons') == ['clipped_waveform']
            and receipt.get('render_settings_sha256') == SETTINGS,
            'ms018_original_failure_scope')
    settings = replacement_settings(receipt['render_settings'])
    fingerprint = identity(settings)
    result = dict(original_rows)
    new_rows = load_replacement(fingerprint)
    require(set(new_rows) <= {INDEX}, 'ms018_replacement_scope')
    if INDEX in new_rows:
        row = new_rows[INDEX]
        new = row['receipt']
        require(new.get('status') == 'passed', 'ms018_replacement_already_failed')
        require(new.get('render_settings_sha256') == fingerprint
                and new.get('render_settings') == settings
                and new.get('seed') == NEW_SEED, 'ms018_replacement_pin')
        result[INDEX] = row
    else:
        del result[INDEX]
    return result, settings
