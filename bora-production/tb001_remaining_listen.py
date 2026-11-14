"""Owner-encrypted listening copies of the 15 remaining saved TB-001 reviews.

Five envelopes reuse the existing three-clip cryptography and payload format.
No TTS, ASR, production receipt mutation or checkpoint write is performed.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import tempfile

import listening_preview as preview
import tb001_full_diagnostic as full

SCHEMA = 'bora-tb001-remaining-owner-listen/v1'
WORKFLOW = full.d.REPO+'/.github/workflows/bora-tb001-remaining-listen.yml@refs/heads/'+full.d.BRANCH
HERE = Path(__file__).resolve().parent
REQUEST = HERE/'tb001-remaining-listen-request.json'
EVIDENCE = HERE/'evidence/tb001-turbo-crosscheck-20261114.json'
EVIDENCE_SHA256 = '3346b1fec4841e97a71abeedebbe59c120ae088e8d2ffbd6181d0de7d30a1f19'
DIAGNOSTIC_SUMMARY_SHA256 = '9ef90124cbe9500d3cb05c89dd948d070613e5a14e1f9b0f0c3a47670fca18a4'
TARGETS = (6, 7, 8, 20, 25, 27, 28, 34, 36, 37, 40, 41, 43, 47, 53)


def require(value):
    if not value:
        raise ValueError('remaining_listening_validation_failed')


def subrequest(request, group):
    require(type(group) is int and 1 <= group <= 5)
    return dict(schema=preview.SCHEMA, request_id=request['request_id']+f'-g{group:02d}',
        purpose='owner-only-listening-review', pin=copy.deepcopy(full.PIN), max_clips=3,
        recipient_spki_der_b64=request['recipient_spki_der_b64'],
        recipient_spki_sha256=request['recipient_spki_sha256'],
        synthesis=False, promotion=False, public_plaintext_export=False)


def validate_request(request):
    require(isinstance(request, dict) and set(request) == {
        'schema', 'request_id', 'purpose', 'pin', 'diagnostic_summary_sha256',
        'unit_indices', 'recipient_spki_der_b64', 'recipient_spki_sha256',
        'synthesis', 'promotion', 'public_plaintext_export'})
    require(request['schema'] == SCHEMA
        and request['purpose'] == 'owner-only-remaining-listening-review'
        and request['pin'] == full.PIN
        and request['diagnostic_summary_sha256'] == DIAGNOSTIC_SUMMARY_SHA256
        and request['unit_indices'] == list(TARGETS)
        and isinstance(request['request_id'], str)
        and re.fullmatch(r'[a-z0-9-]{1,70}', request['request_id'])
        and request['synthesis'] is False and request['promotion'] is False
        and request['public_plaintext_export'] is False)
    # The unchanged legacy validator authenticates the public RSA key and
    # enforces its original three-clip limit independently for all groups.
    for group in range(1, 6):
        preview.recipient(subrequest(request, group))
    return request


def check_context(env, uid, euid):
    require(env.get('GITHUB_REPOSITORY') == full.d.REPO
        and env.get('GITHUB_REF') == 'refs/heads/'+full.d.BRANCH
        and env.get('GITHUB_EVENT_NAME') == 'push'
        and env.get('GITHUB_WORKFLOW_REF') == WORKFLOW
        and uid == euid and uid != 0)


def read_evidence(path=EVIDENCE):
    path = Path(path)
    require(path.is_file() and not path.is_symlink()
        and 0 < path.stat().st_size < 128*1024)
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == EVIDENCE_SHA256)
    evidence = json.loads(raw)
    provenance, result = evidence['provenance'], evidence['result']
    require(provenance['run_id'] == 43538224355
        and provenance['head_sha'] == 'c83da2b2a947a9c1e8a6afad750a6fbb7bf4bb1b'
        and provenance['conclusion'] == 'success'
        and provenance['summary_sha256'] == DIAGNOSTIC_SUMMARY_SHA256
        and result['source_pin'] == full.PIN
        and result['checked_units'] == len(result['units']) == 28
        and result['larger_exact_units'] == 13 and result['larger_mismatch_units'] == 15
        and result['synthesized'] == result['promoted'] == result['original_checkpoint_writes'] == 0
        and result['production_status_changed'] is False
        and result['production_eligible'] is False)
    rows = [row for row in result['units'] if row['larger_exact'] is False]
    require([row['unit_index'] for row in rows] == list(TARGETS)
        and all(isinstance(row['wav_sha256'], str)
                and preview.HASH.fullmatch(row['wav_sha256']) for row in rows))
    return {row['unit_index']: row['wav_sha256'] for row in rows}


def select_remaining(reviews, pins):
    require(isinstance(reviews, list) and len(reviews) == 31
        and len({row['index'] for row in reviews}) == 31
        and set(pins) == set(TARGETS))
    by_index = {row['index']: row for row in reviews}
    require(set(TARGETS) <= set(by_index))
    chosen = [by_index[index] for index in TARGETS]
    require(all(row['wav_sha256'] == pins[row['index']] for row in chosen))
    return chosen


def make_envelopes(reviews, pins, request):
    validate_request(request)
    selected = select_remaining(reviews, pins)
    outputs = []
    for group in range(1, 6):
        part = selected[(group-1)*3:group*3]
        require(len(part) == 3)
        child = subrequest(request, group)
        # This allowed legacy reason does not claim perceptual quality or
        # automatic approval. The explicit 15-index selection is pinned above.
        chosen = [(row, 'additional_short_blocked_review') for row in part]
        plain = preview.make_payload(chosen, child)
        envelope = preview.encrypt_payload(plain, child, clip_count=3)
        del plain
        outputs.append((f'owner-remaining-{group:02d}.json', envelope))
    require(len(outputs) == 5)
    return outputs


def export(directory, output_dir, request):
    validate_request(request)
    check_context(os.environ, os.getuid(), os.geteuid())
    from namespace_guard import validate_snapshot
    validate_snapshot(os.getuid(), os.getuid(), socket.if_nameindex(),
        Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
        os.environ.get('BORA_HOST_NETNS', ''), Path('/proc/net/ipv6_route').read_text())
    values = [os.environ.pop(name, '') for name in preview.PRIVATE_KEYS]
    source_key, media_key = [base64.b64decode(value, validate=True) for value in values]
    del values
    require(len(source_key) == len(media_key) == 32 and source_key != media_key)
    def deny(*args, **kwargs):
        raise RuntimeError('outbound_forbidden')
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    pins = read_evidence()
    import runtime
    campaign, recipe, _, _ = runtime.config()
    require(runtime.identity(recipe) == full.PIN['recipe_sha256'])
    full.inventory(directory)
    with runtime.quiet():
        plan, _ = runtime.source_plan('TB-001', campaign, source_key)
    del source_key
    require(runtime.identity(plan) == full.PIN['plan_sha256']
        and len(plan['units']) == 58
        and not any(unit.get('review') or unit.get('review_flags') for unit in plan['units']))
    with tempfile.TemporaryDirectory(prefix='bora-private-remaining-listen-',
                                     dir=os.environ['RUNNER_TEMP']) as temporary:
        with runtime.quiet():
            rows = runtime.restore(directory, media_key, plan, full.PIN['recipe_sha256'],
                full.PIN['render_settings_sha256'], Path(temporary))
            before = copy.deepcopy({index: row['receipt'] for index, row in rows.items()})
            reviews = preview.available_reviews(rows, plan)
            envelopes = make_envelopes(reviews, pins, request)
            require(before == {index: row['receipt'] for index, row in rows.items()})
    del media_key
    output = Path(output_dir)
    require(not output.exists() and output.parent.is_dir()
        and not any(parent.is_symlink() for parent in output.parents))
    output.mkdir(mode=0o700)
    for name, envelope in envelopes:
        full.d.dump(output/name, envelope)
    print(json.dumps(dict(status='remaining_originals_owner_encrypted', clips=15,
        envelopes=5, synthesized=0, promoted=0, original_checkpoint_writes=0,
        public_plaintext_export=False)), flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('validate', 'export'))
    parser.add_argument('--directory')
    parser.add_argument('--output-dir')
    args = parser.parse_args()
    request = validate_request(json.loads(REQUEST.read_text()))
    read_evidence()
    if args.mode == 'validate':
        require(args.directory is None and args.output_dir is None)
        print('{"status":"remaining_listen_request_verified","clips":15}')
    else:
        require(bool(args.directory) and bool(args.output_dir))
        export(args.directory, args.output_dir, request)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"failed","reason":"remaining_listening_validation_failed"}', flush=True)
        raise SystemExit(1)
