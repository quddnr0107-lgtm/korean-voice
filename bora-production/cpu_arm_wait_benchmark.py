"""Original ARM64 FP32 Sohee: fresh-process default versus passive wait hints.

Two original generic public explanation fixtures per resident model, four calls
total. Native imports occur only after the subprocess environment is fixed.
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
import signal
import subprocess
import sys
import time

from basic_sohee_benchmark import FIXTURES, MODEL, REVISION, generation_args, observe_eos
from cpu_arm_benchmark import EXPECTED_PACKAGES, native_threads, validate_machine, loaded_math_libraries
from cpu_precision_benchmark import hardware
from cpu_thread_benchmark import rss_snapshot

SCHEMA = 'sohee-public-cpu-arm-wait/v1'
VARIANTS = ('default', 'passive')
WAIT_KEYS = ('OMP_WAIT_POLICY', 'GOMP_SPINCOUNT', 'KMP_BLOCKTIME')
PASSIVE = dict(OMP_WAIT_POLICY='PASSIVE', GOMP_SPINCOUNT='0', KMP_BLOCKTIME='0')
WORKER_SECONDS = 600
ROOT = Path(__file__).resolve().parents[1]


def require(ok):
    if not ok:
        raise ValueError('public_cpu_arm_wait_benchmark_invalid')


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_request():
    obj = json.loads(Path(__file__).with_name('cpu-arm-wait-request.json').read_text())
    require(obj == dict(schema=SCHEMA, request_id='public-sohee-fp32-arm-wait-20261113-01',
        model_revision=REVISION, fixture_count=2, variants=list(VARIANTS), threads=4,
        interop_threads=1, worker_timeout_seconds=WORKER_SECONDS,
        sample_timeout_seconds=240, runner='ubuntu-24.04-arm',
        production=False, secrets=False, gpu=False))


def policy_environment(variant, inherited):
    require(variant in VARIANTS and isinstance(inherited, dict))
    env = {key: value for key, value in inherited.items() if key not in WAIT_KEYS}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4')
    if variant == 'passive':
        env.update(PASSIVE)
    return env


def wait_values(environment):
    return {key: environment[key] for key in WAIT_KEYS if key in environment}


def totals(rows, variant):
    require(variant in VARIANTS and isinstance(rows, list) and len(rows) == len(FIXTURES)
        and [row.get('fixture') for row in rows] == [0, 1])
    for i, row in enumerate(rows):
        require(row.get('variant') == variant and type(row['fixture']) is int
            and row['seed'] == 5192 + i and row['device'] == 'cpu'
            and row['dtype'] == 'float32' and row['threads'] == 4 and row['interop_threads'] == 1
            and row['generation_args'] == generation_args('production_style', FIXTURES[i])
            and type(row['natural_eos']) is bool and type(row['signal_valid']) is bool)
        for name in ('tts_seconds', 'process_cpu_seconds', 'audio_seconds'):
            value = row[name]
            require(type(value) in (int, float) and math.isfinite(value) and value > 0)
        for name in ('wav_sha256', 'raw_pcm_f32le_sha256', 'talker_sequence_sha256'):
            value = row[name]
            require(isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value))
    valid = all(row['natural_eos'] and row['signal_valid'] for row in rows)
    elapsed = sum(row['tts_seconds'] for row in rows)
    audio = sum(row['audio_seconds'] for row in rows)
    return dict(tts_seconds=elapsed, audio_seconds=audio,
        process_cpu_seconds=sum(row['process_cpu_seconds'] for row in rows),
        complete_natural_eos_and_signal=valid,
        synthesis_seconds_per_audio_second=elapsed/audio if valid else None)


def compare_results(variants, cpu):
    require(set(variants) == set(VARIANTS))
    aggregate = {}
    for variant, result in variants.items():
        require(result.get('variant') == variant and result.get('cpu') == cpu
            and result.get('model') == MODEL and result.get('model_revision') == REVISION
            and result.get('machine') in ('aarch64', 'arm64')
            and result.get('device') == 'cpu' and result.get('parameter_dtypes') == ['float32']
            and result.get('threads') == 4 and result.get('interop_threads') == 1
            and result.get('fixtures') == list(FIXTURES) and result.get('fixture_order') == [0, 1]
            and result.get('installed_packages') == EXPECTED_PACKAGES
            and result.get('wait_environment') == (PASSIVE if variant == 'passive' else {}))
        observed = result['native_threads']
        native_threads(4, observed['intraop_threads'], observed['interop_threads'],
            result['parallel_info'], observed['openmp_available'])
        aggregate[variant] = totals(result['rows'], variant)
    valid = all(part['complete_natural_eos_and_signal'] for part in aggregate.values())
    baseline, passive = aggregate['default'], aggregate['passive']
    return dict(schema=SCHEMA, status='arm_wait_timing_trial_no_quality_approval' if valid else 'incomplete_output_no_speed_claim',
        model=MODEL, model_revision=REVISION, device='cpu', dtype='float32', cpu=cpu,
        runner='ubuntu-24.04-arm', threads=4, interop_threads=1, speaker='Sohee',
        fixtures=list(FIXTURES), fixture_order=[0, 1], variant_order=list(VARIANTS),
        variants=variants, totals=aggregate, complete_natural_eos_and_signal=valid,
        observed_pair_wall_ratio_default_over_passive=baseline['tts_seconds']/passive['tts_seconds'] if valid else None,
        observed_rtf_ratio_default_over_passive=(baseline['synthesis_seconds_per_audio_second'] /
            passive['synthesis_seconds_per_audio_second']) if valid else None,
        identical_pcm_hashes_by_fixture={str(i): variants['default']['rows'][i]['raw_pcm_f32le_sha256'] ==
            variants['passive']['rows'][i]['raw_pcm_f32le_sha256'] for i in range(len(FIXTURES))},
        identical_talker_sequence_hashes_by_fixture={str(i): variants['default']['rows'][i]['talker_sequence_sha256'] ==
            variants['passive']['rows'][i]['talker_sequence_sha256'] for i in range(len(FIXTURES))},
        talker_sequence_hash_scope='Returned talker.generate first row only, not full-codebook parity.',
        model_load_count_per_variant=1, model_load_included_in_tts=False,
        wait_hints_guaranteed_applied=False, asr_performed=False, dsp_performed=False,
        retries_performed=False, production_eligible=False, human_listening_verified=False,
        quality_equivalence_verified=False, full_course_forecast_validated=False,
        caveat='One default-then-passive trial on the same ARM64 runner using two generic explanation fixtures and sequential fresh processes. Model file cache, order and host noise remain. The three wait hints change together and may be ignored by the runtime. Do not add this improvement to an earlier x86 or ARM result, or infer full-course or listening equivalence.')


def worker(model_path, output, variant):
    from download_models import require_keyless
    require_keyless()
    require(variant in VARIANTS and wait_values(os.environ) == (PASSIVE if variant == 'passive' else {}))
    require('torch' not in sys.modules and 'numpy' not in sys.modules)
    machine = validate_machine(platform.machine())
    versions = {name: importlib.metadata.version(name) for name in EXPECTED_PACKAGES}
    require(versions == EXPECTED_PACKAGES)
    from public_asset_cache import load_manifest
    from fp32_renderer import verify_assets, MODEL_REQUIRED, MODEL_REVISION
    hashes = {name: entry['sha256'] for name, entry in load_manifest()['models']['qwen']['files'].items()}
    actual_hashes = verify_assets(model_path, MODEL_REQUIRED, MODEL_REVISION, hashes)
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    require(torch.version.cuda is None and not torch.cuda.is_available())
    torch.set_num_threads(4); torch.set_num_interop_threads(1)
    parallel = torch.__config__.parallel_info()
    native = native_threads(4, torch.get_num_threads(), torch.get_num_interop_threads(),
        parallel, torch.backends.openmp.is_available())
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(str(Path(model_path).resolve()), device_map='cpu',
        dtype=torch.float32, attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True)
    model.model.eval()
    load_seconds = time.perf_counter() - started
    floating = sorted({str(p.dtype).removeprefix('torch.') for p in model.model.parameters() if p.is_floating_point()})
    require(floating == ['float32'] and all(p.device.type == 'cpu' for p in model.model.parameters()))
    after_load = rss_snapshot()
    def deadline(*unused):
        raise TimeoutError('public_cpu_arm_wait_sample_timeout')
    old_handler = signal.signal(signal.SIGALRM, deadline)
    rows = []
    try:
        for i, text in enumerate(FIXTURES):
            talker = model.model.talker; original = talker.generate; observations = []
            eos_id = int(model.model.config.talker_config.codec_eos_token_id)
            def observe(*a, **kw):
                result = original(*a, **kw)
                tokens = result.sequences[0].tolist()
                observations.append(dict(observe_eos(tokens, eos_id),
                    talker_sequence_sha256=hashlib.sha256(json.dumps(tokens, separators=(',', ':')).encode()).hexdigest()))
                return result
            talker.generate = observe; torch.manual_seed(5192 + i); signal.alarm(240)
            started, cpu_started = time.perf_counter(), time.process_time()
            try:
                with torch.inference_mode():
                    waves, sr = model.generate_custom_voice(**generation_args('production_style', text))
                elapsed, cpu_elapsed = time.perf_counter() - started, time.process_time() - cpu_started
            finally:
                signal.alarm(0); talker.generate = original
            require(len(waves) == 1 and sr == 24000 and len(observations) == 1)
            wave = np.asarray(waves[0], dtype=np.float32)
            valid = bool(wave.ndim == 1 and wave.size > 0 and np.isfinite(wave).all()
                and np.max(np.abs(wave)) < 1.0 and np.sqrt(np.mean(wave.astype('float64')**2)) > 1e-5)
            target = output / f'{variant}-{i}.wav'; sf.write(target, wave, sr, subtype='FLOAT')
            row = dict(variant=variant, fixture=i, seed=5192+i, dtype='float32', device='cpu',
                threads=4, interop_threads=1, tts_seconds=elapsed, process_cpu_seconds=cpu_elapsed,
                audio_seconds=len(wave)/sr, sample_rate=sr, pcm_samples=len(wave), signal_valid=valid,
                wav_file=target.name, wav_sha256=file_hash(target),
                raw_pcm_f32le_sha256=hashlib.sha256(wave.astype('<f4').tobytes()).hexdigest(),
                generation_args=generation_args('production_style', text), memory=rss_snapshot(), **observations[0])
            rows.append(row); dump(output/'partial-rows.json', rows)
            print(json.dumps(row, sort_keys=True), flush=True)
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, old_handler)
    lscpu = subprocess.run(['lscpu', '--json'], capture_output=True, text=True,
        env=dict(os.environ, LC_ALL='C'), check=True, timeout=15)
    report = dict(variant=variant, model=MODEL, model_revision=REVISION, machine=machine,
        installed_packages=versions, device='cpu', threads=4, interop_threads=1,
        parameter_dtypes=floating, wait_environment=wait_values(os.environ),
        fixture_order=[0, 1], cpu=hardware(), fixtures=list(FIXTURES), native_threads=native,
        parallel_info=parallel, model_assets_sha256=actual_hashes, model_load_seconds=load_seconds,
        memory_after_load=after_load, memory_after_synthesis=rss_snapshot(),
        loaded_math_libraries=loaded_math_libraries(), lscpu=json.loads(lscpu.stdout),
        mkldnn_enabled=torch.backends.mkldnn.enabled,
        rows=rows, totals=totals(rows, variant))
    dump(output/'result.json', report)


def run(model_path, output):
    from download_models import require_keyless
    require_keyless()
    validate_machine(platform.machine())
    output, model_path = Path(output).resolve(), Path(model_path).resolve()
    require(not output.exists() and not output.is_relative_to(ROOT) and not output.is_relative_to(model_path))
    output.mkdir(parents=True)
    cpu, variants = hardware(), {}
    for variant in VARIANTS:
        started = time.perf_counter()
        try:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), 'worker',
                '--model', str(model_path), '--output', str(output/variant), '--variant', variant],
                env=policy_environment(variant, dict(os.environ)), check=False, timeout=WORKER_SECONDS)
        except subprocess.TimeoutExpired:
            report = dict(schema=SCHEMA, status='arm_wait_worker_timeout', failed_variant=variant,
                cpu=cpu, completed_variants=variants, observed_rtf_ratio_default_over_passive=None,
                production_eligible=False)
            break
        if process.returncode:
            report = dict(schema=SCHEMA, status='arm_wait_worker_failed', failed_variant=variant,
                returncode=process.returncode, cpu=cpu, completed_variants=variants,
                observed_rtf_ratio_default_over_passive=None, production_eligible=False)
            break
        variants[variant] = json.loads((output/variant/'result.json').read_text())
        variants[variant]['worker_mode_wall_seconds'] = time.perf_counter()-started
    else:
        report = compare_results(variants, cpu)
    dump(output/'summary.json', report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if report['status'] == 'arm_wait_timing_trial_no_quality_approval' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('run', 'worker'))
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--variant', choices=VARIANTS)
    args = parser.parse_args(); validate_request()
    if args.mode == 'worker':
        require(args.variant in VARIANTS)
        worker(args.model, args.output, args.variant)
        return 0
    require(args.variant is None)
    return run(args.model, args.output)


if __name__ == '__main__':
    raise SystemExit(main())
