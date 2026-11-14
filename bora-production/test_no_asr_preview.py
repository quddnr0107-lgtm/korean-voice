import ast
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import no_asr_preview as preview


class NoASRPreviewTests(unittest.TestCase):
    def test_exact_public_request_rejects_asr_private_or_production_scope(self):
        request = preview.expected_request()
        preview.validate_request(request)
        for name, value in [('asr', True), ('production_eligible', True),
                             ('source', 'other manuscript'), ('mode', 'reading'),
                             ('seed', 7319), ('synthesis', False)]:
            changed = copy.deepcopy(request)
            changed[name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                preview.validate_request(changed)
        changed = copy.deepcopy(request)
        changed['private_source'] = True
        with self.assertRaises(ValueError):
            preview.validate_request(changed)

    def test_generation_preserves_original_sampling_style_voice_and_cap(self):
        kwargs = preview.generation_kwargs()
        self.assertEqual(kwargs, dict(text=preview.SOURCE, language='Korean',
            speaker='Sohee', instruct=preview.STYLES['explain'],
            **preview.SAMPLING, max_new_tokens=1100))
        self.assertEqual(preview.SEED, 5192)
        self.assertNotIn('speed', kwargs)
        self.assertNotIn('pitch', kwargs)

    def test_download_fetches_only_fixed_tts_assets(self):
        calls = []
        fake_hub = SimpleNamespace(snapshot_download=lambda **kwargs: calls.append(kwargs))
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(sys.modules, {'huggingface_hub': fake_hub}), \
             patch.object(preview, 'verify_assets', return_value={'tts': 'verified'}), \
             patch.object(preview, 'require_keyless') as keyless:
            result = preview.download(Path(temporary)/'model')
        keyless.assert_called_once_with()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['repo_id'], preview.MODEL_REPOSITORY)
        self.assertEqual(calls[0]['revision'], preview.MODEL_REVISION)
        self.assertEqual(set(calls[0]['allow_patterns']), set(preview.MODEL_REQUIRED))
        self.assertNotIn('model.bin', calls[0]['allow_patterns'])
        self.assertEqual(result['recognition_models_downloaded'], 0)
        self.assertFalse(result['asr_performed'])

    def test_download_refuses_protected_keys_before_network_import(self):
        with patch.dict(os.environ, {'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64': 'forbidden'}), \
             patch.object(preview, 'verify_assets') as verify:
            with self.assertRaises(RuntimeError):
                preview.download('/unused-no-asr-test')
            verify.assert_not_called()

    def make_model(self, *, fail=False, eos=True):
        def original(*args, **kwargs):
            return SimpleNamespace(sequences=[SimpleNamespace(
                tolist=lambda: [4, 5, 99 if eos else 6])])
        talker = SimpleNamespace(generate=original)
        model = SimpleNamespace(model=SimpleNamespace(talker=talker,
            config=SimpleNamespace(talker_config=SimpleNamespace(codec_eos_token_id=99))))
        received = []
        def generate_custom_voice(**kwargs):
            received.append(kwargs)
            talker.generate()
            if fail:
                raise RuntimeError('public fake failure')
            return [[.1, -.1]], 24000
        model.generate_custom_voice = generate_custom_voice
        return model, talker, original, received

    def test_generation_observes_eos_and_restores_hook_without_recognition(self):
        model, talker, original, received = self.make_model()
        seeds = []
        torch = SimpleNamespace(manual_seed=seeds.append,
                                inference_mode=contextlib.nullcontext)
        wave, rate, observed, elapsed = preview.generate_once(model, torch)
        self.assertEqual(seeds, [5192])
        self.assertIs(talker.generate, original)
        self.assertEqual(received, [preview.generation_kwargs()])
        self.assertTrue(observed['natural_eos'])
        self.assertFalse(observed['cap_reached'])
        self.assertEqual(wave, [.1, -.1])
        self.assertEqual(rate, 24000)
        self.assertGreaterEqual(elapsed, 0)

    def test_failed_or_incomplete_generation_cannot_leave_hook_installed(self):
        torch = SimpleNamespace(manual_seed=lambda seed: None,
                                inference_mode=contextlib.nullcontext)
        for fail, eos in [(True, True), (False, False)]:
            model, talker, original, _ = self.make_model(fail=fail, eos=eos)
            with self.subTest(fail=fail, eos=eos), self.assertRaises((ValueError, RuntimeError)):
                preview.generate_once(model, torch)
            self.assertIs(talker.generate, original)

    def test_only_exact_workflow_and_nonroot_context_allowed(self):
        env = dict(GITHUB_REPOSITORY=preview.REPOSITORY,
            GITHUB_REF='refs/heads/'+preview.BRANCH,
            GITHUB_EVENT_NAME='push', GITHUB_WORKFLOW_REF=preview.WORKFLOW)
        preview.check_context(env, 1001, 1001)
        for uid, euid in [(0, 0), (1001, 0), (1001, 1002)]:
            with self.assertRaises(ValueError):
                preview.check_context(env, uid, euid)
        env['GITHUB_WORKFLOW_REF'] += '-other'
        with self.assertRaises(ValueError):
            preview.check_context(env, 1001, 1001)

    def test_no_recognizer_imports_or_calls_and_loaded_recognizer_rejected(self):
        tree = ast.parse(Path(preview.__file__).read_text())
        forbidden = {'faster_whisper', 'whisper', 'whisperx', 'vosk', 'speech_recognition'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(alias.name.split('.')[0] not in forbidden for alias in node.names))
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or '').split('.')[0], forbidden)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotEqual(node.func.attr, 'transcribe')
        preview.assert_no_recognizer_modules()
        with patch.dict(sys.modules, {'faster_whisper': object()}):
            with self.assertRaises(ValueError):
                preview.assert_no_recognizer_modules()

    def raw_fixture(self, output):
        wav = output/'raw-preview.wav'
        wav.write_bytes(b'RIFF verified raw-wave fixture for optional codec tests')
        return wav, dict(status='new_public_raw_audio_without_asr',
            wav_sha256=preview.file_hash(wav), wav_bytes=wav.stat().st_size,
            asr_performed=False, production_eligible=False,
            timing_seconds=dict(model_load=4.25, synthesis=87.125, wav_write=.12))

    def test_missing_ffmpeg_keeps_raw_wav_and_exact_synthesis_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            wav, result = self.raw_fixture(output)
            with patch.object(preview.shutil, 'which', return_value=None), \
                 patch.object(preview.subprocess, 'run') as run, \
                 contextlib.redirect_stdout(io.StringIO()):
                returned = preview.finish_preview(output, result, time.perf_counter())
            run.assert_not_called()
            persisted = json.loads((output/'no-asr-preview-summary.json').read_text())
            self.assertEqual(persisted, returned)
            self.assertEqual(persisted['playback_conversion']['error_category'], 'ffmpeg_unavailable')
            self.assertFalse(persisted['audio_reencoded_for_playback'])
            self.assertTrue(persisted['raw_audio_ready'])
            self.assertFalse(persisted['asr_performed'])
            self.assertEqual(persisted['timing_seconds']['synthesis'], 87.125)
            self.assertEqual(persisted['timing_seconds']['model_load'], 4.25)
            self.assertEqual(persisted['wav_sha256'], preview.file_hash(wav))
            self.assertFalse((output/'raw-preview.mp3').exists())
            self.assertEqual((output/'source.txt').read_text(), preview.SOURCE+'\n')

    def test_failed_codec_has_raw_receipt_already_saved_and_removes_partial_mp3(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            wav, result = self.raw_fixture(output)
            summary = output/'no-asr-preview-summary.json'

            def failed_encode(argv, **kwargs):
                old = json.loads(summary.read_text())
                self.assertEqual(old['playback_conversion']['status'], 'not_attempted')
                self.assertEqual(old['timing_seconds']['synthesis'], 87.125)
                self.assertEqual(old['wav_sha256'], preview.file_hash(wav))
                self.assertTrue(old['raw_audio_ready'])
                Path(argv[-1]).write_bytes(b'partial mp3')
                raise subprocess.CalledProcessError(2, argv, stderr=b'never copied to receipt')

            with patch.object(preview.shutil, 'which', return_value='/fake/ffmpeg'), \
                 patch.object(preview.subprocess, 'run', side_effect=failed_encode), \
                 contextlib.redirect_stdout(io.StringIO()):
                returned = preview.finish_preview(output, result, time.perf_counter())
            self.assertEqual(returned['playback_conversion'], dict(status='unavailable',
                phase='encode', error_category='ffmpeg_failed', returncode=2))
            self.assertEqual(json.loads(summary.read_text()), returned)
            self.assertEqual(returned['wav_sha256'], preview.file_hash(wav))
            self.assertFalse(returned['audio_reencoded_for_playback'])
            self.assertFalse(list(output.glob('*.mp3')))
            self.assertNotIn('never copied', summary.read_text())

    def test_decode_timeout_does_not_publish_unverified_playback_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            wav, result = self.raw_fixture(output)
            calls = []

            def timeout_on_decode(argv, **kwargs):
                calls.append(argv)
                if len(calls) == 1:
                    Path(argv[-1]).write_bytes(b'codec output awaiting decode check')
                else:
                    raise subprocess.TimeoutExpired(argv, timeout=120)

            with patch.object(preview.shutil, 'which', return_value='/fake/ffmpeg'), \
                 patch.object(preview.subprocess, 'run', side_effect=timeout_on_decode), \
                 contextlib.redirect_stdout(io.StringIO()):
                returned = preview.finish_preview(output, result, time.perf_counter())
            self.assertEqual(len(calls), 2)
            self.assertEqual(returned['playback_conversion']['phase'], 'decode_check')
            self.assertEqual(returned['playback_conversion']['error_category'], 'ffmpeg_timeout')
            self.assertEqual(returned['timing_seconds']['model_load'], 4.25)
            self.assertEqual(returned['timing_seconds']['synthesis'], 87.125)
            self.assertEqual(returned['wav_sha256'], preview.file_hash(wav))
            self.assertFalse(list(output.glob('*.mp3')))


if __name__ == '__main__':
    unittest.main()
