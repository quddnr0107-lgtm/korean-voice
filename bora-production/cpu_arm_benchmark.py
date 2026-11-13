"""Original FP32 Sohee on an actual ARM64 CPU, four bounded public calls.

This changes the runner architecture, not the voice model or official sampler.
Within one resident model, fixture zero uses 4/2 threads, fixture one 2/4.
Different architecture-specific kernels can alter FP32 reductions and speech.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import time

from basic_sohee_benchmark import FIXTURES, MODEL, REVISION, generation_args, observe_eos
from cpu_precision_benchmark import hardware
from cpu_thread_benchmark import rss_snapshot

SCHEMA = 'sohee-public-cpu-arm/v1'
THREAD_COUNTS = (4, 2)
ORDERS = (THREAD_COUNTS, tuple(reversed(THREAD_COUNTS)))
EXPECTED_PACKAGES = {'torch': '2.8.0+cpu', 'torchaudio': '2.8.0',
    'qwen-tts': '0.1.1', 'transformers': '4.57.3', 'soundfile': '0.14.0',
    'numpy': '2.5.4', 'scipy': '1.18.1'}
ROOT = Path(__file__).resolve().parents[1]


def require(ok):
    if not ok:
        raise ValueError('public_cpu_arm_benchmark_invalid')


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def validate_request():
    request = json.loads(Path(__file__).with_name('cpu-arm-request.json').read_text())
    require(request == dict(schema=SCHEMA, request_id='public-sohee-fp32-arm-20261113-01',
        model_revision=REVISION, fixture_count=2, thread_counts=[4, 2],
        interop_threads=1, runner='ubuntu-24.04-arm', sample_timeout_seconds=300,
        production=False, secrets=False, gpu=False))


def validate_machine(machine):
    require(isinstance(machine, str) and machine.lower() in ('aarch64', 'arm64'))
    return machine.lower()


def native_threads(requested, actual, interop, parallel_info, openmp_available):
    require(type(requested) is int and requested in THREAD_COUNTS
        and type(actual) is int and actual == requested
        and type(interop) is int and interop == 1
        and type(openmp_available) is bool and isinstance(parallel_info, str))
    observed = {}
    for name in ('omp_get_max_threads', 'mkl_get_max_threads'):
        match = re.search(re.escape(name) + r'\(\)\s*:\s*([0-9]+)', parallel_info)
        observed[name] = int(match[1]) if match else None
    # MKL is not assumed to exist on ARM64. ATen is always checked above.
    if openmp_available:
        require(observed['omp_get_max_threads'] == requested)
    if observed['mkl_get_max_threads'] is not None:
        require(observed['mkl_get_max_threads'] == requested)
    return dict(intraop_threads=actual, interop_threads=interop,
        openmp_available=openmp_available, **observed)


def loaded_math_libraries():
    libraries = set()
    for line in Path('/proc/self/maps').read_text().splitlines():
        path = line.split()[-1]
        if path.startswith('/') and re.search(r'lib(?:gomp|omp|iomp|openblas|blas|mkl|dnnl)', Path(path).name):
            libraries.add(path)
    return sorted(libraries)


def summarize(rows):
    expected = [(i, n) for i, order in enumerate(ORDERS) for n in order]
    require(isinstance(rows, list) and len(rows) <= len(expected)
        and [(r.get('fixture'), r.get('threads_requested')) for r in rows] == expected[:len(rows)])
    for row in rows:
        require(type(row['fixture']) is int and row['seed'] == 5192 + row['fixture']
            and row['generation_args'] == generation_args('production_style', FIXTURES[row['fixture']])
            and row['dtype'] == 'float32' and row['device'] == 'cpu'
            and type(row['natural_eos']) is bool and type(row['signal_valid']) is bool)
        native_threads(row['threads_requested'], row['native_threads']['intraop_threads'],
            row['native_threads']['interop_threads'], row['parallel_info'],
            row['native_threads']['openmp_available'])
        for key in ('tts_seconds', 'audio_seconds'):
            require(type(row[key]) in (int, float) and math.isfinite(row[key]) and row[key] > 0)
        for key in ('wav_sha256', 'pcm_f32le_sha256', 'talker_sequence_sha256'):
            require(isinstance(row[key], str) and re.fullmatch(r'[a-f0-9]{64}', row[key]))
    valid = len(rows) == len(expected) and all(r['natural_eos'] and r['signal_valid'] for r in rows)
    totals = {}
    for threads in THREAD_COUNTS:
        part = [r for r in rows if r['threads_requested'] == threads]
        elapsed = sum(r['tts_seconds'] for r in part)
        audio = sum(r['audio_seconds'] for r in part)
        totals[str(threads)] = dict(tts_seconds=elapsed, audio_seconds=audio,
            synthesis_seconds_per_audio_second=elapsed / audio if valid else None)
    equality = {}
    for fixture in range(len(FIXTURES)):
        pair = [r for r in rows if r['fixture'] == fixture]
        equality[str(fixture)] = {key: len({r[key] for r in pair}) == 1 if len(pair) == 2 else None
            for key in ('pcm_f32le_sha256', 'talker_sequence_sha256')}
    return dict(schema=SCHEMA, status='arm_timing_trial_no_quality_approval' if valid else 'incomplete_output_no_speed_claim',
        model=MODEL, model_revision=REVISION, device='cpu', dtype='float32', speaker='Sohee',
        runner='ubuntu-24.04-arm', thread_counts=list(THREAD_COUNTS), interop_threads=1,
        fixtures=list(FIXTURES), fixture_thread_orders=[list(order) for order in ORDERS],
        rows=rows, totals=totals, complete_natural_eos_and_signal=valid,
        observed_arm_rtf_ratio_4_over_2=(totals['4']['synthesis_seconds_per_audio_second'] /
            totals['2']['synthesis_seconds_per_audio_second']) if valid else None,
        identical_output_by_fixture=equality,
        talker_sequence_hash_scope='Returned talker.generate first row only, not full-codebook parity.',
        model_load_count=1, model_load_included_in_tts=False, asr_performed=False,
        dsp_performed=False, retries_performed=False, human_listening_verified=False,
        production_eligible=False, full_course_forecast_validated=False,
        caveat='One four-call ARM64 trial on two short public texts. AB/BA order partly balances warmup only. Different ARM/x86 kernels and separate runners prevent a controlled architecture-only comparison with earlier x86 jobs. Torchaudio ARM wheel is 2.8.0, while x86 uses 2.8.0+cpu. No pronunciation, listening or full-course equivalence is inferred.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(); validate_request()
    from download_models import require_keyless
    from fp32_renderer import verify_assets, MODEL_REQUIRED, MODEL_REVISION
    require_keyless()
    machine = validate_machine(platform.machine())
    model_path, output = Path(args.model).resolve(), Path(args.output).resolve()
    require(not output.exists() and not output.is_relative_to(ROOT)
        and not output.is_relative_to(model_path))
    assets = verify_assets(model_path, MODEL_REQUIRED, MODEL_REVISION)
    versions = {name: importlib.metadata.version(name) for name in EXPECTED_PACKAGES}
    require(versions == EXPECTED_PACKAGES)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4')
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    require(torch.version.cuda is None and not torch.cuda.is_available())
    torch.set_num_threads(4); torch.set_num_interop_threads(1)
    output.mkdir(parents=True)
    metadata = dict(machine=machine, cpu=hardware(), platform=platform.platform(),
        installed_packages=versions, model_sha256=assets,
        script_sha256=file_hash(Path(__file__)),
        source_paths={name: file_hash(Path(__file__).with_name(name))
            for name in ('basic_sohee_benchmark.py', 'fp32_renderer.py', 'pilot_render.py')},
        wheel_sources=['https://download.pytorch.org/whl/cpu/torch/',
            'https://download.pytorch.org/whl/cpu/torchaudio/'],
        pinned_generation_config=json.loads((model_path / 'generation_config.json').read_text()))
    proc = subprocess.run(['lscpu', '--json'], capture_output=True, text=True, check=True,
        env=dict(os.environ, LC_ALL='C'), timeout=15)
    metadata['lscpu'] = json.loads(proc.stdout)
    report = summarize([]); report.update(metadata, status='loading_original_fp32_arm_model')
    dump(output / 'summary.json', report)
    rows = []
    def deadline(*unused):
        raise TimeoutError('public_cpu_arm_sample_timeout')
    old_handler = signal.signal(signal.SIGALRM, deadline)
    try:
        started = time.perf_counter()
        model = Qwen3TTSModel.from_pretrained(str(model_path), device_map='cpu', dtype=torch.float32,
            attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True)
        model.model.eval()
        metadata['model_load_seconds'] = time.perf_counter() - started
        floating = sorted({str(p.dtype).removeprefix('torch.') for p in model.model.parameters()
            if p.is_floating_point()})
        require(floating == ['float32'] and all(p.device.type == 'cpu' for p in model.model.parameters()))
        metadata.update(parameter_dtypes=floating, memory_after_load=rss_snapshot(),
            mkldnn_enabled=torch.backends.mkldnn.enabled, torch_build_config=torch.__config__.show())
        for fixture, text in enumerate(FIXTURES):
            for threads in ORDERS[fixture]:
                torch.set_num_threads(threads)
                parallel_info = torch.__config__.parallel_info()
                observed_threads = native_threads(threads, torch.get_num_threads(), torch.get_num_interop_threads(),
                    parallel_info, torch.backends.openmp.is_available())
                talker = model.model.talker; original = talker.generate; observed = []
                eos_id = int(model.model.config.talker_config.codec_eos_token_id)
                def observe(*a, **kw):
                    result = original(*a, **kw)
                    tokens = result.sequences[0].tolist()
                    seen = observe_eos(tokens, eos_id)
                    seen['talker_sequence_sha256'] = hashlib.sha256(json.dumps(tokens,
                        separators=(',', ':')).encode('ascii')).hexdigest()
                    observed.append(seen)
                    return result
                talker.generate = observe; torch.manual_seed(5192 + fixture); signal.alarm(300)
                started = time.perf_counter()
                try:
                    with torch.inference_mode():
                        waves, sr = model.generate_custom_voice(**generation_args('production_style', text))
                    elapsed = time.perf_counter() - started
                finally:
                    signal.alarm(0); talker.generate = original
                require(len(waves) == 1 and sr == 24000 and len(observed) == 1)
                wave = np.asarray(waves[0], dtype=np.float32)
                valid = bool(wave.ndim == 1 and wave.size > 0 and np.isfinite(wave).all()
                    and np.max(np.abs(wave)) < 1 and np.sqrt(np.mean(wave.astype('float64') ** 2)) > 1e-5)
                target = output / f'arm-fp32-threads-{threads}-fixture-{fixture}.wav'
                sf.write(target, wave, sr, subtype='FLOAT')
                row = dict(fixture=fixture, seed=5192 + fixture, threads_requested=threads,
                    native_threads=observed_threads, parallel_info=parallel_info,
                    dtype='float32', device='cpu', tts_seconds=elapsed, audio_seconds=len(wave) / sr,
                    signal_valid=valid, sample_rate=sr, pcm_samples=len(wave), wav_file=target.name,
                    wav_sha256=file_hash(target), pcm_f32le_sha256=hashlib.sha256(wave.astype('<f4').tobytes()).hexdigest(),
                    generation_args=generation_args('production_style', text), memory=rss_snapshot(), **observed[0])
                rows.append(row)
                report = summarize(rows); report.update(metadata)
                dump(output / 'summary.json', report)
                print(json.dumps(row, sort_keys=True), flush=True)
    except Exception as error:
        report = summarize(rows)
        report.update(metadata, status='arm_trial_failed', error_type=type(error).__name__)
        dump(output / 'summary.json', report)
        raise
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, old_handler)
    report.update(memory_after_synthesis=rss_snapshot(), loaded_math_libraries=loaded_math_libraries())
    dump(output / 'summary.json', report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    if not report['complete_natural_eos_and_signal']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
