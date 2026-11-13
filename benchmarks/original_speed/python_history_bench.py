"""Public, offline FP32 Kim Bora ablation: disable only unused inner CP history.

This is an independent experiment, never a production renderer or auto-promoter.
It preserves the pinned model, Sohee, Korean, instruction, sampling, seed, outer
codec history and FLOAT WAV output. Only the inner Code Predictor's unused
output_hidden_states collection differs. No lossy precision or voice conversion.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import platform
import signal
import statistics
import sys
import time

MODEL = 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'
REVISION = '0c0e3051f131929182e2c023b9537f8b1c68adfe'
MODEL_WEIGHTS_SHA256 = '38b1d5971bdbd982b561cccec982669a53b0537c3cf5e9bd4778ed07bb2f5137'
CODEC_WEIGHTS_SHA256 = '836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258'
MODEL_CODE_SHA256 = '25c42656bcf810f06ef6bc1839bd7083f3c8cfedac3a147c4060b4262b1c96a0'
WRAPPER_CODE_SHA256 = 'e1da450732857c1f5fe3e36ebab85db2f6dc6a48caaff6973f463384e30275e4'
PROFILES = ('baseline', 'inner_history_off')
FIXTURES = (
    '오늘 배운 내용을 잠깐 덮고, 자기 말로 한 번 설명해 보세요.',
    '지금은 조금 느려도 괜찮습니다. 한 문장씩 차분하게 읽어 보세요.',
)
SEEDS = (5192, 5193)
INSTRUCT = ('Speak Korean to one learner. Ask the questions with genuine curiosity, '
    'then explain with clear, conversational phrasing. Keep a consistent speaking '
    'voice throughout the paragraph. Allow occasional quiet, unforced inhalations '
    'and small thoughtful pauses at natural phrase boundaries when the meaning '
    'calls for them. Keep the vocal affect appropriate to the subject, and keep '
    'the spoken words clear and complete.')
SAMPLING = dict(non_streaming_mode=True, do_sample=True, temperature=0.8,
    top_p=0.9, top_k=50, repetition_penalty=1.05, subtalker_dosample=True,
    subtalker_temperature=0.9, subtalker_top_p=1.0, subtalker_top_k=50,
    max_new_tokens=1100)
SCHEMA = 'kim-bora-original-inner-history-cpu-ablation/v1'


def require(ok, code):
    if not ok:
        raise ValueError(code)


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def trial_order(repeats):
    require(type(repeats) is int and 1 <= repeats <= 3, 'repeat_bounds')
    result = []
    for repeat in range(repeats):
        for fixture in range(len(FIXTURES)):
            profiles = PROFILES if (repeat + fixture) % 2 == 0 else PROFILES[::-1]
            result.extend((repeat, fixture, profile) for profile in profiles)
    return result


def rss_snapshot():
    status = Path('/proc/self/status').read_text()
    result = {}
    for label, key in (('VmRSS:', 'current_rss_kib'), ('VmHWM:', 'process_peak_rss_kib')):
        match = next((line.split() for line in status.splitlines() if line.startswith(label)), None)
        if match:
            result[key] = int(match[1])
    return result


def eos_observation(tokens, eos_id, max_tokens):
    require(isinstance(tokens, list) and tokens
        and all(type(token) is int for token in tokens), 'invalid_observed_tokens')
    return dict(sequence_length=len(tokens), eos_id=eos_id,
        last_token=tokens[-1], eos_seen=eos_id in tokens,
        cap_reached=len(tokens) >= max_tokens,
        natural_eos=eos_id in tokens and tokens[-1] == eos_id and len(tokens) < max_tokens)


def inner_history_wrapper(original, disable, observations):
    """Preserve all arguments except the one unused inner-output history flag."""
    require(type(disable) is bool, 'invalid_disable_flag')

    def wrapped(*args, **kwargs):
        require(kwargs.get('output_hidden_states') is True, 'inner_caller_contract_changed')
        require(kwargs.get('return_dict_in_generate') is True, 'inner_return_contract_changed')
        forwarded = dict(kwargs)
        if disable:
            forwarded['output_hidden_states'] = False
        result = original(*args, **forwarded)
        observations['calls'] = observations.get('calls', 0) + 1
        observations['history_disabled_calls'] = observations.get('history_disabled_calls', 0) + int(disable)
        if disable:
            require(getattr(result, 'hidden_states', None) is None, 'inner_history_still_returned')
        return result

    return wrapped


def summarize(rows, repeats):
    expected = {(repeat, fixture, profile) for repeat, fixture, profile in trial_order(repeats)}
    actual = {(r['repeat'], r['fixture'], r['profile']) for r in rows}
    require(len(rows) == len(actual) and actual <= expected, 'duplicate_or_unexpected_trial')
    for row in rows:
        if row.get('status') == 'ok':
            elapsed = row.get('generate_custom_voice_seconds')
            require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed > 0,
                'invalid_measured_time')
    pairs = []
    for repeat in range(repeats):
        for fixture in range(len(FIXTURES)):
            values = {r['profile']: r for r in rows if r['repeat'] == repeat and r['fixture'] == fixture}
            if set(values) != set(PROFILES):
                continue
            baseline, modified = (values[name] for name in PROFILES)
            good = all(r.get('status') == 'ok' and r.get('natural_eos') is True
                and r.get('signal_valid') is True for r in (baseline, modified))
            equal = good and all(baseline.get(key) == modified.get(key) for key in (
                'pcm_f32_sha256', 'samples', 'sample_rate', 'codec_sha256', 'codec_shape'))
            pairs.append(dict(repeat=repeat, fixture=fixture, valid=good,
                exact_pcm_and_codec_equal=equal))
    baseline_stability = []
    for fixture in range(len(FIXTURES)):
        selected = [r for r in rows if r['profile'] == 'baseline' and r['fixture'] == fixture]
        baseline_stability.append(dict(fixture=fixture,
            complete=len(selected) == repeats,
            repeat_pcm_equal=len(selected) == repeats and len({r.get('pcm_f32_sha256') for r in selected}) == 1,
            repeat_codec_equal=len(selected) == repeats and len({r.get('codec_sha256') for r in selected}) == 1))
    complete = actual == expected and len(pairs) == repeats * len(FIXTURES)
    exact = (complete and all(p['exact_pcm_and_codec_equal'] for p in pairs)
        and all(v['complete'] and v['repeat_pcm_equal'] and v['repeat_codec_equal'] for v in baseline_stability))
    totals = {}
    for profile in PROFILES:
        selected = [r for r in rows if r['profile'] == profile]
        repeat_totals = []
        for repeat in range(repeats):
            rr = [r for r in selected if r['repeat'] == repeat]
            if len(rr) == len(FIXTURES) and all(r.get('status') == 'ok' for r in rr):
                repeat_totals.append(sum(r['generate_custom_voice_seconds'] for r in rr))
        totals[profile] = dict(two_fixture_seconds_per_repeat=repeat_totals,
            median_two_fixture_seconds=statistics.median(repeat_totals) if len(repeat_totals) == repeats else None)
    old = totals['baseline']['median_two_fixture_seconds']
    new = totals['inner_history_off']['median_two_fixture_seconds']
    return dict(schema=SCHEMA, complete=complete, exact_pcm_and_codec_equal=exact,
        pairs=pairs, baseline_repeat_stability=baseline_stability, totals=totals,
        observed_time_reduction_pct=(1 - new / old) * 100 if exact and old and new else None,
        adoption_decision='exact_output_match_in_public_screen_only' if exact else 'do_not_adopt',
        production_eligible=False, human_listening_verified=False, asr_performed=False,
        scope='Two short public Kim Bora explain fixtures, balanced paired order, repeated fixed seeds. '
            'Exact equality applies only to measured outputs; timings exclude model load, file save and ASR. '
            'No full-course forecast or production promotion.')


@contextlib.contextmanager
def observe_model(model, disable, observed):
    core, talker = model.model, model.model.talker
    predictor = talker.code_predictor
    original_inner, original_core, original_talker = predictor.generate, core.generate, talker.generate
    inner = {}
    predictor.generate = inner_history_wrapper(original_inner, disable, inner)

    def record_talker(*args, **kwargs):
        require(kwargs.get('output_hidden_states') is True, 'outer_codec_history_must_remain_enabled')
        result = original_talker(*args, **kwargs)
        tokens = result.sequences[0].tolist()
        observed.update(eos_observation(tokens,
            int(core.config.talker_config.codec_eos_token_id), SAMPLING['max_new_tokens']))
        return result

    def record_core(*args, **kwargs):
        started = time.perf_counter()
        result = original_core(*args, **kwargs)
        observed['generation_only_seconds'] = time.perf_counter() - started
        require(isinstance(result, tuple) and len(result) == 2 and len(result[0]) == 1,
            'core_output_contract_changed')
        code = result[0][0].detach().cpu().contiguous().numpy()
        observed['codec_shape'] = list(code.shape)
        observed['codec_dtype'] = str(code.dtype)
        observed['codec_sha256'] = hashlib.sha256(code.tobytes(order='C')).hexdigest()
        observed['codec_array'] = code
        return result

    talker.generate, core.generate = record_talker, record_core
    try:
        yield
    finally:
        predictor.generate, core.generate, talker.generate = original_inner, original_core, original_talker
        observed['inner_calls'] = inner.get('calls', 0)
        observed['inner_history_disabled_calls'] = inner.get('history_disabled_calls', 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--call-timeout', type=int, default=300)
    parser.add_argument('--total-timeout', type=int, default=1200)
    args = parser.parse_args(argv)
    benchmark_started = time.perf_counter()
    trial_order(args.repeats)
    require(30 <= args.call_timeout <= 600, 'timeout_bounds')
    require(60 <= args.total_timeout <= 1500, 'total_timeout_bounds')
    deadline = benchmark_started + args.total_timeout
    require(not any(os.environ.get(name) for name in (
        'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')), 'private_keys_forbidden')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1', ORT_DISABLE_TELEMETRY='1',
        OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4', MKL_NUM_THREADS='4')
    output, model_dir = Path(args.output).resolve(), Path(args.model).resolve()
    require(not output.exists(), 'output_must_be_fresh')
    require(model_dir.is_dir() and not model_dir.is_symlink(), 'invalid_model_directory')
    output.mkdir(parents=True)
    packages = {name: importlib.metadata.version(name) for name in ('torch', 'qwen-tts', 'transformers', 'numpy', 'soundfile')}
    require(packages['qwen-tts'] == '0.1.1' and packages['transformers'] == '4.57.3'
        and packages['torch'] == '2.8.0+cpu', 'pinned_package_mismatch')
    config = json.loads((model_dir / 'config.json').read_text())
    require(config.get('tts_model_type') == 'custom_voice' and config.get('tts_model_size') == '1b7',
        'model_architecture_mismatch')
    weights = {name: file_hash(model_dir / name) for name in (
        'model.safetensors', 'speech_tokenizer/model.safetensors')}
    require(weights['model.safetensors'] == MODEL_WEIGHTS_SHA256
        and weights['speech_tokenizer/model.safetensors'] == CODEC_WEIGHTS_SHA256,
        'pinned_weight_hash_mismatch')
    import numpy as np
    import soundfile as sf
    import torch
    import qwen_tts.core.models.modeling_qwen3_tts as model_module
    import qwen_tts.inference.qwen3_tts_model as wrapper_module
    from qwen_tts import Qwen3TTSModel
    source_hashes = {'model': file_hash(inspect.getsourcefile(model_module)),
        'wrapper': file_hash(inspect.getsourcefile(wrapper_module))}
    require(source_hashes == {'model': MODEL_CODE_SHA256, 'wrapper': WRAPPER_CODE_SHA256},
        'pinned_model_source_mismatch')
    torch.set_num_threads(4)
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(str(model_dir), device_map='cpu', dtype=torch.float32,
        attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True)
    model.model.eval()
    load_seconds = time.perf_counter() - started
    require({p.dtype for p in model.model.parameters() if p.is_floating_point()} == {torch.float32},
        'model_dtype_mismatch')
    manifest = dict(schema=SCHEMA, model=MODEL, revision=REVISION, speaker='Sohee', language='Korean',
        instruct=INSTRUCT, sampling=SAMPLING, seeds=list(SEEDS), fixtures=list(FIXTURES),
        profiles=list(PROFILES), repetitions=args.repeats, trial_order=trial_order(args.repeats),
        packages=packages, model_source_sha256=source_hashes, model_weights_sha256=weights,
        script_sha256=file_hash(__file__), model_load_seconds=load_seconds,
        call_timeout_seconds=args.call_timeout, total_timeout_seconds=args.total_timeout,
        dtype='float32', attention='sdpa', torch_threads=torch.get_num_threads(),
        torch_interop_threads=torch.get_num_interop_threads(), platform=platform.platform(),
        cpuinfo=Path('/proc/cpuinfo').read_text(), cpu_count=os.cpu_count(),
        generation_timer_scope='generate_custom_voice including equal observation/hash hooks; excludes WAV/codec save',
        production_changed=False, network_required_during_inference=False)
    atomic_json(output / 'manifest.json', manifest)

    def call_timeout(*unused):
        raise TimeoutError('public_history_benchmark_call_timeout')

    signal.signal(signal.SIGALRM, call_timeout)
    rows = []
    def next_alarm_seconds():
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError('public_history_benchmark_total_timeout')
        return max(1, min(args.call_timeout, math.ceil(remaining)))

    # One unmeasured baseline synthesis before balanced paired trials.
    torch.manual_seed(SEEDS[0])
    warmup_started = time.perf_counter()
    try:
        signal.alarm(next_alarm_seconds())
        with torch.inference_mode():
            model.generate_custom_voice(text=FIXTURES[0], language='Korean', speaker='Sohee',
                instruct=INSTRUCT, **SAMPLING)
    except Exception as error:
        failure = summarize([], args.repeats)
        failure.update(status='warmup_failed', error_type=type(error).__name__, error=str(error))
        atomic_json(output / 'rows.json', [])
        atomic_json(output / 'summary.json', failure)
        print(json.dumps(failure), flush=True)
        return 1
    finally:
        signal.alarm(0)
    atomic_json(output / 'warmup.json', dict(status='ok',
        seconds=time.perf_counter() - warmup_started, included_in_trial_timing=False))
    for repeat, fixture, profile in trial_order(args.repeats):
        observed = {}
        row = dict(repeat=repeat, fixture=fixture, profile=profile, seed=SEEDS[fixture],
            text=FIXTURES[fixture], sampling=SAMPLING, status='error')
        try:
            signal.alarm(next_alarm_seconds())
            torch.manual_seed(SEEDS[fixture])
            with observe_model(model, profile == 'inner_history_off', observed), torch.inference_mode():
                started = time.perf_counter()
                waves, sr = model.generate_custom_voice(text=FIXTURES[fixture], language='Korean',
                    speaker='Sohee', instruct=INSTRUCT, **SAMPLING)
                elapsed = time.perf_counter() - started
            require(len(waves) == 1 and sr == 24000, 'audio_shape_or_rate_mismatch')
            wave = np.asarray(waves[0], dtype='<f4').reshape(-1)
            require(wave.size > 0, 'empty_audio')
            valid = bool(np.isfinite(wave).all() and np.max(np.abs(wave)) < 1.0
                and np.sqrt(np.mean(wave.astype(np.float64) ** 2)) > 1e-5)
            stem = f'{profile}-f{fixture}-r{repeat}'
            sf.write(output / (stem + '.wav'), wave, sr, subtype='FLOAT')
            np.save(output / (stem + '.codes.npy'), observed.pop('codec_array'))
            saved, saved_sr = sf.read(output / (stem + '.wav'), dtype='float32')
            require(saved_sr == sr and saved.shape == wave.shape
                and hashlib.sha256(saved.astype('<f4').tobytes()).hexdigest()
                == hashlib.sha256(wave.tobytes()).hexdigest(), 'float_wav_roundtrip_mismatch')
            require(observed.get('inner_calls', 0) > 0, 'inner_hook_not_exercised')
            require(observed.get('inner_history_disabled_calls')
                == (observed['inner_calls'] if profile == 'inner_history_off' else 0), 'inner_flag_scope_mismatch')
            row.update(status='ok', generate_custom_voice_seconds=elapsed,
                decode_and_observation_overhead_seconds=elapsed - observed['generation_only_seconds'],
                sample_rate=sr, samples=len(wave), audio_seconds=len(wave) / sr,
                pcm_f32_sha256=hashlib.sha256(wave.tobytes()).hexdigest(),
                wav_file_sha256=file_hash(output / (stem + '.wav')), wav_file=stem + '.wav',
                codec_file=stem + '.codes.npy', signal_valid=valid, **observed, **rss_snapshot())
        except Exception as error:
            observed.pop('codec_array', None)
            row.update(error_type=type(error).__name__, error=str(error), **observed, **rss_snapshot())
        finally:
            signal.alarm(0)
        rows.append(row)
        atomic_json(output / 'rows.json', rows)
        atomic_json(output / 'summary.json', summarize(rows, args.repeats))
        print(json.dumps({k: row.get(k) for k in ('profile', 'fixture', 'repeat', 'status',
            'generate_custom_voice_seconds', 'audio_seconds', 'natural_eos', 'pcm_f32_sha256', 'error')},
            ensure_ascii=False), flush=True)
        if row['status'] != 'ok':
            return 1
    result = summarize(rows, args.repeats)
    result['benchmark_wall_seconds'] = time.perf_counter() - benchmark_started
    atomic_json(output / 'summary.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['exact_pcm_and_codec_equal'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
