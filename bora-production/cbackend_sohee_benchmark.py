"""Bounded public CPU experiment for a pinned community C Qwen3-TTS backend.

Same official 1.7B CustomVoice weights and Sohee, but a different inference
implementation/precision path. This does not approve production audio or prove
quality equivalence to PyTorch FP32. No keys, private sources or publication.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import time

SOURCE_REPO = 'https://github.com/gabriele-mastrapasqua/qwen3-tts.git'
SOURCE_SHA = 'ef339be58a778b062e1c14382347964552eae007'
MODEL = 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'
MODEL_REVISION = '0c0e3051f131929182e2c023b9537f8b1c68adfe'
FIXTURES = (
    '오늘 배운 내용을 잠깐 덮고, 자기 말로 한 번 설명해 보세요.',
    '지금은 조금 느려도 괜찮습니다. 한 문장씩 차분하게 읽어 보세요.',
)
TOKEN_CAP = 1100


def require(ok):
    if not ok:
        raise ValueError('public_cbackend_benchmark_invalid')


def command(binary, model, target, fixture, instruct=''):
    require(type(fixture) is int and 0 <= fixture < len(FIXTURES))
    args = [str(binary), '-d', str(model), '-s', 'sohee', '-l', 'Korean',
        '-j', '4', '--text', FIXTURES[fixture], '--seed', str(5192 + fixture),
        '--temperature', '0.9', '--top-k', '50', '--top-p', '1.0',
        '--rep-penalty', '1.05', '--max-tokens', str(TOKEN_CAP),
        '--eos-strategy', 'off', '-o', str(target)]
    if instruct:
        args += ['--instruct', instruct]
    return args


def eos_evidence(log):
    # Pinned source prints progress without a newline before the EOS message:
    # "  Frame 50/1100 (4.0s audio)...  EOS at frame 56\n".
    # Require the independent Generated count and explicit disabled EOS assist.
    require(isinstance(log, str))
    text = log.replace('\r', '\n')
    pattern = (r'(?m)^[ \t]*(?:Frame [0-9]+/[0-9]+ '
        r'\([0-9]+(?:\.[0-9]+)?s audio\)\.\.\.[ \t]*)?'
        r'EOS at frame ([0-9]+)[ \t]*$')
    frames = re.findall(pattern, text)
    all_mentions = re.findall(r'\bEOS at frame ([0-9]+)', text)
    generated = re.findall(r'(?m)^[ \t]*Generated ([0-9]+) frames '
        r'\([0-9]+(?:\.[0-9]+)?s audio\)[ \t]*$', text)
    strategies = re.findall(r'(?m)^[ \t]*EOS strategy: ([a-z0-9_]+) '
        r'\(suppress=[0-9]+[^\n)]*\)[ \t]*$', text)
    observed = int(frames[0]) if len(frames) == len(all_mentions) == 1 else None
    count = int(generated[0]) if len(generated) == 1 else None
    strategy = strategies[0] if len(strategies) == 1 else None
    complete = (observed is not None and count == observed
        and 0 < observed < TOKEN_CAP and strategy == 'off')
    return dict(eos_frame=observed, generated_frames=count, eos_strategy=strategy,
        natural_eos_observed=complete,
        eos_assistance_enabled=(strategy != 'off') if strategy is not None else None,
        eos_parser_version='pinned-cbackend-inline-progress/v2')


def summarize(rows):
    require(isinstance(rows, list) and len(rows) == len(FIXTURES)
        and [r['fixture'] for r in rows] == list(range(len(FIXTURES))))
    for row in rows:
        for key in ('process_wall_seconds', 'audio_seconds'):
            value = row[key]
            require(type(value) in (int, float) and math.isfinite(value) and value > 0)
        require(type(row['natural_eos_observed']) is bool
            and type(row['signal_valid']) is bool)
    wall = sum(r['process_wall_seconds'] for r in rows)
    audio = sum(r['audio_seconds'] for r in rows)
    complete = all(r['natural_eos_observed'] and r['signal_valid'] for r in rows)
    return dict(schema='cbackend-sohee-public-cpu-benchmark/v1',
        source_repository=SOURCE_REPO, source_revision=SOURCE_SHA,
        model=MODEL, model_revision=MODEL_REVISION, speaker='sohee',
        language='Korean', threads=4, quantization_enabled=False,
        dtype_scope='Native BF16 checkpoint storage with community C kernels; not PyTorch FP32 equivalence',
        public_fixtures=list(FIXTURES), rows=rows,
        complete_natural_eos_and_signal=complete,
        total_process_wall_seconds=round(wall, 3), total_audio_seconds=round(audio, 3),
        process_seconds_per_audio_second=round(wall / audio, 5) if complete else None,
        process_time_includes_each_model_load=True, asr_performed=False,
        retries_performed=False, human_listening_verified=False,
        production_eligible=False, full_course_forecast_validated=False,
        scope='Two short public fixtures on one CPU. EOS assistance disabled. No INT8/INT4, voice conversion or emotion steering. Different backend and numerical path; no bit identity or same quality claim. Historical PyTorch timings are cross-run references only.')


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--instruct', default='')
    args = parser.parse_args()
    require(not any(os.environ.get(k) for k in
        ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')))
    source, model, output = map(lambda p: Path(p).resolve(), (args.source, args.model, args.output))
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    require(head == SOURCE_SHA and not output.exists())
    from download_models import verify_assets, QWEN_REQUIRED
    verify_assets(model, QWEN_REQUIRED)
    config = json.loads((model / 'config.json').read_text())
    require(config.get('tts_model_type') == 'custom_voice' and config.get('tts_model_size') == '1b7')
    binary = source / 'qwen_tts'
    require(binary.is_file() and not binary.is_symlink())
    output.mkdir(parents=True)
    env = dict(os.environ, OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='1',
        MKL_NUM_THREADS='1', ORT_DISABLE_TELEMETRY='1')
    caps = subprocess.run([str(binary), '--caps'], env=env, check=True,
        capture_output=True, text=True, timeout=30)
    (output / 'backend-capabilities.txt').write_text(caps.stdout + caps.stderr)
    import numpy as np
    import soundfile as sf
    rows = []
    for fixture, text in enumerate(FIXTURES):
        target = output / f'cbackend-sohee-{fixture}.wav'
        started = time.perf_counter()
        completed = subprocess.run(command(binary, model, target, fixture, args.instruct),
            cwd=source, env=env, check=True, capture_output=True, text=True, timeout=600)
        elapsed = time.perf_counter() - started
        log = completed.stdout + '\n' + completed.stderr
        (output / f'cbackend-sohee-{fixture}.log').write_text(log)
        require(target.is_file() and 0 < target.stat().st_size < 16 * 1024 * 1024)
        wave, sample_rate = sf.read(target, dtype='float32')
        require(sample_rate == 24000 and wave.ndim == 1 and wave.size > 0)
        valid = bool(np.isfinite(wave).all() and np.max(np.abs(wave)) < 1.0
            and np.sqrt(np.mean(wave.astype('float64') ** 2)) > 1e-5)
        row = dict(fixture=fixture, process_wall_seconds=round(elapsed, 6),
            audio_seconds=round(len(wave) / sample_rate, 6), sample_rate=sample_rate,
            signal_valid=valid, wav_sha256=file_hash(target), **eos_evidence(log))
        rows.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
    report = summarize(rows)
    cpuinfo = Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else ''
    report.update(binary_sha256=file_hash(binary),
        model_weights_sha256=file_hash(model / 'model.safetensors'),
        codec_weights_sha256=file_hash(model / 'speech_tokenizer/model.safetensors'),
        cpu_count=os.cpu_count(), platform=platform.platform(),
        cpu_model=next((l.split(':', 1)[1].strip() for l in cpuinfo.splitlines() if l.startswith('model name')), 'unknown'),
        cpu_flags=next((l.split(':', 1)[1].strip() for l in cpuinfo.splitlines() if l.startswith('flags')), 'unknown'),
        instruction=args.instruct)
    (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
