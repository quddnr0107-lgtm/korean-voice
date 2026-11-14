import ast
import contextlib
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
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


if __name__ == '__main__':
    unittest.main()
