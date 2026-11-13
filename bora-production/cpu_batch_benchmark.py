"""Original FP32 Sohee on CPU: two serial calls versus one official batch of two.

One model stays resident. Production instruction, model, sampling and precision
are unchanged. Batch sampling consumes RNG differently; numeric mode seeds do
not imply matching per-unit draws, waveforms or quality. No production writes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import signal
import time

from basic_sohee_benchmark import FIXTURES, MODEL, REVISION
from cpu_precision_benchmark import hardware
from cpu_thread_benchmark import rss_snapshot, validate_native_threads
from fp32_renderer import SAMPLING, SEEDS, STYLES

SCHEMA = 'sohee-public-cpu-batch/v1'
MODES = ('serial', 'batch2')
CAP = 1100
THREADS = 4
MODE_SECONDS = 600
MODE_SEED = 5192
SAMPLE_RATE = 24000
EXPECTED_PACKAGES = {'torch': '2.8.0+cpu', 'torchaudio': '2.8.0+cpu',
    'qwen-tts': '0.1.1', 'transformers': '4.57.3', 'soundfile': '0.14.0',
    'numpy': '2.5.4', 'scipy': '1.18.1'}
# Exact source bytes from the published qwen-tts 0.1.1 wheel, inspected offline.
MODELING_SHA256 = '25c42656bcf810f06ef6bc1839bd7083f3c8cfedac3a147c4060b4262b1c96a0'
INFERENCE_SHA256 = 'e1da450732857c1f5fe3e36ebab85db2f6dc6a48caaff6973f463384e30275e4'
ROOT = Path(__file__).resolve().parents[1]


def require(ok):
    if not ok:
        raise ValueError('public_cpu_batch_benchmark_invalid')


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def validate_request():
    request = json.loads(Path(__file__).with_name('cpu-batch-request.json').read_text())
    require(request == dict(schema=SCHEMA, request_id='public-sohee-fp32-batch2-20261113-01',
        model_revision=REVISION, fixture_count=2, threads=4, interop_threads=1,
        modes=list(MODES), mode_timeout_seconds=MODE_SECONDS,
        production=False, secrets=False, gpu=False))
    require(len(FIXTURES) == 2 and SEEDS[0] == MODE_SEED)


def generation_args(mode, fixture=None):
    require(mode in MODES)
    result = copy.deepcopy(SAMPLING)
    result['max_new_tokens'] = CAP
    if mode == 'serial':
        require(type(fixture) is int and 0 <= fixture < len(FIXTURES))
        result.update(text=FIXTURES[fixture], speaker='Sohee', language='Korean',
            instruct=STYLES['explain'])
    else:
        require(fixture is None)
        result.update(text=list(FIXTURES), speaker=['Sohee'] * 2,
            language=['Korean'] * 2, instruct=[STYLES['explain']] * 2)
    return result


def row_eos(tokens, eos_id, pad_id, code_frames, upsample_rate, pcm_samples):
    """Validate each generated row, including only runtime-resolved EOS padding.

    With inputs_embeds-only, HF starts with an empty ID prefix. The Qwen decoder
    receives codes before the first EOS, so its code count must equal EOS index.
    The tokenizer trims each waveform to code count * decode upsample rate.
    """
    require(isinstance(tokens, list) and tokens and all(type(t) is int for t in tokens)
        and type(eos_id) is int and eos_id >= 0
        and (pad_id is None or type(pad_id) is int)
        and type(code_frames) is int and code_frames >= 0
        and type(upsample_rate) is int and upsample_rate > 0
        and type(pcm_samples) is int and pcm_samples >= 0)
    first = tokens.index(eos_id) if eos_id in tokens else None
    suffix = tokens[first + 1:] if first is not None else []
    # Unknown pad is never guessed to be zero (or EOS), even without a suffix.
    padding_verified = pad_id is not None and all(token == pad_id for token in suffix)
    before_cap = first is not None and first + 1 < CAP
    codec_verified = first is not None and code_frames == first
    pcm_verified = pcm_samples == code_frames * upsample_rate
    natural = bool(first is not None and first > 0 and before_cap and padding_verified
        and codec_verified and pcm_verified)
    return dict(eos_token_id=eos_id, resolved_pad_token_id=pad_id,
        returned_sequence_tokens=len(tokens), first_eos_index=first,
        actual_tokens_through_eos=(first + 1) if first is not None else None,
        post_eos_padding_tokens=len(suffix), post_eos_padding_verified=padding_verified,
        eos_before_safety_cap=before_cap, decoder_code_frames=code_frames,
        decoder_code_frames_match_first_eos=codec_verified,
        decode_upsample_rate=upsample_rate, pcm_frame_coverage_verified=pcm_verified,
        natural_eos=natural,
        talker_sequence_sha256=hashlib.sha256(json.dumps(tokens, separators=(',', ':')).encode()).hexdigest())


def summarize(calls, rows):
    expected_calls = [('serial-0', 'serial', [0]), ('serial-1', 'serial', [1]),
        ('batch2-0', 'batch2', [0, 1])]
    require(isinstance(calls, list) and len(calls) <= 3 and isinstance(rows, list) and len(rows) <= 4)
    require([(c['id'], c['mode'], c['fixtures']) for c in calls] == expected_calls[:len(calls)])
    seen = set(); call_ids = {c['id']: c for c in calls}
    for call in calls:
        require(type(call['tts_seconds']) in (int, float) and math.isfinite(call['tts_seconds'])
            and call['tts_seconds'] > 0 and call['mode_seed'] == MODE_SEED)
    for row in rows:
        pair = (row['mode'], row['fixture'])
        require(row['mode'] in MODES and type(row['fixture']) is int and 0 <= row['fixture'] < 2
            and pair not in seen and row['call_id'] in call_ids
            and row['mode'] == call_ids[row['call_id']]['mode']
            and row['fixture'] in call_ids[row['call_id']]['fixtures']
            and row['text'] == FIXTURES[row['fixture']] and row['mode_seed'] == MODE_SEED
            and type(row['natural_eos']) is bool and type(row['signal_valid']) is bool)
        seen.add(pair)
        require(type(row['audio_seconds']) in (int, float)
            and math.isfinite(row['audio_seconds']) and row['audio_seconds'] > 0)
    totals = {}
    for mode in MODES:
        selected = [row for row in rows if row['mode'] == mode]
        elapsed = sum(c['tts_seconds'] for c in calls if c['mode'] == mode)
        audio = sum(row['audio_seconds'] for row in selected)
        expected_count = 2 if mode == 'serial' else 1
        complete = len(selected) == 2 and sum(c['mode'] == mode for c in calls) == expected_count
        valid = complete and all(row['natural_eos'] and row['signal_valid'] for row in selected)
        totals[mode] = dict(tts_seconds=round(elapsed, 6), audio_seconds=round(audio, 6),
            complete_natural_eos_and_signal=bool(valid),
            synthesis_seconds_per_audio_second=round(elapsed / audio, 6) if valid and audio else None)
    valid = all(part['complete_natural_eos_and_signal'] for part in totals.values())
    return dict(schema=SCHEMA,
        status='timing_trial_no_quality_approval' if valid else 'incomplete_output_no_speed_claim',
        model=MODEL, model_revision=REVISION, device='cpu', dtype='float32', threads=4,
        interop_threads=1, speaker='Sohee', language='Korean', fixtures=list(FIXTURES),
        style=STYLES['explain'], sampling=dict(SAMPLING, max_new_tokens=CAP),
        mode_seed=MODE_SEED, calls=calls, rows=rows, totals=totals,
        complete_natural_eos_and_signal=valid,
        observed_pair_wall_ratio_serial_over_batch=round(totals['serial']['tts_seconds'] /
            totals['batch2']['tts_seconds'], 6) if valid else None,
        observed_rtf_ratio_serial_over_batch=round(
            totals['serial']['synthesis_seconds_per_audio_second'] /
            totals['batch2']['synthesis_seconds_per_audio_second'], 6) if valid else None,
        model_load_count=1, model_load_included_in_tts=False, model_resident_between_modes=True,
        mode_order=list(MODES), per_unit_seed_equivalence_claimed=False,
        seed_relation='manual_seed(5192) once before each mode. Serial calls share one advancing stream; batch draws and padding consume that stream differently.',
        asr_performed=False, dsp_performed=False, retries_performed=False,
        human_listening_verified=False, production_eligible=False,
        quality_equivalence_verified=False, full_course_forecast_validated=False,
        caveat='One serial-then-batch trial on two short public texts. The batch follows the serial warmup, and RNG draw order differs. Different waveform durations can affect wall ratios. No promise of speedup, same pronunciation or full-course savings.')


def source_contract(model):
    import transformers.generation.utils as hf_generation
    files = dict(modeling=Path(inspect.getsourcefile(type(model.model))),
        inference=Path(inspect.getsourcefile(type(model))),
        hf_generation=Path(inspect.getsourcefile(hf_generation.GenerationMixin)))
    hashes = {key: file_hash(path) for key, path in files.items()}
    require(hashes['modeling'] == MODELING_SHA256 and hashes['inference'] == INFERENCE_SHA256)
    require(callable(model.model.talker._prepare_special_tokens))
    upsample = int(model.model.speech_tokenizer.get_decode_upsample_rate())
    require(upsample == 1920)
    return dict(source_files_sha256=hashes, decode_upsample_rate=upsample,
        official_batch_trim_contract_verified=True,
        eos_basis='qwen-tts 0.1.1 cuts audio codes before first codebook-0 EOS; HF 4.57.3 pads completed rows with resolved _pad_token_tensor; actual runtime values are observed.',
        sources=[
            'https://github.com/QwenLM/Qwen3-TTS/blob/022e286b98fbec7e1e916cb940cdf532cd9f488e/qwen_tts/core/models/modeling_qwen3_tts.py#L2280',
            'https://github.com/huggingface/transformers/blob/v4.57.3/src/transformers/generation/utils.py'])


def observed_generation(model, kwargs, torch):
    """Read-only instrumentation; restore every bound method after the call."""
    core, talker = model.model, model.model.talker
    original_talker, original_core = talker.generate, core.generate
    original_special = talker._prepare_special_tokens
    observed = dict(special=[], sequences=[], decoder=[])
    def observe_special(config, *args, **kw):
        result = original_special(config, *args, **kw)
        pad = getattr(config, '_pad_token_tensor', None)
        eos = getattr(config, '_eos_token_tensor', None)
        observed['special'].append(dict(
            pad=(int(pad.item()) if pad is not None and pad.numel() == 1 else None),
            eos=(eos.reshape(-1).tolist() if eos is not None else [])))
        return result
    def observe_talker(*args, **kw):
        result = original_talker(*args, **kw)
        observed['sequences'].append(result.sequences.tolist())
        return result
    def observe_core(*args, **kw):
        result = original_core(*args, **kw)
        codes, _ = result
        eos = int(core.config.talker_config.codec_eos_token_id)
        observed['decoder'].append([dict(frames=int(code.shape[0]),
            first_book_contains_eos=bool((code[:, 0] == eos).any().item())) for code in codes])
        return result
    talker._prepare_special_tokens = observe_special
    talker.generate, core.generate = observe_talker, observe_core
    started = time.perf_counter()
    try:
        with torch.inference_mode():
            waves, sr = model.generate_custom_voice(**kwargs)
        elapsed = time.perf_counter() - started
    finally:
        talker._prepare_special_tokens = original_special
        talker.generate, core.generate = original_talker, original_core
    require(all(len(observed[key]) == 1 for key in observed))
    return waves, sr, elapsed, {key: value[0] for key, value in observed.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(); validate_request()
    from download_models import require_keyless, verify_assets, QWEN_REQUIRED
    require_keyless()
    model_path, output = Path(args.model).resolve(), Path(args.output).resolve()
    require(not output.exists() and not output.is_relative_to(ROOT)
        and not output.is_relative_to(model_path))
    verify_assets(model_path, QWEN_REQUIRED)
    config = json.loads((model_path / 'config.json').read_text())
    require(config.get('tts_model_type') == 'custom_voice' and config.get('tts_model_size') == '1b7')
    output.mkdir(parents=True)
    versions = {name: importlib.metadata.version(name) for name in EXPECTED_PACKAGES}
    require(versions == EXPECTED_PACKAGES)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
        HF_HUB_DISABLE_IMPLICIT_TOKEN='1', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4')
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(4); torch.set_num_interop_threads(1)
    native = validate_native_threads(4, torch.get_num_threads(), torch.get_num_interop_threads(),
        torch.__config__.parallel_info())
    metadata = dict(cpu=hardware(), installed_packages=versions, native_threads=native,
        script_sha256=file_hash(Path(__file__)), model_weights_sha256=file_hash(model_path / 'model.safetensors'),
        codec_weights_sha256=file_hash(model_path / 'speech_tokenizer/model.safetensors'),
        recipe_source_sha256={name: file_hash(Path(__file__).with_name(name))
            for name in ('pilot_render.py', 'fp32_renderer.py')},
        pinned_generation_config=json.loads((model_path / 'generation_config.json').read_text()))
    report = summarize([], []); report.update(metadata, status='loading_one_fp32_model')
    dump(output / 'summary.json', report)
    started = time.perf_counter()
    try:
        model = Qwen3TTSModel.from_pretrained(str(model_path), device_map='cpu', dtype=torch.float32,
            attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True)
        model.model.eval()
        metadata['model_load_seconds'] = round(time.perf_counter() - started, 6)
        floating = sorted({str(p.dtype).removeprefix('torch.') for p in model.model.parameters()
            if p.is_floating_point()})
        require(floating == ['float32'] and all(p.device.type == 'cpu' for p in model.model.parameters()))
        metadata.update(floating_parameter_dtypes=floating, memory_after_load=rss_snapshot(),
            runtime_source_contract=source_contract(model))
    except Exception as error:
        report.update(status='model_or_source_preflight_failed', error_type=type(error).__name__)
        dump(output / 'summary.json', report)
        raise
    calls, rows = [], []
    def timeout(*unused):
        raise TimeoutError('public_cpu_batch_mode_timeout')
    old_handler = signal.signal(signal.SIGALRM, timeout)
    try:
        for mode in MODES:
            torch.manual_seed(MODE_SEED)
            signal.alarm(MODE_SECONDS)
            batches = [[0], [1]] if mode == 'serial' else [[0, 1]]
            for index, fixtures in enumerate(batches):
                call_id = f'{mode}-{index}'
                kwargs = generation_args(mode, fixtures[0] if mode == 'serial' else None)
                waves, sr, elapsed, evidence = observed_generation(model, kwargs, torch)
                require(sr == SAMPLE_RATE and len(waves) == len(fixtures)
                    and len(evidence['sequences']) == len(evidence['decoder']) == len(fixtures))
                eos_id = int(model.model.config.talker_config.codec_eos_token_id)
                special = evidence['special']
                require(special['eos'] == [eos_id])
                calls.append(dict(id=call_id, mode=mode, fixtures=fixtures, mode_seed=MODE_SEED,
                    seed_reset_before_call=(index == 0), tts_seconds=round(elapsed, 6),
                    generation_args=kwargs, resolved_special_tokens=special, memory=rss_snapshot()))
                for position, fixture in enumerate(fixtures):
                    wave = np.asarray(waves[position])
                    valid = bool(wave.ndim == 1 and wave.size > 0 and np.isfinite(wave).all()
                        and np.max(np.abs(wave)) < 1.0
                        and np.sqrt(np.mean(wave.astype('float64') ** 2)) > 1e-5)
                    target = output / f'{mode}-{fixture}.wav'
                    sf.write(target, wave, sr, subtype='FLOAT')
                    decoded = evidence['decoder'][position]
                    eos = row_eos(evidence['sequences'][position], eos_id, special['pad'],
                        decoded['frames'], metadata['runtime_source_contract']['decode_upsample_rate'], len(wave))
                    require(not decoded['first_book_contains_eos'])
                    row = dict(mode=mode, fixture=fixture, text=FIXTURES[fixture], call_id=call_id,
                        mode_seed=MODE_SEED, per_unit_seed_assigned=False,
                        seed_relation='This row uses its mode shared RNG stream; no serial/batch per-unit equivalence.',
                        audio_seconds=len(wave) / sr, sample_rate=sr, pcm_samples=len(wave),
                        signal_valid=valid, wav_file=target.name, wav_sha256=file_hash(target), **eos)
                    rows.append(row)
                    print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)
                report = summarize(calls, rows); report.update(metadata)
                dump(output / 'summary.json', report)
                dump(output / 'partial-calls-and-rows.json', dict(calls=calls, rows=rows))
            signal.alarm(0)
    except Exception as error:
        report = summarize(calls, rows)
        report.update(metadata, status='batch_trial_failed', failed_mode=mode, error_type=type(error).__name__)
        dump(output / 'summary.json', report)
        raise
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, old_handler)
    report.update(memory_after_synthesis=rss_snapshot())
    dump(output / 'summary.json', report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    if not report['complete_natural_eos_and_signal']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
