"""Public FP32 Kim Bora ablation: compile ONLY five Code Predictor MLPs.

No voice, sampling, hidden-state collection, Talker, attention or vocoder changes.
Compiled warmup is excluded from synthesis timings and recorded separately.
"""
from __future__ import annotations
import os
os.environ['TORCHINDUCTOR_FREEZING'] = '1'
import argparse
import contextlib
import hashlib
import importlib.metadata
import inspect
import json
import math
from pathlib import Path
import platform
import signal
import statistics
import time

import python_history_bench as b

SCHEMA = 'kim-bora-original-cp-five-mlp-compile/v1'
PROFILES = ('baseline', 'cp_mlp_compiled')


def trial_order(repeats):
    b.require(type(repeats) is int and 1 <= repeats <= 3, 'repeat_bounds')
    return [(repeat, fixture, profile)
        for repeat in range(repeats) for fixture in range(len(b.FIXTURES))
        for profile in (PROFILES if (repeat + fixture) % 2 == 0 else PROFILES[::-1])]


def select_modules(layers, originals, compiled, profile):
    b.require(len(layers) == len(originals) == len(compiled) == 5, 'five_mlp_contract')
    b.require(profile in PROFILES, 'unknown_profile')
    chosen = originals if profile == 'baseline' else compiled
    for layer, module in zip(layers, chosen):
        layer.mlp = module
    b.require(all(layer.mlp is expected for layer, expected in zip(layers, chosen)),
        'module_switch_failed')


def verify_compiled_modules(originals, compiled):
    b.require(len(originals) == len(compiled) == 5, 'five_mlp_contract')
    b.require(all(old is not new and getattr(new, '_orig_mod', None) is old
        for old, new in zip(originals, compiled)), 'compiled_wrapper_not_original_module')
    b.require(all([id(p) for p in old.parameters()] == [id(p) for p in new.parameters()]
        for old, new in zip(originals, compiled)), 'compiled_parameter_identity_changed')


def compiler_snapshot(torch):
    from torch._dynamo.utils import counters
    from torch._inductor import metrics
    result = {'dynamo': {str(group): {str(k): int(v) for k, v in values.items()
        if isinstance(v, (int, bool))} for group, values in counters.items()}}
    result['inductor'] = {name: int(getattr(metrics, name)) for name in (
        'generated_kernel_count', 'generated_cpp_vec_kernel_count', 'cpp_to_dtype_count',
        'num_bytes_accessed', 'num_matches_for_scatter_upon_const_tensor')
        if isinstance(getattr(metrics, name, None), (int, bool))}
    return result


def proof_from_snapshots(before, after, warm_calls):
    graphs = after.get('dynamo', {}).get('stats', {}).get('unique_graphs', 0) - before.get('dynamo', {}).get('stats', {}).get('unique_graphs', 0)
    kernels = after.get('inductor', {}).get('generated_kernel_count', 0) - before.get('inductor', {}).get('generated_kernel_count', 0)
    return dict(new_unique_graphs=graphs, new_generated_kernels=kernels,
        compiled_mlp_warm_calls=list(warm_calls),
        all_five_wrappers_exercised=all(count > 0 for count in warm_calls) and len(warm_calls) == 5,
        actual_compilation_verified=graphs > 0 and kernels > 0
            and len(warm_calls) == 5 and all(count > 0 for count in warm_calls))


def compile_events(snapshot):
    return (snapshot.get('dynamo', {}).get('stats', {}).get('unique_graphs', 0),
        snapshot.get('inductor', {}).get('generated_kernel_count', 0))


def parameter_manifest(originals):
    result = []
    for i, mlp in enumerate(originals):
        for name, tensor in mlp.named_parameters():
            array = tensor.detach().cpu().contiguous().numpy()
            result.append(dict(name=f'talker.code_predictor.model.layers.{i}.mlp.{name}',
                dtype=str(array.dtype), shape=list(array.shape),
                sha256=hashlib.sha256(memoryview(array).cast('B')).hexdigest()))
    return result


def source_inventory(cache):
    results = []
    for path in sorted(cache.rglob('*')):
        if not path.is_file() or path.suffix not in ('.py', '.cpp', '.h') or path.stat().st_size > 8 * 1024 * 1024:
            continue
        text = path.read_text(errors='replace')
        results.append(dict(path=str(path.relative_to(cache)), bytes=path.stat().st_size,
            sha256=b.file_hash(path), cpp_packed_gemm_mentions=text.count('cpp_packed_gemm'),
            cpp_fused_mentions=text.count('cpp_fused'),
            extern_kernels_mentions=text.count('extern_kernels'),
            inductor_entry_mentions=text.count('inductor_entry_impl')))
    return results


def summarize(rows, repeats, proof, compile_seconds, parameter_equal=True):
    expected = set(trial_order(repeats))
    actual = {(row['repeat'], row['fixture'], row['profile']) for row in rows}
    b.require(len(rows) == len(actual) and actual <= expected, 'unexpected_or_duplicate_trial')
    pairs = []
    for repeat in range(repeats):
        for fixture in range(len(b.FIXTURES)):
            pair = {r['profile']: r for r in rows if r['repeat'] == repeat and r['fixture'] == fixture}
            if set(pair) != set(PROFILES):
                continue
            old, new = (pair[name] for name in PROFILES)
            valid = all(r.get('status') == 'ok' and r.get('natural_eos') is True
                and r.get('signal_valid') is True for r in (old, new))
            equal = valid and all(old.get(k) == new.get(k) for k in (
                'codec_sha256', 'codec_shape', 'pcm_f32_sha256', 'samples', 'sample_rate'))
            pairs.append(dict(repeat=repeat, fixture=fixture, valid=valid,
                exact_pcm_and_codec_equal=equal))
    totals = {}
    for profile in PROFILES:
        per_repeat = []
        for repeat in range(repeats):
            selected = [r for r in rows if r['profile'] == profile and r['repeat'] == repeat]
            if len(selected) == len(b.FIXTURES) and all(r.get('status') == 'ok' for r in selected):
                times = [r['generate_custom_voice_seconds'] for r in selected]
                b.require(all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in times),
                    'invalid_measured_time')
                per_repeat.append(sum(times))
        totals[profile] = dict(two_fixture_seconds_per_repeat=per_repeat,
            median_two_fixture_seconds=statistics.median(per_repeat) if len(per_repeat) == repeats else None)
    stability = []
    for fixture in range(len(b.FIXTURES)):
        baseline = [r for r in rows if r['profile'] == 'baseline' and r['fixture'] == fixture]
        stability.append(dict(fixture=fixture, complete=len(baseline) == repeats,
            baseline_repeat_pcm_equal=len(baseline) == repeats and len({r.get('pcm_f32_sha256') for r in baseline}) == 1,
            baseline_repeat_codec_equal=len(baseline) == repeats and len({r.get('codec_sha256') for r in baseline}) == 1))
    complete = actual == expected and len(pairs) == repeats * len(b.FIXTURES)
    exact = complete and all(p['exact_pcm_and_codec_equal'] for p in pairs) and all(
        s['complete'] and s['baseline_repeat_pcm_equal'] and s['baseline_repeat_codec_equal'] for s in stability)
    no_timed_compilation = all(r.get('compiler_events_before') == r.get('compiler_events_after')
        for r in rows if r.get('status') == 'ok')
    valid_speed = exact and proof.get('actual_compilation_verified') is True and parameter_equal and no_timed_compilation
    old, new = (totals[name]['median_two_fixture_seconds'] for name in PROFILES)
    saved = old - new if valid_speed and old is not None and new is not None else None
    return dict(schema=SCHEMA, complete=complete, exact_pcm_and_codec_equal=exact,
        compiler_proof=proof, no_compilation_during_timed_trials=no_timed_compilation,
        original_fp32_parameter_bytes_unchanged=parameter_equal,
        pairs=pairs, baseline_repeat_stability=stability, totals=totals,
        compile_setup_and_warmup_seconds=compile_seconds,
        observed_time_reduction_pct=(1 - new / old) * 100 if valid_speed and old and new else None,
        saved_seconds_per_two_fixture_batch=saved,
        warmup_break_even_two_fixture_batches=math.ceil(compile_seconds / saved)
            if saved is not None and saved > 0 else None,
        adoption_decision='public_exact_output_and_timing_screen_only' if valid_speed else 'do_not_adopt',
        production_eligible=False, asr_performed=False, human_listening_verified=False,
        scope='Only five Code Predictor MLPs compiled; two short public original Kim Bora fixtures. '
            'Compile/warmup and load excluded from synthesis times. No full-course forecast. '
            'Compiled graph verification does not prove a packed GEMM backend was selected; see source inventory/counters.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--call-timeout', type=int, default=180)
    parser.add_argument('--compile-timeout', type=int, default=420)
    parser.add_argument('--total-timeout', type=int, default=1500)
    args = parser.parse_args(argv)
    started = time.perf_counter(); deadline = started + args.total_timeout
    trial_order(args.repeats)
    b.require(30 <= args.call_timeout <= 300 and 60 <= args.compile_timeout <= 600
        and 60 <= args.total_timeout <= 1500, 'timeout_bounds')
    b.require(not any(os.environ.get(name) for name in (
        'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')), 'private_keys_forbidden')
    output, model_dir = Path(args.output).resolve(), Path(args.model).resolve()
    b.require(not output.exists(), 'output_must_be_fresh'); output.mkdir(parents=True)
    cache = output.parent / (output.name + '-inductor-cache')
    b.require(not cache.exists(), 'compiler_cache_must_be_fresh')
    cache.mkdir()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        TORCHINDUCTOR_FREEZING='1', TORCHINDUCTOR_CACHE_DIR=str(cache),
        OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4', MKL_NUM_THREADS='4')
    rows = []; proof = dict(actual_compilation_verified=False); compile_seconds = 0.0
    def save_failure(error, phase):
        result = summarize(rows, args.repeats, proof, compile_seconds)
        result.update(status=phase + '_failed', error_type=type(error).__name__, error=str(error),
            benchmark_wall_seconds=time.perf_counter() - started)
        b.atomic_json(output / 'rows.json', rows); b.atomic_json(output / 'summary.json', result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 1
    try:
        packages = {name: importlib.metadata.version(name) for name in ('torch', 'qwen-tts', 'transformers', 'numpy', 'soundfile')}
        b.require(packages['torch'] == '2.8.0+cpu' and packages['qwen-tts'] == '0.1.1'
            and packages['transformers'] == '4.57.3', 'pinned_package_mismatch')
        cfg = json.loads((model_dir / 'config.json').read_text())
        b.require(cfg.get('tts_model_type') == 'custom_voice' and cfg.get('tts_model_size') == '1b7',
            'model_architecture_mismatch')
        weights = {name: b.file_hash(model_dir / name) for name in (
            'model.safetensors', 'speech_tokenizer/model.safetensors')}
        b.require(weights['model.safetensors'] == b.MODEL_WEIGHTS_SHA256
            and weights['speech_tokenizer/model.safetensors'] == b.CODEC_WEIGHTS_SHA256,
            'pinned_weight_hash_mismatch')
        import numpy as np
        import soundfile as sf
        import torch
        import torch._dynamo.config as dynamo_config
        import torch._inductor.config as inductor_config
        import qwen_tts.core.models.modeling_qwen3_tts as model_module
        import qwen_tts.inference.qwen3_tts_model as wrapper_module
        from qwen_tts import Qwen3TTSModel
        b.require(b.file_hash(inspect.getsourcefile(model_module)) == b.MODEL_CODE_SHA256
            and b.file_hash(inspect.getsourcefile(wrapper_module)) == b.WRAPPER_CODE_SHA256,
            'pinned_model_source_mismatch')
        dynamo_config.suppress_errors = False
        inductor_config.cpp_wrapper = True
        b.require(dynamo_config.suppress_errors is False and inductor_config.cpp_wrapper is True,
            'compiler_config_not_applied')
        torch.set_num_threads(4)
        load_started = time.perf_counter()
        model = Qwen3TTSModel.from_pretrained(str(model_dir), device_map='cpu', dtype=torch.float32,
            attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True)
        model.model.eval(); load_seconds = time.perf_counter() - load_started
        b.require({p.dtype for p in model.model.parameters() if p.is_floating_point()} == {torch.float32},
            'model_dtype_mismatch')
        layers = list(model.model.talker.code_predictor.model.layers)
        b.require(len(layers) == 5, 'code_predictor_layer_count_changed')
        originals = [layer.mlp for layer in layers]
        b.require(all(type(mlp).__name__ == 'Qwen3TTSTalkerTextMLP' for mlp in originals), 'unexpected_mlp_type')
        original_parameters = parameter_manifest(originals)
        b.atomic_json(output / 'parameters_before.json', original_parameters)
        manifest = dict(schema=SCHEMA, model=b.MODEL, revision=b.REVISION, speaker='Sohee', language='Korean',
            instruct=b.INSTRUCT, sampling=b.SAMPLING, seeds=list(b.SEEDS), fixtures=list(b.FIXTURES),
            trial_order=trial_order(args.repeats), packages=packages, dtype='float32', attention='sdpa',
            compile_scope=[f'talker.code_predictor.model.layers.{i}.mlp' for i in range(5)],
            compile_options=dict(mode='max-autotune', dynamic=True, fullgraph=True,
                cpp_wrapper=True, freezing=True, suppress_errors=False),
            script_sha256=b.file_hash(__file__), helper_sha256=b.file_hash(b.__file__),
            model_weights_sha256=weights, model_load_seconds=load_seconds, cpu_threads=torch.get_num_threads(),
            platform=platform.platform(), cpuinfo=Path('/proc/cpuinfo').read_text(),
            call_timeout_seconds=args.call_timeout, compile_timeout_seconds=args.compile_timeout,
            total_timeout_seconds=args.total_timeout, compiler_cache=str(cache), production_changed=False)
        b.atomic_json(output / 'manifest.json', manifest)
    except Exception as error:
        return save_failure(error, 'setup')

    def alarm(*unused):
        raise TimeoutError('cp_compile_benchmark_timeout')
    signal.signal(signal.SIGALRM, alarm)
    def arm(limit):
        remaining = deadline - time.perf_counter()
        if remaining <= 0: raise TimeoutError('cp_compile_benchmark_total_timeout')
        signal.alarm(max(1, min(limit, math.ceil(remaining))))
    def synth(fixture):
        return model.generate_custom_voice(text=b.FIXTURES[fixture], language='Korean', speaker='Sohee',
            instruct=b.INSTRUCT, **b.SAMPLING)
    try:
        arm(args.call_timeout); torch.manual_seed(b.SEEDS[0]); warm_started = time.perf_counter()
        with torch.inference_mode():synth(0)
        baseline_warm_seconds = time.perf_counter() - warm_started
    except Exception as error:
        return save_failure(error, 'baseline_warmup')
    finally:signal.alarm(0)

    compiled = []; hooks = []; warm_calls = [0] * 5
    try:
        before = compiler_snapshot(torch); compile_started = time.perf_counter(); arm(args.compile_timeout)
        with torch.inference_mode():
            compiled = [torch.compile(mlp, mode='max-autotune', dynamic=True, fullgraph=True)
                for mlp in originals]
            verify_compiled_modules(originals, compiled)
            select_modules(layers, originals, compiled, 'cp_mlp_compiled')
            for index, module in enumerate(compiled):
                def observe_warm(module, inputs, i=index):warm_calls[i] += 1
                hooks.append(module.register_forward_pre_hook(observe_warm))
            torch.manual_seed(b.SEEDS[0]); synth(0)
        compile_seconds = time.perf_counter() - compile_started
        after = compiler_snapshot(torch); proof = proof_from_snapshots(before, after, warm_calls)
        b.atomic_json(output / 'compiler_warmup.json', dict(before=before, after=after,
            proof=proof, seconds=compile_seconds, baseline_warmup_seconds=baseline_warm_seconds))
        b.atomic_json(output / 'compiler_sources.json', source_inventory(cache))
        b.require(proof['actual_compilation_verified'], 'no_actual_compilation_evidence')
    except Exception as error:
        if 'compile_started' in locals():compile_seconds = time.perf_counter() - compile_started
        return save_failure(error, 'compiled_warmup')
    finally:
        signal.alarm(0)
        for hook in hooks:hook.remove()
        if len(compiled) == 5:select_modules(layers, originals, compiled, 'baseline')

    for repeat, fixture, profile in trial_order(args.repeats):
        row = dict(repeat=repeat, fixture=fixture, profile=profile, seed=b.SEEDS[fixture],
            text=b.FIXTURES[fixture], status='error'); observed = {}
        try:
            arm(args.call_timeout); select_modules(layers, originals, compiled, profile)
            verify_compiled_modules(originals, compiled)
            counters_before = compiler_snapshot(torch); torch.manual_seed(b.SEEDS[fixture])
            with b.observe_model(model, False, observed), torch.inference_mode():
                timing = time.perf_counter(); waves, sr = synth(fixture); elapsed = time.perf_counter() - timing
            counters_after = compiler_snapshot(torch)
            b.require(len(waves) == 1 and sr == 24000, 'audio_shape_or_rate_mismatch')
            wave = np.asarray(waves[0], dtype='<f4').reshape(-1); b.require(wave.size > 0, 'empty_audio')
            valid = bool(np.isfinite(wave).all() and np.max(np.abs(wave)) < 1
                and np.sqrt(np.mean(wave.astype(np.float64)**2)) > 1e-5)
            stem=f'{profile}-f{fixture}-r{repeat}'
            sf.write(output/(stem+'.wav'),wave,sr,subtype='FLOAT')
            np.save(output/(stem+'.codes.npy'),observed.pop('codec_array'))
            row.update(status='ok', generate_custom_voice_seconds=elapsed, sample_rate=sr,
                samples=len(wave),audio_seconds=len(wave)/sr,signal_valid=valid,
                pcm_f32_sha256=hashlib.sha256(wave.tobytes()).hexdigest(),
                wav_file=stem+'.wav',wav_file_sha256=b.file_hash(output/(stem+'.wav')),
                compiler_events_before=compile_events(counters_before),
                compiler_events_after=compile_events(counters_after),
                compiler_counters_before=counters_before,compiler_counters_after=counters_after,
                **observed,**b.rss_snapshot())
        except Exception as error:
            observed.pop('codec_array',None)
            row.update(error_type=type(error).__name__,error=str(error),**observed)
        finally:signal.alarm(0)
        rows.append(row);b.atomic_json(output/'rows.json',rows)
        b.atomic_json(output/'summary.json',summarize(rows,args.repeats,proof,compile_seconds))
        print(json.dumps({k:row.get(k) for k in ('profile','fixture','repeat','status',
            'generate_custom_voice_seconds','audio_seconds','natural_eos','error')},ensure_ascii=False),flush=True)
        if row['status']!='ok':return 1
    select_modules(layers, originals, compiled, 'baseline')
    after_parameters = parameter_manifest(originals)
    b.atomic_json(output/'parameters_after.json',after_parameters)
    result=summarize(rows,args.repeats,proof,compile_seconds,after_parameters==original_parameters)
    result['benchmark_wall_seconds']=time.perf_counter()-started
    b.atomic_json(output/'compiler_sources.json',source_inventory(cache))
    b.atomic_json(output/'summary.json',result);print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['exact_pcm_and_codec_equal'] and result['no_compilation_during_timed_trials'] else 2


if __name__=='__main__':raise SystemExit(main())
