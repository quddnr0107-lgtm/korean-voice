"""Package and verify matching public no-ASR audio on both CPU paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time


RATE = 24000
GAP_SAMPLES = 8400
MAX_SAMPLES = RATE * 750
MAX_MP3_BYTES = 16 * 1024 * 1024
MODES = ('eager', 'compiled')


def require(condition, code):
    if not condition:
        raise ValueError(code)


def file_sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def ffmpeg(*args, input=None):
    return subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', *args],
        input=input, capture_output=True, check=True, timeout=90,
    ).stdout


def decode(path, expected_samples):
    """Validate the encoded stream and measure its full, gapless PCM decode."""
    import numpy as np

    path = Path(path)
    require(path.is_file() and not path.is_symlink()
            and 0 < path.stat().st_size <= MAX_MP3_BYTES, 'mp3_file')
    require(type(expected_samples) is int and 0 < expected_samples <= MAX_SAMPLES,
            'mp3_expected_samples')
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries',
         'stream=codec_name,sample_rate,channels', '-of', 'json', str(path)],
        capture_output=True, check=True, timeout=20,
    )
    streams = json.loads(probe.stdout)['streams']
    require(streams == [{'codec_name': 'mp3', 'sample_rate': str(RATE), 'channels': 1}],
            'mp3_stream_format')
    # Cap output even if an input claims a much longer duration than expected.
    horizon_seconds = (expected_samples + RATE) / RATE
    raw = ffmpeg('-i', str(path), '-map', '0:a:0', '-t', str(horizon_seconds),
                 '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
    require(len(raw) % 4 == 0, 'mp3_decoded_sample_format')
    wave = np.frombuffer(raw, dtype='<f4')
    require(abs(wave.size - expected_samples) <= 1 and np.isfinite(wave).all(),
            'mp3_decoded_sample_count')
    return wave


def assemble(waves, out_path):
    """Write one MP3 with equal pauses, then confirm the complete decode."""
    import numpy as np

    started = time.perf_counter()
    waves = [np.asarray(wave) for wave in waves]
    require(1 <= len(waves) <= 12, 'audio_unit_count')
    require(all(wave.ndim == 1 and wave.dtype.kind == 'f' and wave.dtype.itemsize == 4
                and wave.size > 0 for wave in waves), 'audio_pcm_format')
    sample_count = sum(wave.size for wave in waves) + (len(waves) - 1) * GAP_SAMPLES
    require(sample_count <= MAX_SAMPLES, 'audio_sample_limit')
    require(all(np.isfinite(wave).all() for wave in waves), 'audio_pcm_finite')
    pause = bytes(GAP_SAMPLES * 4)
    joined = pause.join(wave.astype('<f4', copy=False).tobytes() for wave in waves)
    out_path = Path(out_path).absolute()
    require(out_path.parent.is_dir() and not out_path.exists() and not out_path.is_symlink(),
            'fresh_mp3_path')
    with tempfile.TemporaryDirectory(prefix='.bora-no-asr-mp3-', dir=out_path.parent) as temp:
        staged = Path(temp) / 'audio.mp3'
        ffmpeg('-f', 'f32le', '-ar', str(RATE), '-ac', '1', '-i', 'pipe:0', '-map_metadata',
               '-1', '-c:a', 'libmp3lame', '-b:a', '128k', str(staged), input=joined)
        decode(staged, sample_count)
        result = dict(name=out_path.name, sha256=file_sha256(staged), sample_count=sample_count,
                      audio_seconds=sample_count / RATE)
        staged.rename(out_path)
    return dict(result, packaging_seconds=time.perf_counter() - started)


def bounded_summary(path):
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 256 * 1024,
            'shard_summary_file')
    value = json.loads(path.read_text(encoding='utf-8'))
    require(isinstance(value, dict), 'shard_summary_format')
    return value


def nonnegative_seconds(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def text_sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def is_sha256(value):
    return (isinstance(value, str) and len(value) == 64
            and all(char in '0123456789abcdef' for char in value))


def validate_rows(rows, shard):
    from no_asr_ab_fixtures import FIXTURES

    require(isinstance(rows, list) and len(rows) == 8, 'shard_row_count')
    pairs = {index: {} for index in range(shard * 4, (shard + 1) * 4)}
    for row in rows:
        index, mode = row['index'], row['mode']
        require(type(index) is int and index in pairs and mode in MODES
                and mode not in pairs[index], 'shard_row_identity')
        style, text = FIXTURES[index]
        require(row['style'] == style and row['text_sha256'] == text_sha256(text)
                and type(row['seed']) is int and row['seed'] >= 0, 'shard_input_fingerprint')
        require(row['natural_eos'] is True and nonnegative_seconds(row['tts_seconds'])
                and row['tts_seconds'] > 0 and type(row['sample_count']) is int
                and 0 < row['sample_count'] <= 60 * RATE
                and is_sha256(row['pcm_sha256']) and is_sha256(row['sequence_sha256']),
                'shard_row_measurements')
        require(all(type(row[k]) is int and row[k] >= 0
                    for k in ('compiled_graph_invocations', 'new_graphs', 'compile_attempts'))
                and row['new_graphs'] <= row['compile_attempts']
                and (row['compiled_graph_invocations'] > 0 if mode == 'compiled'
                     else all(row[k] == 0 for k in (
                         'compiled_graph_invocations', 'new_graphs', 'compile_attempts'))),
                'shard_row_compilation')
        pairs[index][mode] = row
    for pair in pairs.values():
        require(set(pair) == set(MODES) and pair['eager']['seed'] == pair['compiled']['seed'],
                'shard_pair_input')
    return pairs


def collect_shard(source, shard):
    from fp32_renderer import MODEL_REVISION

    folder = source / f'bora-no-asr-ab-shard-{shard}'
    require(folder.is_dir() and not folder.is_symlink(), 'shard_directory')
    summary = bounded_summary(folder / 'summary.json')
    require(summary['schema'] == 'bora-no-asr-ab-shard/v1'
            and summary['status'] == 'complete' and summary['shard'] == shard
            and summary['model_revision'] == MODEL_REVISION
            and summary['sample_rate'] == RATE and summary['gap_ms'] == 350
            and summary['asr_performed'] is False and type(summary['retries']) is int
            and summary['retries'] == 0 and isinstance(summary['cpu'], str)
            and 0 < len(summary['cpu']) <= 512, 'shard_run_evidence')
    assets = summary['model_assets_sha256']
    require(isinstance(assets, dict) and 0 < len(assets) <= 32
            and all(is_sha256(sha) for sha in assets.values()), 'shard_model_assets')
    require(all(nonnegative_seconds(summary[k]) for k in
                ('model_load_seconds', 'wrapper_setup_seconds', 'synthesis_elapsed_seconds')),
                'shard_run_timing')
    pairs = validate_rows(summary['rows'], shard)
    require(sum(p['compiled']['new_graphs'] for p in pairs.values()) > 0,
            'shard_cold_compile_observed')
    pcm = {}
    for mode in MODES:
        record = summary['audio'][mode]
        samples = sum(pair[mode]['sample_count'] for pair in pairs.values()) + 3 * GAP_SAMPLES
        require(record['name'] == f'{mode}.mp3' and record['sample_count'] == samples
                and record['audio_seconds'] == samples / RATE
                and nonnegative_seconds(record['packaging_seconds'])
                and is_sha256(record['sha256']), 'shard_mp3_report')
        path = folder / record['name']
        pcm[mode] = decode(path, samples)
        require(file_sha256(path) == record['sha256'], 'shard_mp3_digest')
    return summary, pairs, pcm


def speed_comparison(eager, compiled):
    return dict(eager_seconds=eager, compiled_seconds=compiled, saved_seconds=eager - compiled,
                reduction_percent=100 * (eager - compiled) / eager if eager else None)


def timing_summary(shards, final_audio):
    workers, all_pairs = [], []
    for summary, pairs, _ in shards:
        synthesis = {mode: sum(pair[mode]['tts_seconds'] for pair in pairs.values())
                     for mode in MODES}
        packaging = {mode: summary['audio'][mode]['packaging_seconds'] for mode in MODES}
        setup = summary['wrapper_setup_seconds']
        workers.append(dict(
            shard=summary['shard'], cpu=summary['cpu'], raw_synthesis_seconds=synthesis,
            wrapper_setup_seconds=setup, shard_mp3_seconds=packaging,
            inclusive=speed_comparison(synthesis['eager'] + packaging['eager'],
                                       synthesis['compiled'] + setup + packaging['compiled']),
            shared_model_load_seconds=summary['model_load_seconds'],
            measured_interleaved_elapsed_seconds=summary['synthesis_elapsed_seconds'],
        ))
        all_pairs.extend(pairs.values())
    steady = [pair for pair in all_pairs if pair['compiled']['compile_attempts'] == 0
              and pair['compiled']['new_graphs'] == 0]
    # Keep actual measured intra-worker sums distinct from parallel makespan estimates.
    inclusive = {mode: [worker['inclusive'][f'{mode}_seconds'] for worker in workers]
                 for mode in MODES}
    final_packaging = {mode: final_audio[mode]['packaging_seconds'] for mode in MODES}
    return dict(
        worker_count=len(workers), workers=workers,
        sum_synthesis_compile_and_shard_mp3=speed_comparison(
            sum(inclusive['eager']), sum(inclusive['compiled'])),
        final_mp3_seconds=final_packaging,
        sum_synthesis_compile_and_all_mp3=speed_comparison(
            sum(inclusive['eager']) + final_packaging['eager'],
            sum(inclusive['compiled']) + final_packaging['compiled']),
        estimated_parallel_compute_span=speed_comparison(
            max(inclusive['eager']), max(inclusive['compiled'])),
        warm_pairs=len(steady), warm_synthesis_only=speed_comparison(
            sum(p['eager']['tts_seconds'] for p in steady),
            sum(p['compiled']['tts_seconds'] for p in steady)),
        timing_basis='Paired calls on the same worker within each shard. Totals sum measured '
                     'synthesis, compiled setup and MP3 packaging. The max-per-mode estimate '
                     'uses shard times only and excludes shared setup and scheduling.',
    )


def collect(source, output):
    source, output = Path(source).absolute(), Path(output).absolute()
    require(source.is_dir() and not source.is_symlink(), 'collect_input_directory')
    require(not output.exists() and not output.is_symlink(), 'collect_output_directory')
    require(output.parent.is_dir(), 'collect_output_parent')
    shards = [collect_shard(source, shard) for shard in range(3)]
    model = shards[0][0]
    require(all(shard[0]['model_assets_sha256'] == model['model_assets_sha256']
                for shard in shards), 'collect_model_mismatch')
    pairs = dict(item for _, by_index, _ in shards for item in by_index.items())
    require(list(pairs.keys()) == list(range(12)), 'collect_unit_coverage')
    pcm_equal = [i for i, p in pairs.items()
                 if p['eager']['sample_count'] == p['compiled']['sample_count']
                 and p['eager']['pcm_sha256'] == p['compiled']['pcm_sha256']]
    seq_equal = [i for i, p in pairs.items()
                 if p['eager']['sequence_sha256'] == p['compiled']['sequence_sha256']]
    with tempfile.TemporaryDirectory(prefix='.bora-no-asr-collect-', dir=output.parent) as temp:
        stage = Path(temp) / 'results'
        stage.mkdir(mode=0o700)
        audio = {mode: assemble([shard[2][mode] for shard in shards],
                                stage / f'bora-{mode}-5min.mp3') for mode in MODES}
        results = dict(
            schema='bora-no-asr-ab-comparison/v1', status='complete',
            model_revision=model['model_revision'], model_assets_sha256=model['model_assets_sha256'],
            asr_performed=False, retries=0, sample_rate=RATE, gap_ms=350, units=len(pairs),
            same_input_verified=True, natural_eos_all=True,
            raw_pcm_exact_pairs=len(pcm_equal), sequence_exact_pairs=len(seq_equal),
            raw_pcm_difference_indices=sorted(set(pairs) - set(pcm_equal)),
            sequence_difference_indices=sorted(set(pairs) - set(seq_equal)),
            final_mp3_bytes_equal=audio['eager']['sha256'] == audio['compiled']['sha256'],
            timing=timing_summary(shards, audio), audio=audio, rows=pairs,
        )
        (stage / 'summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2,
                                                       allow_nan=False) + '\n', encoding='utf-8')
        stage.rename(output)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('collect')
    command.add_argument('source')
    command.add_argument('output')
    args = parser.parse_args()
    result = collect(args.source, args.output)
    print(json.dumps({key: result[key] for key in (
        'status', 'units', 'raw_pcm_exact_pairs', 'sequence_exact_pairs',
        'final_mp3_bytes_equal', 'timing', 'audio',
    )}, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
