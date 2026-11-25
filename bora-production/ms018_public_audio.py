"""Explicitly public audio-only output for the one MS-018 recovery request.

Private metadata is used only to validate the parts. No transcript,
descriptor, master key, or internal receipt is copied to the output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np

import ms018_recovery as recovery
from assemble import _child_env, _metrics, _require_signal, _run
from lecture_pack import SAMPLE_RATE, MAX_PARTS, MAX_PART_BYTES, MAX_PART_SAMPLES, MAX_TOTAL_SAMPLES

SCHEMA = 'ms018-public-audio/v1'
MP3 = 'MS-018_Kim_Bora_FP32.mp3'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(ffmpeg, path):
    return _run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(path),
                 '-f', 'f32le', '-ac', '1', '-ar', str(SAMPLE_RATE), 'pipe:1'])


def export_parts(directory, metadata, output):
    directory, output = Path(directory), Path(output)
    require(metadata['id'] == recovery.LESSON
            and metadata['plan_sha256'] == recovery.PLAN
            and metadata['recipe_sha256'] == recovery.RECIPE
            and metadata['render_settings_sha256'] == recovery.SETTINGS
            and metadata['automatic_unit_checks_passed'] is True
            and metadata['sample_rate'] == SAMPLE_RATE
            and metadata['ms018_seed_recovery']['unit_index'] == recovery.INDEX,
            'public_audio_identity')
    require(not output.exists(), 'public_audio_output_exists')
    parts = metadata['audio_parts']
    require(isinstance(parts, list) and 1 <= len(parts) <= MAX_PARTS, 'public_audio_parts')
    total = metadata['total_samples']
    require(type(total) is int and 0 < total <= MAX_TOTAL_SAMPLES, 'public_audio_clock')
    manifest = dict(schema=SCHEMA, id=recovery.LESSON, sample_rate=SAMPLE_RATE,
                    total_samples=total, parts=[])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ms018-audio-', dir=output.parent) as td:
        stage = Path(td) / 'parts'
        stage.mkdir(mode=0o700)
        cursor = 0
        for i, part in enumerate(parts):
            name = f'{recovery.LESSON}.part-{i:04d}.mp3'
            start, end = part['start_sample'], part['end_sample']
            measured = part['decoded_sample_count']
            require(part['index'] == i and part['file_name'] == name
                    and type(start) is int and type(end) is int
                    and start == cursor and 0 < end - start <= MAX_PART_SAMPLES
                    and end <= total and type(measured) is int
                    and abs(measured - (end - start)) <= 1,
                    'public_audio_part_clock')
            path = directory / name
            require(path.is_file() and not path.is_symlink()
                    and 0 < path.stat().st_size <= MAX_PART_BYTES, 'public_audio_part_size')
            raw = path.read_bytes()
            require(len(raw) == part['plain_bytes'] and sha(raw) == part['audio_sha256'],
                    'public_audio_part_hash')
            (stage / name).write_bytes(raw)
            manifest['parts'].append(dict(file=name, start_sample=start, end_sample=end,
                                         decoded_samples=measured, sha256=sha(raw)))
            cursor = end
        require(cursor == total, 'public_audio_coverage')
        (stage / 'audio-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        stage.rename(output)
    return manifest


def join_audio(directory, output):
    directory, output = Path(directory), Path(output)
    require(not output.exists() and output.name == MP3, 'public_mp3_output')
    manifest = json.loads((directory / 'audio-manifest.json').read_text())
    require(set(manifest) == {'schema', 'id', 'sample_rate', 'total_samples', 'parts'}
            and manifest['schema'] == SCHEMA and manifest['id'] == recovery.LESSON
            and manifest['sample_rate'] == SAMPLE_RATE, 'public_mp3_manifest')
    parts = manifest['parts']
    require(isinstance(parts, list) and 1 <= len(parts) <= MAX_PARTS
            and type(manifest['total_samples']) is int
            and 0 < manifest['total_samples'] <= MAX_TOTAL_SAMPLES,
            'public_mp3_manifest_bounds')
    ffmpeg = shutil.which('ffmpeg')
    require(bool(ffmpeg), 'public_mp3_ffmpeg')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ms018-join-', dir=output.parent) as td:
        work = Path(td)
        spool, candidate, decoded = work/'audio.f32le', work/'audio.mp3', work/'decoded.f32le'
        samples = cursor = 0
        expected = {'audio-manifest.json'}
        with spool.open('xb') as handle:
            for i, part in enumerate(parts):
                name = f'{recovery.LESSON}.part-{i:04d}.mp3'
                expected.add(name)
                start, end = part['start_sample'], part['end_sample']
                require(set(part) == {'file','start_sample','end_sample','decoded_samples','sha256'}
                        and part['file'] == name and type(start) is int and type(end) is int
                        and start == cursor and 0 < end - start <= MAX_PART_SAMPLES,
                        'public_mp3_part_scope')
                path = directory/name
                require(path.is_file() and not path.is_symlink()
                        and 0 < path.stat().st_size <= MAX_PART_BYTES
                        and sha(path.read_bytes()) == part['sha256'], 'public_mp3_part_hash')
                raw = decode(ffmpeg, path)
                require(type(part['decoded_samples']) is int
                        and len(raw) == 4 * part['decoded_samples']
                        and abs(part['decoded_samples'] - (end-start)) <= 1,
                        'public_mp3_part_samples')
                _require_signal(_metrics(np.frombuffer(raw, dtype='<f4')))
                handle.write(raw)
                samples += len(raw)//4
                cursor = end
        require(set(p.name for p in directory.iterdir()) == expected
                and cursor == manifest['total_samples'], 'public_mp3_coverage')
        _run([ffmpeg, '-nostdin','-v','error','-f','f32le','-ar',str(SAMPLE_RATE),'-ac','1',
              '-i',str(spool),'-map_metadata','-1','-c:a','libmp3lame','-b:a','256k',
              '-write_xing','1',str(candidate)])
        raw = decode(ffmpeg, candidate)
        count = len(raw)//4
        if 1 < count-samples <= 46:
            from mp3_gapless import repair
            candidate.write_bytes(repair(candidate.read_bytes(), samples, count))
            raw = decode(ffmpeg, candidate)
            count = len(raw)//4
        require(len(raw) % 4 == 0 and count == samples, 'public_mp3_total_samples')
        # A second lossy encode must independently pass the same signal gates.
        _require_signal(_metrics(np.frombuffer(raw, dtype='<f4')))
        require(0 < candidate.stat().st_size <= 256*1024*1024, 'public_mp3_size')
        with output.open('xb') as handle, candidate.open('rb') as source:
            shutil.copyfileobj(source, handle)
    return dict(id=recovery.LESSON, samples=samples, duration_s=samples/SAMPLE_RATE,
                original_sample_delta=samples-manifest['total_samples'],
                size_bytes=output.stat().st_size, sha256=sha(output.read_bytes()))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('parts', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    print(json.dumps(join_audio(args.parts, args.output)), flush=True)
