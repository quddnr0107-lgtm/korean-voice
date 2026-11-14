import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import tb001_asr_crosscheck as cross


class CrosscheckTests(unittest.TestCase):
    def reviews(self, directory):
        # Match the earlier deterministic owner preview: shortest=38,
        # shortest critical mismatch=1, next shortest=33.
        indices = list(range(29))+[33, 38]
        rows = []
        for index in indices:
            path = Path(directory)/f'{index}.wav'
            path.write_bytes(f'unchanged review {index}'.encode())
            count = {38: 1, 1: 2, 33: 3}.get(index, 100+index)
            rows.append(dict(index=index, path=path,
                wav_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                info=dict(sample_count=count), expected_spoken='통합 방위법 제일조',
                saved_recognized='통합 방입법 제일조', critical_terms=['통합 방위법'],
                missing_critical=['통합 방위법'] if index == 1 else [],
                character_errors=1))
        return rows

    def test_request_rejects_scope_or_model_changes(self):
        request = cross.expected_request()
        cross.validate_request(request)
        for field, value in [('promotion', True), ('synthesis', True),
                             ('excluded_owner_heard_unit_indices', [])]:
            changed = copy.deepcopy(request)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                cross.validate_request(changed)
        changed = copy.deepcopy(request)
        changed['model']['revision'] = 'main'
        with self.assertRaises(ValueError):
            cross.validate_request(changed)

    def test_exact_context_nonroot_and_workflow(self):
        env = dict(GITHUB_REPOSITORY=cross.full.d.REPO,
            GITHUB_REF='refs/heads/'+cross.full.d.BRANCH,
            GITHUB_EVENT_NAME='push', GITHUB_WORKFLOW_REF=cross.WORKFLOW)
        cross.check_context(env, 1001, 1001)
        for uid, euid in [(0, 0), (1001, 0), (1001, 1002)]:
            with self.assertRaises(ValueError):
                cross.check_context(env, uid, euid)
        env['GITHUB_WORKFLOW_REF'] += '-other'
        with self.assertRaises(ValueError):
            cross.check_context(env, 1001, 1001)

    def test_only_unheard_28_are_transcribed_without_prompt_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            reviews = self.reviews(directory)
            original = copy.deepcopy(reviews)
            calls = []
            class Model:
                def transcribe(self, path, **kwargs):
                    calls.append((int(Path(path).stem), kwargs))
                    return iter([SimpleNamespace(text='통합 방위법 제1조')]), None
            results = cross.run_reviews(reviews, Model())
            self.assertEqual(len(results), 28)
            self.assertTrue(all(x['larger_exact'] for x in results))
            self.assertEqual({i for i, _ in calls}, set(range(29))-{1})
            self.assertEqual(reviews, original)
            for _, kwargs in calls:
                self.assertEqual(kwargs, cross.TRANSCRIBE)
                self.assertNotIn('initial_prompt', kwargs)
            encoded = json.dumps(results, ensure_ascii=False)
            self.assertNotIn('통합', encoded)
            self.assertNotIn('recognized', encoded)
            self.assertNotIn('expected_spoken', encoded)

    def test_lexical_mismatch_remains_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            class Model:
                def transcribe(self, path, **kwargs):
                    return iter([SimpleNamespace(text='통합 방입법 제1조')]), None
            results = cross.run_reviews(self.reviews(directory), Model())
            self.assertTrue(all(not x['larger_exact'] for x in results))
            self.assertTrue(all(x['larger_missing_critical_count'] == 1 for x in results))

    def test_shortlist_cannot_expand_or_drop_owner_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            reviews = self.reviews(directory)
            with self.assertRaises(ValueError):
                cross.select_unheard(reviews[:-1])
            changed = copy.deepcopy(reviews)
            changed[-1]['index'] = 37
            with self.assertRaises(ValueError):
                cross.select_unheard(changed)
            changed = copy.deepcopy(reviews)
            changed[0]['info']['sample_count'] = 0
            with self.assertRaises(ValueError):
                cross.select_unheard(changed)

    def test_tampered_audio_fails_before_asr(self):
        with tempfile.TemporaryDirectory() as directory:
            reviews = self.reviews(directory)
            reviews[0]['path'].write_bytes(b'changed')
            model = SimpleNamespace(transcribe=lambda *a, **k: self.fail('ASR must not run'))
            with self.assertRaises(ValueError):
                cross.run_reviews(reviews, model)

    def test_audio_mutation_during_asr_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            reviews = self.reviews(directory)
            class Model:
                def transcribe(self, path, **kwargs):
                    Path(path).write_bytes(b'changed')
                    return iter([SimpleNamespace(text='통합 방위법 제일조')]), None
            with self.assertRaises(ValueError):
                cross.run_reviews(reviews, Model())

    def test_model_download_is_keyless(self):
        with patch.dict(os.environ, {cross.preview.PRIVATE_KEYS[0]: 'must-not-download'}):
            with self.assertRaises(ValueError):
                cross.download_model('/does-not-exist-crosscheck')

    def test_model_verification_requires_explicit_weight_hash(self):
        import fp32_renderer
        good = {key: 'a'*64 for key in cross.MODEL_FILES}
        good['model.bin'] = cross.MODEL_SHA256
        with patch.object(fp32_renderer, 'verify_assets', return_value=good) as verify:
            self.assertEqual(cross.verify_model('/model'), good)
            verify.assert_called_once_with('/model', cross.MODEL_FILES, cross.MODEL_REVISION)
        good['model.bin'] = 'b'*64
        with patch.object(fp32_renderer, 'verify_assets', return_value=good):
            with self.assertRaises(ValueError):
                cross.verify_model('/model')


if __name__ == '__main__':
    unittest.main()
