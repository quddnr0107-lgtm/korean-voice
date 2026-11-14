"""One new public original-Bora CPU sample, with no speech recognition.

This deliberately does not instantiate FP32Renderer: its production constructor
loads a recognition model. Only its pure model/namespace/sampling helpers are
reused. Audio is an untrimmed, unfiltered listening preview, never a release.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

from download_models import require_keyless
from fp32_renderer import (MODEL_REPOSITORY, MODEL_REVISION, MODEL_REQUIRED,
    SAMPLING, STYLES, EXPECTED_PACKAGES, eos_observation, file_hash,
    quiet_native, require_guard, signal_stats, verify_assets)

SCHEMA = 'bora-no-asr-preview/v1'
REPOSITORY = 'quddnr0107-lgtm/korean-voice'
BRANCH = 'integrated-lectures-premium-20261028'
WORKFLOW = REPOSITORY+'/.github/workflows/bora-no-asr-preview.yml@refs/heads/'+BRANCH
SOURCE = ('오늘은 문장을 짧게 나누어 읽는 연습을 하겠습니다. 먼저 누가 무엇을 하는지 살펴보세요. '
          '조건과 예외를 구분하고, 중요한 단어를 놓치지 않도록 차분하게 들어 보겠습니다.')
REQUEST = Path(__file__).with_name('no-asr-preview-request.json')
SEED, MAX_NEW_TOKENS, SAMPLE_RATE = 5192, 1100, 24000
TTS_PACKAGES = {key: EXPECTED_PACKAGES[key] for key in
    ('torch', 'torchaudio', 'qwen-tts', 'transformers', 'numpy', 'soundfile', 'scipy')}
STAGE = 'request'


def require(value):
    if not value:
        raise ValueError('no_asr_preview_validation_failed')


def expected_request():
    return dict(schema=SCHEMA, request_id='bora-no-asr-20261114-01',
        purpose='public-raw-listening-preview-without-asr', source=SOURCE,
        seed=SEED, mode='explain', synthesis=True, asr=False,
        production_eligible=False)


def validate_request(request):
    require(request == expected_request())
    return request


def check_context(env, uid, euid):
    require(env.get('GITHUB_REPOSITORY') == REPOSITORY
        and env.get('GITHUB_REF') == 'refs/heads/'+BRANCH
        and env.get('GITHUB_EVENT_NAME') == 'push'
        and env.get('GITHUB_WORKFLOW_REF') == WORKFLOW
        and uid == euid and uid != 0)


def dump(path, value):
    path = Path(path)
    require(not path.is_symlink())
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.raw-summary-', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write('\n')
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def download(model_dir):
    """Download only the fixed TTS model files; there is no ASR asset path."""
    require_keyless()
    root = Path(model_dir)
    require(not root.is_symlink() and not any(p.is_symlink() for p in root.parents))
    os.environ.update(HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                      HF_HUB_DISABLE_PROGRESS_BARS='1', DO_NOT_TRACK='1')
    from huggingface_hub import snapshot_download
    started = time.perf_counter()
    snapshot_download(repo_id=MODEL_REPOSITORY, revision=MODEL_REVISION,
        local_dir=str(root), allow_patterns=list(MODEL_REQUIRED), max_workers=4)
    assets = verify_assets(root, MODEL_REQUIRED, MODEL_REVISION)
    summary = dict(schema=SCHEMA, status='pinned_tts_only_assets_verified',
        model=MODEL_REPOSITORY, revision=MODEL_REVISION,
        model_assets_sha256=assets, download_and_verify_seconds=time.perf_counter()-started,
        recognition_models_downloaded=0, asr_performed=False)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary


def generation_kwargs():
    return dict(text=SOURCE, language='Korean', speaker='Sohee',
        instruct=STYLES['explain'], **SAMPLING, max_new_tokens=MAX_NEW_TOKENS)


def assert_no_recognizer_modules():
    require(not any(name.split('.', 1)[0] in
        ('faster_whisper', 'whisper', 'whisperx', 'vosk', 'speech_recognition')
        for name in sys.modules))


def generate_once(model, torch_module):
    """Observe the unchanged original generation's EOS, without altering tokens."""
    talker = model.model.talker
    original = talker.generate
    observed = {}
    eos_id = int(model.model.config.talker_config.codec_eos_token_id)

    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.update(eos_observation(result.sequences[0].tolist(), eos_id, MAX_NEW_TOKENS))
        return result

    talker.generate = observe
    started = time.perf_counter()
    try:
        torch_module.manual_seed(SEED)
        with torch_module.inference_mode():
            waves, rate = model.generate_custom_voice(**generation_kwargs())
    finally:
        talker.generate = original
    elapsed = time.perf_counter()-started
    require(len(waves) == 1 and rate == SAMPLE_RATE and observed.get('natural_eos') is True)
    return waves[0], rate, observed, elapsed


def optional_mp3(wav, mp3):
    """Create a verified playback copy, keeping any codec failure nonfatal."""
    started = time.perf_counter()
    phase = 'find_ffmpeg'
    temporary = None

    def outcome(category=None, returncode=None, sha256=None, size=0):
        return dict(status='ready' if category is None else 'unavailable',
                    phase=phase, error_category=category, returncode=returncode,
                    seconds=time.perf_counter()-started, sha256=sha256, bytes=size)

    try:
        ffmpeg = shutil.which('ffmpeg')
        if ffmpeg is None:
            return outcome('ffmpeg_unavailable')
        phase = 'encode'
        require(not mp3.exists() and not mp3.is_symlink())
        with tempfile.NamedTemporaryFile(dir=mp3.parent, prefix='.raw-preview-',
                                         suffix='.mp3', delete=False) as handle:
            temporary = Path(handle.name)
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-i', str(wav), '-map_metadata', '-1', '-c:a', 'libmp3lame', '-b:a', '128k',
            '-ar', str(SAMPLE_RATE), '-ac', '1', str(temporary)], check=True,
            capture_output=True, timeout=120)
        phase = 'decode_check'
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-i', str(temporary), '-f', 'null', '-'], check=True, capture_output=True, timeout=120)
        size = temporary.stat().st_size
        require(size > 0)
        digest = file_hash(temporary)
        os.replace(temporary, mp3)
        return outcome(sha256=digest, size=size)
    except subprocess.CalledProcessError as error:
        return outcome('ffmpeg_failed', returncode=error.returncode)
    except subprocess.TimeoutExpired:
        return outcome('ffmpeg_timeout')
    except FileNotFoundError:
        return outcome('ffmpeg_or_file_unavailable')
    except OSError:
        return outcome('playback_io_error')
    except ValueError:
        return outcome('invalid_playback_copy')
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # The unverified derivative is hidden and never named raw-preview.mp3.


def finish_preview(output, result, started):
    """Persist the raw result before trying any optional playback conversion."""
    summary = output/'no-asr-preview-summary.json'
    result.update(raw_audio_ready=True, mp3_sha256=None, mp3_bytes=0,
        audio_reencoded_for_playback=False,
        playback_conversion=dict(status='not_attempted', phase='not_started',
                                 error_category=None, returncode=None))
    result['timing_seconds'].update(raw_ready=time.perf_counter()-started,
                                    mp3_encode_and_decode=None,
                                    total_render=time.perf_counter()-started)
    dump(summary, result)
    (output/'source.txt').write_text(SOURCE+'\n', encoding='utf-8')

    converted = optional_mp3(output/'raw-preview.wav', output/'raw-preview.mp3')
    result['playback_conversion'] = {key: converted[key] for key in
        ('status', 'phase', 'error_category', 'returncode')}
    result.update(mp3_sha256=converted['sha256'], mp3_bytes=converted['bytes'],
                  audio_reencoded_for_playback=converted['status'] == 'ready')
    result['timing_seconds'].update(mp3_encode_and_decode=converted['seconds'],
                                    total_render=time.perf_counter()-started)
    dump(summary, result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


def render(model_dir, output_dir):
    global STAGE
    started = time.perf_counter()
    STAGE = 'context'
    require_keyless()
    assert_no_recognizer_modules()
    check_context(os.environ, os.getuid(), os.geteuid())
    require_guard()
    def deny(*args, **kwargs):
        raise RuntimeError('outbound_forbidden')
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    output = Path(output_dir)
    require(not output.exists() and output.parent.is_dir()
        and not any(p.is_symlink() for p in output.parents))
    STAGE = 'assets'
    verification_start = time.perf_counter()
    assets = verify_assets(model_dir, MODEL_REQUIRED, MODEL_REVISION)
    config = json.loads((Path(model_dir)/'config.json').read_text())
    require(config.get('model_type') == 'qwen3_tts'
        and config.get('tts_model_type') == 'custom_voice'
        and config.get('tts_model_size') == '1b7')
    packages = {key: importlib.metadata.version(key) for key in TTS_PACKAGES}
    require(packages == TTS_PACKAGES)
    verification_seconds = time.perf_counter()-verification_start
    STAGE = 'model_load'
    load_start = time.perf_counter()
    with quiet_native():
        import torch
        import soundfile as sf
        from qwen_tts import Qwen3TTSModel
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        model = Qwen3TTSModel.from_pretrained(str(model_dir), device_map='cpu',
            dtype=torch.float32, attn_implementation='sdpa', low_cpu_mem_usage=True,
            local_files_only=True)
    require(torch.get_num_threads() == 4 and torch.get_num_interop_threads() == 1
        and {p.dtype for p in model.model.parameters() if p.is_floating_point()} == {torch.float32}
        and {p.device.type for p in model.model.parameters()} == {'cpu'})
    assert_no_recognizer_modules()
    load_seconds = time.perf_counter()-load_start
    STAGE = 'synthesis'
    def deadline(*args):
        raise TimeoutError('public_preview_generation_deadline')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(600)
    try:
        with quiet_native():
            wave, rate, observed, synthesis_seconds = generate_once(model, torch)
    finally:
        signal.alarm(0)
    assert_no_recognizer_modules()
    STAGE = 'signal'
    stats = signal_stats(wave, rate)
    require(stats['blocked'] is False and stats['samples'] > 0)
    output.mkdir(mode=0o700)
    STAGE = 'wav_write'
    write_start = time.perf_counter()
    wav = output/'raw-preview.wav'
    sf.write(str(wav), wave, rate, subtype='FLOAT')
    require(0 < wav.stat().st_size <= 16*1024*1024)
    write_seconds = time.perf_counter()-write_start
    STAGE = 'receipt_and_optional_playback'
    result = dict(schema=SCHEMA, status='new_public_raw_audio_without_asr',
        source=SOURCE, source_sha256=hashlib.sha256(SOURCE.encode('utf-8')).hexdigest(), seed=SEED,
        model=MODEL_REPOSITORY, model_revision=MODEL_REVISION,
        model_assets_sha256=assets, device='cpu', dtype='float32', cpu_threads=4,
        cpu_interop_threads=1,
        attention='sdpa', speaker='Sohee', language='Korean', mode='explain',
        original_style=STYLES['explain'], sampling=dict(SAMPLING, max_new_tokens=MAX_NEW_TOKENS),
        packages=packages, timing_seconds=dict(asset_verification=verification_seconds,
        model_load=load_seconds, synthesis=synthesis_seconds, wav_write=write_seconds),
        audio_seconds=stats['seconds'], sample_rate=rate, sample_count=stats['samples'],
        signal=stats, eos=observed,
        wav_sha256=file_hash(wav), wav_bytes=wav.stat().st_size,
        recognition_models_downloaded=0, recognition_models_loaded=0,
        asr_performed=False, transcript_generated=False, audio_trimmed=False,
        audio_filtered=False, audio_tempo_factor=1, audio_pitch_shift=0,
        production_eligible=False, production_status_changed=False, human_listening_required=True)
    return finish_preview(output, result, started)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('validate', 'download', 'render'))
    parser.add_argument('--model-dir')
    parser.add_argument('--output-dir')
    args = parser.parse_args()
    validate_request(json.loads(REQUEST.read_text()))
    if args.mode == 'validate':
        require(args.model_dir is None and args.output_dir is None)
        print('{"status":"public_no_asr_request_verified"}')
    elif args.mode == 'download':
        require(bool(args.model_dir) and args.output_dir is None)
        download(args.model_dir)
    else:
        require(bool(args.model_dir) and bool(args.output_dir))
        render(args.model_dir, args.output_dir)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps(dict(status='failed', reason='public_no_asr_preview_failed', stage=STAGE)), flush=True)
        raise SystemExit(1)
