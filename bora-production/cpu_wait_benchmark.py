"""Original FP32 Sohee waiting confirmation: passive then default.

Each variant gets a fresh sequential subprocess and one resident model. Waiting
environment is fixed before Python starts, without changing forward, sampler,
weights, arithmetic precision, text, instruction, or requested thread count.
OpenMP hints may be ignored by a runtime; requested values are not proof that
the native library applied them. This second suite uses one explanation with
legal names and one formal-reading test sentence, never substantive law text.
Four public TTS calls; no production writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from basic_sohee_benchmark import MODEL, REVISION, observe_eos
from cpu_precision_benchmark import hardware
from cpu_thread_benchmark import rss_snapshot, validate_native_threads
from fp32_renderer import SAMPLING, SEEDS, STYLES

SCHEMA = 'sohee-public-cpu-wait/v2'
SUITE = 'legal-names-and-formal-reading-reversed-wait-order/v2'
VARIANTS = ('passive', 'default')
FIXTURE_MODES = ('explain', 'reading')
FIXTURES = (
    '다음 표현을 또박또박 읽겠습니다. 통합방위법, 예비군법, 병역법.',
    '제삼조 제일항. 이 문장은 음성 합성 속도를 비교하기 위한 시험 문장입니다.',
)
WAIT_KEYS = ('OMP_WAIT_POLICY', 'GOMP_SPINCOUNT', 'KMP_BLOCKTIME')
PASSIVE = dict(OMP_WAIT_POLICY='PASSIVE', GOMP_SPINCOUNT='0', KMP_BLOCKTIME='0')
THREADS = 4
WORKER_SECONDS = 600
ROOT = Path(__file__).resolve().parents[1]
DOCUMENTATION = [
    'https://www.openmp.org/spec-html/5.1/openmpse64.html',
    'https://gcc.gnu.org/onlinedocs/libgomp/GOMP_005fSPINCOUNT.html',
    'https://openmp.llvm.org/design/Runtimes.html',
]


def require(ok):
    if not ok:
        raise ValueError('public_cpu_wait_benchmark_invalid')


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_request():
    obj = json.loads(Path(__file__).with_name('cpu-wait-request.json').read_text())
    require(obj == dict(schema=SCHEMA,
        request_id='public-sohee-fp32-wait-20261113-02', suite=SUITE, model_revision=REVISION,
        fixture_count=len(FIXTURES), fixture_modes=list(FIXTURE_MODES),
        variants=list(VARIANTS), threads=4,
        interop_threads=1, worker_timeout_seconds=WORKER_SECONDS,
        production=False, secrets=False, gpu=False))
    require(SEEDS[0] == 5192 and len(FIXTURES) == len(FIXTURE_MODES) == 2)


def generation_args(fixture):
    require(type(fixture) is int and 0 <= fixture < len(FIXTURES))
    return dict(SAMPLING, max_new_tokens=1100, text=FIXTURES[fixture],
        language='Korean', speaker='Sohee', instruct=STYLES[FIXTURE_MODES[fixture]])


def policy_environment(variant, inherited):
    require(variant in VARIANTS and isinstance(inherited, dict))
    result = {key: value for key, value in inherited.items() if key not in WAIT_KEYS}
    result.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4')
    if variant == 'passive':
        result.update(PASSIVE)
    return result


def wait_values(environment):
    return {key: environment[key] for key in WAIT_KEYS if key in environment}


def openmp_libraries(maps=None):
    if maps is None:
        maps = Path('/proc/self/maps').read_text()
    return sorted({line.split()[-1] for line in maps.splitlines()
        if line.split() and re.search(r'/lib(?:gomp|iomp|omp)[^/]*\.so(?:\.|\s|$)', line.split()[-1])})


def totals(rows, variant):
    require(variant in VARIANTS and isinstance(rows, list) and len(rows) == len(FIXTURES)
        and [r.get('fixture') for r in rows] == list(range(len(FIXTURES))))
    for i, row in enumerate(rows):
        require(row.get('variant') == variant and type(row['fixture']) is int
            and row.get('seed') == 5192 + i and row.get('device') == 'cpu'
            and row.get('dtype') == 'float32' and row.get('threads') == 4
            and row.get('interop_threads') == 1
            and row.get('voice_mode') == FIXTURE_MODES[i]
            and row.get('generation_args') == generation_args(i)
            and type(row.get('natural_eos')) is bool and type(row.get('signal_valid')) is bool)
        for field in ('tts_seconds', 'audio_seconds', 'process_cpu_seconds'):
            value = row.get(field)
            require(type(value) in (int, float) and math.isfinite(value) and value > 0)
        for field in ('wav_sha256', 'raw_pcm_f32le_sha256', 'talker_sequence_sha256'):
            require(isinstance(row.get(field), str) and re.fullmatch('[a-f0-9]{64}', row[field]))
    valid = all(row['natural_eos'] and row['signal_valid'] for row in rows)
    elapsed = sum(row['tts_seconds'] for row in rows)
    audio = sum(row['audio_seconds'] for row in rows)
    return dict(tts_seconds=round(elapsed, 6), audio_seconds=round(audio, 6),
        process_cpu_seconds=round(sum(row['process_cpu_seconds'] for row in rows), 6),
        complete_natural_eos_and_signal=valid,
        synthesis_seconds_per_audio_second=round(elapsed/audio, 6) if valid else None)


def compare_results(variants, cpu):
    require(set(variants) == set(VARIANTS))
    aggregate = {}
    for variant, result in variants.items():
        require(result.get('variant') == variant and result.get('cpu') == cpu
            and result.get('model') == MODEL and result.get('model_revision') == REVISION
            and result.get('device') == 'cpu' and result.get('parameter_dtypes') == ['float32']
            and result.get('threads') == 4 and result.get('interop_threads') == 1
            and result.get('fixtures') == list(FIXTURES)
            and result.get('suite') == SUITE and result.get('fixture_modes') == list(FIXTURE_MODES)
            and result.get('fixture_order') == [0, 1]
            and result.get('wait_environment') == (PASSIVE if variant == 'passive' else {}))
        validate_native_threads(4, result['native_threads']['intraop_threads'],
            result['native_threads']['interop_threads'], result['parallel_info'])
        aggregate[variant] = totals(result['rows'], variant)
    valid = all(part['complete_natural_eos_and_signal'] for part in aggregate.values())
    baseline, passive = aggregate['default'], aggregate['passive']
    return dict(schema=SCHEMA,
        status='timing_trial_no_quality_approval' if valid else 'incomplete_output_no_speed_claim',
        suite=SUITE, variant_order=list(VARIANTS), fixture_modes=list(FIXTURE_MODES),
        fixture_order=[0, 1], model=MODEL, model_revision=REVISION, device='cpu', dtype='float32', cpu=cpu,
        threads=4, interop_threads=1, speaker='Sohee', fixtures=list(FIXTURES),
        variants=variants, totals=aggregate, complete_natural_eos_and_signal=valid,
        observed_pair_wall_ratio_default_over_passive=round(baseline['tts_seconds']/passive['tts_seconds'], 6)
            if valid else None,
        observed_rtf_ratio_default_over_passive=round(baseline['synthesis_seconds_per_audio_second']/
            passive['synthesis_seconds_per_audio_second'], 6) if valid else None,
        identical_pcm_hashes_by_fixture={str(i): variants['default']['rows'][i]['raw_pcm_f32le_sha256'] ==
            variants['passive']['rows'][i]['raw_pcm_f32le_sha256'] for i in range(len(FIXTURES))},
        identical_talker_sequence_hashes_by_fixture={str(i): variants['default']['rows'][i]['talker_sequence_sha256'] ==
            variants['passive']['rows'][i]['talker_sequence_sha256'] for i in range(len(FIXTURES))},
        talker_sequence_hash_scope='Returned talker.generate first row only, not full-codebook parity.',
        model_load_count_per_variant=1, model_load_included_in_tts=False,
        wait_hints_guaranteed_applied=False, asr_performed=False, dsp_performed=False,
        retries_performed=False, production_eligible=False, human_listening_verified=False,
        quality_equivalence_verified=False, full_course_forecast_validated=False,
        documentation=DOCUMENTATION,
        caveat='One passive-then-default confirmation trial on two new short public texts, explanation then formal reading, in sequential fresh processes on one runner. Both models process fixtures 0 then 1. Legal names and a synthetic reading-test sentence are used, not substantive law text. Order, shared file cache and host noise remain. The three passive hints change together; their individual effects are not isolated. Requested hints may be ignored by a particular runtime. Same seeds do not prove waveform or quality equivalence.')


def worker(model_path, output, variant):
    from download_models import require_keyless
    require_keyless()
    expected = PASSIVE if variant == 'passive' else {}
    require(variant in VARIANTS and wait_values(os.environ) == expected)
    # Every dependency imported above is standard library only. Native imports
    # happen after the fresh child process received its complete environment.
    require('torch' not in sys.modules and 'numpy' not in sys.modules)
    from public_asset_cache import load_manifest
    from fp32_renderer import verify_assets, MODEL_REQUIRED, MODEL_REVISION
    hashes = {name: entry['sha256'] for name, entry in load_manifest()['models']['qwen']['files'].items()}
    actual_hashes = verify_assets(model_path, MODEL_REQUIRED, MODEL_REVISION, hashes)
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    parallel = torch.__config__.parallel_info()
    native = validate_native_threads(4, torch.get_num_threads(), torch.get_num_interop_threads(), parallel)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(str(Path(model_path).resolve()), device_map='cpu',
        dtype=torch.float32, attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True)
    model.model.eval()
    load_seconds = time.perf_counter() - started
    floating = sorted({str(p.dtype).removeprefix('torch.') for p in model.model.parameters() if p.is_floating_point()})
    require(floating == ['float32'] and all(p.device.type == 'cpu' for p in model.model.parameters()))
    after_load = rss_snapshot()
    def deadline(*unused):
        raise TimeoutError('public_cpu_wait_sample_timeout')
    signal.signal(signal.SIGALRM, deadline)
    rows = []
    for i, text in enumerate(FIXTURES):
        talker = model.model.talker
        original = talker.generate
        observations = []
        eos_id = int(model.model.config.talker_config.codec_eos_token_id)
        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            tokens = result.sequences[0].tolist()
            observations.append(dict(observe_eos(tokens, eos_id),
                talker_sequence_sha256=hashlib.sha256(json.dumps(tokens, separators=(',', ':')).encode()).hexdigest()))
            return result
        talker.generate = observe
        torch.manual_seed(5192 + i)
        signal.alarm(240)
        started, cpu_started = time.perf_counter(), time.process_time()
        try:
            with torch.inference_mode():
                waves, sr = model.generate_custom_voice(**generation_args(i))
            elapsed, cpu_elapsed = time.perf_counter() - started, time.process_time() - cpu_started
        finally:
            signal.alarm(0)
            talker.generate = original
        require(len(waves) == 1 and sr == 24000 and len(observations) == 1)
        wave = np.asarray(waves[0])
        valid = bool(wave.ndim == 1 and wave.size > 0 and np.isfinite(wave).all()
            and np.max(np.abs(wave)) < 1.0 and np.sqrt(np.mean(wave.astype('float64')**2)) > 1e-5)
        target = output / f'{variant}-{i}.wav'
        sf.write(target, wave, sr, subtype='FLOAT')
        row = dict(variant=variant, fixture=i, voice_mode=FIXTURE_MODES[i], seed=5192+i, dtype='float32', device='cpu',
            threads=4, interop_threads=1, tts_seconds=round(elapsed, 6),
            process_cpu_seconds=round(cpu_elapsed, 6), audio_seconds=round(len(wave)/sr, 6),
            sample_rate=sr, pcm_samples=len(wave), signal_valid=valid, wav_file=target.name,
            wav_sha256=file_hash(target), raw_pcm_f32le_sha256=hashlib.sha256(np.asarray(wave, dtype='<f4').tobytes()).hexdigest(),
            generation_args=generation_args(i), memory=rss_snapshot(), **observations[0])
        rows.append(row)
        dump(output/'partial-rows.json', rows)
        print(json.dumps(row, sort_keys=True), flush=True)
    report = dict(variant=variant, model=MODEL, model_revision=REVISION, device='cpu', threads=4,
        interop_threads=1, parameter_dtypes=floating, wait_environment=wait_values(os.environ),
        suite=SUITE, fixture_modes=list(FIXTURE_MODES), fixture_order=[0, 1],
        cpu=hardware(), fixtures=list(FIXTURES), native_threads=native, parallel_info=parallel,
        model_assets_sha256=actual_hashes, model_load_seconds=round(load_seconds, 6),
        memory_after_load=after_load, memory_after_synthesis=rss_snapshot(),
        torch_version=torch.__version__, mkldnn_enabled=torch.backends.mkldnn.enabled,
        loaded_openmp_libraries=openmp_libraries(), rows=rows, totals=totals(rows, variant))
    dump(output/'result.json', report)


def run(model_path, output):
    from download_models import require_keyless
    require_keyless()
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
            report = dict(schema=SCHEMA, status='wait_worker_timeout', failed_variant=variant,
                cpu=cpu, completed_variants=variants, observed_rtf_ratio_default_over_passive=None,
                production_eligible=False)
            break
        if process.returncode:
            report = dict(schema=SCHEMA, status='wait_worker_failed', failed_variant=variant,
                returncode=process.returncode, cpu=cpu, completed_variants=variants,
                observed_rtf_ratio_default_over_passive=None, production_eligible=False)
            break
        variants[variant] = json.loads((output/variant/'result.json').read_text())
        variants[variant]['worker_mode_wall_seconds'] = round(time.perf_counter()-started, 6)
    else:
        report = compare_results(variants, cpu)
    dump(output/'summary.json', report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if report['status'] == 'timing_trial_no_quality_approval' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('run', 'worker'))
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--variant', choices=VARIANTS)
    args = parser.parse_args()
    validate_request()
    if args.mode == 'worker':
        require(args.variant in VARIANTS)
        worker(args.model, args.output, args.variant)
        return 0
    require(args.variant is None)
    return run(args.model, args.output)


if __name__ == '__main__':
    raise SystemExit(main())
