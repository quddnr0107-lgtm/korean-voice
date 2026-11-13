"""Six public CPU-only listening trials; no production rendering or publishing.

Build the pinned upstream binary and a separate copy with the accompanying patch
before running. Cases isolate upstream, Talker sampler rules, then sampled CP.
Same numeric seeds do not imply identical random draws or PyTorch output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time

import cbackend_sohee_benchmark as backend
import cbackend_bora_sample as bora

PATCH = Path(__file__).with_name('cpu-lab-20261113-sampling.patch')
PATCH_SHA256 = '0e596420ac2c29dd25755fe35c46181bb1eb7deb9c2f98ebf27a2435c2e71e50'
SOURCE_SHA = backend.SOURCE_SHA
BEFORE = {
    'qwen_tts_sampling.c': '3326deb1e348cf9869cc94310c4e93ba844e4db303395950d07d14136723eba7',
    'qwen_tts_code_predictor.c': '1017b7c412c572674ddaafe5657fae952cf3c24606638cad4ff621d406f2b76f',
}
AFTER = {
    'qwen_tts_sampling.c': 'ed78c6cef894fc57f9f3bf01dfc44777ad5d71926f6a8dc02eff4935b34c68c0',
    'qwen_tts_code_predictor.c': '3efbd5137f322823aca0fdb5505941bdc17745e559236f594ff4c377a1b0e4c9',
}
CASES = ('baseline', 'talker', 'both')
FLAGS = {'baseline': (0, 0), 'talker': (1, 0), 'both': (1, 1)}
HF_FLAG = 'QWEN_CPU_LAB_HF_SAMPLER'
CP_FLAG = 'QWEN_CPU_LAB_CP_SAMPLE'
HF_MARKER = '[cpu-lab] hf_sampler=1 repetition=once topk_before_topp_renormalized=1'
CP_MARKER = '[cpu-lab] cp_sampling=1 temperature=0.9 top_k=50 top_p=1 single_cpu_bf16=1'
TOTAL_SECONDS = 900
CALL_SECONDS = 180
SCHEMA = 'cpu-lab-20261113-public-sampling/v1'


def require(ok):
    if not ok:
        raise ValueError('cpu_lab_public_sampling_invalid')


def validate_source_files(source, *, patched=False):
    source = Path(source)
    expected = AFTER if patched else BEFORE
    for name, sha in expected.items():
        path = source / name
        require(path.is_file() and not path.is_symlink() and backend.file_hash(path) == sha)
    return dict(expected)


def validate_source_tree(source, *, patched=False):
    require(subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'],
        text=True, timeout=15).strip() == SOURCE_SHA)
    changed = subprocess.check_output(['git', '-C', str(source), 'diff', '--name-only', 'HEAD', '--'],
        text=True, timeout=15).splitlines()
    require(set(changed) == (set(AFTER) if patched else set()))
    return validate_source_files(source, patched=patched)


def child_environment(case):
    require(case in CASES)
    # Allowlist excludes credentials and unrelated QWEN_* experimental controls.
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'TMPDIR')
        if key in os.environ}
    hf, cp = FLAGS[case]
    env.update(OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', ORT_DISABLE_TELEMETRY='1')
    env[HF_FLAG], env[CP_FLAG] = str(hf), str(cp)
    return env


def generation_command(binary, model, target, fixture, recipe):
    require(type(fixture) is int and 0 <= fixture < len(backend.FIXTURES))
    local_recipe = dict(recipe, seed=5192 + fixture)
    unit = dict(mode='explain', text=backend.FIXTURES[fixture])
    return bora.native_command(binary, model, target, unit, local_recipe)


def option_evidence(log, case):
    require(isinstance(log, str) and case in CASES)
    hf, cp = FLAGS[case]
    # Markers are emitted exactly once by the active native implementation.
    return dict(native_option_markers_verified=(log.count(HF_MARKER) == hf
        and log.count(CP_MARKER) == cp), hf_sampler_enabled=bool(hf),
        cp_sampling_enabled=bool(cp))


def summarize(rows, recipe):
    require(isinstance(rows, list) and len(rows) <= len(CASES) * len(backend.FIXTURES))
    seen = set()
    for row in rows:
        pair = (row['case'], row['fixture'])
        require(row['case'] in CASES and type(row['fixture']) is int
            and 0 <= row['fixture'] < len(backend.FIXTURES) and pair not in seen
            and row['seed'] == 5192 + row['fixture'])
        seen.add(pair)
        for key in ('process_wall_seconds', 'audio_seconds'):
            if key in row:
                value = row[key]
                require(type(value) in (int, float) and math.isfinite(value) and value > 0)
    def passed(row):
        return all(row.get(k) is True for k in ('natural_eos_observed', 'signal_valid',
            'decoded_pcm_frame_coverage_verified', 'native_option_markers_verified'))
    complete = len(seen) == len(CASES) * len(backend.FIXTURES) and all(map(passed, rows))
    totals = {}
    for case in CASES:
        selected = [row for row in rows if row['case'] == case]
        wall = sum(row.get('process_wall_seconds', 0) for row in selected)
        audio = sum(row.get('audio_seconds', 0) for row in selected)
        valid = len(selected) == len(backend.FIXTURES) and all(map(passed, selected))
        totals[case] = dict(process_wall_seconds=round(wall, 6), audio_seconds=round(audio, 6),
            complete_natural_eos_and_signal=valid,
            process_seconds_per_audio_second=round(wall / audio, 5) if valid and audio else None)
    return dict(schema=SCHEMA, source_repository=backend.SOURCE_REPO, source_revision=SOURCE_SHA,
        model=backend.MODEL, model_revision=backend.MODEL_REVISION, speaker='Sohee', language='Korean',
        device='cpu', threads=4, cases=list(CASES), flags={k: list(v) for k, v in FLAGS.items()},
        public_fixtures=list(backend.FIXTURES), recipe=recipe, rows=rows, totals=totals,
        complete_natural_eos_and_signal=complete, execution_budget_seconds=TOTAL_SECONDS,
        call_timeout_seconds=CALL_SECONDS, process_time_includes_each_model_load=True,
        quantization_enabled=False, dsp_performed=False, asr_performed=False,
        retries_performed=False, human_listening_verified=False, production_eligible=False,
        sampling_rule_changes=[
            'Talker repetition penalty once per token ID instead of once per past occurrence',
            'Talker top-p applied to probabilities renormalized after top-k',
            'Single BF16 CPU code predictor full logits sampled with temperature 0.9, top-k 50, top-p 1.0 instead of argmax'],
        limitations=[
            'Rule changes are experimental; no claim of matching PyTorch random draws, floating-point results or voice quality',
            'The code predictor draws from the native Talker RNG stream; enabling it also changes subsequent RNG draws',
            'No sampled code predictor support for server, batching, GPU or quantized paths',
            'Native EOS policy and numerical kernels still differ from the official Python implementation',
            'One paired trial on two short authored public fixtures; no full-course speed or content-quality conclusion'],
        call_order='Fixture 0 baseline/talker/both; fixture 1 both/talker/baseline')


def _timeout(deadline):
    seconds = deadline - time.perf_counter()
    if seconds <= 0:
        raise TimeoutError('cpu_lab_deadline')
    return min(CALL_SECONDS, seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'patched-source', 'model', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    from download_models import require_keyless, verify_assets, QWEN_REQUIRED
    require_keyless()
    require(PATCH.is_file() and backend.file_hash(PATCH) == PATCH_SHA256)
    original, patched, model = (Path(value).resolve() for value in
        (args.source, args.patched_source, args.model))
    require(original != patched and not original.is_relative_to(patched)
        and not patched.is_relative_to(original))
    original_hashes = validate_source_tree(original)
    patched_hashes = validate_source_tree(patched, patched=True)
    verify_assets(model, QWEN_REQUIRED)
    config = json.loads((model / 'config.json').read_text())
    require(config.get('tts_model_type') == 'custom_voice' and config.get('tts_model_size') == '1b7'
        and backend.file_hash(model / 'model.safetensors') == bora.WEIGHTS_SHA256
        and backend.file_hash(model / 'speech_tokenizer/model.safetensors') == bora.CODEC_SHA256)
    binaries = {'baseline': original / 'qwen_tts', 'experimental': patched / 'qwen_tts'}
    require(all(path.is_file() and not path.is_symlink() for path in binaries.values()))
    output = bora.public_output_path(args.output, original, model)
    require(not output.is_relative_to(patched))
    output.mkdir(parents=True)
    recipe = bora.production_recipe()
    cpuinfo = Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else ''
    metadata = dict(patch_sha256=PATCH_SHA256, source_files_sha256=original_hashes,
        patched_source_files_sha256=patched_hashes,
        binary_sha256={name: backend.file_hash(path) for name, path in binaries.items()},
        script_sha256=backend.file_hash(Path(__file__)),
        model_weights_sha256=bora.WEIGHTS_SHA256, codec_weights_sha256=bora.CODEC_SHA256,
        cpu_count=os.cpu_count(), platform=platform.platform(),
        cpu_model=next((line.split(':', 1)[1].strip() for line in cpuinfo.splitlines()
            if line.startswith('model name')), 'unknown'),
        cpu_flags=next((line.split(':', 1)[1].strip() for line in cpuinfo.splitlines()
            if line.startswith('flags')), 'unknown'))
    for kind, binary in binaries.items():
        caps = subprocess.run([str(binary), '--caps'], capture_output=True, text=True,
            check=True, env=child_environment('baseline'), timeout=30)
        path = output / (kind + '-capabilities.txt')
        path.write_text(caps.stdout + caps.stderr)
    deadline = time.perf_counter() + TOTAL_SECONDS
    rows = []
    for fixture in range(len(backend.FIXTURES)):
        order = CASES if fixture == 0 else tuple(reversed(CASES))
        for case in order:
            basename = f'{case}-{fixture}'
            target, log_path = output / (basename + '.wav'), output / (basename + '.log')
            row = dict(case=case, fixture=fixture, seed=5192 + fixture)
            binary = binaries['baseline' if case == 'baseline' else 'experimental']
            try:
                started = time.perf_counter()
                completed = subprocess.run(generation_command(binary, model, target, fixture, recipe),
                    cwd=binary.parent, env=child_environment(case), capture_output=True,
                    text=True, timeout=_timeout(deadline))
                row['process_wall_seconds'] = round(time.perf_counter() - started, 6)
                log = completed.stdout + '\n' + completed.stderr
                log_path.write_text(log)
                row.update(native_returncode=completed.returncode, log_file=log_path.name,
                    log_sha256=backend.file_hash(log_path), **option_evidence(log, case))
                require(completed.returncode == 0)
                wave, signal = bora.audio(target)
                evidence = backend.eos_evidence(log)
                row.update(wav_file=target.name, wav_sha256=backend.file_hash(target),
                    audio_seconds=signal['seconds'], signal=signal, signal_valid=not signal['blocked'],
                    decoded_pcm_frame_coverage_verified=(len(wave) == (evidence['generated_frames'] or -1) * 1920),
                    **evidence)
            except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
                row['error_type'] = type(error).__name__
                if isinstance(error, subprocess.TimeoutExpired):
                    def raw(value):
                        return value if isinstance(value, bytes) else (value or '').encode()
                    log_path.write_bytes(raw(error.stdout) + b'\n' + raw(error.stderr))
                    row.update(execution_budget_exceeded=True, log_file=log_path.name,
                        log_sha256=backend.file_hash(log_path))
            rows.append(row)
            (output / (basename + '-receipt.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2))
            report = summarize(rows, recipe)
            report.update(metadata)
            (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(row, sort_keys=True), flush=True)
            if row.get('error_type'):
                raise SystemExit(1)
    if not report['complete_natural_eos_and_signal']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
