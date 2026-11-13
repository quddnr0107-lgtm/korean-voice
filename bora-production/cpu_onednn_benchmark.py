"""Same-model public FP32 Sohee CPU oneDNN on/off trial.

Only torch.backends.mkldnn.enabled is toggled. Other backend/precision flags,
weights, the official sampler and the original explanation instruction stay
unchanged. Backend choice can change numerical results; this is not approval.
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
import time

from basic_sohee_benchmark import FIXTURES, MODEL, REVISION, generation_args, observe_eos
from cpu_precision_benchmark import hardware
from cpu_thread_benchmark import rss_snapshot, validate_native_threads

SCHEMA = 'sohee-public-cpu-onednn/v1'
MODES = ('enabled', 'disabled')
ORDERS = (MODES, tuple(reversed(MODES)))
SECONDS_PER_CALL = 300
BACKEND_SOURCE = 'https://github.com/pytorch/pytorch/blob/v2.8.0/torch/backends/mkldnn/__init__.py'
DOC_SOURCE = 'https://docs.pytorch.org/docs/stable/notes/mkldnn.html'


def require(ok):
    if not ok:
        raise ValueError('public_cpu_onednn_benchmark_invalid')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_request():
    request = json.loads(Path(__file__).with_name('cpu-onednn-request.json').read_text())
    require(request == dict(schema=SCHEMA,
        request_id='public-sohee-fp32-onednn-20261113-01', model_revision=REVISION,
        fixture_count=2, modes=list(MODES), threads=4, interop_threads=1,
        seconds_per_call=SECONDS_PER_CALL, production=False, secrets=False, gpu=False))


def backend_flags(torch):
    backend = torch.backends.mkldnn
    flags = dict(enabled=backend.enabled, deterministic=backend.deterministic,
        allow_tf32=backend.allow_tf32,
        float32_matmul_precision=torch.get_float32_matmul_precision())
    require(all(type(flags[name]) is bool for name in ('enabled', 'deterministic', 'allow_tf32'))
        and isinstance(flags['float32_matmul_precision'], str))
    return flags


def set_enabled_only(torch, enabled, original):
    require(type(enabled) is bool)
    # flags(enabled=...) has additional default arguments in torch 2.8; avoid it.
    torch.backends.mkldnn.enabled = enabled
    observed = backend_flags(torch)
    expected = dict(original, enabled=enabled)
    require(observed == expected)
    return observed


def summarize(rows):
    expected = [(i, mode) for i, order in enumerate(ORDERS) for mode in order]
    require(isinstance(rows, list) and len(rows) <= len(expected)
        and [(row.get('fixture'), row.get('mode')) for row in rows] == expected[:len(rows)])
    reference = None
    for row in rows:
        require(type(row['fixture']) is int and row['seed'] == 5192 + row['fixture']
            and row['dtype'] == 'float32' and row['device'] == 'cpu'
            and type(row['natural_eos']) is bool and type(row['signal_valid']) is bool
            and row['generation_args'] == generation_args('production_style', FIXTURES[row['fixture']])
            and type(row['mkldnn_enabled_observed']) is bool
            and row['mkldnn_enabled_observed'] == (row['mode'] == 'enabled')
            and row['backend_before'] == row['backend_after']
            and row['backend_before']['enabled'] == row['mkldnn_enabled_observed'])
        fixed = {k: v for k, v in row['backend_before'].items() if k != 'enabled'}
        require(set(fixed) == {'deterministic', 'allow_tf32', 'float32_matmul_precision'}
            and type(fixed['deterministic']) is bool and type(fixed['allow_tf32']) is bool
            and isinstance(fixed['float32_matmul_precision'], str))
        if reference is None:
            reference = fixed
        require(fixed == reference)
        validate_native_threads(4, row['threads_observed'], row['interop_threads'], row['parallel_info'])
        for key in ('tts_seconds', 'audio_seconds', 'talker_seconds'):
            require(type(row[key]) in (int, float) and math.isfinite(row[key]) and row[key] > 0)
        require(row['talker_seconds'] <= row['tts_seconds'] + 0.001)
        for name in ('wav_sha256', 'pcm_float32_sha256', 'talker_sequence_sha256'):
            require(isinstance(row[name], str) and re.fullmatch('[a-f0-9]{64}', row[name]))
    valid = len(rows) == len(expected) and all(row['natural_eos'] and row['signal_valid'] for row in rows)
    totals = {}
    for mode in MODES:
        selected = [row for row in rows if row['mode'] == mode]
        elapsed = sum(row['tts_seconds'] for row in selected)
        audio = sum(row['audio_seconds'] for row in selected)
        totals[mode] = dict(tts_seconds=round(elapsed, 6), audio_seconds=round(audio, 6),
            talker_seconds=round(sum(row['talker_seconds'] for row in selected), 6),
            synthesis_seconds_per_audio_second=round(elapsed / audio, 6) if valid else None)
    def equal_hashes(key):
        return {str(i): len([row for row in rows if row['fixture'] == i]) == 2
            and len({row[key] for row in rows if row['fixture'] == i}) == 1 for i in range(2)}
    return dict(schema=SCHEMA,
        status='timing_trial_no_quality_approval' if valid else 'incomplete_output_no_speed_claim',
        model=MODEL, model_revision=REVISION, device='cpu', dtype='float32', speaker='Sohee',
        threads=4, interop_threads=1, fixtures=list(FIXTURES),
        modes=list(MODES), fixture_mode_orders=[list(order) for order in ORDERS], rows=rows, totals=totals,
        complete_natural_eos_and_signal=valid,
        observed_rtf_ratio_enabled_over_disabled=round(
            totals['enabled']['synthesis_seconds_per_audio_second'] /
            totals['disabled']['synthesis_seconds_per_audio_second'], 6) if valid else None,
        observed_pair_wall_ratio_enabled_over_disabled=round(
            totals['enabled']['tts_seconds'] / totals['disabled']['tts_seconds'], 6) if valid else None,
        identical_pcm_float32_hashes_by_fixture=equal_hashes('pcm_float32_sha256'),
        identical_wav_hashes_by_fixture=equal_hashes('wav_sha256'),
        identical_talker_sequence_hashes_by_fixture=equal_hashes('talker_sequence_sha256'),
        talker_sequence_hash_scope='Returned talker.generate first row only; not full-codebook parity.',
        model_load_count=1, model_load_included_in_tts=False,
        weights_dtype_changed=False, matmul_precision_changed=False,
        asr_performed=False, dsp_performed=False, retries_performed=False,
        production_eligible=False, human_listening_verified=False, full_course_forecast_validated=False,
        sources=[BACKEND_SOURCE, DOC_SOURCE],
        caveat='One four-call AB/BA trial on two short public fixtures. Only the mkldnn.enabled routing flag changes; deterministic, allow_tf32 and float32 matmul precision are recorded unchanged. Kernel selection can change numerical output and mainly affect codec convolutions rather than every Linear operation. The same resident model and alternating order do not remove all warmup, primitive-cache or host-noise effects. File hashes can differ because of WAV metadata even when PCM matches. No human listening, quality-equivalence or full-course speed claim.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    validate_request()
    from download_models import require_keyless, verify_assets, QWEN_REQUIRED
    require_keyless()
    model_path, output = Path(args.model).resolve(), Path(args.output).resolve()
    require(not output.exists() and not output.is_relative_to(Path(__file__).resolve().parents[1])
        and not output.is_relative_to(model_path))
    verify_assets(model_path, QWEN_REQUIRED)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4')
    output.mkdir(parents=True)
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    require(torch.__version__ == '2.8.0+cpu')
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    initial = backend_flags(torch)
    metadata = dict(cpu=hardware(), torch_version=torch.__version__,
        mkldnn_available=bool(torch.backends.mkldnn.is_available()),
        initial_backend_flags=initial, script_sha256=digest(Path(__file__)),
        model_weights_sha256=digest(model_path/'model.safetensors'),
        codec_weights_sha256=digest(model_path/'speech_tokenizer/model.safetensors'),
        pinned_generation_config=json.loads((model_path/'generation_config.json').read_text()))
    def write_report(rows, **extra):
        report = summarize(rows)
        report.update(metadata, **extra)
        (output/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        return report
    if not metadata['mkldnn_available']:
        write_report([], status='unsupported_mkldnn_backend_no_speed_claim')
        return
    set_enabled_only(torch, True, initial)
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(str(model_path), device_map='cpu', dtype=torch.float32,
        attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True)
    model.model.eval()
    metadata['model_load_seconds'] = round(time.perf_counter()-started, 6)
    floating = sorted({str(p.dtype).removeprefix('torch.') for p in model.model.parameters()
        if p.is_floating_point()})
    require(floating == ['float32'] and all(p.device.type == 'cpu' for p in model.model.parameters()))
    metadata.update(floating_parameter_dtypes=floating, memory_after_load=rss_snapshot())
    rows, waveforms = [], {}
    write_report(rows, status='one_model_loaded')
    def deadline(*unused):
        raise TimeoutError('public_cpu_onednn_call_timeout')
    original_handler = signal.signal(signal.SIGALRM, deadline)
    try:
        for i, text in enumerate(FIXTURES):
            for mode in ORDERS[i]:
                before = set_enabled_only(torch, mode == 'enabled', initial)
                parallel_info = torch.__config__.parallel_info()
                native = validate_native_threads(4, torch.get_num_threads(), torch.get_num_interop_threads(), parallel_info)
                talker = model.model.talker
                original = talker.generate
                observed = []
                eos_id = int(model.model.config.talker_config.codec_eos_token_id)
                def observe(*a, **kw):
                    token_started = time.perf_counter()
                    result = original(*a, **kw)
                    token_elapsed = time.perf_counter()-token_started
                    tokens = result.sequences[0].tolist()
                    observation = observe_eos(tokens, eos_id)
                    observation.update(talker_seconds=round(token_elapsed, 6),
                        talker_sequence_sha256=hashlib.sha256(json.dumps(tokens,
                            separators=(',', ':')).encode('ascii')).hexdigest())
                    observed.append(observation)
                    return result
                talker.generate = observe
                torch.manual_seed(5192+i)
                signal.alarm(SECONDS_PER_CALL)
                started = time.perf_counter()
                try:
                    with torch.inference_mode():
                        waves, sr = model.generate_custom_voice(**generation_args('production_style', text))
                    elapsed = time.perf_counter()-started
                finally:
                    signal.alarm(0)
                    talker.generate = original
                require(len(waves) == 1 and sr == 24000 and len(observed) == 1)
                after = backend_flags(torch)
                require(after == before)
                wave = np.asarray(waves[0], dtype='<f4')
                valid = bool(wave.ndim == 1 and wave.size > 0 and np.isfinite(wave).all()
                    and np.max(np.abs(wave)) < 1.0
                    and np.sqrt(np.mean(wave.astype('float64')**2)) > 1e-5)
                target = output/f'onednn-{mode}-fixture-{i}.wav'
                sf.write(target, wave, sr, subtype='FLOAT')
                decoded, decoded_sr = sf.read(target, dtype='float32')
                require(decoded_sr == sr and np.array_equal(decoded, wave))
                waveforms[(i, mode)] = wave
                row = dict(fixture=i, mode=mode, seed=5192+i, dtype='float32', device='cpu',
                    mkldnn_enabled_observed=before['enabled'], backend_before=before, backend_after=after,
                    threads_observed=torch.get_num_threads(), interop_threads=torch.get_num_interop_threads(),
                    native_threads=native, parallel_info=parallel_info,
                    tts_seconds=round(elapsed, 6), audio_seconds=round(len(wave)/sr, 6),
                    sample_rate=sr, pcm_samples=len(wave), signal_valid=valid, **observed[0],
                    wav_file=target.name, wav_sha256=digest(target),
                    pcm_float32_sha256=hashlib.sha256(wave.tobytes(order='C')).hexdigest(),
                    non_talker_generation_seconds=round(max(0.0, elapsed-observed[0]['talker_seconds']), 6),
                    generation_args=generation_args('production_style', text), memory=rss_snapshot())
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)
                write_report(rows)
        comparisons = []
        for i in range(2):
            a, b = waveforms[(i, 'enabled')], waveforms[(i, 'disabled')]
            comparison = dict(fixture=i, enabled_samples=len(a), disabled_samples=len(b),
                same_sample_count=len(a) == len(b))
            if len(a) == len(b):
                x, y = a.astype('float64'), b.astype('float64')
                delta = y-x
                rms = float(np.sqrt(np.mean(delta**2)))
                comparison.update(max_absolute_difference=float(np.max(np.abs(delta))), rms_delta=rms,
                    relative_rms_delta=rms/float(np.sqrt(np.mean(x**2))),
                    exact_sample_values=bool(np.array_equal(a, b)),
                    pearson_correlation=float(np.corrcoef(x, y)[0, 1]))
            comparisons.append(comparison)
        metadata.update(decoded_pcm_comparisons=comparisons, memory_after_synthesis=rss_snapshot())
        report = write_report(rows)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        if not report['complete_natural_eos_and_signal']:
            raise SystemExit(1)
    except Exception as error:
        report = summarize(rows)
        report.update(metadata, status='onednn_trial_failed_no_speed_claim',
            error_type=type(error).__name__, complete_natural_eos_and_signal=False,
            observed_rtf_ratio_enabled_over_disabled=None,
            observed_pair_wall_ratio_enabled_over_disabled=None)
        for total in report['totals'].values():
            total['synthesis_seconds_per_audio_second'] = None
        (output/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, original_handler)
        set_enabled_only(torch, initial['enabled'], initial)


if __name__ == '__main__':
    main()

