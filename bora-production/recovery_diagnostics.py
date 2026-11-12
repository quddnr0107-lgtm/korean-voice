"""Read-only incident diagnostics: no synthesis, promotion, key export or uploads.

Only a hash-pinned encrypted checkpoint is downloaded. Authenticated receipt
statistics leave the protected offline process; source and ASR text never do.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile

HERE = Path(__file__).resolve().parent
REPO = 'quddnr0107-lgtm/korean-voice'
BRANCH = 'integrated-lectures-premium-20261028'
WORKFLOW = REPO + '/.github/workflows/bora-recovery-check.yml@refs/heads/' + BRANCH
INCIDENT = {
    'run_id': '43213606878',
    'lesson_id': 'TB-018',
    'asset_id': 739850091,
    'asset_name': 'TB-018-s002-r43213606878-a1.zip',
    'asset_bytes': 84917112,
    'asset_sha256': 'acc96ece684673dfc29cc18d9ba81cd9d3a981430eac3801ace46b19795b0b24',
    'recipe_sha256': 'd3d35666712d276c0fa60bb5c5b1f7ab7d11c7042d490c95bb6fb8846682502d',
    'render_settings_sha256': '930c8341302f36e0058eeb0deb3a0821cc6177dc6edc162d41547f93299a0822',
}
ERROR_CODES = ('unit_tts_failed', 'generated_audio_shape_or_rate_mismatch',
    'natural_eos_not_observed', 'candidate_audio_size_invalid',
    'unit_candidate_failed', 'other_error')


def require(value):
    if not value:
        raise ValueError('recovery_validation_failed')


def dump(path, value):
    path = Path(path)
    require(not path.is_symlink())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as f:
        os.chmod(path, 0o600)
        json.dump(value, f, ensure_ascii=True, sort_keys=True)
        f.write('\n')


def validate_request(value):
    require(isinstance(value, dict) and set(value) == {'schema', 'purpose', 'incident'})
    require(value['schema'] == 'bora-recovery-diagnostic-request/v1'
            and value['purpose'] == 'read-only-authenticated-diagnostics'
            and value['incident'] == INCIDENT)
    return value


def validate_context(env, uid, euid):
    require(env.get('GITHUB_REPOSITORY') == REPO
            and env.get('GITHUB_REF') == 'refs/heads/' + BRANCH
            and env.get('GITHUB_EVENT_NAME') == 'push'
            and env.get('GITHUB_WORKFLOW_REF') == WORKFLOW
            and uid == euid and uid != 0)


def check_bundle(directory):
    from transport import validate_bundle
    path = Path(directory) / INCIDENT['asset_name']
    require(path.is_file() and not path.is_symlink()
            and path.stat().st_size == INCIDENT['asset_bytes'])
    with path.open('rb') as f:
        require(hashlib.file_digest(f, 'sha256').hexdigest() == INCIDENT['asset_sha256'])
    report = validate_bundle(path, 'TB')
    require(report['lesson_id'] == INCIDENT['lesson_id']
            and str(report['run_id']) == INCIDENT['run_id']
            and report['recipe_sha256'] == INCIDENT['recipe_sha256']
            and report['render_settings_sha256'] == INCIDENT['render_settings_sha256'])
    return report


def download(directory):
    import transport as t
    t.assert_keyless()
    backend = t.GitHubBackend()
    release = t._release(backend, t.tag_for('TB'), create=False)
    require(release is not None)
    assets = t._assets(backend, release)
    asset = assets.get(INCIDENT['asset_name'])
    require(asset is not None and asset['id'] == INCIDENT['asset_id']
            and asset['size'] == INCIDENT['asset_bytes']
            and asset.get('digest') == 'sha256:' + INCIDENT['asset_sha256'])
    directory = Path(directory)
    require(not directory.exists())
    directory.mkdir(parents=True, mode=0o700)
    backend.download(asset, directory / INCIDENT['asset_name'])
    report = check_bundle(directory)
    result = dict(status='ciphertext_download_and_hash_verified',
                  observed_staging_assets=len(assets), incident=INCIDENT,
                  public_inventory=report, authenticated_receipts=False,
                  synthesis_started=False, production_eligible=False)
    dump(directory / 'transport-report.json', result)
    print(json.dumps(result, sort_keys=True))


def summarize(restored, expected_units, review_units):
    """Project only fixed counters, never arbitrary receipt strings or keys."""
    require(type(expected_units) is int and expected_units > 0
            and type(review_units) is int and 0 <= review_units <= expected_units
            and isinstance(restored, dict) and restored
            and all(type(i) is int and 0 <= i < expected_units for i in restored))
    counts = Counter()
    errors = {key: 0 for key in ERROR_CODES}
    bins = {key: 0 for key in ('zero', 'one', 'two_to_five', 'over_five', 'not_measured')}
    for row in restored.values():
        receipt = row['receipt']
        status = receipt.get('status')
        require(status in ('passed', 'blocked_requires_review'))
        counts['passed_receipts' if status == 'passed' else 'blocked_receipts'] += 1
        counts['receipts_with_input_flags'] += bool(receipt.get('review_flags'))
        counts['receipts_with_review_audio'] += 'review_file' in receipt
        candidates = receipt.get('candidates')
        require(isinstance(candidates, list) and len(candidates) <= 2)
        for candidate in candidates:
            counts['candidates'] += 1
            counts['candidates_without_natural_eos'] += candidate.get('natural_eos') is not True
            counts['candidates_at_token_cap'] += candidate.get('cap_reached') is True
            counts['candidates_with_signal_failure'] += candidate.get('signal', {}).get('blocked') is not False
            asr = candidate.get('asr', {})
            counts['candidates_with_asr_review'] += asr.get('review_required') is not False
            counts['candidates_missing_critical_terms'] += bool(asr.get('missing_critical'))
            n = asr.get('character_errors')
            require(n is None or (type(n) is int and n >= 0))
            bucket = 'not_measured' if n is None else 'zero' if n == 0 else 'one' if n == 1 else 'two_to_five' if n <= 5 else 'over_five'
            bins[bucket] += 1
            code = candidate.get('error_code')
            if code:
                errors[code if code in errors else 'other_error'] += 1
    names = ('passed_receipts', 'blocked_receipts', 'receipts_with_input_flags',
             'receipts_with_review_audio', 'candidates', 'candidates_without_natural_eos',
             'candidates_at_token_cap', 'candidates_with_signal_failure',
             'candidates_with_asr_review', 'candidates_missing_critical_terms')
    return dict(schema='bora-authenticated-recovery-summary/v1',
                status='diagnostics_only_not_a_release',
                incident=INCIDENT, expected_lesson_units=expected_units,
                authenticated_units=len(restored), missing_lesson_units=expected_units-len(restored),
                manuscript_review_units=review_units,
                counts={key: int(counts[key]) for key in names},
                candidate_error_codes=errors, candidate_character_error_bins=bins,
                synthesis_started=False, production_eligible=False, human_listening_verified=False)


def inspect(directory, output):
    validate_context(os.environ, os.getuid(), os.geteuid())
    from namespace_guard import validate_snapshot
    validate_snapshot(os.getuid(), os.getuid(), socket.if_nameindex(),
        Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
        os.environ.get('BORA_HOST_NETNS', ''), Path('/proc/net/ipv6_route').read_text())
    values = [os.environ.pop(name, '') for name in
              ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')]
    source_key, media_key = [base64.b64decode(v, validate=True) for v in values]
    del values
    require(len(source_key) == len(media_key) == 32 and source_key != media_key)
    def deny(*args, **kwargs):
        raise RuntimeError('outbound_forbidden')
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    import runtime as r
    campaign, recipe, _, _ = r.config()
    require(r.identity(recipe) == INCIDENT['recipe_sha256'])
    report = check_bundle(directory)
    with r.quiet():
        plan, _ = r.source_plan(INCIDENT['lesson_id'], campaign, source_key)
    del source_key
    require(r.identity(plan) == report['plan_sha256'])
    review_units = sum(bool(u.get('review') or u.get('review_flags')) for u in plan['units'])
    with tempfile.TemporaryDirectory(prefix='bora-private-diagnostics-', dir=os.environ['RUNNER_TEMP']) as temp:
        with r.quiet():
            restored = r.restore(directory, media_key, plan, INCIDENT['recipe_sha256'],
                                 INCIDENT['render_settings_sha256'], Path(temp))
        require(len(restored) == report['passed'] + report['blocked'])
        summary = summarize(restored, len(plan['units']), review_units)
        require(summary['counts']['passed_receipts'] == report['passed']
                and summary['counts']['blocked_receipts'] == report['blocked'])
    del media_key
    dump(output, summary)
    print(json.dumps(summary, sort_keys=True))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('download', 'inspect'))
    parser.add_argument('--directory', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    validate_request(json.loads((HERE/'recovery-request.json').read_text()))
    if args.mode == 'download':
        require(args.output is None)
        download(args.directory)
    else:
        require(bool(args.output))
        inspect(args.directory, args.output)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"failed","reason":"recovery_validation_failed"}')
        raise SystemExit(1)
