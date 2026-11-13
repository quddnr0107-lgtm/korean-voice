"""Public original-FP32 resident compilation amortization trial.

One compiled callable is retained. Exact decoded PCM and generated sequences
must match the fixture baseline before any measured speed claim is allowed.
"""
from __future__ import annotations
import argparse
import contextlib
import functools
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time

import cpu_compile_benchmark as base

SCHEMA = 'sohee-public-cpu-compile-resident/v1'
COMPILED = base.COMPILED_MODE
SCHEDULE = (
    ('baseline', 'eager', 0), ('baseline', 'eager', 1),
    ('cold', COMPILED, 0), ('cold', COMPILED, 1),
    ('measured', 'eager', 0), ('measured', COMPILED, 0),
    ('measured', COMPILED, 1), ('measured', 'eager', 1),
    ('measured', COMPILED, 0), ('measured', 'eager', 0),
    ('measured', 'eager', 1), ('measured', COMPILED, 1),
)
CALL_SECONDS = 360
WORKER_SECONDS = 1240


def request_value():
    return dict(schema=SCHEMA,
        request_id='public-sohee-fp32-predictor-resident-20261113-01',
        model_revision=base.REVISION, fixture_count=2, threads=4, interop_threads=1,
        call_timeout_seconds=CALL_SECONDS, worker_timeout_seconds=WORKER_SECONDS,
        compile_scope=base.COMPILE_SCOPE, compile_target_path=base.COMPILE_TARGET_PATH,
        eager_mask_aliases=list(base.MASK_ALIASES), compile_threads=1,
        maximum_compiled_graphs=base.MAX_GRAPHS,
        schedule=[list(item) for item in SCHEDULE],
        production=False, secrets=False, gpu=False)


def validate_request():
    base.require(json.loads(Path(__file__).with_name(
        'cpu-compile-resident-request.json').read_text()) == request_value())


class ResidentForward:
    """Construct compiler/mask wrappers once; mode switches only assign them."""
    def __init__(self, predictor, module, compiler, disable, backend, counts):
        self.predictor, self.module, self.counts = predictor, module, counts
        self.original = predictor.forward
        self.masks = {name: getattr(module, name) for name in base.MASK_ALIASES}
        self.disabled_masks = {name: disable(fn, recursive=True)
            for name, fn in self.masks.items()}
        replacement = compiler(self.original, backend=backend, fullgraph=False, dynamic=True)
        @functools.wraps(self.original)
        def forward(*args, **kwargs):
            counts['predictor_forward_calls'] += 1
            return replacement(*args, **kwargs)
        self.compiled = forward

    @contextlib.contextmanager
    def select(self, mode):
        base.require(mode in base.MODES and self.predictor.forward == self.original)
        base.require(all(getattr(self.module, name) is fn for name, fn in self.masks.items()))
        try:
            if mode == COMPILED:
                self.predictor.forward = self.compiled
                for name, fn in self.disabled_masks.items():
                    setattr(self.module, name, fn)
            yield
        finally:
            self.predictor.forward = self.original
            for name, fn in self.masks.items():
                setattr(self.module, name, fn)


def summarize(rows, counts, setup_seconds=0.0):
    base.require(len(rows) <= len(SCHEDULE)
        and [(r['phase'], r['mode'], r['fixture']) for r in rows] == list(SCHEDULE[:len(rows)]))
    base.require(type(setup_seconds) in (int, float) and math.isfinite(setup_seconds) and setup_seconds >= 0)
    for index, row in enumerate(rows):
        base.require(row['call_index'] == index and row['seed'] == 5192+row['fixture']
            and row['generation_args'] == base.generation_args('production_style', base.FIXTURES[row['fixture']]))
        for key in ('tts_seconds', 'audio_seconds'):
            base.require(type(row[key]) in (int, float) and math.isfinite(row[key]) and row[key] > 0)
        for key in ('natural_eos', 'signal_valid', 'exact_pcm_equal', 'exact_sequence_equal', 'exact_rate_and_samples'):
            base.require(type(row[key]) is bool)
        for key in ('compiled_graph_invocations_during_call', 'new_graphs_compiled_during_call'):
            base.require(type(row[key]) is int and row[key] >= 0)
    base.require(all(type(value) is int and value >= 0 for value in counts.values()))
    equality = all(r['exact_pcm_equal'] and r['exact_sequence_equal']
        and r['exact_rate_and_samples'] for r in rows)
    signal_ok = all(r['natural_eos'] and r['signal_valid'] for r in rows)
    compiled_rows = [r for r in rows if r['mode'] == COMPILED]
    executed = bool(compiled_rows) and all(r['compiled_graph_invocations_during_call'] > 0 for r in compiled_rows)
    measured = [r for r in rows if r['phase'] == 'measured']
    steady = bool(measured) and all(r['new_graphs_compiled_during_call'] == 0
        for r in measured if r['mode'] == COMPILED)
    complete = len(rows) == len(SCHEDULE)
    valid = complete and equality and signal_ok and executed and steady
    pairs = []
    for fixture in range(2):
        eager = [r for r in measured if r['fixture'] == fixture and r['mode'] == 'eager']
        compiled = [r for r in measured if r['fixture'] == fixture and r['mode'] == COMPILED]
        for repeat, (a, b) in enumerate(zip(eager, compiled)):
            pairs.append(dict(fixture=fixture, repeat=repeat,
                eager_call_index=a['call_index'], compiled_call_index=b['call_index'],
                eager_seconds=a['tts_seconds'], compiled_seconds=b['tts_seconds'],
                saved_seconds=a['tts_seconds']-b['tts_seconds']))
    warm_eager = sum(r['tts_seconds'] for r in measured if r['mode'] == 'eager')
    warm_compiled = sum(r['tts_seconds'] for r in measured if r['mode'] == COMPILED)
    checkpoints = []
    for end in (4, 8, 12):
        if len(rows) < end:
            continue
        prefix = rows[:end]
        eager = sum(r['tts_seconds'] for r in prefix if r['mode'] == 'eager')
        compiled = setup_seconds+sum(r['tts_seconds'] for r in prefix if r['mode'] == COMPILED)
        checkpoints.append(dict(completed_calls=end, units_per_mode=end//2,
            eager_seconds=eager, compiled_seconds_including_setup=compiled,
            compiled_saved_seconds=eager-compiled))
    total_eager = sum(r['tts_seconds'] for r in rows if r['mode'] == 'eager')
    total_compiled = setup_seconds+sum(r['tts_seconds'] for r in rows if r['mode'] == COMPILED)
    return dict(schema=SCHEMA,
        status='complete_exact_public_outputs_measured' if valid else 'partial_or_unvalidated_no_speed_claim',
        complete=complete, exact_public_outputs_equal=equality if rows else None,
        all_natural_eos_and_signal_valid=signal_ok if rows else None,
        compiled_execution_observed=executed, measured_compiled_calls_have_no_new_graphs=steady,
        speed_claim_valid=valid, rows=rows, compile_counters=dict(counts),
        compile_wrapper_setup_seconds=setup_seconds, measured_pairs=pairs,
        measured_eager_seconds=warm_eager, measured_compiled_seconds=warm_compiled,
        measured_wall_ratio_eager_over_compiled=warm_eager/warm_compiled if valid else None,
        measured_pair_median_saved_seconds=statistics.median(p['saved_seconds'] for p in pairs) if valid else None,
        total_eager_synthesis_seconds=total_eager,
        total_compiled_synthesis_and_setup_seconds=total_compiled,
        observed_total_net_saved_seconds=total_eager-total_compiled,
        total_wall_ratio_including_compile_setup=total_eager/total_compiled if valid else None,
        cumulative_cost_checkpoints=checkpoints,
        observed_break_even_completed_calls=next((p['completed_calls'] for p in checkpoints
            if p['compiled_saved_seconds'] >= 0), None) if valid else None,
        break_even_forecast_performed=False,
        model=base.MODEL, model_revision=base.REVISION, dtype='float32', device='cpu',
        speaker='Sohee', threads=4, interop_threads=1,
        compile_scope=base.COMPILE_SCOPE, compile_target_path=base.COMPILE_TARGET_PATH,
        retained_compiled_callable=True, dynamo_reset_between_calls=False,
        full_course_forecast_validated=False, production_eligible=False,
        human_listening_verified=False, asr_performed=False, dsp_performed=False,
        model_load_included_in_synthesis_totals=False,
        caveat='Two public fixtures only. PCM, rate, sample count and full returned talker sequence match each fixture baseline. Cold compilation and wrapper setup are included in cumulative synthesis cost; common model load, downloads, saving and validation are separately excluded. Only measured completed checkpoints establish cost recovery; no extrapolation or full-course quality approval.')


def worker(model_path, output):
    from download_models import require_keyless
    require_keyless()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4',
        TORCHINDUCTOR_COMPILE_THREADS='1', MAX_JOBS='1')
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    from torch._dynamo.backends.registry import lookup_backend
    from torch._dynamo.utils import counters
    import torch._inductor.config as inductor_config
    from fp32_renderer import verify_assets, MODEL_REQUIRED
    torch.set_num_threads(4); torch.set_num_interop_threads(1)
    torch._dynamo.config.suppress_errors = False
    torch._dynamo.config.recompile_limit = base.MAX_GRAPHS
    inductor_config.compile_threads = 1
    out = Path(output); rows = []; setup_seconds = 0.0
    counts = dict(backend_compile_attempts=0, backend_graphs_compiled=0,
        compiled_graph_invocations=0, predictor_forward_calls=0)
    metadata = dict(cpu=base.hardware(), torch_version=torch.__version__,
        compiler_version=subprocess.run(['g++', '--version'], capture_output=True,
            text=True, check=True).stdout.splitlines()[0],
        native_threads=base.validate_native_threads(4, torch.get_num_threads(),
            torch.get_num_interop_threads(), torch.__config__.parallel_info()),
        model_assets_sha256=verify_assets(model_path, MODEL_REQUIRED, base.REVISION))
    started = time.perf_counter(); trial_started = started
    base.dump(out/'summary.json', dict(summarize(rows, counts), **metadata, status='loading_original_fp32_model'))
    def save(**extra):
        report = dict(summarize(rows, counts, setup_seconds), **metadata,
            worker_elapsed_seconds=time.perf_counter()-trial_started, **extra)
        base.dump(out/'summary.json', report)
        return report
    def deadline(*unused):
        raise TimeoutError('public_resident_compile_call_timeout')
    signal.signal(signal.SIGALRM, deadline)
    index = -1
    try:
        model = Qwen3TTSModel.from_pretrained(str(Path(model_path).resolve()),
            device_map='cpu', dtype=torch.float32, attn_implementation='sdpa',
            low_cpu_mem_usage=True, local_files_only=True)
        model.model.eval(); metadata['model_load_seconds'] = time.perf_counter()-started
        base.require(all(p.dtype == torch.float32 and p.device.type == 'cpu'
            for p in model.model.parameters() if p.is_floating_point()))
        predictor = model.model.talker.code_predictor; target = predictor.model
        original_outer = predictor.forward; modeling = inspect.getmodule(type(predictor))
        base.require(modeling is not None)
        metadata['original_predictor_source_sha256'] = base.digest(inspect.getsourcefile(type(predictor)))
        base.require(metadata['original_predictor_source_sha256'] == base.MODELING_SHA256)
        metadata['memory_after_load'] = base.rss_snapshot()
        resident = None; references = {}
        for index, (phase, mode, fixture) in enumerate(SCHEDULE):
            base.require(predictor.forward == original_outer)
            if phase == 'cold' and resident is None:
                started = time.perf_counter()
                resident = ResidentForward(target, modeling, torch.compile, torch.compiler.disable,
                    base.tracked_backend(lookup_backend('inductor'), counts), counts)
                setup_seconds = time.perf_counter()-started
                save(status='resident_wrapper_ready_before_cold_call')
            before = dict(counts); observed = []
            talker = model.model.talker; original_generate = talker.generate
            eos = int(model.model.config.talker_config.codec_eos_token_id)
            def observe(*args, **kwargs):
                result = original_generate(*args, **kwargs)
                tokens = result.sequences[0].tolist()
                observed.append(dict(base.observe_eos(tokens, eos), talker_sequence=tokens,
                    talker_sequence_sha256=hashlib.sha256(json.dumps(tokens,
                        separators=(',', ':')).encode()).hexdigest()))
                return result
            talker.generate = observe
            torch.manual_seed(5192+fixture); signal.alarm(CALL_SECONDS)
            started = time.perf_counter()
            try:
                context = resident.select(mode) if resident else contextlib.nullcontext()
                with context, torch.inference_mode():
                    waves, rate = model.generate_custom_voice(**base.generation_args(
                        'production_style', base.FIXTURES[fixture]))
                elapsed = time.perf_counter()-started
            finally:
                signal.alarm(0); talker.generate = original_generate
            base.require(len(waves) == 1 and len(observed) == 1 and rate == 24000)
            wave = np.asarray(waves[0], dtype='float32')
            valid = bool(wave.ndim == 1 and wave.size > 0 and np.isfinite(wave).all()
                and np.max(np.abs(wave)) < 1 and np.sqrt(np.mean(wave.astype('float64')**2)) > 1e-5)
            wav = out/f'{index:02d}-{phase}-{mode}-{fixture}.wav'
            sf.write(wav, wave, rate, subtype='FLOAT')
            if phase == 'baseline':
                references[fixture] = (wave.copy(), rate, observed[0]['talker_sequence'])
            canonical_wave, canonical_rate, canonical_sequence = references[fixture]
            row = dict(call_index=index, phase=phase, mode=mode, fixture=fixture,
                seed=5192+fixture, tts_seconds=elapsed, audio_seconds=len(wave)/rate,
                sample_rate=rate, pcm_samples=len(wave), signal_valid=valid, **observed[0],
                exact_pcm_equal=bool(np.array_equal(wave, canonical_wave)
                    and wave.astype('<f4', copy=False).tobytes()
                        == canonical_wave.astype('<f4', copy=False).tobytes()),
                exact_sequence_equal=observed[0]['talker_sequence'] == canonical_sequence,
                exact_rate_and_samples=rate == canonical_rate and len(wave) == len(canonical_wave),
                wav_file=wav.name, wav_sha256=base.digest(wav),
                pcm_sha256=hashlib.sha256(wave.astype('<f4', copy=False).tobytes()).hexdigest(),
                generation_args=base.generation_args('production_style', base.FIXTURES[fixture]),
                compiled_graph_invocations_during_call=counts['compiled_graph_invocations']-before['compiled_graph_invocations'],
                new_graphs_compiled_during_call=counts['backend_graphs_compiled']-before['backend_graphs_compiled'],
                predictor_forward_calls_during_call=counts['predictor_forward_calls']-before['predictor_forward_calls'],
                dynamo_counters=base.dynamo_counts(counters), memory=base.rss_snapshot())
            rows.append(row)
            base.dump(out/f'row-{index:02d}.json', row); save()
            print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)
            base.require(valid and row['natural_eos'] and row['exact_pcm_equal']
                and row['exact_sequence_equal'] and row['exact_rate_and_samples'])
            base.require(predictor.forward == original_outer)
        report = save(memory_after_synthesis=base.rss_snapshot())
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if report['speed_claim_valid'] else 1
    except Exception as error:
        signal.alarm(0)
        diagnostic = out/f'error-call-{index:02d}.json'
        base.dump(diagnostic, base.public_error_report(error))
        save(status='resident_trial_failed', failed_call_index=index,
            error_type=type(error).__name__, error_file=diagnostic.name,
            error_sha256=base.digest(diagnostic), speed_claim_valid=False,
            measured_wall_ratio_eager_over_compiled=None,
            measured_pair_median_saved_seconds=None,
            total_wall_ratio_including_compile_setup=None,
            observed_break_even_completed_calls=None)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args(); validate_request()
    from download_models import require_keyless, verify_assets, QWEN_REQUIRED
    require_keyless(); model, output = Path(args.model).resolve(), Path(args.output).resolve()
    if args.worker:
        return worker(model, output)
    verify_assets(model, QWEN_REQUIRED); output.mkdir(parents=True, exist_ok=False)
    base.dump(output/'summary.json', dict(summarize([], {}), status='worker_starting'))
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
        '--model', str(model), '--output', str(output), '--worker'], start_new_session=True)
    try:
        result = process.wait(timeout=WORKER_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
        result = 1
        report = json.loads((output/'summary.json').read_text())
        report.update(status='resident_worker_timeout', worker_timeout_seconds=WORKER_SECONDS)
        base.dump(output/'summary.json', report)
    if result:
        report = json.loads((output/'summary.json').read_text())
        report.update(worker_returncode=result, speed_claim_valid=False,
            measured_wall_ratio_eager_over_compiled=None, measured_pair_median_saved_seconds=None,
            total_wall_ratio_including_compile_setup=None, observed_break_even_completed_calls=None)
        if report.get('status') not in ('resident_worker_timeout', 'resident_trial_failed'):
            report.update(status='resident_worker_failed', last_recorded_status=report.get('status'))
        base.dump(output/'summary.json', report)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return result


if __name__ == '__main__':
    raise SystemExit(main())
