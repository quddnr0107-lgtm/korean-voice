"""Offline diagnostic only. No rendering, assembly, sealing, upload or repair.
Run alongside unchanged pinned bora-production modules, in existing protected
single_pass assembly context. stdout is one sanitized aggregate JSON record.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

PIN = '836500cd9250f38698235c729ae5553f41e7c262'
LESSONS = ('TB-005','TB-013','TB-018')
PINS = {'runtime.py': 'b8646dd6f77cade4142a53274152e1646dc357c15c0d68ebf0caf5d07d2256c4',
        'assemble.py': '6c5f04ed3c9d995863e61b628765844be65900bfee41133905dfc43d14745059'}
SCHEMA = 'tb3-residual-silence-diagnostic/v1'


def silent_runs(pcm, rate, np):
    """Production frame math; intervals are diagnostic, never trim instructions."""
    frame = max(1, round(rate * .02))
    count = len(pcm) // frame
    squares = pcm.astype(np.float64) ** 2
    flags = squares[:count*frame].reshape(count, frame).mean(axis=1) < 10**(-50/10)
    runs = []
    start = None
    for i in range(count + 1):
        if i < count and flags[i]:
            if start is None: start = i
        elif start is not None:
            end = i * frame
            # Full-frame metric intentionally ignores incomplete terminal frame.
            runs.append({'start_sample': start*frame, 'end_sample': end,
                         'duration_s': (i-start)*frame/rate,
                         'placement': 'whole' if start == 0 and i == count else
                         'leading' if start == 0 else 'trailing' if i == count else 'internal',
                         'exact_zero': bool(np.all(pcm[start*frame:end] == 0))})
            start = None
    return runs


def inspect_unit(a, row):
    """Observe _metrics, not the gate. All original checks still execute/raise."""
    captured = {}
    original = a._metrics
    def observe(pcm):
        metrics = original(pcm)
        runs = silent_runs(pcm, a.SAMPLE_RATE, a.np)
        maximum = max((r['duration_s'] for r in runs), default=0)
        if maximum != metrics['max_silence_s']:
            raise RuntimeError('diagnostic_math_mismatch')
        strict = {}
        frame = round(a.SAMPLE_RATE * .02)
        squares = pcm.astype(a.np.float64) ** 2
        energy = squares[:len(pcm)//frame*frame].reshape(-1,frame).mean(axis=1)
        for db in (-60,-70):
            longest = run = 0
            for silent in energy < 10**(db/10):
                run = run+1 if silent else 0
                longest = max(longest,run)
            strict[str(db)] = longest*frame/a.SAMPLE_RATE
        captured.update(strict_max_silence_s=strict,max_silence_s=maximum,
                        long_runs=[r for r in runs if r['duration_s'] >= 3])
        return metrics
    a._metrics = observe
    try:
        a._input_pcm(row['path'], row['receipt'], 'ffmpeg', 'ffprobe')
        status = 'passed'
    except a.AssemblyError as exc:
        # Never expose exception text: only recognized public gate labels.
        status = str(exc) if str(exc) in {
            'long_silence_pcm','clipped_pcm','silent_pcm','unit_audio_hash',
            'unit_wav_format','unit_decoded_samples','unit_nonfinite_pcm',
            'unit_clipped_pcm','unit_silent_pcm','resample_alignment',
            'resample_duration','nonfinite_pcm','empty_or_nonmono_pcm'} else 'other_assembly_error'
    finally:
        a._metrics = original
    return {'unit_index': row['unit_index'], 'status': status, **captured}


def summarize(lesson, rows):
    counts = Counter(row['status'] for row in rows)
    long_runs = [r for row in rows for r in row.get('long_runs', [])]
    return {'schema': SCHEMA, 'reference_commit': PIN, 'lesson_id': lesson,
            'unit_count': len(rows), 'status_counts': dict(sorted(counts.items())),
            'max_silence_s': max((r.get('max_silence_s', 0) for r in rows), default=0),
            'long_run_count': len(long_runs),
            'long_run_placements': dict(sorted(Counter(r['placement'] for r in long_runs).items())),
            'exact_zero_long_runs': sum(r['exact_zero'] for r in long_runs),
            'strict_max_silence_s': {str(db): max((row.get('strict_max_silence_s',{}).get(str(db),0) for row in rows),default=0) for db in (-60,-70)},
            'padding_certified': False, 'diagnostic_only': True, 'repair_authorized': False}


def run(args):
    if args.lesson_id not in LESSONS: raise ValueError('scope')
    module_dir = Path(args.module_dir).resolve()
    for name, expected in PINS.items():
        if hashlib.sha256((module_dir/name).read_bytes()).hexdigest() != expected:
            raise ValueError('pinned_module_changed')
    sys.path.insert(0, str(module_dir))
    import runtime as rt
    import assemble as a
    if Path(rt.__file__).resolve() != module_dir/'runtime.py' or Path(a.__file__).resolve() != module_dir/'assemble.py':
        raise ValueError('module_origin')
    os.umask(0o077)
    # Match production order; protected_keys enforces original branch, push,
    # workflow identity, unprivileged isolated netns and consumes key env vars.
    with rt.quiet():
        campaign, recipe, request, ids = rt.config('single_pass')
        rt.check_lesson_scope(args, request, ids, 'single_pass')
        source_key, media_key = rt.protected_keys(media=True, profile='single_pass')
        public, plan, _, _ = rt.matched_plan(args,campaign,recipe,request,source_key)
        del source_key
        recipe_hash = rt.identity(recipe)
        settings_hash = public['render_settings_sha256']
        with tempfile.TemporaryDirectory(prefix='bora-diagnose-',dir=os.environ.get('RUNNER_TEMP')) as td:
            restored = rt.restore(args.resume_dir, media_key, plan, recipe_hash,
                                  settings_hash, Path(td)/'restored', profile='single_pass')
            del media_key
            rt.check(set(restored) == set(range(len(plan['units']))) and
                     all(v['receipt'].get('status') == 'passed' for v in restored.values()),
                     'lesson_not_complete')
            _, ordered = a.validate_entries(plan, list(restored.values()), recipe_hash,
                                            settings_hash, profile='single_pass')
            rows = [inspect_unit(a, row) for row in ordered]
            report = summarize(args.lesson_id, rows)
            # Unit offsets remain memory-only. No audio/source/receipts/keys or
            # unit IDs leave the protected process, and restored temp is cleaned.
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--module-dir', required=True)
    parser.add_argument('--lesson-id', choices=LESSONS, required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--resume-dir', required=True)
    args = parser.parse_args()
    try:
        result = run(args)
    except BaseException:
        # Deliberately omit error messages/tracebacks/private locals.
        print(json.dumps({'schema': SCHEMA, 'lesson_id': args.lesson_id,
                          'status': 'diagnostic_failed', 'diagnostic_only': True}))
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
