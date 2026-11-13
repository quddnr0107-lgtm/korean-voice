"""Public saved-WAV timing experiment; never synthesizes or promotes audio.

The four WAV digests pin the prior public basic-Sohee experiment. Only the
word_timestamps option varies, with alternating order on one CPU worker.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import signal
import time

from pilot_render import canonical, transcript_score

MODEL = 'Systran/faster-whisper-small'
REVISION = '536b0662742c02347bc0e980a01041f333bce120'
SOURCE_RUN = 43374157929
FIXTURES = (
    ('plain_defaults-0.wav', '1e17cabc2fc319b06f7344bd9994890dd04d8d164b7cb1e28c83cb8c8a06e492', '오늘 배운 내용을 잠깐 덮고, 자기 말로 한 번 설명해 보세요.', ('설명',)),
    ('production_style-0.wav', '2b8565c4174a87525f3f1b23abbd94a3989a148e32c8a565b22ca9d3e4f684f5', '오늘 배운 내용을 잠깐 덮고, 자기 말로 한 번 설명해 보세요.', ('설명',)),
    ('production_style-1.wav', 'e1cd5baa9f6e16a8346b090963ac99cf967ab94c875841e7b0f1e1823108b133', '지금은 조금 느려도 괜찮습니다. 한 문장씩 차분하게 읽어 보세요.', ('괜찮습니다',)),
    ('plain_defaults-1.wav', '20b5306bf6b94c4152596331db99c3b6e044b66dfc5be6a1fbdbb4139a3f9790', '지금은 조금 느려도 괜찮습니다. 한 문장씩 차분하게 읽어 보세요.', ('괜찮습니다',)),
)
OPTIONS = dict(language='ko', beam_size=5, temperature=0,
               condition_on_previous_text=False, vad_filter=False)
HASH = __import__('re').compile('[0-9a-f]{64}')


def require(ok):
    if not ok:
        raise ValueError('public_asr_timestamp_benchmark_invalid')


def summarize(rows):
    require(isinstance(rows, list) and len(rows) == 2 * len(FIXTURES))
    expected = {(i, setting) for i in range(len(FIXTURES)) for setting in (True, False)}
    require(all(isinstance(r, dict) and type(r.get('fixture')) is int
        and type(r.get('word_timestamps')) is bool for r in rows))
    require({(r['fixture'], r['word_timestamps']) for r in rows} == expected)
    for r in rows:
        require(type(r['seconds']) in (int, float) and math.isfinite(r['seconds']) and r['seconds'] > 0
            and type(r['audio_seconds']) in (int, float) and math.isfinite(r['audio_seconds']) and r['audio_seconds'] > 0
            and type(r['character_errors']) is int and r['character_errors'] >= 0
            and type(r['missing_critical_count']) is int and r['missing_critical_count'] >= 0
            and type(r['word_count']) is int and r['word_count'] >= 0
            and isinstance(r['canonical_sha256'], str) and HASH.fullmatch(r['canonical_sha256']))
    pairs = []
    for i in range(len(FIXTURES)):
        a = next(r for r in rows if r['fixture'] == i and r['word_timestamps'])
        b = next(r for r in rows if r['fixture'] == i and not r['word_timestamps'])
        require(a['audio_seconds'] == b['audio_seconds'])
        pairs.append(dict(fixture=i,
            canonical_transcript_equal=a['canonical_sha256'] == b['canonical_sha256'],
            exact_gate_outcome_equal=(a['character_errors'] == 0 and a['missing_critical_count'] == 0)
                == (b['character_errors'] == 0 and b['missing_critical_count'] == 0),
            character_error_count_equal=a['character_errors'] == b['character_errors'],
            missing_critical_count_equal=a['missing_critical_count'] == b['missing_critical_count']))
    parity = all(all(p[k] for k in ('canonical_transcript_equal', 'exact_gate_outcome_equal',
                                  'character_error_count_equal', 'missing_critical_count_equal')) for p in pairs)
    totals = {str(setting).lower(): round(sum(r['seconds'] for r in rows if r['word_timestamps'] is setting), 6)
              for setting in (True, False)}
    return dict(schema='public-asr-timestamp-benchmark/v1', source_run_id=SOURCE_RUN,
        model=MODEL, model_revision=REVISION, device='cpu', compute_type='int8', cpu_threads=1,
        num_workers=1, decoding_options=OPTIONS, rows=rows, pairs=pairs,
        canonical_and_gate_parity=parity, elapsed_seconds=totals,
        observed_elapsed_reduction_pct=round((1 - totals['false'] / totals['true']) * 100, 3),
        equivalent_transcript_timing_reduction_pct=round((1 - totals['false'] / totals['true']) * 100, 3) if parity else None,
        model_load_included=False, resampling_included=False, no_expected_text_prompt=True,
        synthesis_started=False, production_eligible=False, checkpoints_modified=False,
        scope='One alternating-order trial on four hash-pinned public WAVs. Transcript parity here does not prove parity or quality on operating manuscripts.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    require(not any(os.environ.get(n) for n in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')))
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', ORT_DISABLE_TELEMETRY='1')
    import numpy as np
    import soundfile as sf
    import onnxruntime as ort
    from scipy.signal import resample_poly
    from faster_whisper import WhisperModel
    from download_models import ASR_REQUIRED, verify_assets
    ort.disable_telemetry_events()
    audio_root, model_root = Path(args.audio), Path(args.model)
    verify_assets(model_root, ASR_REQUIRED)
    model_hashes = {n: hashlib.sha256((model_root / n).read_bytes()).hexdigest() for n in ASR_REQUIRED}
    for name, digest, _, _ in FIXTURES:
        path = audio_root / name
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size < 16 * 1024 * 1024
            and hashlib.sha256(path.read_bytes()).hexdigest() == digest)
    load_start = time.perf_counter()
    model = WhisperModel(str(model_root), device='cpu', compute_type='int8',
                         cpu_threads=1, num_workers=1, local_files_only=True)
    load_seconds = time.perf_counter() - load_start
    rows = []
    def timeout(*unused):
        raise TimeoutError('public_asr_deadline')
    signal.signal(signal.SIGALRM, timeout)
    for i, (name, digest, expected_text, terms) in enumerate(FIXTURES):
        wave, sr = sf.read(audio_root / name, dtype='float32')
        require(sr == 24000 and wave.ndim == 1 and wave.size and np.isfinite(wave).all())
        audio = resample_poly(wave, 16000 // math.gcd(16000, sr), sr // math.gcd(16000, sr)).astype('float32')
        order = (True, False) if i % 2 == 0 else (False, True)
        for word_timestamps in order:
            signal.alarm(45)
            started = time.perf_counter()
            try:
                segments, _ = model.transcribe(audio, word_timestamps=word_timestamps, **OPTIONS)
                segments = list(segments)
                elapsed = time.perf_counter() - started
            finally:
                signal.alarm(0)
            recognized = ' '.join(s.text.strip() for s in segments)
            score = transcript_score(expected_text, recognized, terms, max_cer=0.0)
            row = dict(fixture=i, word_timestamps=word_timestamps, seconds=round(elapsed, 6),
                audio_seconds=round(len(wave) / sr, 6),
                canonical_sha256=hashlib.sha256(canonical(recognized).encode()).hexdigest(),
                character_errors=score['character_errors'], missing_critical_count=len(score['missing_critical']),
                word_count=sum(len(s.words or []) for s in segments))
            rows.append(row)
            print(json.dumps(row, sort_keys=True), flush=True)
    result = summarize(rows)
    result.update(model_load_seconds=round(load_seconds, 6), model_file_sha256=model_hashes,
        cpu_count=os.cpu_count(), platform=platform.platform(),
        packages={n: importlib.metadata.version(n) for n in ('faster-whisper', 'ctranslate2', 'numpy', 'scipy', 'soundfile', 'onnxruntime')})
    output = Path(args.output)
    require(not output.exists())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, indent=2))
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
