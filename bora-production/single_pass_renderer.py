"""Original Kim Bora FP32 voice with one synthesis pass and no recognizer.

Source/reading validation, natural completion and signal/DSP checks retain
their strict-profile behavior. The caller owns encrypted checkpoints and
course publication.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import fp32_renderer as original


PROFILE = 'single-pass-no-asr'
TRANSCRIPT_GATE = 'not_run_single_pass_no_asr/v1'
ASR_NOT_RUN = {'status': 'not_run', 'performed': False}
TTS_PACKAGES = {name: original.EXPECTED_PACKAGES[name] for name in
                ('torch', 'torchaudio', 'qwen-tts', 'transformers', 'soundfile', 'numpy', 'scipy')}
SOURCE_FILES = (
    'single_pass_renderer.py', 'fp32_renderer.py', 'pilot_render.py', 'quality.py',
    'paragraph_plan.py', 'production_plan.py', 'planning.py', 'middot_readings.py',
    'tts_normalize.py', 'approved-tb-annotations.json',
)


def _assert_recognizer_not_loaded():
    recognizers = {'faster_whisper', 'whisper', 'whisperx', 'vosk', 'speech_recognition'}
    if any(name.partition('.')[0] in recognizers for name in sys.modules):
        raise original.RenderFailure('single_pass_recognizer_loaded')


class SinglePassRenderer:
    """Loads only the pinned synthesis model after checking offline isolation."""

    _postprocess = original.FP32Renderer._postprocess

    def __init__(self, model_dir, *, max_new_tokens=1100, max_attempts=1, expected_assets=None):
        original.require_guard()
        _assert_recognizer_not_loaded()
        if (type(max_new_tokens) is not int or max_new_tokens != 1100
                or type(max_attempts) is not int or max_attempts != 1):
            raise original.RenderFailure('single_pass_unapproved_settings')
        if (expected_assets is not None
                and (not isinstance(expected_assets, dict)
                     or set(expected_assets) != {'model_revision', 'model_sha256'}
                     or expected_assets['model_revision'] != original.MODEL_REVISION)):
            raise original.RenderFailure('single_pass_asset_manifest_revision')
        self.model_dir = Path(model_dir)
        model_hashes = original.verify_assets(
            self.model_dir, original.MODEL_REQUIRED, original.MODEL_REVISION,
            None if expected_assets is None else expected_assets['model_sha256'],
        )
        config = json.loads((self.model_dir / 'config.json').read_text())
        if (config.get('model_type') != 'qwen3_tts'
                or config.get('tts_model_type') != 'custom_voice'
                or config.get('tts_model_size') != '1b7'):
            raise original.RenderFailure('wrong_model_architecture')
        try:
            packages = {name: importlib.metadata.version(name) for name in TTS_PACKAGES}
        except importlib.metadata.PackageNotFoundError:
            raise original.RenderFailure('runtime_package_missing') from None
        if packages != TTS_PACKAGES:
            raise original.RenderFailure('runtime_package_version_mismatch')
        self.ffmpeg = shutil.which('ffmpeg')
        if not self.ffmpeg:
            raise original.RenderFailure('ffmpeg_missing')
        try:
            ffmpeg_version = subprocess.run(
                [self.ffmpeg, '-version'], check=True, capture_output=True, text=True, timeout=15,
            ).stdout.splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            raise original.RenderFailure('ffmpeg_version_unavailable') from None

        self.max_new_tokens = max_new_tokens
        self.seeds = original.SEEDS[:1]
        self.settings = dict(
            schema=original.RECIPE_SCHEMA, execution_profile=PROFILE,
            engine='qwen-tts-python', dtype='float32', device='cpu', threads=4, interop_threads=1,
            attention='sdpa', inference_mode=True, model=original.MODEL_REPOSITORY,
            model_revision=original.MODEL_REVISION, model_sha256=model_hashes,
            speaker='Sohee', language='Korean', styles=copy.deepcopy(original.STYLES),
            filters=copy.deepcopy(original.FILTERS),
            sampling=dict(original.SAMPLING, max_new_tokens=max_new_tokens),
            seeds=list(self.seeds), sample_rate=original.SAMPLE_RATE,
            paragraph_gap_s=0.35, unit_gap_s=0.18,
            trim=dict(retained_edge_ms=120, fade_ms=5), tempo_factor=1, pitch_shift=0,
            transcript_gate=TRANSCRIPT_GATE, asr=dict(ASR_NOT_RUN),
            packages=packages, ffmpeg_version=ffmpeg_version,
            code_sha256={f'bora-production/{name}': original.file_hash(Path(__file__).parent / name)
                         for name in SOURCE_FILES},
        )
        self.settings_sha256 = original.identity(self.settings)

        try:
            with original.quiet_native():
                import soundfile
                import torch
                from qwen_tts import Qwen3TTSModel

                torch.set_num_threads(4)
                torch.set_num_interop_threads(1)
                self.torch, self.sf = torch, soundfile
                self.model = Qwen3TTSModel.from_pretrained(
                    str(self.model_dir), device_map='cpu', dtype=torch.float32,
                    attn_implementation='sdpa', low_cpu_mem_usage=True, local_files_only=True,
                )
                self.model.model.eval()
                floating = {p.dtype for p in self.model.model.parameters() if p.is_floating_point()}
                devices = {p.device.type for p in self.model.model.parameters()}
                if (floating != {torch.float32} or devices != {'cpu'}
                        or torch.get_num_threads() != 4 or torch.get_num_interop_threads() != 1):
                    raise original.RenderFailure('runtime_dtype_or_threads_mismatch')
                _assert_recognizer_not_loaded()
        except (original.RenderFailure, TimeoutError):
            raise
        except Exception:
            raise original.RenderFailure('single_pass_model_load_failed') from None

    @property
    def render_settings(self):
        return self.settings

    @property
    def render_settings_sha256(self):
        return self.settings_sha256

    def _candidate(self, unit, directory):
        observed = {}
        talker = self.model.model.talker
        generate = talker.generate
        eos_id = int(self.model.model.config.talker_config.codec_eos_token_id)
        seed, = self.seeds

        def observe(*args, **kwargs):
            result = generate(*args, **kwargs)
            if observed or len(result.sequences) != 1:
                raise original.RenderFailure('single_pass_token_observation_invalid')
            observed.update(original.eos_observation(
                result.sequences[0].tolist(), eos_id, self.max_new_tokens,
            ))
            return result

        talker.generate = observe
        started = time.perf_counter()
        try:
            self.torch.manual_seed(seed)
            with original.quiet_native(), self.torch.inference_mode():
                waves, rate = self.model.generate_custom_voice(
                    text=unit['spoken'], language='Korean', speaker='Sohee',
                    instruct=self.settings['styles'][unit['mode']], **self.settings['sampling'],
                )
            elapsed = time.perf_counter() - started
        except (original.RenderFailure, TimeoutError):
            raise
        except Exception:
            raise original.RenderFailure('unit_tts_failed') from None
        finally:
            talker.generate = generate
        _assert_recognizer_not_loaded()
        if len(waves) != 1 or rate != original.SAMPLE_RATE:
            raise original.RenderFailure('generated_audio_shape_or_rate_mismatch')
        if not observed:
            raise original.RenderFailure('natural_eos_not_observed')
        wav = directory / f'candidate-{seed}.wav'
        wave, = waves
        signal = original.signal_stats(wave, rate)
        candidate = dict(seed=seed, tts_seconds=elapsed, signal=signal,
                         asr=dict(ASR_NOT_RUN), **observed)
        if observed['natural_eos'] and not signal['blocked']:
            self.sf.write(wav, wave, rate, subtype='FLOAT')
            if not 0 < wav.stat().st_size <= original.MAX_AUDIO_BYTES:
                wav.unlink(missing_ok=True)
                raise original.RenderFailure('candidate_audio_size_invalid')
            candidate.update(file=wav.name, sha256=original.file_hash(wav))
        return candidate

    def render_unit(self, unit, outdir):
        original.require_guard()
        _assert_recognizer_not_loaded()
        flags = original.validate_unit(unit)
        if (original.identity(self.settings) != self.settings_sha256
                or self.seeds != tuple(self.settings['seeds'])
                or self.max_new_tokens != self.settings['sampling']['max_new_tokens']):
            raise original.RenderFailure('renderer_settings_mutated')
        directory = Path(outdir)
        if directory.is_symlink() or any(parent.is_symlink() for parent in directory.parents):
            raise original.RenderFailure('output_symlink')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(directory.iterdir()):
            raise original.RenderFailure('output_directory_not_fresh')
        os.chmod(directory, 0o700)
        receipt = dict(
            schema=original.SCHEMA, unit_key=original.identity(dict(
                unit=unit, render_settings_sha256=self.settings_sha256)),
            unit_id=unit['id'], paragraph_id=unit['paragraph_id'], lesson_id=unit['id'].split(':', 1)[0],
            start_utf16=unit['start_utf16'], end_utf16=unit['end_utf16'],
            source_sha256=unit['source_sha256'], spoken_sha256=hashlib.sha256(unit['spoken'].encode()).hexdigest(),
            mode=unit['mode'], render_settings_sha256=self.settings_sha256,
            render_settings=copy.deepcopy(self.settings), review_flags=flags,
            status='blocked_requires_review', blocked=True, production_eligible=False,
            review_required=True, perceptual_review_completed=False, automatic_checks_passed=False,
            natural_eos=False, asr=dict(ASR_NOT_RUN), candidates=[],
        )
        if 'start_cp' in unit:
            receipt.update(start_cp=unit['start_cp'], end_cp=unit['end_cp'])
        if flags:
            receipt['synthesis_skipped'] = 'unresolved_input_review'
        else:
            try:
                candidate = self._candidate(unit, directory)
            except TimeoutError:
                raise
            except original.RenderFailure as exc:
                candidate = dict(seed=self.seeds[0], error_code=str(exc), natural_eos=False,
                                 signal={'blocked': True}, asr=dict(ASR_NOT_RUN))
            except Exception:
                candidate = dict(seed=self.seeds[0], error_code='unit_candidate_failed', natural_eos=False,
                                 signal={'blocked': True}, asr=dict(ASR_NOT_RUN))
            receipt['candidates'].append(candidate)
            receipt['natural_eos'] = candidate.get('natural_eos') is True
            if receipt['natural_eos'] and candidate['signal']['blocked'] is False:
                try:
                    final = self._postprocess(candidate, unit, directory)
                except TimeoutError:
                    (directory / 'final.wav').unlink(missing_ok=True)
                    raise
                except Exception:
                    (directory / 'final.wav').unlink(missing_ok=True)
                    receipt['review_flags'].append('postprocess_or_final_signal_failed')
                else:
                    receipt.update(final, status='passed', blocked=False, production_eligible=True,
                                   review_required=False, automatic_checks_passed=True,
                                   seed=candidate['seed'], tts_seconds=candidate['tts_seconds'])
        original._atomic_json(directory / 'receipt.json', receipt)
        return receipt
