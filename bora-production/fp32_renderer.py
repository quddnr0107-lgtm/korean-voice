"""Offline, reusable renderer for the owner's selected Bora option 3.

The caller owns source decryption, checkpoint authentication, encryption and
publication. This module reads local pinned models and writes PRIVATE unit
audio/receipts only. A passed receipt means automatic checks passed; it never
claims that a person listened. No fallback engine or automatic tempo change.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / 'research' / 'reading-quality', ROOT / 'lecture-premium' / 'code'):
    if str(_path) not in sys.path:
        sys.path.append(str(_path))
from pilot_render import (STYLES, FILTERS, canonical, file_hash, identity,
                          signal_stats, transcript_score, trimmed_faded)
from namespace_guard import validate_snapshot

SCHEMA = 'bora-fp32-unit-receipt/v1'
RECIPE_SCHEMA = 'bora-python-fp32-production/v1'
MODEL_REPOSITORY = 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'
MODEL_REVISION = '0c0e3051f131929182e2c023b9537f8b1c68adfe'
ASR_REPOSITORY = 'Systran/faster-whisper-small'
ASR_REVISION = '536b0662742c02347bc0e980a01041f333bce120'
SEEDS = (5192, 7319)
SAMPLE_RATE = 24000
MAX_AUDIO_BYTES = 16 * 1024 * 1024
MODEL_REQUIRED = {
    'model.safetensors': 3833402552,
    'speech_tokenizer/model.safetensors': 682293092,
    'config.json': None, 'generation_config.json': None,
    'tokenizer_config.json': None, 'preprocessor_config.json': None,
    'vocab.json': None, 'merges.txt': None,
    'speech_tokenizer/config.json': None,
    'speech_tokenizer/configuration.json': None,
    'speech_tokenizer/preprocessor_config.json': None,
}
ASR_REQUIRED = {'model.bin': 483546902, 'config.json': None,
                'tokenizer.json': None, 'vocabulary.txt': None}
EXPECTED_PACKAGES = {
    'torch': '2.8.0+cpu', 'torchaudio': '2.8.0+cpu', 'qwen-tts': '0.1.1',
    'transformers': '4.57.3', 'faster-whisper': '1.2.1',
    'soundfile': '0.14.0', 'cryptography': '50.0.0', 'numpy': '2.5.4',
    'scipy': '1.18.1', 'onnxruntime': '1.31.1',
}
SAMPLING = dict(non_streaming_mode=True, do_sample=True, temperature=0.8,
                top_p=0.9, top_k=50, repetition_penalty=1.05,
                subtalker_dosample=True, subtalker_temperature=0.9,
                subtalker_top_p=1.0, subtalker_top_k=50)
_ID = re.compile(r'(?P<lesson>(?P<course>TB|DR|RL|MS)-(?P<number>[0-9]{3})):p[0-9]{4,8}:u[0-9]{3,8}')
_LIMITS = {'TB': 25, 'DR': 78, 'RL': 34, 'MS': 166}
_HASH = re.compile(r'[a-f0-9]{64}')


class RenderFailure(RuntimeError):
    """A fixed public code; never interpolate source text or native errors."""


def require_guard():
    """Require the wrapper's existing OS-level, unprivileged offline guard."""
    if any(os.environ.get(name) for name in (
        'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')):
        raise RenderFailure('keys_in_renderer_environment')
    if os.geteuid() != os.getuid() or os.geteuid() == 0:
        raise RenderFailure('renderer_must_be_unprivileged')
    try:
        validate_snapshot(
            os.getuid(), os.getuid(), socket.if_nameindex(),
            Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
            os.environ.get('BORA_HOST_NETNS', ''),
            Path('/proc/net/ipv6_route').read_text())
    except (OSError, ValueError, RuntimeError):
        raise RenderFailure('offline_namespace_guard_failed') from None
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                      HF_HUB_DISABLE_PROGRESS_BARS='1', DO_NOT_TRACK='1',
                      ORT_DISABLE_TELEMETRY='1')


@contextlib.contextmanager
def quiet_native():
    """Prevent native-library output from printing protected manuscript text."""
    sys.stdout.flush(); sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    dev = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(dev, 1); os.dup2(dev, 2)
        yield
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        os.dup2(saved[0], 1); os.dup2(saved[1], 2)
        for fd in saved + [dev]:
            os.close(fd)


def _atomic_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise RenderFailure('output_symlink')
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.receipt-', delete=False) as handle:
        tmp = Path(handle.name)
        try:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write('\n')
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or any(p.is_symlink() for p in path.parents):
        raise RenderFailure('model_asset_not_regular')
    return path


def verify_assets(root, required, revision, expected_hashes=None):
    """Authenticate local bytes against fixed-revision download evidence.

HF snapshot_download(local_dir=...) records commit/ETag in .metadata. An
explicit SHA256 manifest from the keyless downloader can replace this cache;
it must cover exactly the required files. No model is fetched by this module.
"""
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise RenderFailure('model_directory_invalid')
    if expected_hashes is not None and (not isinstance(expected_hashes, dict)
            or set(expected_hashes) != set(required)
            or any(not isinstance(h, str) or not _HASH.fullmatch(h)
                   for h in expected_hashes.values())):
        raise RenderFailure('asset_manifest_invalid')
    hashes = {}
    for name, size in required.items():
        path = _regular(root / name)
        actual_size = path.stat().st_size
        if actual_size <= 0 or (size is not None and actual_size != size):
            raise RenderFailure('pinned_model_asset_size_mismatch')
        actual = file_hash(path)
        if expected_hashes is not None:
            if actual != expected_hashes[name]:
                raise RenderFailure('pinned_model_asset_hash_mismatch')
        else:
            metadata = _regular(root / '.cache' / 'huggingface' / 'download' / (name + '.metadata'))
            if metadata.stat().st_size > 4096:
                raise RenderFailure('download_metadata_invalid')
            rows = metadata.read_text().splitlines()
            if len(rows) < 3 or rows[0] != revision:
                raise RenderFailure('pinned_model_revision_mismatch')
            etag = rows[1].strip('"')
            if _HASH.fullmatch(etag):
                expected = actual
            elif re.fullmatch('[a-f0-9]{40}', etag):
                h = hashlib.sha1(b'blob ' + str(actual_size).encode() + b'\0')
                with path.open('rb') as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                        h.update(chunk)
                expected = h.hexdigest()
            else:
                raise RenderFailure('download_etag_invalid')
            if expected != etag:
                raise RenderFailure('pinned_model_asset_hash_mismatch')
        if name.endswith('.json'):
            try:
                json.loads(path.read_text())
            except (ValueError, OSError):
                raise RenderFailure('model_json_invalid') from None
        hashes[name] = actual
    return hashes


def validate_unit(unit):
    if not isinstance(unit, dict):
        raise RenderFailure('unit_schema_invalid')
    ident = unit.get('id')
    match = _ID.fullmatch(ident) if isinstance(ident, str) else None
    if not match or not 1 <= int(match['number']) <= _LIMITS[match['course']]:
        raise RenderFailure('unit_id_out_of_scope')
    if unit.get('paragraph_id') != unit['id'].rsplit(':u', 1)[0]:
        raise RenderFailure('unit_paragraph_mismatch')
    source, spoken = unit.get('source'), unit.get('spoken')
    if not isinstance(source, str) or not source.strip() or not isinstance(spoken, str) or not spoken.strip():
        raise RenderFailure('unit_text_invalid')
    start, end = unit.get('start_utf16'), unit.get('end_utf16')
    if (type(start) is not int or type(end) is not int or start < 0 or end <= start
            or end - start != len(source.encode('utf-16-le')) // 2):
        raise RenderFailure('unit_source_offsets_invalid')
    if unit.get('source_sha256') != hashlib.sha256(source.encode()).hexdigest():
        raise RenderFailure('unit_source_hash_mismatch')
    if unit.get('mode') not in STYLES:
        raise RenderFailure('unit_mode_invalid')
    terms = unit.get('critical_terms', [])
    if (not isinstance(terms, list) or len(terms) > 100
            or any(not isinstance(t, str) or not canonical(t)
                   or canonical(t) not in canonical(spoken) for t in terms)):
        raise RenderFailure('unit_critical_terms_invalid')
    flags = []
    for field in ('review', 'review_flags'):
        value = unit.get(field, [])
        if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
            raise RenderFailure('unit_review_flags_invalid')
        flags.extend(value)
    if unit.get('reading_mode_verified') is not True:
        flags.append('reading_mode_unverified')
    if ('start_cp' in unit) != ('end_cp' in unit):
        raise RenderFailure('unit_codepoint_offsets_invalid')
    if 'start_cp' in unit and (type(unit['start_cp']) is not int or type(unit['end_cp']) is not int
            or unit['start_cp'] < 0 or unit['end_cp'] - unit['start_cp'] != len(source)):
        raise RenderFailure('unit_codepoint_offsets_invalid')
    return sorted(set(flags))


def eos_observation(tokens, eos_id, max_new_tokens):
    if (not isinstance(tokens, list) or not tokens or any(type(t) is not int for t in tokens)
            or type(eos_id) is not int or type(max_new_tokens) is not int):
        raise RenderFailure('generation_token_observation_invalid')
    seen = eos_id in tokens
    capped = len(tokens) >= max_new_tokens
    return dict(generated_sequence_tokens=len(tokens), eos_token_id=eos_id,
                eos_seen=seen, last_token=tokens[-1], cap_reached=capped,
                natural_eos=seen and tokens[-1] == eos_id and not capped)


def candidate_passed(candidate):
    signal, asr = candidate.get('signal', {}), candidate.get('asr', {})
    return (candidate.get('natural_eos') is True and signal.get('blocked') is False
            and asr.get('blocked') is False and asr.get('review_required') is False
            and type(asr.get('character_errors')) is int and asr['character_errors'] == 0
            and asr.get('missing_critical') == [] and not candidate.get('error_code'))


def candidate_order(candidate):
    asr = candidate.get('asr', {})
    return (candidate.get('signal', {}).get('blocked') is not False,
            candidate.get('natural_eos') is not True,
            len(asr.get('missing_critical', [])), asr.get('character_errors', 10**9),
            candidate['seed'])


class FP32Renderer:
    """Load once, then call render_unit(unit, fresh_private_directory) repeatedly."""

    def __init__(self, model_dir, asr_dir, *, max_new_tokens=1100, max_attempts=2,
                 expected_assets=None):
        require_guard()
        if max_new_tokens != 1100 or type(max_attempts) is not int or max_attempts not in (1, 2):
            raise RenderFailure('unapproved_render_settings')
        self.model_dir, self.asr_dir = Path(model_dir), Path(asr_dir)
        if expected_assets is not None and (not isinstance(expected_assets, dict)
                or set(expected_assets) != {'model_revision', 'asr_revision', 'model_sha256', 'asr_sha256'}
                or expected_assets['model_revision'] != MODEL_REVISION
                or expected_assets['asr_revision'] != ASR_REVISION):
            raise RenderFailure('asset_manifest_revision_mismatch')
        assets = dict(
            model_sha256=verify_assets(self.model_dir, MODEL_REQUIRED, MODEL_REVISION,
                                      expected_assets['model_sha256'] if expected_assets else None),
            asr_sha256=verify_assets(self.asr_dir, ASR_REQUIRED, ASR_REVISION,
                                    expected_assets['asr_sha256'] if expected_assets else None))
        config = json.loads((self.model_dir / 'config.json').read_text())
        if (config.get('model_type') != 'qwen3_tts' or config.get('tts_model_type') != 'custom_voice'
                or config.get('tts_model_size') != '1b7'):
            raise RenderFailure('wrong_model_architecture')
        try:
            packages = {name: importlib.metadata.version(name) for name in EXPECTED_PACKAGES}
        except importlib.metadata.PackageNotFoundError:
            raise RenderFailure('runtime_package_missing') from None
        if packages != EXPECTED_PACKAGES:
            raise RenderFailure('runtime_package_version_mismatch')
        self.ffmpeg = shutil.which('ffmpeg')
        if not self.ffmpeg:
            raise RenderFailure('ffmpeg_missing')
        ffmpeg_version = subprocess.run([self.ffmpeg, '-version'], check=True,
                                       capture_output=True, text=True).stdout.splitlines()[0]
        self.settings = dict(
            schema=RECIPE_SCHEMA, engine='qwen-tts-python', dtype='float32', threads=4,
            attention='sdpa', device='cpu', inference_mode=True,
            model=MODEL_REPOSITORY, model_revision=MODEL_REVISION,
            asr_model=ASR_REPOSITORY, asr_revision=ASR_REVISION,
            speaker='Sohee', language='Korean', styles=copy.deepcopy(STYLES),
            filters=copy.deepcopy(FILTERS), sampling=dict(SAMPLING, max_new_tokens=max_new_tokens),
            seeds=list(SEEDS[:max_attempts]), sample_rate=SAMPLE_RATE,
            paragraph_gap_s=0.35, unit_gap_s=0.18,
            trim=dict(retained_edge_ms=120, fade_ms=5), tempo_factor=1, pitch_shift=0,
            transcript_gate='zero_canonical_errors_and_no_missing_critical/v1',
            asr=dict(compute_type='int8', cpu_threads=1, num_workers=1, language='ko',
                     beam_size=5, temperature=0, condition_on_previous_text=False,
                     vad_filter=False, word_timestamps=True),
            packages=packages, ffmpeg_version=ffmpeg_version,
            code_sha256={str(p.relative_to(ROOT)): file_hash(p) for p in (
                Path(__file__), ROOT / 'bora-production/pilot_render.py',
                ROOT / 'bora-production/quality.py',
                ROOT / 'bora-production/paragraph_plan.py',
                ROOT / 'bora-production/production_plan.py',
                ROOT / 'bora-production/planning.py',
                ROOT / 'bora-production/middot_readings.py',
                ROOT / 'bora-production/tts_normalize.py',
                ROOT / 'bora-production/approved-tb-annotations.json')}, **assets)
        self.settings_sha256 = identity(self.settings)
        self.max_new_tokens = max_new_tokens
        self.seeds = SEEDS[:max_attempts]
        try:
            with quiet_native():
                import onnxruntime as ort
                ort.disable_telemetry_events()
                import torch
                import soundfile as sf
                from qwen_tts import Qwen3TTSModel
                from faster_whisper import WhisperModel
                torch.set_num_threads(4)
                self.torch, self.sf = torch, sf
                self.model = Qwen3TTSModel.from_pretrained(
                    str(self.model_dir), device_map='cpu', dtype=torch.float32,
                    attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True)
                floating = {p.dtype for p in self.model.model.parameters() if p.is_floating_point()}
                if floating != {torch.float32} or torch.get_num_threads() != 4:
                    raise RenderFailure('runtime_dtype_or_threads_mismatch')
                self.asr = WhisperModel(str(self.asr_dir), device='cpu', compute_type='int8',
                                        cpu_threads=1, num_workers=1, local_files_only=True)
        except RenderFailure:
            raise
        except TimeoutError:
            raise
        except Exception:
            raise RenderFailure('runtime_model_load_failed') from None

    @property
    def render_settings(self):
        return self.settings

    @property
    def render_settings_sha256(self):
        return self.settings_sha256

    def _recognize(self, wave, sr):
        import numpy as np
        from scipy.signal import resample_poly
        a = resample_poly(wave, 16000 // math.gcd(16000, sr),
                          sr // math.gcd(16000, sr)).astype('float32')
        with quiet_native():
            segments, _ = self.asr.transcribe(a, language='ko', beam_size=5, temperature=0,
                condition_on_previous_text=False, vad_filter=False, word_timestamps=True)
            segments = list(segments)
        return dict(recognized=' '.join(s.text.strip() for s in segments),
                    words=[dict(text=w.word, start_s=w.start, end_s=w.end, probability=w.probability)
                           for s in segments for w in (s.words or [])],
                    timing_basis='raw_candidate_asr_not_forced_source_alignment')

    def _candidate(self, unit, directory, seed):
        observed = {}
        talker = self.model.model.talker
        original = talker.generate
        eos_id = int(self.model.model.config.talker_config.codec_eos_token_id)

        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            observed.update(eos_observation(result.sequences[0].tolist(), eos_id, self.max_new_tokens))
            return result

        talker.generate = observe
        started = time.perf_counter()
        try:
            self.torch.manual_seed(seed)
            with quiet_native(), self.torch.inference_mode():
                waves, sr = self.model.generate_custom_voice(
                    text=unit['spoken'], language='Korean', speaker='Sohee',
                    instruct=self.settings['styles'][unit['mode']],
                    **self.settings['sampling'])
            tts_s = time.perf_counter() - started
        except TimeoutError:
            raise
        except Exception:
            raise RenderFailure('unit_tts_failed') from None
        finally:
            talker.generate = original
        if len(waves) != 1 or sr != SAMPLE_RATE:
            raise RenderFailure('generated_audio_shape_or_rate_mismatch')
        wave = waves[0]
        signal = signal_stats(wave, sr)
        if not observed:
            raise RenderFailure('natural_eos_not_observed')
        name = f'candidate-{seed}.wav'
        path = directory / name
        self.sf.write(path, wave, sr, subtype='FLOAT')
        if not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
            path.unlink(missing_ok=True)
            raise RenderFailure('candidate_audio_size_invalid')
        recognized = self._recognize(wave, sr) if not signal['blocked'] else dict(recognized='', words=[])
        # Every mismatch remains blocking here, including small CER disagreements.
        recognized.update(transcript_score(unit['spoken'], recognized['recognized'],
                                            unit.get('critical_terms', []), max_cer=0.0))
        recognized['blocked'] = bool(recognized['review_required'])
        recognized.pop('perceptual_review_required', None)
        return dict(seed=seed, file=name, sha256=file_hash(path), signal=signal, asr=recognized,
                    tts_seconds=tts_s, **observed)

    def _postprocess(self, candidate, unit, directory):
        source, processed, final = (directory / candidate['file'], directory / 'processed.wav',
                                    directory / 'final.wav')
        if file_hash(source) != candidate['sha256']:
            raise RenderFailure('candidate_audio_hash_mismatch')
        subprocess.run([self.ffmpeg, '-v', 'error', '-y', '-i', str(source), '-af',
                        self.settings['filters'][unit['mode']], '-ar', str(SAMPLE_RATE),
                        '-ac', '1', '-c:a', 'pcm_f32le', str(processed)],
                       capture_output=True, check=True, timeout=180)
        wave, sr = self.sf.read(processed, dtype='float32')
        if sr != SAMPLE_RATE:
            raise RenderFailure('processed_sample_rate_mismatch')
        wave, trim = trimmed_faded(wave, sr)
        self.sf.write(final, wave, sr, subtype='FLOAT')
        checked, checked_sr = self.sf.read(final, dtype='float32')
        signal = signal_stats(checked, checked_sr)
        if signal['blocked'] or checked_sr != SAMPLE_RATE or len(checked) != len(wave):
            final.unlink(missing_ok=True)
            raise RenderFailure('final_audio_signal_or_samples_invalid')
        return dict(file=final.name, sha256=file_hash(final), sample_rate=checked_sr,
                    sample_count=len(checked), measurement_kind='final_decoded_pcm',
                    trim=trim, signal=signal)

    def _review_file(self, candidates, directory):
        usable = [c for c in candidates if c.get('signal', {}).get('blocked') is False and c.get('file')]
        if not usable:
            return {}
        candidate = min(usable, key=candidate_order)
        source = directory / candidate['file']
        if source.is_symlink() or file_hash(source) != candidate['sha256']:
            raise RenderFailure('review_audio_hash_mismatch')
        wave, sr = self.sf.read(source, dtype='float32')
        signal = signal_stats(wave, sr)
        if signal['blocked'] or sr != SAMPLE_RATE or len(wave) != candidate['signal']['samples']:
            raise RenderFailure('review_audio_signal_invalid')
        target = directory / 'review.wav'
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)
        return dict(review_file=target.name, review_sha256=file_hash(target),
                    review_only=True, review_sample_rate=sr, review_sample_count=len(wave),
                    review_natural_eos=candidate.get('natural_eos') is True)

    def render_unit(self, unit, outdir):
        """Return a private receipt. Only status=passed is a publishable unit.

        A blocked receipt has no `file` or `sha256` claim. A signal-valid raw
        candidate may be retained as `review.wav`; it remains ineligible.
        The caller must also authenticate the full source/plan and cross-unit
        coverage, and must not discard unresolved reading-mode review flags.
        """
        require_guard()
        flags = validate_unit(unit)
        if (identity(self.settings) != self.settings_sha256
                or self.seeds != tuple(self.settings['seeds'])
                or self.max_new_tokens != self.settings['sampling']['max_new_tokens']):
            raise RenderFailure('renderer_settings_mutated')
        directory = Path(outdir)
        if directory.is_symlink() or any(p.is_symlink() for p in directory.parents):
            raise RenderFailure('output_symlink')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(directory.iterdir()):
            raise RenderFailure('output_directory_not_fresh')
        os.chmod(directory, 0o700)
        unit_key = identity(dict(unit=unit, render_settings_sha256=self.settings_sha256))
        receipt = dict(schema=SCHEMA, unit_key=unit_key, unit_id=unit['id'],
                       paragraph_id=unit['paragraph_id'], lesson_id=unit['id'].split(':', 1)[0],
                       start_utf16=unit['start_utf16'], end_utf16=unit['end_utf16'],
                       source_sha256=unit['source_sha256'],
                       spoken_sha256=hashlib.sha256(unit['spoken'].encode()).hexdigest(),
                       mode=unit['mode'], render_settings_sha256=self.settings_sha256,
                       render_settings=copy.deepcopy(self.settings), review_flags=flags,
                       status='blocked_requires_review', blocked=True, production_eligible=False,
                       review_required=True, perceptual_review_completed=False,
                       automatic_checks_passed=False)
        if 'start_cp' in unit:
            receipt.update(start_cp=unit['start_cp'], end_cp=unit['end_cp'])
        if flags:
            # Known source/normalization ambiguity cannot be repaired by TTS.
            # Preserve a source- and settings-bound receipt for the caller to
            # authenticate/seal without spending synthesis or ASR CPU time.
            receipt.update(candidates=[], natural_eos=False,
                           synthesis_skipped='unresolved_input_review',
                           asr=dict(blocked=True, review_required=True,
                                    status='not_run_input_review'))
            _atomic_json(directory / 'receipt.json', receipt)
            return receipt
        candidates = []
        for seed in self.seeds:
            try:
                candidate = self._candidate(unit, directory, seed)
            except TimeoutError:
                raise
            except RenderFailure as error:
                candidate = dict(seed=seed, error_code=str(error), natural_eos=False,
                                 signal={'blocked': True}, asr={'blocked': True, 'review_required': True})
            except Exception:
                candidate = dict(seed=seed, error_code='unit_candidate_failed', natural_eos=False,
                                 signal={'blocked': True}, asr={'blocked': True, 'review_required': True})
            candidates.append(candidate)
            if candidate_passed(candidate):
                break  # A source review flag cannot be repaired by repeating TTS.
        receipt['candidates'] = candidates
        accepted = [c for c in candidates if candidate_passed(c)]
        if accepted and not flags:
            selected = min(accepted, key=candidate_order)
            try:
                final = self._postprocess(selected, unit, directory)
            except TimeoutError:
                (directory / 'final.wav').unlink(missing_ok=True)
                raise
            except Exception:
                (directory / 'final.wav').unlink(missing_ok=True)
                receipt['review_flags'].append('postprocess_or_final_signal_failed')
            else:
                receipt.update(final, status='passed', blocked=False, production_eligible=True,
                               review_required=False, automatic_checks_passed=True,
                               seed=selected['seed'], natural_eos=True,
                               asr=selected['asr'], tts_seconds=selected['tts_seconds'])
        if receipt['blocked']:
            receipt.update(self._review_file(candidates, directory))
            best = min(candidates, key=candidate_order)
            receipt.update(asr=best['asr'], natural_eos=best.get('natural_eos') is True)
        _atomic_json(directory / 'receipt.json', receipt)
        return receipt


Renderer = FP32Renderer
