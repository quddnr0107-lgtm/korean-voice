"""Bounded public CPU Inductor trial on only the original code predictor.

The official sampler, FP32 weights, voice and text are unchanged. Eager runs
first; the first compiled call includes compilation. No silent eager fallback
is accepted as evidence that a compiled graph actually executed.
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
import re
import signal
import subprocess
import sys
import time

from basic_sohee_benchmark import FIXTURES, MODEL, REVISION, generation_args, observe_eos
from cpu_precision_benchmark import hardware
from cpu_thread_benchmark import rss_snapshot, validate_native_threads

SCHEMA = 'sohee-public-cpu-compile/v1'
MODES = ('eager', 'compiled_predictor')
CALL_SECONDS = 360
WORKER_SECONDS = 1240
MAX_GRAPHS = 32
MODELING_SHA256 = '25c42656bcf810f06ef6bc1839bd7083f3c8cfedac3a147c4060b4262b1c96a0'
SOURCES = [
    'https://docs.pytorch.org/docs/2.8/generated/torch.compile.html',
    'https://github.com/pytorch/pytorch/blob/v2.8.0/torch/_inductor/config.py',
    'https://github.com/QwenLM/Qwen3-TTS/blob/022e286b98fbec7e1e916cb940cdf532cd9f488e/qwen_tts/core/models/modeling_qwen3_tts.py']


def require(ok):
    if not ok:
        raise ValueError('public_cpu_compile_benchmark_invalid')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def validate_request():
    request = json.loads(Path(__file__).with_name('cpu-compile-request.json').read_text())
    require(request == dict(schema=SCHEMA,
        request_id='public-sohee-fp32-predictor-compile-20261113-01',
        model_revision=REVISION, fixture_count=2, threads=4, interop_threads=1,
        modes=list(MODES), call_timeout_seconds=CALL_SECONDS,
        compile_threads=1, maximum_compiled_graphs=MAX_GRAPHS,
        production=False, secrets=False, gpu=False))


def tracked_backend(inductor, counts):
    """Count successful backend compilation AND calls to its returned code."""
    def backend(graph, example_inputs):
        counts['backend_compile_attempts'] += 1
        require(counts['backend_compile_attempts'] <= MAX_GRAPHS)
        compiled = inductor(graph, example_inputs)
        require(callable(compiled))
        counts['backend_graphs_compiled'] += 1
        @functools.wraps(compiled)
        def execute(*args, **kwargs):
            counts['compiled_graph_invocations'] += 1
            return compiled(*args, **kwargs)
        return execute
    return backend


@contextlib.contextmanager
def compiled_forward(predictor, compiler, backend, counts):
    original = predictor.forward
    replacement = compiler(original, backend=backend, fullgraph=False, dynamic=True)
    @functools.wraps(original)
    def forward(*args, **kwargs):
        counts['predictor_forward_calls'] += 1
        return replacement(*args, **kwargs)
    predictor.forward = forward
    try:
        yield
    finally:
        predictor.forward = original


def summarize(rows, counts):
    expected = [(mode, fixture) for mode in MODES for fixture in range(2)]
    require(isinstance(rows, list) and len(rows) <= len(expected)
        and [(r['mode'], r['fixture']) for r in rows] == expected[:len(rows)])
    for row in rows:
        require(row['seed'] == 5192 + row['fixture']
            and row['generation_args'] == generation_args('production_style', FIXTURES[row['fixture']])
            and type(row['natural_eos']) is bool and type(row['signal_valid']) is bool
            and re.fullmatch(r'[a-f0-9]{64}', row['wav_sha256'])
            and re.fullmatch(r'[a-f0-9]{64}', row['pcm_sha256'])
            and re.fullmatch(r'[a-f0-9]{64}', row['talker_sequence_sha256']))
        for key in ('tts_seconds', 'audio_seconds'):
            require(type(row[key]) in (int, float) and math.isfinite(row[key]) and row[key] > 0)
    require(all(type(v) is int and v >= 0 for v in counts.values()))
    complete = len(rows) == 4 and all(r['natural_eos'] and r['signal_valid'] for r in rows)
    executed = (counts.get('backend_graphs_compiled', 0) > 0
        and counts.get('compiled_graph_invocations', 0) > 0
        and all(r.get('compiled_graph_invocations_during_call', 0) > 0
                for r in rows if r['mode'] == 'compiled_predictor'))
    valid = complete and executed
    totals = {}
    for mode in MODES:
        selected = [r for r in rows if r['mode'] == mode]
        elapsed, audio = sum(r['tts_seconds'] for r in selected), sum(r['audio_seconds'] for r in selected)
        totals[mode] = dict(tts_seconds=round(elapsed, 6), audio_seconds=round(audio, 6),
            synthesis_seconds_per_audio_second=round(elapsed / audio, 6) if valid else None)
    compiled_rows = [r for r in rows if r['mode'] == 'compiled_predictor']
    return dict(schema=SCHEMA,
        status='compiled_graph_timing_trial_no_quality_approval' if valid else 'incomplete_or_no_compiled_execution_no_speed_claim',
        model=MODEL, model_revision=REVISION, device='cpu', dtype='float32', threads=4,
        interop_threads=1, speaker='Sohee', fixtures=list(FIXTURES), rows=rows, totals=totals,
        complete_natural_eos_and_signal=complete, compiled_execution_observed=executed,
        compile_counters=dict(counts),
        observed_pair_wall_ratio_eager_over_compiled=round(totals['eager']['tts_seconds'] /
            totals['compiled_predictor']['tts_seconds'], 6) if valid else None,
        observed_rtf_ratio_eager_over_compiled=round(totals['eager']['synthesis_seconds_per_audio_second'] /
            totals['compiled_predictor']['synthesis_seconds_per_audio_second'], 6) if valid else None,
        first_compiled_call_includes_compilation=True,
        first_compiled_call_seconds=compiled_rows[0]['tts_seconds'] if compiled_rows else None,
        second_compiled_call_seconds=compiled_rows[1]['tts_seconds'] if len(compiled_rows) == 2 else None,
        second_compiled_call_new_graphs=compiled_rows[1]['new_graphs_compiled_during_call'] if len(compiled_rows) == 2 else None,
        second_call_proven_steady_state=bool(len(compiled_rows) == 2
            and compiled_rows[1]['new_graphs_compiled_during_call'] == 0),
        compile_scope='model.model.talker.code_predictor.forward only; sampler and generate stay eager',
        backend='inductor', fullgraph=False, dynamic=True, compile_threads=1,
        maximum_compiled_graphs=MAX_GRAPHS,
        entire_predictor_compiled_coverage_claimed=False,
        model_load_included_in_tts=False, asr_performed=False, dsp_performed=False,
        production_eligible=False, human_listening_verified=False,
        full_course_forecast_validated=False, sources=SOURCES,
        caveat='One eager-then-compiled trial, two short public fixtures, one resident model. Graph breaks can leave eager regions; backend counters prove some compiled code ran, not full compiled coverage. First compiled TTS includes compilation; the second is steady only if it compiled no new graph. FP32 fusion may change floating-point results and sampled audio. No pronunciation or full-course equivalence claim.')


def dynamo_counts(counters):
    return {category: {str(key)[:160]: int(value) for key, value in list(counters[category].items())[:80]
                       if type(value) is int}
            for category in ('frames', 'stats', 'inductor', 'graph_break', 'unimplemented')}


def failed_worker_report(report, returncode):
    return dict(report, status='compile_worker_failed', worker_returncode=returncode,
        last_recorded_status=report.get('status'),
        observed_pair_wall_ratio_eager_over_compiled=None,
        observed_rtf_ratio_eager_over_compiled=None)


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
    torch.set_num_threads(4); torch.set_num_interop_threads(1)
    torch._dynamo.config.suppress_errors = False
    torch._dynamo.config.recompile_limit = MAX_GRAPHS
    inductor_config.compile_threads = 1
    native = validate_native_threads(4, torch.get_num_threads(), torch.get_num_interop_threads(),
        torch.__config__.parallel_info())
    out = Path(output)
    counts = dict(backend_compile_attempts=0, backend_graphs_compiled=0,
        compiled_graph_invocations=0, predictor_forward_calls=0)
    from fp32_renderer import verify_assets, MODEL_REQUIRED
    rows = []; metadata = dict(cpu=hardware(), native_threads=native,
        torch_version=torch.__version__, compiler_version=subprocess.run(['g++', '--version'],
            capture_output=True, text=True, check=True).stdout.splitlines()[0],
        model_assets_sha256=verify_assets(model_path, MODEL_REQUIRED, REVISION))
    dump(out/'summary.json', dict(summarize(rows, counts), **metadata, status='loading_original_fp32_model'))
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(str(Path(model_path).resolve()), device_map='cpu',
        dtype=torch.float32, attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True)
    model.model.eval()
    metadata['model_load_seconds'] = time.perf_counter()-started
    require(all(p.dtype == torch.float32 and p.device.type == 'cpu'
        for p in model.model.parameters() if p.is_floating_point()))
    predictor = model.model.talker.code_predictor
    require(type(predictor).__name__ == 'Qwen3TTSTalkerCodePredictorModelForConditionalGeneration')
    metadata.update(memory_after_load=rss_snapshot(),
        original_predictor_source_sha256=digest(inspect.getsourcefile(type(predictor))))
    require(metadata['original_predictor_source_sha256'] == MODELING_SHA256)
    def deadline(*unused):
        raise TimeoutError('public_cpu_compile_call_timeout')
    signal.signal(signal.SIGALRM, deadline)
    mode = 'eager'; fixture = 0
    try:
        for mode in MODES:
            if mode == 'compiled_predictor':
                torch._dynamo.reset(); counters.clear()
            started = time.perf_counter()
            context = compiled_forward(predictor, torch.compile,
                tracked_backend(lookup_backend('inductor'), counts), counts) if mode == 'compiled_predictor' else contextlib.nullcontext()
            with context:
                metadata['compiled_wrapper_setup_seconds' if mode == 'compiled_predictor' else 'eager_wrapper_setup_seconds'] = time.perf_counter()-started
                for fixture, text in enumerate(FIXTURES):
                    before = dict(counts)
                    talker = model.model.talker; original = talker.generate; observed = []
                    eos = int(model.model.config.talker_config.codec_eos_token_id)
                    def observe(*args, **kwargs):
                        result = original(*args, **kwargs)
                        tokens = result.sequences[0].tolist()
                        observed.append(dict(observe_eos(tokens, eos),
                            talker_sequence_sha256=hashlib.sha256(json.dumps(tokens, separators=(',', ':')).encode()).hexdigest()))
                        return result
                    talker.generate = observe
                    torch.manual_seed(5192+fixture); signal.alarm(CALL_SECONDS)
                    started = time.perf_counter()
                    try:
                        with torch.inference_mode():
                            waves, rate = model.generate_custom_voice(**generation_args('production_style', text))
                        elapsed = time.perf_counter()-started
                    finally:
                        signal.alarm(0); talker.generate = original
                    require(len(waves) == 1 and rate == 24000 and len(observed) == 1)
                    wave = np.asarray(waves[0], dtype='float32')
                    valid = bool(wave.ndim == 1 and wave.size > 0 and np.isfinite(wave).all()
                        and np.max(np.abs(wave)) < 1
                        and np.sqrt(np.mean(wave.astype('float64')**2)) > 1e-5)
                    target = out/f'{mode}-{fixture}.wav'
                    sf.write(target, wave, rate, subtype='FLOAT')
                    row = dict(mode=mode, fixture=fixture, seed=5192+fixture,
                        tts_seconds=elapsed, audio_seconds=len(wave)/rate, sample_rate=rate,
                        pcm_samples=len(wave), signal_valid=valid, **observed[0],
                        wav_file=target.name, wav_sha256=digest(target),
                        pcm_sha256=hashlib.sha256(wave.astype('<f4', copy=False).tobytes()).hexdigest(),
                        generation_args=generation_args('production_style', text),
                        compiled_graph_invocations_during_call=counts['compiled_graph_invocations']-before['compiled_graph_invocations'],
                        new_graphs_compiled_during_call=counts['backend_graphs_compiled']-before['backend_graphs_compiled'],
                        predictor_forward_calls_during_call=counts['predictor_forward_calls']-before['predictor_forward_calls'],
                        dynamo_counters=dynamo_counts(counters), memory=rss_snapshot())
                    rows.append(row)
                    dump(out/'summary.json', dict(summarize(rows, counts), **metadata))
                    print(json.dumps(row, sort_keys=True), flush=True)
    except Exception as error:
        report = dict(summarize(rows, counts), **metadata, status='compile_trial_failed',
            failed_mode=mode, failed_fixture=fixture, error_type=type(error).__name__,
            observed_pair_wall_ratio_eager_over_compiled=None, observed_rtf_ratio_eager_over_compiled=None,
            dynamo_counters=dynamo_counts(counters), memory_at_failure=rss_snapshot())
        dump(out/'summary.json', report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        return 1
    report = dict(summarize(rows, counts), **metadata, memory_after_synthesis=rss_snapshot())
    dump(out/'summary.json', report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if report['complete_natural_eos_and_signal'] and report['compiled_execution_observed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args(); validate_request()
    from download_models import require_keyless, verify_assets, QWEN_REQUIRED
    require_keyless()
    model, output = Path(args.model).resolve(), Path(args.output).resolve()
    if args.worker:
        return worker(model, output)
    verify_assets(model, QWEN_REQUIRED)
    output.mkdir(parents=True, exist_ok=False)
    dump(output/'summary.json', dict(summarize([], dict(backend_compile_attempts=0,
        backend_graphs_compiled=0, compiled_graph_invocations=0, predictor_forward_calls=0)),
        status='worker_starting', cpu=hardware()))
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
        report = json.loads((output/'summary.json').read_text())
        report.update(status='compile_worker_timeout', worker_timeout_seconds=WORKER_SECONDS,
            observed_pair_wall_ratio_eager_over_compiled=None, observed_rtf_ratio_eager_over_compiled=None)
        dump(output/'summary.json', report)
        return 1
    if result:
        report = json.loads((output/'summary.json').read_text())
        if report.get('status') != 'compile_trial_failed':
            dump(output/'summary.json', failed_worker_report(report, result))
        # Also stop any compiler subprocesses left after a failed worker.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return result


if __name__ == '__main__':
    raise SystemExit(main())
