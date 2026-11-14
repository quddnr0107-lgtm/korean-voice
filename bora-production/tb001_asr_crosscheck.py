"""Read-only larger-Whisper diagnosis of the 28 unheard saved TB-001 reviews.

No TTS engine is loaded. Only fixed numeric diagnostics leave the protected
offline process; transcripts, source text and raw audio remain private.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import socket
import tempfile
import time

import listening_preview as preview
import tb001_full_diagnostic as full
from pilot_render import transcript_score
from tts_normalize import RULESET, normalize

SCHEMA = 'bora-tb001-asr-crosscheck/v1'
REQUEST_ID = 'tb001-asr-crosscheck-av18-normalization-20261114-01'
DECODER_AV_VERSION = '18.1.0'
WORKFLOW = full.d.REPO+'/.github/workflows/bora-tb001-asr-crosscheck.yml@refs/heads/'+full.d.BRANCH
REQUEST = Path(__file__).with_name('tb001-asr-crosscheck-request.json')
MODEL_REPO = 'dropbox-dash/faster-whisper-large-v3-turbo'
MODEL_REVISION = '0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf'
MODEL_SHA256 = 'e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da'
MODEL_FILES = dict.fromkeys(('config.json', 'model.bin', 'preprocessor_config.json',
                            'tokenizer.json', 'vocabulary.json'))
MODEL_FILES['model.bin'] = 1617884929
OWNER_HEARD = frozenset((1, 33, 38))
TRANSCRIBE = dict(language='ko', beam_size=5, temperature=0.0,
                  condition_on_previous_text=False, vad_filter=False,
                  word_timestamps=False)


def require(ok):
    if not ok:
        raise ValueError('tb001_crosscheck_validation_failed')


def expected_request():
    return dict(schema=SCHEMA, request_id=REQUEST_ID, decoder_av_version=DECODER_AV_VERSION,
        pronunciation_diagnostics=True, normalization_ruleset=RULESET,
        purpose='read-only-cross-model-diagnostics',
        pin=copy.deepcopy(full.PIN), model=dict(repo=MODEL_REPO,
        revision=MODEL_REVISION, model_sha256=MODEL_SHA256),
        excluded_owner_heard_unit_indices=sorted(OWNER_HEARD),
        synthesis=False, promotion=False)


def validate_request(request):
    require(request == expected_request())
    return request


def verify_decoder_version():
    try:
        observed = importlib.metadata.version('av')
    except importlib.metadata.PackageNotFoundError:
        raise ValueError('tb001_crosscheck_validation_failed') from None
    require(observed == DECODER_AV_VERSION)
    return observed


def check_context(env, uid, euid):
    require(env.get('GITHUB_REPOSITORY') == full.d.REPO
        and env.get('GITHUB_REF') == 'refs/heads/'+full.d.BRANCH
        and env.get('GITHUB_EVENT_NAME') == 'push'
        and env.get('GITHUB_WORKFLOW_REF') == WORKFLOW
        and uid == euid and uid != 0)


def verify_model(path):
    from fp32_renderer import verify_assets
    hashes = verify_assets(path, MODEL_FILES, MODEL_REVISION)
    require(hashes['model.bin'] == MODEL_SHA256)
    return hashes


def download_model(path):
    require(all(not os.environ.get(name) for name in preview.PRIVATE_KEYS))
    path = Path(path)
    require(not path.exists())
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=MODEL_REPO, revision=MODEL_REVISION,
                      local_dir=str(path), allow_patterns=list(MODEL_FILES))
    verify_model(path)
    print(json.dumps(dict(status='pinned_crosscheck_model_verified',
                          files=len(MODEL_FILES), synthesized=0)), flush=True)


def select_unheard(reviews):
    require(isinstance(reviews, list) and len(reviews) == 31
        and len({x['index'] for x in reviews}) == 31
        and OWNER_HEARD <= {x['index'] for x in reviews})
    # Bind the feedback to the exact deterministic preview selection that the
    # owner heard, rather than treating it as approval of all blocked units.
    require({x['index'] for x, _ in preview.select_reviews(reviews)} == OWNER_HEARD)
    selected = sorted((x for x in reviews if x['index'] not in OWNER_HEARD),
                      key=lambda x: x['index'])
    require(len(selected) == 28)
    return selected


def file_hash(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink())
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def run_reviews(reviews, model, progress=None):
    selected = select_unheard(reviews)
    result = []
    for row in selected:
        require(file_hash(row['path']) == row['wav_sha256'])
        start = time.monotonic()
        segments, _ = model.transcribe(str(row['path']), **TRANSCRIBE)
        recognized = ''.join(segment.text for segment in segments).strip()
        require(isinstance(recognized, str) and len(recognized) <= 10000)
        score = transcript_score(row['expected_spoken'], recognized,
                                 row['critical_terms'], max_cer=0)
        normalized, normalization_edits, normalization_flags = normalize(recognized)
        normalized_score = transcript_score(row['expected_spoken'], normalized,
                                            row['critical_terms'], max_cer=0)
        normalization_flag_count = len(normalization_flags)
        del recognized, normalized, normalization_edits, normalization_flags
        elapsed = time.monotonic()-start
        require(math.isfinite(elapsed) and elapsed >= 0
            and file_hash(row['path']) == row['wav_sha256'])
        result.append(dict(unit_index=row['index'], wav_sha256=row['wav_sha256'],
            saved_small_character_errors=row['character_errors'],
            saved_small_missing_critical_count=len(row['missing_critical']),
            larger_character_errors=score['character_errors'],
            larger_missing_critical_count=len(score['missing_critical']),
            larger_exact=score['character_errors'] == 0 and not score['missing_critical'],
            normalized_character_errors=normalized_score['character_errors'],
            normalized_missing_critical_count=len(normalized_score['missing_critical']),
            normalization_review_flag_count=normalization_flag_count,
            normalized_exact=normalized_score['character_errors'] == 0
                and not normalized_score['missing_critical'] and normalization_flag_count == 0,
            saved_small_exact=row['character_errors'] == 0 and not row['missing_critical'],
            asr_seconds=round(elapsed, 3)))
        if progress is not None:
            progress(len(result), len(selected))
    require(len(result) == 28)
    return result


def inspect(directory, model_path, output, *, decoder_av_version):
    require(decoder_av_version == DECODER_AV_VERSION)
    check_context(os.environ, os.getuid(), os.geteuid())
    from namespace_guard import validate_snapshot
    validate_snapshot(os.getuid(), os.getuid(), socket.if_nameindex(),
        Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
        os.environ.get('BORA_HOST_NETNS', ''), Path('/proc/net/ipv6_route').read_text())
    values = [os.environ.pop(name, '') for name in preview.PRIVATE_KEYS]
    source_key, media_key = [base64.b64decode(value, validate=True) for value in values]
    del values
    require(len(source_key) == len(media_key) == 32 and source_key != media_key)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
    def deny(*args, **kwargs):
        raise RuntimeError('outbound_forbidden')
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    import runtime
    campaign, recipe, _, _ = runtime.config()
    require(runtime.identity(recipe) == full.PIN['recipe_sha256'])
    full.inventory(directory)
    with runtime.quiet():
        verify_model(model_path)
        from faster_whisper import WhisperModel
        model = WhisperModel(str(model_path), device='cpu', compute_type='int8',
                             cpu_threads=4, num_workers=1, local_files_only=True)
        plan, _ = runtime.source_plan('TB-001', campaign, source_key)
    del source_key
    require(runtime.identity(plan) == full.PIN['plan_sha256']
        and len(plan['units']) == 58
        and not any(u.get('review') or u.get('review_flags') for u in plan['units']))
    with tempfile.TemporaryDirectory(prefix='bora-private-crosscheck-',
                                     dir=os.environ['RUNNER_TEMP']) as temporary:
        with runtime.quiet():
            rows = runtime.restore(directory, media_key, plan, full.PIN['recipe_sha256'],
                full.PIN['render_settings_sha256'], Path(temporary))
            receipt_before = copy.deepcopy({i: row['receipt'] for i, row in rows.items()})
            reviews = preview.available_reviews(rows, plan)
            results = run_reviews(reviews, model)
            require(receipt_before == {i: row['receipt'] for i, row in rows.items()})
            heard = [dict(unit_index=x['index'], wav_sha256=x['wav_sha256'])
                     for x in reviews if x['index'] in OWNER_HEARD]
    del media_key, model
    summary = dict(schema=SCHEMA, status='read_only_diagnostics_not_a_release',
        request_id=REQUEST_ID, decoder_av_version=decoder_av_version,
        source_pin=full.PIN, model=dict(repo=MODEL_REPO, revision=MODEL_REVISION,
        model_sha256=MODEL_SHA256, compute_type='int8', cpu_threads=4),
        authenticated_units=58, original_passed=27, original_blocked=31,
        owner_heard_skipped=sorted(heard, key=lambda x: x['unit_index']),
        checked_units=len(results), larger_exact_units=sum(x['larger_exact'] for x in results),
        larger_mismatch_units=sum(not x['larger_exact'] for x in results),
        pronunciation_diagnostics=True, normalization_ruleset=RULESET,
        normalized_exact_units=sum(x['normalized_exact'] for x in results),
        normalized_mismatch_units=sum(not x['normalized_exact'] for x in results),
        asr_seconds=round(sum(x['asr_seconds'] for x in results), 3), units=results,
        synthesized=0, promoted=0, original_checkpoint_writes=0,
        production_status_changed=False, production_eligible=False,
        human_listening_verified_all_units=False, causal_attribution_confirmed=False)
    full.d.dump(output, summary)
    print(json.dumps(summary, sort_keys=True), flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('download-model', 'inspect'))
    parser.add_argument('--model-path', required=True)
    parser.add_argument('--directory')
    parser.add_argument('--output')
    args = parser.parse_args()
    validate_request(json.loads(REQUEST.read_text()))
    decoder_version = verify_decoder_version()
    if args.mode == 'download-model':
        require(args.directory is None and args.output is None)
        download_model(args.model_path)
    else:
        require(bool(args.directory) and bool(args.output))
        inspect(args.directory, args.model_path, args.output, decoder_av_version=decoder_version)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"failed","reason":"tb001_crosscheck_validation_failed"}', flush=True)
        raise SystemExit(1)
