"""Assemble accepted FP32 units for the existing encrypted lecture-pack/v2.

This module never reads keys, uploads, deletes existing audio, or publishes.
``assemble_lesson`` returns the private metadata and independently decodable
MP3 parts. The protected caller must seal those parts with lecture_pack.seal_part,
set metadata.audio_pack_sha256 to the resulting ciphertext pack hash, and seal
the metadata with lecture_crypto.seal(..., kind='private-metadata').

Input rows are {unit_index, path, receipt}; receipt has already been authenticated
by the caller. Hashes, acceptance, transcript/EOS gates, source coverage, actual
WAV samples and the final MP3 decode are independently checked here. The target
directory must not exist; only a fully verified result is atomically published
to that LOCAL directory. No human listening approval is inferred from these
automatic checks.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / 'lecture-premium' / 'code'
if str(CODE) not in sys.path:
    sys.path.append(str(CODE))
from lecture_crypto import namespace_for
from lecture_pack import MAX_PART_BYTES, MAX_PART_SAMPLES, MAX_PARTS, MAX_TOTAL_SAMPLES, SAMPLE_RATE
from mp3_gapless import repair as repair_gapless

INPUT_RATE = 24000
MAX_UNIT_BYTES = 64 * 1024 * 1024
PLAN_SCHEMA = 'kim-bora-production-paragraph-plan/v1'
PLAN_SCHEMAS = {PLAN_SCHEMA, 'kim-bora-paragraph-plan/v1'}
RECEIPT_SCHEMA = 'bora-fp32-unit-receipt/v1'
ASSEMBLY_SCHEMA = 'bora-fp32-assembly/v1'


class AssemblyError(ValueError):
    """Fixed error codes only; never interpolate manuscripts or raw tool errors."""


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':'), allow_nan=False).encode('ascii')).hexdigest()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _require(ok, code):
    if not ok:
        raise AssemblyError(code)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _integer(value, low=0, high=2**53 - 1):
    return type(value) is int and low <= value <= high


def _utf16_positions(source):
    positions = {0: 0}
    offset = 0
    for cp, char in enumerate(source, 1):
        offset += len(char.encode('utf-16-le')) // 2
        positions[offset] = cp
    return positions, offset


def validate_plan(plan):
    """Verify complete raw source coverage without estimating audio timing."""
    _require(isinstance(plan, dict) and plan.get('schema') in PLAN_SCHEMAS, 'plan_schema')
    lesson_id = plan.get('lesson_id')
    try:
        namespace_for(lesson_id)
    except (TypeError, ValueError, KeyError):
        raise AssemblyError('lesson_scope') from None
    source = plan.get('source')
    _require(isinstance(source, str) and bool(source.strip()), 'plan_source')
    _require(plan.get('source_sha256') == _sha(source.encode('utf-8')), 'plan_source_hash')
    positions, length = _utf16_positions(source)
    _require(plan.get('source_utf16_length') == length, 'plan_utf16_length')
    _require(plan.get('merge_across_paragraphs') is False, 'cross_paragraph_policy')
    paragraphs, units = plan.get('paragraphs'), plan.get('units')
    _require(isinstance(paragraphs, list) and paragraphs and isinstance(units, list) and units,
             'plan_inventory')
    owners, cursor = {}, 0
    for index, paragraph in enumerate(paragraphs):
        _require(isinstance(paragraph, dict), 'paragraph_schema')
        a, b = paragraph.get('start_utf16'), paragraph.get('end_utf16')
        pid = paragraph.get('id')
        _require(pid == f'{lesson_id}:p{index:04d}' and pid not in owners, 'paragraph_identity')
        _require(_integer(a) and _integer(b, 1) and a == cursor and b > a
                 and a in positions and b in positions, 'paragraph_coverage')
        _require(paragraph.get('source') == source[positions[a]:positions[b]], 'paragraph_source')
        owners[pid] = paragraph
        cursor = b
    _require(cursor == length, 'paragraph_coverage')
    cursor, seen, counts, validated = 0, set(), {}, []
    for unit in units:
        _require(isinstance(unit, dict), 'unit_schema')
        uid, pid = unit.get('id'), unit.get('paragraph_id')
        _require(isinstance(pid, str) and pid in owners, 'unit_paragraph')
        local_index = counts.get(pid, 0)
        _require(uid == f'{pid}:u{local_index:03d}' and uid not in seen, 'unit_identity')
        counts[pid] = local_index + 1
        seen.add(uid)
        a, b = unit.get('start_utf16'), unit.get('end_utf16')
        _require(_integer(a) and _integer(b, 1) and a == cursor and b > a
                 and a in positions and b in positions, 'unit_coverage')
        owner = owners[pid]
        _require(owner['start_utf16'] <= a < b <= owner['end_utf16'], 'cross_paragraph_unit')
        raw = source[positions[a]:positions[b]]
        _require(unit.get('source') == raw and unit.get('source_sha256') == _sha(raw.encode()),
                 'unit_source_hash')
        _require(unit.get('mode') in ('reading', 'explain') and isinstance(unit.get('spoken'), str)
                 and bool(unit['spoken'].strip()), 'unit_spoken_mode')
        for field in ('review', 'review_flags'):
            _require(unit.get(field, []) == [], 'unresolved_plan_review')
        _require(unit.get('reading_mode_verified') is not False, 'unverified_reading_mode')
        _require(('start_cp' in unit) == ('end_cp' in unit), 'unit_cp_bounds')
        if 'start_cp' in unit:
            _require(type(unit['start_cp']) is int and type(unit['end_cp']) is int
                     and unit['start_cp'] == positions[a] and unit['end_cp'] == positions[b], 'unit_cp_bounds')
        validated.append((unit, positions[a], positions[b]))
        cursor = b
    _require(cursor == length and set(counts) == set(owners), 'unit_coverage')
    return validated


def _safe_path(path):
    path = Path(path).absolute()
    _require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink_path')
    return path


def validate_entries(plan, entries, expected_recipe_sha256, expected_render_settings_sha256):
    """Return rows in source order; input order itself may be arbitrary."""
    validated = validate_plan(plan)
    _require(_hash(expected_recipe_sha256) and _hash(expected_render_settings_sha256), 'expected_pins')
    _require(isinstance(entries, (list, tuple)) and len(entries) == len(validated), 'entry_count')
    plan_hash = identity(plan)
    by_index, paths = {}, set()
    for row in entries:
        _require(isinstance(row, dict), 'entry_schema')
        index = row.get('unit_index')
        _require(_integer(index, 0, len(validated) - 1) and index not in by_index, 'entry_duplicate_or_index')
        receipt = row.get('receipt')
        _require(isinstance(receipt, dict) and receipt.get('schema') == RECEIPT_SCHEMA, 'receipt_schema')
        unit = validated[index][0]
        _require(receipt.get('status') == 'passed' and receipt.get('production_eligible') is True
                 and receipt.get('blocked') is False and receipt.get('automatic_checks_passed') is True,
                 'receipt_not_accepted')
        _require(receipt.get('review_required') is False and receipt.get('review_flags') == []
                 and receipt.get('review_only') is not True
                 and not any(k in receipt for k in ('review_file', 'review_relative_file', 'review_sha256')),
                 'receipt_review_only')
        _require(receipt.get('natural_eos') is True, 'receipt_missing_natural_eos')
        asr = receipt.get('asr', {})
        _require(isinstance(asr, dict) and asr.get('blocked') is False
                 and asr.get('review_required') is False and type(asr.get('character_errors')) is int
                 and asr['character_errors'] == 0 and asr.get('missing_critical') == [], 'receipt_transcript_gate')
        expected = {
            'lesson_id': plan['lesson_id'], 'unit_index': index, 'unit_id': unit['id'],
            'paragraph_id': unit['paragraph_id'], 'start_utf16': unit['start_utf16'],
            'end_utf16': unit['end_utf16'], 'source_sha256': unit['source_sha256'],
            'spoken_sha256': _sha(unit['spoken'].encode()), 'plan_sha256': plan_hash,
            'recipe_sha256': expected_recipe_sha256,
            'render_settings_sha256': expected_render_settings_sha256, 'mode': unit['mode'],
            'file': 'final.wav', 'sample_rate': INPUT_RATE, 'measurement_kind': 'final_decoded_pcm',
        }
        _require(all(receipt.get(k) == v for k, v in expected.items()), 'receipt_scope_or_pin')
        _require(type(receipt.get('sample_rate')) is int and type(receipt.get('unit_index')) is int
                 and _integer(receipt.get('sample_count'), 1)
                 and _integer(receipt.get('start_utf16')) and _integer(receipt.get('end_utf16'), 1),
                 'receipt_samples')
        for field, value in (('start_cp', validated[index][1]), ('end_cp', validated[index][2])):
            if field in receipt:
                _require(type(receipt[field]) is int and receipt[field] == value, 'receipt_cp_bounds')
        settings = receipt.get('render_settings')
        _require(isinstance(settings, dict) and identity(settings) == expected_render_settings_sha256,
                 'receipt_render_settings_hash')
        required_settings = {'schema': 'bora-python-fp32-production/v1', 'engine': 'qwen-tts-python',
                             'dtype': 'float32', 'threads': 4, 'device': 'cpu', 'attention': 'sdpa',
                             'inference_mode': True, 'sample_rate': INPUT_RATE, 'tempo_factor': 1,
                             'pitch_shift': 0}
        _require(all(settings.get(k) == v for k, v in required_settings.items()), 'not_selected_fp32_profile')
        _require(type(settings['threads']) is int and type(settings['sample_rate']) is int
                 and settings['inference_mode'] is True, 'not_selected_fp32_profile')
        _require(receipt.get('unit_key') == identity(dict(unit=unit,
                 render_settings_sha256=expected_render_settings_sha256)), 'receipt_unit_key')
        _require(isinstance(row.get('path'), (str, os.PathLike)), 'entry_path')
        path = _safe_path(row['path'])
        _require(path.is_file() and 0 < path.stat().st_size <= MAX_UNIT_BYTES, 'unit_file_size')
        _require(path not in paths, 'duplicate_unit_file')
        paths.add(path)
        _require(_hash(receipt.get('sha256')), 'unit_audio_hash')
        by_index[index] = dict(unit_index=index, path=path, receipt=receipt)
    _require(set(by_index) == set(range(len(validated))), 'entry_coverage')
    return validated, [by_index[i] for i in range(len(validated))]


def _child_env():
    return {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT')}


def _run(arguments, data=None):
    try:
        return subprocess.run(arguments, input=data, capture_output=True, check=True,
                              timeout=180, env=_child_env()).stdout
    except (subprocess.SubprocessError, OSError):
        raise AssemblyError('audio_tool_failed') from None


def _metrics(pcm):
    _require(pcm.ndim == 1 and len(pcm) > 0, 'empty_or_nonmono_pcm')
    finite = bool(np.isfinite(pcm).all())
    _require(finite, 'nonfinite_pcm')
    clipped = int(np.count_nonzero(np.abs(pcm) >= .999))
    squares = pcm.astype(np.float64) ** 2
    energy = float(squares.sum())
    rms = math.sqrt(energy / len(pcm))
    frame = max(1, round(SAMPLE_RATE * .02))
    n = len(pcm) // frame
    longest = run = 0
    for silent in (squares[:n * frame].reshape(n, frame).mean(axis=1) < 10**(-50 / 10)) if n else []:
        run = run + 1 if silent else 0
        longest = max(longest, run)
    return dict(finite=True, sample_count=len(pcm), duration_s=len(pcm) / SAMPLE_RATE,
                peak=float(np.max(np.abs(pcm))), clipped_samples=clipped, rms=rms,
                energy=energy, max_silence_s=longest * frame / SAMPLE_RATE)


def _require_signal(metrics):
    _require(metrics['clipped_samples'] == 0, 'clipped_pcm')
    _require(metrics['rms'] >= 1e-5, 'silent_pcm')
    _require(metrics['max_silence_s'] < 3, 'long_silence_pcm')


def _input_pcm(path, receipt, ffmpeg, ffprobe):
    raw = path.read_bytes()
    _require(0 < len(raw) <= MAX_UNIT_BYTES and _sha(raw) == receipt['sha256'], 'unit_audio_hash')
    probe = _run([ffprobe, '-v', 'error', '-show_entries',
                  'stream=codec_type,codec_name,sample_rate,channels:format=format_name',
                  '-of', 'json', '-i', 'pipe:0'], raw)
    try:
        info = json.loads(probe)
        streams = info['streams']
        _require(len(streams) == 1 and streams[0]['codec_type'] == 'audio'
                 and streams[0]['codec_name'].startswith('pcm_') and streams[0]['channels'] == 1
                 and int(streams[0]['sample_rate']) == INPUT_RATE and info['format']['format_name'] == 'wav',
                 'unit_wav_format')
    except (ValueError, TypeError, KeyError, IndexError):
        raise AssemblyError('unit_wav_format') from None
    decoded = _run([ffmpeg, '-nostdin', '-v', 'error', '-i', 'pipe:0', '-map', '0:a:0',
                    '-f', 'f32le', '-ac', '1', 'pipe:1'], raw)
    _require(len(decoded) % 4 == 0 and len(decoded) // 4 == receipt['sample_count'], 'unit_decoded_samples')
    pcm = np.frombuffer(decoded, dtype='<f4')
    _require(np.isfinite(pcm).all() and len(pcm) > 0, 'unit_nonfinite_pcm')
    _require(np.count_nonzero(np.abs(pcm) >= .999) == 0, 'unit_clipped_pcm')
    _require(float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))) >= 1e-5, 'unit_silent_pcm')
    resampled = _run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'f32le', '-ar', str(INPUT_RATE),
                      '-ac', '1', '-i', 'pipe:0', '-af', 'aresample=44100', '-ar', str(SAMPLE_RATE),
                      '-ac', '1', '-f', 'f32le', 'pipe:1'], decoded)
    _require(len(resampled) % 4 == 0, 'resample_alignment')
    final = np.frombuffer(resampled, dtype='<f4')
    _require(abs(len(final) - receipt['sample_count'] * SAMPLE_RATE / INPUT_RATE) <= 1,
             'resample_duration')
    metrics = _metrics(final)
    _require_signal(metrics)
    return resampled, metrics


def _part_boundaries(segments, total, limit):
    # Prefer the next unit start: its preceding pause belongs to the previous
    # part. Unit ends are a fallback at a limit; never cut inside spoken audio.
    starts = sorted({s['start_sample'] for s in segments} | {total})
    ends = sorted({s['end_sample'] for s in segments})
    result = [0]
    while result[-1] < total:
        begin = result[-1]
        candidates = [s for s in starts if begin < s <= min(begin + limit, total)]
        if not candidates:
            candidates = [s for s in ends if begin < s <= min(begin + limit, total)]
        _require(bool(candidates), 'no_safe_unit_boundary_under_part_limit')
        end = max(candidates)
        _require(any(s['start_sample'] < end and s['end_sample'] > begin for s in segments),
                 'silent_only_audio_part')
        result.append(end)
        _require(len(result) - 1 <= MAX_PARTS, 'part_count_limit')
    return result


def _encode_part(pcm, target, expected_samples, ffmpeg):
    _run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(SAMPLE_RATE),
          '-ac', '1', '-i', 'pipe:0', '-map_metadata', '-1', '-c:a', 'libmp3lame',
          '-b:a', '128k', '-write_xing', '1', str(target)], pcm)
    os.chmod(target, 0o600)

    def decode():
        raw = _run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(target), '-f', 'f32le',
                    '-ac', '1', '-ar', str(SAMPLE_RATE), 'pipe:1'])
        _require(len(raw) % 4 == 0, 'part_decode_alignment')
        return np.frombuffer(raw, dtype='<f4')

    decoded, corrected = decode(), False
    if 1 < len(decoded) - expected_samples <= 46:
        try:
            repaired = repair_gapless(target.read_bytes(), expected_samples, len(decoded))
        except ValueError:
            pass  # Unknown encoder/header cases still fail the unchanged gate.
        else:
            target.write_bytes(repaired)
            decoded, corrected = decode(), True
    _require(abs(len(decoded) - expected_samples) <= 1, 'part_decoded_sample_mismatch')
    metrics = _metrics(decoded)
    _require_signal(metrics)
    raw = target.read_bytes()
    _require(0 < len(raw) <= MAX_PART_BYTES and 0 < expected_samples <= MAX_PART_SAMPLES, 'part_bounds')
    return dict(plain_bytes=len(raw), audio_sha256=_sha(raw), validation=metrics,
                decoded_sample_count=len(decoded), decoded_sample_delta=len(decoded) - expected_samples,
                gapless_metadata_corrected=corrected)


def assemble_lesson(plan, entries, output_dir, *, expected_recipe_sha256,
                    expected_render_settings_sha256, title=None, ffmpeg='ffmpeg', ffprobe='ffprobe',
                    unit_gap_ms=0, paragraph_gap_ms=0, max_part_seconds=60):
    """Return metadata and write final MP3 parts to a fresh private directory.

    Root's production recipe uses unit_gap_ms=180, paragraph_gap_ms=350.
    Each gap is before the next unit; no leading or final trailing gap is added.
    Source offsets remain unchanged and timestamps use measured final 44.1k PCM.
    The caller owns encrypted packaging and eventual publication separately.
    """
    validated, ordered = validate_entries(plan, entries, expected_recipe_sha256,
                                          expected_render_settings_sha256)
    _require(_integer(unit_gap_ms, 0, 2000) and _integer(paragraph_gap_ms, 0, 2000), 'gap_setting')
    render_settings = ordered[0]['receipt']['render_settings']
    for key, value in (('unit_gap_s', unit_gap_ms), ('paragraph_gap_s', paragraph_gap_ms)):
        if key in render_settings:
            _require(type(render_settings[key]) in (int, float)
                     and math.isfinite(render_settings[key])
                     and abs(render_settings[key] * 1000 - value) < 1e-6, 'pinned_gap_setting')
    _require(isinstance(max_part_seconds, (int, float)) and not isinstance(max_part_seconds, bool)
             and math.isfinite(max_part_seconds) and 1 <= max_part_seconds <= 60, 'part_duration_setting')
    limit = int(max_part_seconds * SAMPLE_RATE)
    output = _safe_path(output_dir)
    _require(not output.exists(), 'output_directory_not_fresh')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    executable, probe = shutil.which(str(ffmpeg)), shutil.which(str(ffprobe))
    _require(bool(executable and probe), 'audio_tools_missing')
    version = _run([executable, '-version']).decode('utf-8', errors='replace').splitlines()[0]
    assembly_settings = dict(schema=ASSEMBLY_SCHEMA, input_rate=INPUT_RATE, output_rate=SAMPLE_RATE,
                             unit_gap_ms=unit_gap_ms, paragraph_gap_ms=paragraph_gap_ms,
                             max_part_seconds=max_part_seconds, codec='libmp3lame', bitrate='128k',
                             boundary='measured-unit-boundary', ffmpeg_version=version,
                             assembler_sha256=_sha(Path(__file__).read_bytes()))
    stage = Path(tempfile.mkdtemp(prefix='.bora-assembly-', dir=output.parent))
    os.chmod(stage, 0o700)
    try:
        spool = stage / '.pcm.f32le'
        segments, measurements, cursor = [], [], 0
        with spool.open('xb') as handle:
            os.chmod(spool, 0o600)
            for index, (row, (unit, start_cp, end_cp)) in enumerate(zip(ordered, validated)):
                final, metrics = _input_pcm(row['path'], row['receipt'], executable, probe)
                count = len(final) // 4
                _require(count <= limit, 'unit_exceeds_part_duration')
                gap = 0
                if index:
                    previous = validated[index - 1][0]
                    ms = paragraph_gap_ms if previous['paragraph_id'] != unit['paragraph_id'] else unit_gap_ms
                    gap = round(ms * SAMPLE_RATE / 1000)
                    handle.write(bytes(gap * 4))
                    cursor += gap
                start = cursor
                handle.write(final)
                cursor += count
                _require(cursor <= MAX_TOTAL_SAMPLES, 'lesson_duration_limit')
                segments.append(dict(unit_id=unit['id'], paragraph_id=unit['paragraph_id'], mode=unit['mode'],
                                     start_cp=start_cp, end_cp=end_cp, start_utf16=unit['start_utf16'],
                                     end_utf16=unit['end_utf16'], text=unit['source'],
                                     start_sample=start, end_sample=cursor,
                                     start_s=start / SAMPLE_RATE, end_s=cursor / SAMPLE_RATE))
                measurements.append(dict(unit_index=index, unit_id=unit['id'],
                                         input_sha256=row['receipt']['sha256'], receipt_sha256=identity(row['receipt']),
                                         sample_rate=SAMPLE_RATE, sample_count=count, gap_before_samples=gap,
                                         measurement_kind='final_decoded_pcm', validation=metrics))
        boundaries = _part_boundaries(segments, cursor, limit)
        parts = []
        with spool.open('rb') as handle:
            for index, (begin, end) in enumerate(zip(boundaries, boundaries[1:])):
                handle.seek(begin * 4)
                pcm = handle.read((end - begin) * 4)
                _require(len(pcm) == (end - begin) * 4, 'pcm_partition_truncated')
                name = f'{plan["lesson_id"]}.part-{index:04d}.mp3'
                details = _encode_part(pcm, stage / name, end - begin, executable)
                parts.append(dict(index=index, start_sample=begin, end_sample=end,
                                  start_s=begin / SAMPLE_RATE, end_s=end / SAMPLE_RATE,
                                  sample_rate=SAMPLE_RATE, file_name=name, **details))
        spool.unlink()
        _require(parts and parts[-1]['end_sample'] == cursor, 'part_coverage')
        _require(sum(p['plain_bytes'] + 16 * math.ceil(p['plain_bytes'] / 65536) for p in parts)
                 <= 128 * 1024 * 1024, 'encrypted_pack_size_limit')
        decoded_total = sum(p['decoded_sample_count'] for p in parts)
        _require(abs(decoded_total - cursor) <= SAMPLE_RATE * .03, 'accumulated_decode_delta')
        validation = dict(duration_s=cursor / SAMPLE_RATE, finite=True, clipped_samples=0,
                          peak=max(p['validation']['peak'] for p in parts),
                          rms=math.sqrt(sum(p['validation']['energy'] for p in parts) / decoded_total),
                          max_silence_s=max(p['validation']['max_silence_s'] for p in parts),
                          decoded_sample_count=decoded_total, decoded_sample_delta=decoded_total - cursor)
        metadata = dict(id=plan['lesson_id'], title=title or plan.get('title', plan['lesson_id']),
                        course=namespace_for(plan['lesson_id']).split('/', 2)[2],
                        access='premium-only', ai_generated=True, narration_text=plan['source'],
                        source_sha256=plan['source_sha256'], plan_sha256=identity(plan),
                        recipe_sha256=expected_recipe_sha256,
                        render_settings_sha256=expected_render_settings_sha256,
                        assembly=assembly_settings, assembly_sha256=identity(assembly_settings),
                        sample_rate=SAMPLE_RATE, total_samples=cursor, validation=validation,
                        segments=segments, audio_parts=parts, unit_measurements=measurements,
                        source_text_coverage='exact original code points and UTF-16; complete contiguous coverage',
                        pcm_part_coverage='contiguous measured PCM; independent MP3 decode delta <=1 sample per part',
                        automatic_unit_checks_passed=True, human_listening_verified=False,
                        asr_verified=True,
                        asr_scope='each accepted raw candidate: zero canonical transcript errors; final PCM signal checked separately',
                        status='private_assembly_complete_not_sealed_or_published')
        metadata_path = stage / f'{plan["lesson_id"]}.metadata.json'
        with metadata_path.open('x', encoding='utf-8') as handle:
            os.chmod(metadata_path, 0o600)
            json.dump(metadata, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
        _require(not output.exists(), 'output_directory_not_fresh')
        stage.rename(output)
        return copy.deepcopy(metadata)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
