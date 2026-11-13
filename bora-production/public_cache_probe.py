"""Public file-cache timing only. No inference, protected environment or keys."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

import public_asset_cache as cache

REQUEST = Path(__file__).with_name('public-cache-probe-request.json')
SCHEMA = 'bora-public-cache-probe/v1'


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def context():
    cache.pins.require_keyless()
    cache.check(not any(os.environ.get(name) for name in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN')),
                'probe_authentication_forbidden')
    request = json.loads(REQUEST.read_text())
    cache.check(request == {
        'schema': 'bora-public-cache-probe-request/v1',
        'request_id': request.get('request_id'),
        'purpose': 'three-fresh-runners-original-vs-cached-public-model-timing',
        'public_asset_count': 15, 'synthesis': False, 'private_inputs': False,
        'production_changed': False}, 'probe_request')
    cache.check(isinstance(request['request_id'], str)
                and re.fullmatch(r'[a-z0-9-]{1,80}', request['request_id']), 'probe_request_id')
    run, attempt = os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT']
    cache.check(run.isdigit() and attempt.isdigit(), 'probe_run_identity')
    manifest = cache.load_manifest()
    pinset = {name: model['files'] for name, model in manifest['models'].items()}
    return manifest, dict(schema=SCHEMA, request_sha256=identity(request),
        manifest_sha256=cache.digest(cache.MANIFEST), pinset_sha256=identity(pinset),
        run_id=run, run_attempt=attempt, commit=os.environ['GITHUB_SHA'],
        file_count=sum(len(m['files']) for m in manifest['models'].values()),
        pinned_bytes=sum(f['bytes'] for m in manifest['models'].values() for f in m['files'].values()),
        production_eligible=False, synthesis_started=False, private_inputs_used=False)


def state_path(value):
    path = Path(value).absolute()
    base = Path(os.environ['RUNNER_TEMP']).absolute()
    cache.check(path == base/'bora-public-cache-probe.json', 'probe_state_path')
    cache.no_symlink_parents(path)
    return path


def load_state(path):
    if not path.exists():
        return {'markers': {}}
    cache.check(path.stat().st_size <= 32 * 1024, 'probe_state_bounds')
    result = json.loads(path.read_text())
    cache.check(isinstance(result, dict) and isinstance(result.get('markers'), dict), 'probe_state')
    return result


def interval(markers, start, end):
    a, b = markers[start], markers[end]
    cache.check(type(a) is int and type(b) is int and 0 < a < b, 'probe_clock')
    return (b-a)/1e9


def verify_renderer_compatibility(runtime, manifest):
    # Importing this module does not construct a renderer or import torch.
    import fp32_renderer as original
    actual = {
        'qwen': original.verify_assets(runtime/'qwen', original.MODEL_REQUIRED, original.MODEL_REVISION),
        'asr': original.verify_assets(runtime/'asr', original.ASR_REQUIRED, original.ASR_REVISION)}
    expected = {name: {path: entry['sha256'] for path, entry in model['files'].items()}
                for name, model in manifest['models'].items()}
    cache.check(actual == expected, 'probe_original_verifier_hashes')


def summarize(fill, restored, baseline):
    fields = ('schema', 'request_sha256', 'manifest_sha256', 'pinset_sha256', 'run_id',
              'run_attempt', 'commit', 'file_count', 'pinned_bytes')
    cache.check(all(fill.get(k) == restored.get(k) == baseline.get(k) for k in fields)
                and fill.get('schema') == SCHEMA and fill.get('file_count') == 15,
                'probe_evidence_identity')
    cache.check(fill.get('phase') == 'fill' and restored.get('phase') == 'restore'
                and baseline.get('phase') == 'baseline', 'probe_phase')
    for row in (fill, restored):
        for field in ('prepare_seconds', 'verify_seconds', 'cache_action_bracket_seconds',
                      'renderer_compatibility_verify_seconds'):
            value = row.get(field)
            cache.check(type(value) in (int, float) and math.isfinite(value) and value > 0,
                        'probe_timing')
    for field in ('download_and_size_json_verify_seconds', 'renderer_compatibility_verify_seconds',
                  'original_baseline_total_seconds'):
        value = baseline.get(field)
        cache.check(type(value) in (int, float) and math.isfinite(value) and value > 0,
                    'probe_baseline_timing')
    valid = (baseline.get('all_pinned_files_verified') is True
             and baseline.get('original_renderer_verifier_compatible') is True
             and baseline.get('original_hf_metadata_preserved') is True
             and fill.get('fresh_download') is True and fill.get('all_pinned_files_verified') is True
             and fill.get('original_renderer_verifier_compatible') is True
             and fill.get('cache_action_outcome') == 'success'
             and restored.get('all_pinned_files_verified') is True
             and restored.get('original_renderer_verifier_compatible') is True
             and restored.get('cache_action_outcome') == 'success'
             and restored.get('cache_hit') is True and restored.get('fresh_download') is False)
    cold = fill['prepare_seconds'] + fill['verify_seconds']
    warm = (restored['cache_action_bracket_seconds'] + restored['prepare_seconds']
            + restored['verify_seconds'])
    production_warm = warm + restored['renderer_compatibility_verify_seconds']
    original = baseline['original_baseline_total_seconds']
    return dict(schema=SCHEMA, status='cache_restore_verified' if valid else 'warm_comparison_invalid',
        fill=fill, restore=restored, baseline=baseline, warm_cache_verified=valid,
        original_production_prepare_seconds=original,
        proposed_warm_production_prepare_seconds=production_warm,
        observed_prepare_seconds_saved=original-production_warm if valid else None,
        observed_prepare_reduction_pct=100*(original-production_warm)/original if valid else None,
        cache_faster_than_original=production_warm < original if valid else None,
        auxiliary_new_cold_prepare_verify_seconds=cold,
        auxiliary_new_warm_restore_prepare_verify_seconds=warm,
        auxiliary_new_path_prepare_seconds_saved=cold-warm if valid else None,
        fill_with_cache_save_seconds=cold+fill['cache_action_bracket_seconds'],
        production_eligible=False, synthesis_started=False, private_inputs_used=False,
        limitations=[
            'Cache action brackets include runner step transitions; they are not pure transfer time.',
            'Independent fresh runners can have different CPU, disk and network performance.',
            'One trial measures public model preparation only; installation, queues and inference are excluded.',
            'Primary comparison includes the original renderer hash verification on both baseline and warm paths.',
            'New cold-vs-new warm figures are auxiliary and cannot establish an improvement over the original downloader.',
            'The isolated run-specific probe cache does not measure production cache hit frequency.',
            'Negative savings are retained; no overall lecture-production speedup is established.'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('mark', 'prepare', 'baseline', 'finish', 'aggregate'))
    parser.add_argument('--state', required=True)
    parser.add_argument('--phase', choices=('fill', 'restore'))
    parser.add_argument('--name', choices=('save_start', 'restore_start', 'restore_end'))
    parser.add_argument('--cache-hit', choices=('true', 'false', ''), default='')
    parser.add_argument('--cache-outcome', default='')
    args = parser.parse_args()
    manifest, expected = context()
    path = state_path(args.state)
    state = load_state(path)
    if args.command == 'mark':
        cache.check(args.name is not None and args.name not in state['markers'], 'probe_marker')
        state['markers'][args.name] = time.monotonic_ns()
    elif args.command == 'prepare':
        cache.check(args.phase in ('fill', 'restore'), 'probe_phase')
        base = Path(os.environ['RUNNER_TEMP']).absolute()
        cached, runtime = base/'bora-public-assets', base/'bora-verified-models'
        if args.phase == 'fill':
            cache.check(not cached.exists() and not cached.is_symlink()
                        and not runtime.exists() and not runtime.is_symlink(), 'probe_fill_not_fresh')
        os.environ.update(HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                          HF_HUB_DISABLE_PROGRESS_BARS='1', DO_NOT_TRACK='1')
        started = time.perf_counter()
        fresh = cache.prepare(cached, runtime, manifest)
        prepared = time.perf_counter()-started
        started = time.perf_counter()
        cache.verify(runtime, manifest, runtime=True)
        verified = time.perf_counter()-started
        started = time.perf_counter()
        verify_renderer_compatibility(runtime, manifest)
        compatible = time.perf_counter()-started
        state.update(expected, phase=args.phase, fresh_download=fresh,
            cache_hit=args.cache_hit == 'true', prepare_seconds=prepared,
            verify_seconds=verified, all_pinned_files_verified=True,
            renderer_compatibility_verify_seconds=compatible,
            original_renderer_verifier_compatible=True)
    elif args.command == 'baseline':
        from huggingface_hub import snapshot_download
        base = Path(os.environ['RUNNER_TEMP']).absolute()
        runtime = base/'bora-original-models'
        cache.no_symlink_parents(runtime)
        cache.check(not runtime.exists(), 'probe_baseline_not_fresh')
        os.environ.update(HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                          HF_HUB_DISABLE_PROGRESS_BARS='1', DO_NOT_TRACK='1')
        total_started = time.perf_counter()
        started = time.perf_counter()
        for name, (repository, revision, required) in cache.MODELS.items():
            target = runtime/name
            # Match the current downloader; preserve its original HF metadata.
            snapshot_download(repository, revision=revision, local_dir=target,
                              allow_patterns=list(required), max_workers=4)
            cache.pins.verify_assets(target, required)
        downloaded = time.perf_counter()-started
        started = time.perf_counter()
        verify_renderer_compatibility(runtime, manifest)
        compatible = time.perf_counter()-started
        total = time.perf_counter()-total_started
        state.update(expected, phase='baseline', download_and_size_json_verify_seconds=downloaded,
            renderer_compatibility_verify_seconds=compatible, original_baseline_total_seconds=total,
            all_pinned_files_verified=True, original_renderer_verifier_compatible=True,
            original_hf_metadata_preserved=True)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('summary='+json.dumps(state, sort_keys=True, separators=(',', ':'))+'\n')
        print(json.dumps(state, sort_keys=True))
    elif args.command == 'finish':
        cache.check(all(state.get(k) == v for k, v in expected.items()), 'probe_state_identity')
        cache.check(args.phase == state.get('phase'), 'probe_phase')
        if args.phase == 'fill':
            state['markers']['save_end'] = time.monotonic_ns()
            span = interval(state['markers'], 'save_start', 'save_end')
        else:
            span = interval(state['markers'], 'restore_start', 'restore_end')
        state.update(cache_action_bracket_seconds=span, cache_action_outcome=args.cache_outcome,
                     cache_action_bracket_includes_step_transitions=True)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('summary='+json.dumps(state, sort_keys=True, separators=(',', ':'))+'\n')
        print(json.dumps(state, sort_keys=True))
    else:
        cache.check(all(state.get(k) == v for k, v in expected.items()), 'probe_state_identity')
        fill = json.loads(os.environ['FILL_SUMMARY_JSON'])
        baseline = json.loads(os.environ['BASELINE_SUMMARY_JSON'])
        print(json.dumps(summarize(fill, state, baseline), sort_keys=True))
        return
    path.write_text(json.dumps(state, sort_keys=True))


if __name__ == '__main__':
    main()
