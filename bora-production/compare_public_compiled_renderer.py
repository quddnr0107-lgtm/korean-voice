"""Prove byte-identical public audio through the actual producer entrypoints."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from runtime import atomic, check, close_renderer, config, enc, identity, make_renderer, quiet

PUBLIC_TEXT = '안녕하세요. 오늘은 차근차근 설명하겠습니다.'


def pcm(renderer, path):
    data, rate = renderer.sf.read(path, dtype='float32')
    check(rate == 24000 and data.ndim == 1 and len(data) > 0, 'public_pcm_format')
    return dict(samples=len(data), rate=rate,
                sha256=hashlib.sha256(data.astype('<f4', copy=False).tobytes()).hexdigest())


def sample(profile, result):
    from assemble import assemble_lesson
    from fp32_renderer import require_guard
    from planning import build_plan

    require_guard()
    _, recipe, _, _ = config(profile)
    plan = build_plan('TB-002', PUBLIC_TEXT)
    with tempfile.TemporaryDirectory(prefix='bora-public-parity-', dir=os.environ['RUNNER_TEMP']) as temp:
        work = Path(temp)
        renderer = None
        started = time.perf_counter()
        try:
            with quiet():
                renderer = make_renderer(recipe, profile)
            load_seconds = time.perf_counter() - started
            entries, units = [], []
            for index, unit in enumerate(plan['units']):
                directory = work / f'unit-{index}'
                with quiet():
                    receipt = renderer.render_unit(unit, directory)
                check(receipt['status'] == 'passed' and receipt['natural_eos'] is True,
                      'public_render_or_asr_failed')
                picked = next(c for c in receipt['candidates'] if c['seed'] == receipt['seed'])
                units.append(dict(seed=receipt['seed'], tries=len(receipt['candidates']),
                    raw=pcm(renderer, directory / picked['file']),
                    final=pcm(renderer, directory / 'final.wav'),
                    tts_seconds=receipt['tts_seconds']))
                receipt.update(lesson_id=plan['lesson_id'], unit_index=index,
                               plan_sha256=identity(plan), recipe_sha256=identity(recipe))
                entries.append(dict(unit_index=index, receipt=receipt, path=directory / 'final.wav'))
            assembled = assemble_lesson(plan, entries, work / 'assembled',
                expected_recipe_sha256=identity(recipe),
                expected_render_settings_sha256=renderer.settings_sha256,
                unit_gap_ms=recipe['unit_gap_ms'], paragraph_gap_ms=recipe['paragraph_gap_ms'],
                max_part_seconds=recipe['max_part_seconds'])
            data = dict(profile=profile, plan_sha256=identity(plan), recipe_sha256=identity(recipe),
                settings_sha256=renderer.settings_sha256, load_seconds=load_seconds, units=units,
                assembled_samples=assembled['total_samples'],
                assembled_sha256=[p['audio_sha256'] for p in assembled['audio_parts']],
                strict_asr_and_signal_passed=True, source_key_used=False, private_manuscript_read=False)
            if profile == 'compiled':
                evidence = renderer.execution_evidence()
                check(evidence['counters']['compiled_graph_invocations'] > 0
                    and evidence['counters']['backend_graphs_compiled'] > 0
                    and evidence['fallback_used'] is False, 'public_compiled_execution_not_observed')
                data.update(base_settings_sha256=renderer.base_render_settings_sha256,
                            execution=evidence)
        finally:
            close_renderer(renderer)
    atomic(result, enc(data))


def compare(eager_path, compiled_path, result):
    eager, compiled = (json.loads(Path(p).read_text()) for p in (eager_path, compiled_path))
    check(eager['profile'] == 'eager' and compiled['profile'] == 'compiled', 'public_profile_pair')
    check(all(x['strict_asr_and_signal_passed'] is True and x['source_key_used'] is False
              and x['private_manuscript_read'] is False for x in (eager, compiled)), 'public_scope')
    check(eager['settings_sha256'] == compiled['base_settings_sha256']
          and eager['settings_sha256'] != compiled['settings_sha256'], 'public_settings_boundary')
    check(all(eager[k] == compiled[k] for k in ('plan_sha256', 'recipe_sha256',
          'assembled_sha256', 'assembled_samples')), 'public_assembly_parity')
    check(len(eager['units']) == len(compiled['units']) > 0 and
          all(all(a[k] == b[k] for k in ('seed', 'tries', 'raw', 'final'))
              for a, b in zip(eager['units'], compiled['units'])), 'public_pcm_parity')
    output = dict(status='public_compiled_producer_parity_passed',
                  render_settings_sha256=compiled['settings_sha256'], course='TB',
                  eager=eager, compiled=compiled)
    atomic(result, enc(output))
    print(json.dumps(output, allow_nan=False))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('sample')
    run.add_argument('profile', choices=('eager', 'compiled'))
    run.add_argument('result')
    pair = sub.add_parser('compare')
    pair.add_argument('eager')
    pair.add_argument('compiled')
    pair.add_argument('result')
    args = parser.parse_args()
    if args.command == 'sample':
        sample(args.profile, args.result)
    else:
        compare(args.eager, args.compiled, args.result)


if __name__ == '__main__':
    main()
