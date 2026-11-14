"""Keyless, read-only ASR diagnostics of one hash-pinned PUBLIC CPU experiment.

Only the two authored long-benchmark fixtures are accepted. Four FLOAT WAVs
must prove exact eager/compiled PCM parity before one eager WAV per fixture
is recognized. No source envelope, private checkpoint or TTS model is opened.
"""
from __future__ import annotations
import argparse
import array
import contextlib
import copy
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import struct
import sys
import time
import traceback

import cpu_compile_long_benchmark as long
from pilot_render import transcript_score
from tts_normalize import normalize, RULESET

SCHEMA = 'bora-public-long-asr-check/v1'
SOURCE_RUN = 43533278937
SOURCE_SHA = 'd7d3a46cd7dd8461a276eace71ccd57393ae9cc0'
REQUEST = Path(__file__).with_name('cpu-public-asr-request.json')
SECRETS = ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')
WAV_NAMES = tuple(f'{i:02d}-{mode}-{long.FIXTURE_MODES[fixture]}.wav'
    for i, (mode, fixture) in enumerate(long.SCHEDULE))
INPUT_NAMES = ('summary.json',) + WAV_NAMES
MODELS = {
    'small': dict(repo='Systran/faster-whisper-small',
        revision='536b0662742c02347bc0e980a01041f333bce120',
        model_sha256='3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671',
        files={'model.bin': 483546902, 'config.json': None,
               'tokenizer.json': None, 'vocabulary.txt': None}),
    'turbo': dict(repo='dropbox-dash/faster-whisper-large-v3-turbo',
        revision='0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf',
        model_sha256='e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da',
        files={'model.bin': 1617884929, 'config.json': None,
               'preprocessor_config.json': None, 'tokenizer.json': None,
               'vocabulary.json': None}),
}
CRITICAL = (
    ('주체', '조건', '예외', '원문'),
    ('제십일조', '제이항', '세 시간', '제삼항', '스물다섯 명',
     '이천이십육년', '십일월', '십사일', '아홉 시 삼십 분', '제이호', '제사호'),
)
TRANSCRIBE = dict(language='ko', beam_size=5, temperature=0.0,
    condition_on_previous_text=False, vad_filter=False)
ASR_SETTINGS = {
    'small': dict(cpu_threads=1, word_timestamps=True,
        input_representation='soundfile_FLOAT32_mono_24000Hz_to_scipy_resample_poly_2_over_3_FLOAT32_16000Hz_array'),
    'turbo': dict(cpu_threads=4, word_timestamps=False,
        input_representation='original_FLOAT_WAV_path_to_faster_whisper_PyAV_decode_and_resample'),
}
RUNTIME_PINS = {'faster-whisper': '1.2.1', 'ctranslate2': '4.9.0',
    'av': '18.1.0',
    'soundfile': '0.14.0', 'numpy': '2.5.4', 'scipy': '1.18.1',
    'onnxruntime': '1.31.1', 'huggingface-hub': '0.36.2'}
CALL_SECONDS = 240
HASH = re.compile(r'[a-f0-9]{64}')


def require(ok):
    if not ok:
        raise ValueError('public_asr_check_validation_failed')


def keyless():
    require(not any(os.environ.get(name) for name in SECRETS))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def request_value(artifact_id=None, files=None):
    ready = artifact_id is not None and files is not None
    return dict(schema=SCHEMA, request_id='public-long-asr-20261114-03',
        ready=ready, source_run_id=SOURCE_RUN, source_commit=SOURCE_SHA,
        artifact_id=artifact_id, fixtures=long.fixtures(),
        critical_terms=[list(value) for value in CRITICAL],
        files=copy.deepcopy(files) if files is not None else
            {name: dict(sha256='0'*64, size_bytes=0) for name in INPUT_NAMES},
        models=copy.deepcopy(MODELS), asr_settings=copy.deepcopy(ASR_SETTINGS),
        synthesis=False, production=False,
        private_inputs=False, secrets=False, promotion=False)


def validate_request(value):
    require(isinstance(value, dict) and type(value.get('artifact_id')) is int
        and value['artifact_id'] > 0 and value.get('ready') is True)
    files = value.get('files')
    require(isinstance(files, dict) and set(files) == set(INPUT_NAMES))
    for name, pin in files.items():
        require(isinstance(pin, dict) and set(pin) == {'sha256', 'size_bytes'}
            and isinstance(pin['sha256'], str) and HASH.fullmatch(pin['sha256'])
            and pin['sha256'] != '0'*64 and type(pin['size_bytes']) is int
            and 0 < pin['size_bytes'] <= (2*1024*1024 if name == 'summary.json' else 16*1024*1024))
    require(value == request_value(value['artifact_id'], files))
    return value


def file_pins(directory):
    """Root can finalize the request after independently resolving the run artifact."""
    root = Path(directory)
    return {name: dict(sha256=digest(root/name), size_bytes=(root/name).stat().st_size)
            for name in INPUT_NAMES}


def regular(path):
    path = Path(path)
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)))
    return path


def float_pcm(data):
    """Independent RIFF parser: mono 24kHz float32, no decode/DSP dependency."""
    require(44 <= len(data) <= 16*1024*1024 and data[:4] == b'RIFF'
        and data[8:12] == b'WAVE' and struct.unpack_from('<I', data, 4)[0]+8 == len(data))
    offset, fmt, pcm = 12, None, None
    while offset+8 <= len(data):
        kind, size = struct.unpack_from('<4sI', data, offset)
        begin, end = offset+8, offset+8+size
        require(end <= len(data))
        chunk = data[begin:end]
        if kind == b'fmt ':
            require(fmt is None and size >= 16)
            fmt = struct.unpack_from('<HHIIHH', chunk)
            code, channels, rate, byte_rate, align, bits = fmt
            if code == 65534:
                require(size >= 40 and struct.unpack_from('<H', chunk, 16)[0] >= 22)
                code = struct.unpack_from('<I', chunk, 24)[0]
            require((code, channels, rate, byte_rate, align, bits) == (3, 1, 24000, 96000, 4, 32))
        elif kind == b'data':
            require(pcm is None and size > 0 and size % 4 == 0)
            pcm = chunk
        offset = end+(size % 2)
    require(offset == len(data) and fmt is not None and pcm is not None)
    samples = array.array('f'); samples.frombytes(pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    require(all(math.isfinite(value) and abs(value) < 1 for value in samples)
        and math.sqrt(sum(value*value for value in samples)/len(samples)) > 1e-5
        and len(samples) <= 60*24000)
    return pcm, len(samples)


def validate_inputs(directory, request):
    keyless(); validate_request(request)
    root = Path(directory)
    require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)))
    require({p.relative_to(root).as_posix() for p in root.rglob('*.wav')} == set(WAV_NAMES))
    for name in INPUT_NAMES:
        path = regular(root/name); pin = request['files'][name]
        require(path.stat().st_size == pin['size_bytes'] and digest(path) == pin['sha256'])
    summary = json.loads((root/'summary.json').read_text())
    require(summary.get('schema') == long.SCHEMA and summary.get('complete') is True
        and summary.get('comparison_valid') is True
        and summary.get('exact_public_outputs_equal') is True
        and summary.get('fixture_manifest') == long.fixtures()
        and summary.get('production_eligible') is False)
    rows = summary['rows']
    checked = long.summarize(rows, summary['compile_counters'], summary['compile_wrapper_setup_seconds'])
    require(checked['comparison_valid'] is True and len(rows) == 4)
    pcm_by_fixture, selected = {}, []
    for index, row in enumerate(rows):
        fixture = long.fixtures()[row['fixture']]
        require(row['wav_file'] == WAV_NAMES[index]
            and all(row[field] == value for field, value in fixture.items())
            and row['generation_args'] == long.generation_args(row['fixture']))
        path = root/WAV_NAMES[index]; pcm, count = float_pcm(path.read_bytes())
        require(row['wav_sha256'] == digest(path)
            and row['pcm_sha256'] == hashlib.sha256(pcm).hexdigest()
            and row['pcm_samples'] == count and row['sample_rate'] == 24000
            and row['audio_seconds'] == count/24000
            and row['talker_sequence_sha256'] == hashlib.sha256(json.dumps(
                row['talker_sequences'], separators=(',', ':')).encode()).hexdigest())
        prior = pcm_by_fixture.setdefault(row['fixture'], (pcm, row['talker_sequences']))
        require(prior == (pcm, row['talker_sequences']))
        if row['mode'] == 'eager':
            selected.append(dict(fixture=row['fixture'], path=path,
                wav_sha256=row['wav_sha256'], pcm_sha256=row['pcm_sha256'],
                expected_spoken=fixture['spoken'], critical_terms=list(CRITICAL[row['fixture']])))
    require([row['fixture'] for row in selected] == [0, 1])
    return selected


def verify_model(directory, name):
    from fp32_renderer import verify_assets
    model = MODELS[name]
    observed = verify_assets(directory, model['files'], model['revision'])
    require(observed['model.bin'] == model['model_sha256'])
    if name == 'small':
        manifest = json.loads(Path(__file__).with_name('pinned-public-assets.json').read_text())['models']['asr']
        require((manifest['repository'], manifest['revision']) == (model['repo'], model['revision'])
            and observed == {key: value['sha256'] for key, value in manifest['files'].items()})
    return observed


def download_models(directory):
    keyless(); root = Path(directory)
    require(not root.exists())
    from huggingface_hub import snapshot_download
    root.mkdir(parents=True)
    for name, model in MODELS.items():
        keyless()
        snapshot_download(repo_id=model['repo'], revision=model['revision'],
            local_dir=str(root/name), allow_patterns=list(model['files']), max_workers=4)
        verify_model(root/name, name)


@contextlib.contextmanager
def offline():
    keyless()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        DO_NOT_TRACK='1', ORT_DISABLE_TELEMETRY='1')
    names = ('connect', 'connect_ex')
    original = {name: getattr(socket.socket, name) for name in names}
    create = socket.create_connection
    def deny(*args, **kwargs):
        raise RuntimeError('public_asr_network_forbidden')
    for name in names:
        setattr(socket.socket, name, deny)
    socket.create_connection = deny
    try:
        yield
    finally:
        for name, value in original.items():
            setattr(socket.socket, name, value)
        socket.create_connection = create


def diagnostic_score(expected, recognized, critical):
    require(isinstance(recognized, str) and len(recognized) <= 10000)
    raw = transcript_score(expected, recognized, critical, max_cer=0)
    pronounced, edits, flags = normalize(recognized)
    normalized = transcript_score(expected, pronounced, critical, max_cer=0)
    return dict(recognized=recognized,
        raw_canonical_strict=dict(raw, exact=raw['character_errors'] == 0 and not raw['missing_critical']),
        pronunciation_normalized=dict(normalized,
            exact=not flags and normalized['character_errors'] == 0 and not normalized['missing_critical'],
            text=pronounced, edits=edits, review_flags=flags, ruleset=RULESET),
        production_eligible=False, promotion_performed=False)


def recognition_input(path, name):
    """Reproduce each existing ASR caller's input path, without a new DSP policy."""
    require(name in MODELS)
    if name == 'turbo':
        return str(path)
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    wave, sr = sf.read(str(path), dtype='float32')
    require(sr == 24000 and wave.ndim == 1 and wave.dtype == np.dtype('float32'))
    # Same operation and final cast as FP32Renderer._recognize. Reading the
    # original FLOAT WAV recovers the exact already-verified 24kHz samples.
    return resample_poly(wave, 16000//math.gcd(16000, sr),
                         sr//math.gcd(16000, sr)).astype('float32')


def recognize(selected, model, name):
    result = []
    for row in selected:
        keyless(); require(digest(row['path']) == row['wav_sha256'])
        started = time.monotonic()
        options = dict(TRANSCRIBE, word_timestamps=ASR_SETTINGS[name]['word_timestamps'])
        segments, _ = model.transcribe(recognition_input(row['path'], name), **options)
        recognized = ' '.join(segment.text.strip() for segment in segments).strip()
        result.append(dict(fixture=row['fixture'], model=name,
            input_representation=ASR_SETTINGS[name]['input_representation'],
            wav_sha256=row['wav_sha256'], pcm_sha256=row['pcm_sha256'],
            expected_public_spoken=row['expected_spoken'], critical_terms=row['critical_terms'],
            asr_seconds=time.monotonic()-started,
            **diagnostic_score(row['expected_spoken'], recognized, row['critical_terms'])))
        require(digest(row['path']) == row['wav_sha256'])
    return result


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def public_failure(error):
    """Bounded diagnostics only for this fixed public/keyless input path."""
    keyless()
    try:
        message = str(error)
    except Exception:
        message = '<exception message unavailable>'
    return dict(exception_type=type(error).__name__[:200],
        exception_module=type(error).__module__[:200], message=message[:1200],
        message_truncated=len(message) > 1200,
        frames=[dict(file=Path(frame.filename).name[:200], line=frame.lineno)
            for frame in traceback.extract_tb(error.__traceback__, limit=-10)],
        includes_locals=False, includes_environment=False, public_inputs_only=True)


def inspect(directory, models, output, request):
    selected = validate_inputs(directory, request)
    runtime = {name: importlib.metadata.version(name) for name in RUNTIME_PINS}
    require(runtime == RUNTIME_PINS and not Path(output).exists())
    result = dict(schema=SCHEMA, status='public_diagnostics_in_progress',
        source_run_id=SOURCE_RUN, source_commit=SOURCE_SHA, artifact_id=request['artifact_id'],
        request=request, runtime_packages=runtime, transcribe=copy.deepcopy(TRANSCRIBE),
        asr_settings=copy.deepcopy(ASR_SETTINGS),
        model_load_options=dict(device='cpu', compute_type='int8', num_workers=1, local_files_only=True),
        settings_note='Small reproduces production ASR input preprocessing: original FLOAT samples read as float32, scipy.resample_poly to 16kHz then cast float32, 1 CPU thread and word_timestamps=True. Turbo reproduces the existing crosscheck WAV-path/PyAV preprocessing, 4 CPU threads and word_timestamps=False. This compares models plus configurations and preprocessing; no model-only causal claim or ASR speed claim.',
        raw_gate='Existing canonical zero character errors and zero missing critical terms before pronunciation normalization.',
        normalization_gate='Separate diagnostic only; flags or any lexical/critical mismatch remain non-exact.',
        rows=[], model_assets_sha256={}, exact_public_pcm_verified=True,
        stage='loading_asr_runtime', current_model=None, current_fixture=None,
        synthesized=0, private_audio_used=False, secrets_used=False,
        promotion_performed=False, production_eligible=False, human_listening_verified=False)
    def deadline(*args):
        raise TimeoutError('public_asr_call_timeout')
    previous = signal.signal(signal.SIGALRM, deadline)
    try:
        dump(output, result)
        with offline():
            from faster_whisper import WhisperModel
            for name in MODELS:
                result.update(stage='authenticating_model', current_model=name, current_fixture=None)
                dump(output, result)
                result['model_assets_sha256'][name] = verify_model(Path(models)/name, name)
                result['stage'] = 'loading_model'; dump(output, result)
                model = WhisperModel(str(Path(models)/name), cpu_threads=ASR_SETTINGS[name]['cpu_threads'],
                    **result['model_load_options'])
                for row in selected:
                    result.update(stage='recognizing_fixture', current_fixture=row['fixture'])
                    dump(output, result)
                    signal.alarm(CALL_SECONDS)
                    try:
                        result['rows'].extend(recognize([row], model, name)); dump(output, result)
                    finally:
                        signal.alarm(0)
                del model
        result.update(status='complete_public_diagnostics_no_promotion', stage='complete',
            current_model=None, current_fixture=None)
        dump(output, result)
        print(json.dumps(dict(status=result['status'], checks=len(result['rows']),
            raw_exact=sum(row['raw_canonical_strict']['exact'] for row in result['rows']),
            normalized_exact=sum(row['pronunciation_normalized']['exact'] for row in result['rows']),
            synthesized=0, promoted=0)), flush=True)
    except Exception as error:
        result['status'] = 'public_diagnostic_failed_partial_results_not_approval'
        result['failure'] = public_failure(error)
        dump(output, result)
        raise
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, previous)


def main():
    os.umask(0o077); keyless()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('download-models', 'inspect'))
    parser.add_argument('--models', required=True); parser.add_argument('--input'); parser.add_argument('--output')
    args = parser.parse_args()
    request = validate_request(json.loads(REQUEST.read_text()))
    if args.mode == 'download-models':
        require(args.input is None and args.output is None); download_models(args.models)
    else:
        require(bool(args.input) and bool(args.output)); inspect(args.input, args.models, args.output, request)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"failed","reason":"public_asr_check_validation_failed"}', flush=True)
        raise SystemExit(1)
