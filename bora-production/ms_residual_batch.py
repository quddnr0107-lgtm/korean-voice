"""Owner-approved residual MS batch, independent of the in-flight MS-018 run.

Only exact authenticated source text and explicitly reviewed paragraph hashes
may be used. The public delivery is audio-only; other lessons stay unchanged.
"""
import hashlib
import json
import copy

REQUEST_ID = 'ms-residual-parallel-public-20261126'
IDS = ['MS-005','MS-006','MS-021','MS-022','MS-028','MS-031','MS-053','MS-054','MS-071','MS-073','MS-075','MS-094','MS-097','MS-103','MS-115','MS-141','MS-144']
PRIVATE_REQUEST_ID = 'ms-original-private-final15-20261127'
CURRENT_MS094_REQUEST = 'ms-private-original-094-current-script-20261127'
PRIVATE_IDS = [ident for ident in IDS if ident not in ('MS-022', 'MS-053')]
WORKERS = 17
RECIPE = 'd3d35666712d276c0fa60bb5c5b1f7ab7d11c7042d490c95bb6fb8846682502d'
SETTINGS = '5597ef328c8f1381e634e45a00b11dd556a8423597d7dfc80771f3085649dd32'
MS022 = 'MS-022'
MS022_INDEX = 28
MS022_PLAN = 'ce621e9fe576dc6b1f56f3241e5dc8d7a8013160b8b3375b16841073718435e9'
FRAGMENT = 'reading_mode_ambiguous_legal_fragment'
MS094_LEXEME = '64b7df38ec2e23d27f7b14dca14eb768e55a88b019a8b3dbb0e784cde1500e2f'


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('ascii')).hexdigest()


def enabled(request, profile):
    return profile == 'single_pass' and request.get('request_id') in (REQUEST_ID, PRIVATE_REQUEST_ID, CURRENT_MS094_REQUEST)


def validate_request_scope(request):
    request_id = request.get('request_id')
    if request_id not in (REQUEST_ID, PRIVATE_REQUEST_ID, CURRENT_MS094_REQUEST):
        return
    expected = dict(schema='bora-fp32-request/v1',
        campaign_sha256='60e9763ab344e8a59264ec634a47ee60321667bc426108100f2973a04cfdca76',
        course='MS', execution_profile='bora-python-fp32-single-pass/v1',
        execution_shard_chars=4000, retry_blocked=False, request_id=request_id,
        lesson_ids=(['MS-094'] if request_id == CURRENT_MS094_REQUEST else
                    PRIVATE_IDS if request_id == PRIVATE_REQUEST_ID else IDS),
        render_workers=(6 if request_id == CURRENT_MS094_REQUEST else
                        40 if request_id == PRIVATE_REQUEST_ID else WORKERS))
    require(request == expected and type(request['render_workers']) is int
            and type(request['execution_shard_chars']) is int
            and request['retry_blocked'] is False, 'ms_residual_request_scope')


def fragment_review(plan, pins):
    """Rebind only the same approved paragraph bytes within its authenticated lesson.

    The full operating source stays pinned by runtime.source_plan, not by
    substituting a public manuscript or pretending its digest matches.
    """
    ident = plan['lesson_id']
    require(ident in IDS and ident not in (MS022, 'MS-094')
            and sha(plan['source']) == plan['source_sha256'], 'residual_source_identity')
    _, _, fragments, _, rule_sha = pins
    approved = {}
    for lid, old_source, pid, digest in fragments:
        if lid == ident:
            key = (pid, digest)
            require(key not in approved or approved[key] == old_source,
                    'residual_ambiguous_review_pin')
            approved[key] = old_source
    paragraphs = {p['id']: p for p in plan['paragraphs']}
    spans = set(map(tuple, plan['reading_spans']))
    units = []
    for unit in plan['units']:
        if FRAGMENT not in unit['review']:
            units.append(unit)
            continue
        paragraph = paragraphs[unit['paragraph_id']]
        digest = sha(paragraph['source'])
        prior = approved.get((paragraph['id'], digest))
        if prior is None:
            units.append(unit)
            continue
        require(unit['mode'] == 'explain' and unit['reading_mode_verified'] is False,
                'residual_fragment_mode')
        review = [flag for flag in unit['review'] if flag != FRAGMENT]
        evidence = dict(unit['reading_mode_verification'],
            method='authenticated_exact_reviewed_paragraph', ms_rules_sha256=rule_sha,
            prior_source_sha256=prior, operating_source_sha256=plan['source_sha256'],
            paragraph_sha256=digest)
        units.append(dict(unit, mode='reading', review=review,
            reading_mode_verified=not any(f.startswith('reading_mode_') for f in review),
            reading_mode_verification=evidence))
        spans.add((paragraph['visible_start_utf16'], paragraph['visible_end_utf16']))
    require(''.join(u['source'] for u in units) == plan['source'], 'residual_text_coverage')
    return dict(plan, units=units, reading_spans=[list(s) for s in sorted(spans)],
                review_unit_count=sum(bool(u['review'] or u.get('review_flags')) for u in units))


def ms094_review(plan):
    """One exact legal-reference separator, not a global middle-dot rewrite."""
    import ms_pronunciation as pron
    require(plan['lesson_id'] == 'MS-094' and sha(plan['source']) == plan['source_sha256'],
            'ms094_source_identity')
    lexemes, contexts, fragments, han, prior = pron._rules()
    rule_sha = identity(dict(existing_rules=prior, lesson_id='MS-094', additional_lexeme=MS094_LEXEME))
    units = []
    for unit in plan['units']:
        # The original approved normalizer proves reversible edits and unchanged
        # lexical content. Extend its whitelist only for this lesson.
        result = pron._pronunciation(unit, 'MS-094', lexemes | {MS094_LEXEME},
                                     contexts, han, rule_sha)
        units.append(result)
    require(''.join(u['source'] for u in units) == plan['source'], 'ms094_text_coverage')
    return dict(plan, units=units,
                review_unit_count=sum(bool(u['review'] or u.get('review_flags')) for u in units))


def ms022_scope(plan, recipe_hash, settings_hash):
    require(plan['lesson_id'] == MS022 and identity(plan) == MS022_PLAN
            and recipe_hash == RECIPE and settings_hash == SETTINGS
            and len(plan['units']) > MS022_INDEX, 'ms022_recovery_identity')


def resolve_ms022(plan, recipe_hash, settings_hash, original_rows, load_replacement):
    from ms018_recovery import replacement_settings, is_replacement
    ms022_scope(plan, recipe_hash, settings_hash)
    require(set(original_rows) == set(range(len(plan['units'])))
            and all(row['receipt'].get('status') == 'passed'
                    for i, row in original_rows.items() if i != MS022_INDEX),
            'ms022_original_coverage')
    old = original_rows[MS022_INDEX]['receipt']
    if old.get('status') == 'passed':
        return dict(original_rows), None
    candidates = old.get('candidates')
    require(old.get('status') == 'blocked_requires_review' and old.get('review_flags') == []
            and old.get('natural_eos') is False and isinstance(candidates, list)
            and len(candidates) == 1 and isinstance(candidates[0], dict), 'ms022_old_receipt')
    c = candidates[0]
    require(c.get('seed') == 5192 and c.get('natural_eos') is False
            and c.get('cap_reached') is True and c.get('eos_seen') is False
            and c.get('generated_sequence_tokens') == 1100
            and type(c.get('last_token')) is int and type(c.get('eos_token_id')) is int
            and c['last_token'] != c['eos_token_id']
            and old.get('render_settings_sha256') == SETTINGS,
            'ms022_original_failure_scope')
    settings = replacement_settings(old['render_settings'])
    fingerprint = identity(settings)
    restored = load_replacement(fingerprint)
    require(set(restored) <= {MS022_INDEX}, 'ms022_replacement_scope')
    rows = dict(original_rows)
    if MS022_INDEX in restored:
        row = restored[MS022_INDEX]
        receipt = row['receipt']
        require(receipt.get('status') == 'passed' and receipt.get('seed') == 7319
                and receipt.get('render_settings_sha256') == fingerprint
                and receipt.get('render_settings') == settings
                and is_replacement(settings, fingerprint), 'ms022_replacement_not_passed')
        rows[MS022_INDEX] = row
    else:
        del rows[MS022_INDEX]
    return rows, settings
