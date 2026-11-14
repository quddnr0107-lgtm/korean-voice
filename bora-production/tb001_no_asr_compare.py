"""One-attempt Bora timing comparison on the pinned TB-001 listening units.

The protected worker reads historical receipts offline. This module returns
private WAV paths for the caller to encrypt and numeric timing measurements.
"""

from __future__ import annotations

import base64
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import signal
import socket
import time

import fp32_renderer as fp32
import listening_preview as preview
import no_asr_preview as sample
import tb001_full_diagnostic as full
import tb001_remaining_listen as remaining
from namespace_guard import validate_snapshot


WORKFLOW = full.d.REPO + '/.github/workflows/bora-tb001-no-asr-compare.yml@refs/heads/' + full.d.BRANCH
TARGETS = remaining.TARGETS
GROUPS = ((8, 36, 47), (6, 27, 28), (34, 41, 43), (37, 40, 53), (7, 20, 25))
FIRST_SEED = fp32.SEEDS[0]
MAX_NEW_TOKENS = 1100
UNIT_TIMEOUT_SECONDS = 1200


def require(condition, code='tb001_no_asr_comparison_invalid'):
    if not condition:
        raise ValueError(code)


def check_context(env, uid, euid):
    require(env.get('GITHUB_REPOSITORY') == full.d.REPO
            and env.get('GITHUB_REF') == 'refs/heads/' + full.d.BRANCH
            and env.get('GITHUB_EVENT_NAME') == 'push'
            and env.get('GITHUB_WORKFLOW_REF') == WORKFLOW
            and uid == euid and uid != 0,
            'tb001_no_asr_worker_context_invalid')


def group_indices(group):
    require(type(group) is int and 1 <= group <= len(GROUPS),
            'tb001_no_asr_group_invalid')
    return GROUPS[group - 1]


def _protected_keys():
    check_context(os.environ, os.getuid(), os.geteuid())
    validate_snapshot(os.getuid(), os.getuid(), socket.if_nameindex(),
                      Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
                      os.environ.get('BORA_HOST_NETNS', ''),
                      Path('/proc/net/ipv6_route').read_text())
    encoded = [os.environ.pop(name, '') for name in preview.PRIVATE_KEYS]
    keys = tuple(base64.b64decode(value, validate=True) for value in encoded)
    require(all(len(key) == 32 for key in keys) and keys[0] != keys[1],
            'tb001_no_asr_protected_keys_invalid')

    def deny_outbound(*args, **kwargs):
        raise RuntimeError('outbound_forbidden')

    socket.socket.connect = deny_outbound
    socket.socket.connect_ex = deny_outbound
    socket.create_connection = deny_outbound
    return keys


def _candidate_measurements(candidate):
    signal_stats = candidate.get('signal', {})
    asr = candidate.get('asr', {})
    seconds, samples = candidate.get('tts_seconds'), signal_stats.get('samples')
    require(type(seconds) in (float, int) and math.isfinite(seconds) and seconds > 0
            and type(samples) is int and samples > 0
            and signal_stats.get('sample_rate') == fp32.SAMPLE_RATE
            and signal_stats.get('seconds') == samples / fp32.SAMPLE_RATE
            and signal_stats.get('blocked') is False
            and candidate.get('natural_eos') is True
            and candidate.get('cap_reached') is False
            and not candidate.get('error_code')
            and asr.get('blocked') is True and asr.get('review_required') is True
            and (type(asr.get('character_errors')) is int and asr['character_errors'] > 0
                 or isinstance(asr.get('missing_critical'), list) and asr['missing_critical'])
            and isinstance(candidate.get('sha256'), str)
            and preview.HASH.fullmatch(candidate['sha256']),
            'tb001_no_asr_original_candidate_invalid')
    return float(seconds), samples


def baseline_from_receipt(receipt, review_info, review_sha256):
    """Project original seed timings and the saved review duration."""
    settings = receipt.get('render_settings', {})
    require(receipt.get('status') == 'blocked_requires_review'
            and receipt.get('blocked') is True and receipt.get('review_only') is True
            and receipt.get('production_eligible') is False
            and not receipt.get('review_flags')
            and receipt.get('review_sha256') == review_sha256
            and review_info.get('original_wav_sha256') == review_sha256
            and preview.digest(preview.canonical(settings)) == full.PIN['render_settings_sha256']
            and settings.get('model') == fp32.MODEL_REPOSITORY
            and settings.get('model_revision') == fp32.MODEL_REVISION
            and settings.get('speaker') == 'Sohee' and settings.get('language') == 'Korean'
            and settings.get('device') == 'cpu' and settings.get('dtype') == 'float32'
            and settings.get('attention') == 'sdpa' and settings.get('threads') == 4
            and settings.get('sample_rate') == fp32.SAMPLE_RATE
            and settings.get('seeds') == list(fp32.SEEDS)
            and settings.get('styles') == fp32.STYLES
            and settings.get('sampling') == dict(fp32.SAMPLING, max_new_tokens=MAX_NEW_TOKENS),
            'tb001_no_asr_original_settings_invalid')
    model_assets = settings.get('model_sha256', {})
    require(isinstance(model_assets, dict) and set(model_assets) == set(fp32.MODEL_REQUIRED)
            and all(isinstance(digest, str) and preview.HASH.fullmatch(digest)
                    for digest in model_assets.values()), 'tb001_no_asr_original_assets_invalid')
    candidates = receipt.get('candidates')
    require(isinstance(candidates, list)
            and [candidate.get('seed') for candidate in candidates] == list(fp32.SEEDS),
            'tb001_no_asr_original_attempts_invalid')
    measures = [_candidate_measurements(candidate) for candidate in candidates]
    saved_candidates = [candidate for candidate in candidates if candidate['sha256'] == review_sha256]
    require(len(saved_candidates) == 1
            and review_info['sample_count'] == saved_candidates[0]['signal']['samples']
            and review_info['sample_rate'] == fp32.SAMPLE_RATE,
            'tb001_no_asr_original_review_invalid')
    elapsed = [seconds for seconds, _ in measures]
    audio = [samples / fp32.SAMPLE_RATE for _, samples in measures]
    return dict(old_attempts=len(candidates), old_tts_seconds=math.fsum(elapsed),
                old_first_tts_seconds=elapsed[0], old_retry_tts_seconds=math.fsum(elapsed[1:]),
                old_audio_seconds=math.fsum(audio), old_first_audio_seconds=audio[0],
                old_strict_reference_audio_seconds=review_info['seconds'],
                old_first_seed=candidates[0]['seed'], old_first_wav_sha256=candidates[0]['sha256'],
                old_strict_reference_wav_sha256=review_sha256,
                old_model_assets_sha256=model_assets)


def load_private_inputs(directory, work, group):
    """Restore the original source and receipts before loading the TTS model."""
    indices = group_indices(group)
    source_key, media_key = _protected_keys()
    import runtime

    campaign, recipe, _, _ = runtime.config()
    require(runtime.identity(recipe) == full.PIN['recipe_sha256'])
    full.inventory(directory)
    with runtime.quiet():
        plan, _ = runtime.source_plan(full.PIN['lesson_id'], campaign, source_key)
    del source_key
    require(runtime.identity(plan) == full.PIN['plan_sha256']
            and len(plan['units']) == full.PIN['units'])
    with runtime.quiet():
        rows = runtime.restore(directory, media_key, plan, full.PIN['recipe_sha256'],
                               full.PIN['render_settings_sha256'], Path(work))
    del media_key
    selected = remaining.select_remaining(preview.available_reviews(rows, plan), remaining.read_evidence())
    saved_by_index = {row['index']: row for row in selected}
    results = []
    for index in indices:
        unit = plan['units'][index]
        require(not fp32.validate_unit(unit), 'tb001_no_asr_original_source_requires_review')
        review = saved_by_index[index]
        baseline = baseline_from_receipt(rows[index]['receipt'], review['info'], review['wav_sha256'])
        results.append((index, unit, baseline))
    assets = results[0][2]['old_model_assets_sha256']
    require(all(baseline['old_model_assets_sha256'] == assets for _, _, baseline in results),
            'tb001_no_asr_original_model_assets_disagree')
    return results


def download(model_dir):
    return sample.download(model_dir)


def _cpu_model():
    names = {line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
             if line.startswith('model name') and ':' in line}
    require(platform.machine() == 'x86_64' and len(names) == 1, 'tb001_no_asr_cpu_platform_invalid')
    return names.pop()


def load_model(model_dir, *, expected_assets):
    """Load original TTS weights and runtime packages without an ASR model."""
    fp32.require_guard()
    sample.assert_no_recognizer_modules()
    started = time.perf_counter()
    require(expected_assets is not None, 'tb001_no_asr_original_assets_required')
    cpu = _cpu_model()
    assets = fp32.verify_assets(model_dir, fp32.MODEL_REQUIRED, fp32.MODEL_REVISION, expected_assets)
    config = json.loads((Path(model_dir) / 'config.json').read_text())
    require(config.get('model_type') == 'qwen3_tts' and config.get('tts_model_type') == 'custom_voice'
            and config.get('tts_model_size') == '1b7', 'tb001_no_asr_architecture_invalid')
    packages = {name: importlib.metadata.version(name) for name in sample.TTS_PACKAGES}
    require(packages == sample.TTS_PACKAGES, 'tb001_no_asr_runtime_version_invalid')
    verified = time.perf_counter()
    with fp32.quiet_native():
        import torch
        import soundfile
        from qwen_tts import Qwen3TTSModel

        torch.set_num_threads(4)
        model = Qwen3TTSModel.from_pretrained(str(model_dir), device_map='cpu', dtype=torch.float32,
            attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True)
    require(torch.get_num_threads() == 4
            and {p.dtype for p in model.model.parameters() if p.is_floating_point()} == {torch.float32}
            and {p.device.type for p in model.model.parameters()} == {'cpu'},
            'tb001_no_asr_runtime_dtype_or_threads_invalid')
    sample.assert_no_recognizer_modules()
    return model, torch, soundfile, dict(
        model=fp32.MODEL_REPOSITORY, model_revision=fp32.MODEL_REVISION, model_assets_sha256=assets,
        architecture=platform.machine(), cpu_model=cpu, cpu_threads=torch.get_num_threads(),
        cpu_interop_threads=torch.get_num_interop_threads(), cpu_count=os.cpu_count(),
        device='cpu', dtype='float32', attention='sdpa', packages=packages,
        asset_verification_seconds=verified - started, model_load_seconds=time.perf_counter() - verified,
        asr_performed=False)


def _generate_once(model, torch, unit):
    talker = model.model.talker
    original = talker.generate
    observed = {}
    eos_id = int(model.model.config.talker_config.codec_eos_token_id)

    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.update(fp32.eos_observation(result.sequences[0].tolist(), eos_id, MAX_NEW_TOKENS))
        return result

    def deadline(signum, frame):
        raise TimeoutError('tb001_no_asr_unit_timeout')

    previous = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(UNIT_TIMEOUT_SECONDS)
    talker.generate = observe
    try:
        started = time.perf_counter()
        torch.manual_seed(FIRST_SEED)
        with fp32.quiet_native(), torch.inference_mode():
            waves, rate = model.generate_custom_voice(text=unit['spoken'], language='Korean', speaker='Sohee',
                instruct=fp32.STYLES[unit['mode']], **fp32.SAMPLING, max_new_tokens=MAX_NEW_TOKENS)
        elapsed = time.perf_counter() - started
    finally:
        talker.generate = original
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    require(len(waves) == 1 and rate == fp32.SAMPLE_RATE and observed.get('natural_eos') is True,
            'tb001_no_asr_audio_shape_or_eos_invalid')
    return waves[0], rate, observed, elapsed


def render_unit(model, torch, soundfile, index, unit, baseline, output_dir):
    """Generate the original first-try voice once and retain the raw WAV."""
    fp32.require_guard()
    require(index in TARGETS and not fp32.validate_unit(unit)
            and unit['id'].startswith(full.PIN['lesson_id'] + ':'), 'tb001_no_asr_unit_invalid')
    out = Path(output_dir)
    require(out.is_dir() and not any(path.is_symlink() for path in (out, *out.parents)),
            'tb001_no_asr_private_directory_invalid')
    wav = out / f'unit-{index:03d}.wav'
    require(not wav.exists() and not wav.is_symlink(), 'tb001_no_asr_wav_exists')
    sample.assert_no_recognizer_modules()
    started = time.perf_counter()
    wave, rate, observed, elapsed = _generate_once(model, torch, unit)
    sample.assert_no_recognizer_modules()
    measured = fp32.signal_stats(wave, rate)
    require(measured['blocked'] is False, 'tb001_no_asr_audio_signal_invalid')
    soundfile.write(str(wav), wave, rate, subtype='FLOAT')
    os.chmod(wav, 0o600)
    require(0 < wav.stat().st_size <= preview.MAX_WAV_BYTES, 'tb001_no_asr_wav_bounds_invalid')
    audio = preview.wav_info(wav.read_bytes())
    require(audio['sample_count'] == measured['samples'] and audio['sample_rate'] == rate,
            'tb001_no_asr_wav_samples_invalid')
    wav_sha256 = audio['original_wav_sha256']
    row = {key: baseline[key] for key in ('old_attempts', 'old_tts_seconds', 'old_first_tts_seconds',
        'old_retry_tts_seconds', 'old_audio_seconds', 'old_first_audio_seconds',
        'old_strict_reference_audio_seconds', 'old_first_seed')}
    row.update(unit_index=index, new_attempts=1, new_seed=FIRST_SEED, new_tts_seconds=elapsed,
        new_unit_wall_seconds=time.perf_counter() - started, audio_seconds=audio['seconds'],
        audio_sample_rate=rate, audio_sample_count=audio['sample_count'], wav_sha256=wav_sha256,
        matches_old_first_wav=wav_sha256 == baseline['old_first_wav_sha256'],
        matches_old_saved_review_wav=wav_sha256 == baseline['old_strict_reference_wav_sha256'],
        generated_sequence_tokens=observed['generated_sequence_tokens'], natural_eos=True,
        signal_passed=True, asr_performed=False, production_eligible=False)
    return wav, row
