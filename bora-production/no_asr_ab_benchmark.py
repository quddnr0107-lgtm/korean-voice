"""Paired public speech comparison without loading a recognizer."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import tempfile
import time

from compiled_fp32_renderer import PredictorController, compiler_backend, prepare_torch, verified_predictor
from cpu_precision_benchmark import hardware
from fp32_renderer import (
    MODEL_REQUIRED, MODEL_REVISION, SAMPLING, STYLES, eos_observation,
    quiet_native, require_guard, signal_stats, verify_assets,
)
from no_asr_ab_fixtures import FIXTURES
from no_asr_preview import BRANCH, REPOSITORY, TTS_PACKAGES, assert_no_recognizer_modules, require_keyless

REQUEST = Path(__file__).with_name('no-asr-ab-request.json')
SCHEMA = 'bora-no-asr-ab-shard/v1'
RATE = 24000
GAP_MS = 350
SEED = 5192
TOKEN_CAP = 1100
MODES = ('eager', 'compiled')
SHARD_SIZE = 4


def check(condition, message):
    if not condition:
        raise ValueError(message)


def request():
    return dict(schema='bora-no-asr-ab-request/v1',
        request_id='bora-cpu-no-asr-five-minute-comparison-20261114',
        revision=MODEL_REVISION, chunks=len(FIXTURES), shards=len(FIXTURES)//SHARD_SIZE,
        paired_cpu_threads=4, asr_performed=False, retries=0,
        fixture_sha256=hashlib.sha256(json.dumps(FIXTURES, ensure_ascii=False,
            separators=(',', ':')).encode()).hexdigest())


def validate():
    check(len(FIXTURES) == 12 and len({text for _, text in FIXTURES}) == 12
        and all(style == ('reading' if i % 2 else 'explain')
                and 120 <= len(text) <= 180 and not any(c.isdigit() for c in text)
                for i, (style, text) in enumerate(FIXTURES)), 'public_fixture_shape')
    check(json.loads(REQUEST.read_text()) == request(), 'public_request_pin')


def write_json(path, value):
    temporary = path.with_suffix('.next')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
    temporary.replace(path)


def generate(model, torch, np, text, style, context):
    talker = model.model.talker
    original = talker.generate
    observed = []
    eos_id = int(model.model.config.talker_config.codec_eos_token_id)

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        sequences = result.sequences.tolist()
        check(len(sequences) == 1, 'one_talker_sequence')
        observed.append(sequences[0])
        return result

    talker.generate = capture
    torch.manual_seed(SEED)
    signal.alarm(600)
    try:
        with quiet_native(), torch.inference_mode():
            started = time.perf_counter()
            with context:
                audio, rate = model.generate_custom_voice(
                    text=text, language='Korean', speaker='Sohee', instruct=STYLES[style],
                    **SAMPLING, max_new_tokens=TOKEN_CAP)
            elapsed = time.perf_counter()-started
    finally:
        signal.alarm(0)
        talker.generate = original
    check(len(audio) == len(observed) == 1 and rate == RATE, 'generated_shape')
    wave = np.asarray(audio[0], dtype='<f4')
    natural_eos = eos_observation(observed[0], eos_id, TOKEN_CAP)['natural_eos']
    stats = signal_stats(wave, rate)
    check(natural_eos and stats['blocked'] is False and 0 < len(wave) <= 60*rate,
          'public_audio_signal_or_eos')
    assert_no_recognizer_modules()
    return wave, dict(tts_seconds=elapsed, sample_count=len(wave), natural_eos=natural_eos,
        pcm_sha256=hashlib.sha256(wave.tobytes()).hexdigest(),
        sequence_sha256=hashlib.sha256(json.dumps(observed[0], separators=(',', ':')).encode()).hexdigest())


def render(model_path, output, shard):
    validate()
    require_keyless()
    require_guard()
    check(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
        and os.environ.get('GITHUB_REF') == 'refs/heads/'+BRANCH
        and os.environ.get('GITHUB_EVENT_NAME') == 'push'
        and os.environ.get('GITHUB_WORKFLOW_REF') ==
            REPOSITORY+'/.github/workflows/bora-no-asr-ab.yml@refs/heads/'+BRANCH,
        'public_workflow_context')
    check(0 <= shard < len(FIXTURES)//SHARD_SIZE, 'shard_scope')
    check(not output.exists() and output.parent.is_dir(), 'fresh_output')
    output.mkdir(mode=0o700)
    os.environ.update(OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4',
        TORCHINDUCTOR_COMPILE_THREADS='1', MAX_JOBS='1')
    assets = verify_assets(model_path, MODEL_REQUIRED, MODEL_REVISION)
    check({key: importlib.metadata.version(key) for key in TTS_PACKAGES} == TTS_PACKAGES,
          'pinned_runtime_packages')

    def deadline(*_):
        raise TimeoutError('public_synthesis_call_timeout')

    signal.signal(signal.SIGALRM, deadline)
    counts = dict(backend_compile_attempts=0, backend_graphs_compiled=0,
        compiled_graph_invocations=0, predictor_forward_calls=0)
    rows = []
    waves = {mode: [] for mode in MODES}
    host = hardware()
    cpu = ', '.join(host['cpu_models']) + f" ({host['logical_cpus']} logical CPUs)"
    report = dict(schema=SCHEMA, status='running', shard=shard, cpu=cpu,
        model_revision=MODEL_REVISION, model_assets_sha256=assets, sample_rate=RATE,
        gap_ms=GAP_MS, asr_performed=False, retries=0, rows=rows, counts=counts,
        model_load_seconds=0.0, wrapper_setup_seconds=0.0, synthesis_elapsed_seconds=0.0)
    path = output/'summary.json'
    with tempfile.TemporaryDirectory(prefix='bora-public-ab-cache-', dir=os.environ['RUNNER_TEMP']) as cache:
        os.environ['TORCHINDUCTOR_CACHE_DIR'] = cache
        controller = None
        try:
            started = time.perf_counter()
            with quiet_native():
                torch = prepare_torch()
                import numpy as np
                from qwen_tts import Qwen3TTSModel
                model = Qwen3TTSModel.from_pretrained(str(model_path), device_map='cpu',
                    dtype=torch.float32, attn_implementation='sdpa', low_cpu_mem_usage=True,
                    local_files_only=True)
                model.model.eval()
            report['model_load_seconds'] = time.perf_counter()-started
            check({p.dtype for p in model.model.parameters() if p.is_floating_point()} == {torch.float32}
                and {p.device.type for p in model.model.parameters()} == {'cpu'}, 'original_cpu_fp32')
            assert_no_recognizer_modules()
            target, modeling = verified_predictor(model)
            write_json(path, report)
            batch_started = time.perf_counter()
            start_index = shard*SHARD_SIZE
            for index in range(start_index, start_index+SHARD_SIZE):
                style, source = FIXTURES[index]
                for mode in (MODES if index % 2 == 0 else MODES[::-1]):
                    compiled = mode == 'compiled'
                    if compiled and controller is None:
                        started = time.perf_counter()
                        with quiet_native():
                            controller = PredictorController(target, modeling, torch,
                                compiler_backend(torch), counts)
                        report['wrapper_setup_seconds'] = time.perf_counter()-started
                    before = dict(counts)
                    wave, measured = generate(model, torch, np, source, style,
                        controller.activate() if compiled else nullcontext())
                    row = dict(index=index, mode=mode, style=style, seed=SEED,
                        text_sha256=hashlib.sha256(source.encode()).hexdigest(),
                        compiled_graph_invocations=counts['compiled_graph_invocations']-before['compiled_graph_invocations'],
                        new_graphs=counts['backend_graphs_compiled']-before['backend_graphs_compiled'],
                        compile_attempts=counts['backend_compile_attempts']-before['backend_compile_attempts'],
                        **measured)
                    check((row['compiled_graph_invocations'] > 0) == compiled, 'compiled_execution_evidence')
                    rows.append(row)
                    waves[mode].append(wave)
                    report['synthesis_elapsed_seconds'] = time.perf_counter()-batch_started
                    write_json(path, report)
                    print(json.dumps(row, sort_keys=True), flush=True)
            from no_asr_ab_collect import assemble
            report['audio'] = {mode: assemble(waves[mode], output/f'{mode}.mp3') for mode in MODES}
            assert_no_recognizer_modules()
            report['status'] = 'complete'
            write_json(path, report)
            print(json.dumps(dict(shard=shard, status='complete', calls=len(rows),
                synthesis_elapsed_seconds=report['synthesis_elapsed_seconds'])), flush=True)
        finally:
            if controller is not None:
                controller.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--shard', type=int)
    parser.add_argument('--validate', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    if args.validate:
        validate()
        print('Public comparison request and fixtures verified')
    else:
        render(args.model, args.output, args.shard)


if __name__ == '__main__':
    main()
