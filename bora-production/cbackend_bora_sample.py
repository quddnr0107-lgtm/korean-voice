"""Bounded public Bora-style listening sample using the pinned community C backend.

Read the production styles, DSP and sampling recipe without rendering private
content. C's code predictor is greedy and its BF16 path differs from PyTorch
FP32; this is a listening comparison, never a production-quality approval.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

import cbackend_sohee_benchmark as backend

FIXTURE_SCHEMA = 'cbackend-bora-public-fixtures/v1'
REPORT_SCHEMA = 'cbackend-bora-public-sample/v1'
MAX_UNITS = 4
MAX_SECONDS = 300
SAMPLE_RATE = 24000
ROOT = Path(__file__).resolve().parents[1]
# Observed hashes of the same pinned official assets in public run 43377295943.
WEIGHTS_SHA256 = '38b1d5971bdbd982b561cccec982669a53b0537c3cf5e9bd4778ed07bb2f5137'
CODEC_SHA256 = '836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258'


def require(ok):
    if not ok:
        raise ValueError('public_cbackend_bora_sample_invalid')


def production_recipe():
    # These modules have no model loads or renderer construction on import.
    import fp32_renderer as production
    import pilot_render as pilot
    require(production.MODEL_REPOSITORY == backend.MODEL
        and production.MODEL_REVISION == backend.MODEL_REVISION
        and set(pilot.STYLES) == set(pilot.FILTERS) == {'explain', 'reading'})
    sampling = copy.deepcopy(production.SAMPLING)
    require(sampling == dict(non_streaming_mode=True, do_sample=True,
        temperature=0.8, top_p=0.9, top_k=50, repetition_penalty=1.05,
        subtalker_dosample=True, subtalker_temperature=0.9,
        subtalker_top_p=1.0, subtalker_top_k=50))
    return dict(styles=copy.deepcopy(pilot.STYLES),
        filters=copy.deepcopy(pilot.FILTERS), sampling=sampling,
        seed=production.SEEDS[0], token_cap=backend.TOKEN_CAP,
        source_sha256={name: backend.file_hash(Path(__file__).with_name(name))
            for name in ('pilot_render.py', 'fp32_renderer.py')})


def validate_fixtures(value):
    require(isinstance(value, dict) and set(value) == {'schema', 'units'}
        and value['schema'] == FIXTURE_SCHEMA
        and isinstance(value['units'], list) and 1 <= len(value['units']) <= MAX_UNITS)
    seen = set()
    for unit in value['units']:
        require(isinstance(unit, dict) and set(unit) == {'id', 'mode', 'text'})
        name, mode, text = unit['id'], unit['mode'], unit['text']
        require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name)
            and name not in seen and mode in ('explain', 'reading')
            and isinstance(text, str) and text == text.strip() and 1 <= len(text) <= 500
            and all(ord(c) >= 32 or c in '\n\t' for c in text))
        seen.add(name)
    return copy.deepcopy(value['units'])


def native_command(binary, model, raw, unit, recipe):
    require(unit['mode'] in recipe['styles'])
    sampling = recipe['sampling']
    return [str(binary), '-d', str(model), '-s', 'sohee', '-l', 'Korean',
        '-j', '4', '--text', unit['text'], '--seed', str(recipe['seed']),
        '--temperature', str(sampling['temperature']),
        '--top-k', str(sampling['top_k']), '--top-p', str(sampling['top_p']),
        '--rep-penalty', str(sampling['repetition_penalty']),
        '--max-tokens', str(recipe['token_cap']), '--eos-strategy', 'off',
        '--instruct', recipe['styles'][unit['mode']], '-o', str(raw)]


def filter_command(ffmpeg, raw, filtered, mode, recipe):
    require(mode in recipe['filters'])
    return [str(ffmpeg), '-nostdin', '-v', 'error', '-y', '-i', str(raw),
        '-af', recipe['filters'][mode], '-ar', str(SAMPLE_RATE), '-ac', '1',
        '-c:a', 'pcm_f32le', str(filtered)]


def sampling_comparison(recipe):
    return dict(production_requested=recipe['sampling'],
        talker_applied={k: recipe['sampling'][k] for k in
            ('temperature', 'top_p', 'top_k', 'repetition_penalty')},
        subtalker_actual='greedy_argmax',
        unsupported_production_controls=['subtalker_dosample', 'subtalker_temperature',
            'subtalker_top_p', 'subtalker_top_k', 'non_streaming_mode'],
        seed_implementation='Native C RNG; seed value matches production, RNG does not',
        backend_decode='C backend internally decodes chunks; no Python non_streaming_mode control',
        source_evidence='qwen_tts_code_predictor.c at pinned revision: qwen_argmax_matvec_*',
        sampling_equivalence_claimed=False, fp32_equivalence_claimed=False)


def summarize(units, rows, recipe):
    require(isinstance(rows, list) and len(rows) <= len(units)
        and [r['id'] for r in rows] == [u['id'] for u in units[:len(rows)]])
    for row in rows:
        require(row.get('mode') in ('explain', 'reading'))
        for key in ('tts_process_wall_seconds', 'raw_audio_seconds', 'processed_audio_seconds'):
            if key in row:
                require(type(row[key]) in (int, float) and math.isfinite(row[key]) and row[key] > 0)
    complete = len(rows) == len(units) and all(
        r.get('natural_eos_observed') is True and r.get('eos_assistance_enabled') is False
        and r.get('decoded_pcm_frame_coverage_verified') is True
        and r.get('raw_signal', {}).get('blocked') is False
        and r.get('processed_signal', {}).get('blocked') is False
        and r.get('postprocessing_complete') is True for r in rows)
    wall = sum(r.get('tts_process_wall_seconds', 0) for r in rows)
    raw = sum(r.get('raw_audio_seconds', 0) for r in rows)
    processed = sum(r.get('processed_audio_seconds', 0) for r in rows)
    return dict(schema=REPORT_SCHEMA, source_repository=backend.SOURCE_REPO,
        source_revision=backend.SOURCE_SHA, model=backend.MODEL,
        model_revision=backend.MODEL_REVISION, speaker='Sohee', language='Korean',
        device='cpu', threads=4, max_units=MAX_UNITS, execution_budget_seconds=MAX_SECONDS,
        quantization_enabled=False,
        dtype_scope='Native BF16 checkpoint storage and C kernels; codec F32; different from PyTorch FP32',
        recipe=recipe, sampling_comparison=sampling_comparison(recipe), units=units, rows=rows,
        complete_natural_eos_signal_and_postprocessing=complete,
        total_tts_process_wall_seconds=round(wall, 6),
        total_raw_audio_seconds=round(raw, 6), total_processed_audio_seconds=round(processed, 6),
        tts_seconds_per_raw_audio_second=round(wall / raw, 5) if complete and raw else None,
        process_time_includes_each_model_load=True, public_fixtures_only=True,
        asr_performed=False, retries_performed=False, human_listening_verified=False,
        production_eligible=False, full_course_forecast_validated=False,
        scope='Public authored listening samples only. Same model, speaker, instruction strings and DSP recipe; different sampling and numerical implementation. No production writes or voice-quality equivalence claim.')


def audio(path):
    import soundfile as sf
    from pilot_render import signal_stats
    require(path.is_file() and not path.is_symlink()
        and 0 < path.stat().st_size <= 16 * 1024 * 1024)
    wave, sr = sf.read(path, dtype='float32')
    require(sr == SAMPLE_RATE)
    stats = signal_stats(wave, sr)
    return wave, stats


def remaining(deadline):
    value = deadline - time.perf_counter()
    require(value > 0)
    return min(MAX_SECONDS, value)


def public_output_path(path, source, model):
    path = Path(path)
    require(not path.exists() and not path.is_symlink())
    output = path.resolve()
    # Only a fresh experiment directory, outside source/model/production trees.
    require(all(output != p and not output.is_relative_to(p) for p in (ROOT, source, model)))
    require(not any(p.is_symlink() for p in (path, *path.parents)))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('source', 'model', 'fixtures', 'output'):
        parser.add_argument('--' + flag, required=True)
    args = parser.parse_args()
    from download_models import QWEN_REQUIRED, require_keyless, verify_assets
    require_keyless()
    recipe = production_recipe()
    fixture_path = Path(args.fixtures)
    require(fixture_path.is_file() and not fixture_path.is_symlink()
        and 0 < fixture_path.stat().st_size <= 16000)
    units = validate_fixtures(json.loads(fixture_path.read_text()))
    source, model = Path(args.source).resolve(), Path(args.model).resolve()
    require(subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'],
        text=True, timeout=15).strip() == backend.SOURCE_SHA)
    # Require the compiled source inputs to be clean, ignoring the built binary.
    require(not subprocess.check_output(['git', '-C', str(source), 'diff', 'HEAD', '--'],
        text=True, timeout=15).strip())
    verify_assets(model, QWEN_REQUIRED)
    config = json.loads((model / 'config.json').read_text())
    require(config.get('tts_model_type') == 'custom_voice' and config.get('tts_model_size') == '1b7'
        and backend.file_hash(model / 'model.safetensors') == WEIGHTS_SHA256
        and backend.file_hash(model / 'speech_tokenizer/model.safetensors') == CODEC_SHA256)
    binary, ffmpeg = source / 'qwen_tts', shutil.which('ffmpeg')
    require(binary.is_file() and not binary.is_symlink() and ffmpeg)
    output = public_output_path(args.output, source, model)
    output.mkdir(parents=True)
    # Native processes receive no inherited service credentials.
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'TMPDIR')
        if key in os.environ}
    env.update(OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', ORT_DISABLE_TELEMETRY='1')
    cpuinfo = Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else ''
    metadata = dict(binary_sha256=backend.file_hash(binary),
        model_weights_sha256=WEIGHTS_SHA256, codec_weights_sha256=CODEC_SHA256,
        fixture_manifest_sha256=backend.file_hash(fixture_path),
        script_sha256=backend.file_hash(Path(__file__)), cpu_count=os.cpu_count(),
        platform=platform.platform(),
        cpu_model=next((line.split(':', 1)[1].strip() for line in cpuinfo.splitlines()
            if line.startswith('model name')), 'unknown'),
        cpu_flags=next((line.split(':', 1)[1].strip() for line in cpuinfo.splitlines()
            if line.startswith('flags')), 'unknown'))
    rows = []
    deadline = time.perf_counter() + MAX_SECONDS
    for unit in units:
        row = dict(id=unit['id'], mode=unit['mode'], seed=recipe['seed'], postprocessing_complete=False)
        raw = output / (unit['id'] + '-raw.wav')
        filtered = output / (unit['id'] + '-filtered.wav')
        processed = output / (unit['id'] + '-processed.wav')
        log_path = output / (unit['id'] + '.log')
        try:
            started = time.perf_counter()
            result = subprocess.run(native_command(binary, model, raw, unit, recipe),
                cwd=source, env=env, capture_output=True, text=True, timeout=remaining(deadline))
            elapsed = time.perf_counter() - started
            log = result.stdout + '\n' + result.stderr
            log_path.write_text(log)
            row.update(tts_process_wall_seconds=round(elapsed, 6), native_returncode=result.returncode,
                native_log_file=log_path.name, native_log_sha256=backend.file_hash(log_path))
            require(result.returncode == 0)
            wave, stats = audio(raw)
            evidence = backend.eos_evidence(log)
            row.update(raw_file=raw.name, raw_sha256=backend.file_hash(raw),
                raw_audio_seconds=stats['seconds'], raw_signal=stats, **evidence)
            coverage = len(wave) == (evidence['generated_frames'] or -1) * 1920
            row['decoded_pcm_frame_coverage_verified'] = coverage
            require(evidence['natural_eos_observed'] and coverage and not stats['blocked'])
            started = time.perf_counter()
            subprocess.run(filter_command(ffmpeg, raw, filtered, unit['mode'], recipe),
                env=env, check=True, capture_output=True, timeout=remaining(deadline))
            wave, filtered_stats = audio(filtered)
            require(not filtered_stats['blocked'])
            from pilot_render import trimmed_faded
            import soundfile as sf
            wave, trim = trimmed_faded(wave, SAMPLE_RATE)
            sf.write(processed, wave, SAMPLE_RATE, subtype='FLOAT')
            checked, stats = audio(processed)
            require(not stats['blocked'] and len(checked) == len(wave))
            row.update(processed_file=processed.name, processed_sha256=backend.file_hash(processed),
                filtered_file=filtered.name, filtered_sha256=backend.file_hash(filtered),
                processed_audio_seconds=stats['seconds'], processed_signal=stats, trim=trim,
                postprocess_wall_seconds=round(time.perf_counter() - started, 6),
                postprocessing_complete=True)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            row['error_type'] = type(error).__name__
            if isinstance(error, subprocess.TimeoutExpired):
                row['execution_budget_exceeded'] = True
                log_path.write_bytes((error.stdout or b'') if isinstance(error.stdout, bytes)
                    else (error.stdout or '').encode())
                with log_path.open('ab') as stream:
                    stream.write((error.stderr or b'') if isinstance(error.stderr, bytes)
                        else (error.stderr or '').encode())
        rows.append(row)
        (output / (unit['id'] + '-receipt.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2))
        report = summarize(units, rows, recipe)
        report.update(metadata)
        (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)
        if row.get('error_type'):
            break
    if not report['complete_natural_eos_signal_and_postprocessing']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
