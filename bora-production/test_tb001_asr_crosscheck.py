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
                             ('excluded_owner_heard_unit_indices', []),
                             ('request_id', 'tb001-asr-crosscheck-old'),
                             ('decoder_av_version', '19.0.2'),
                             ('pronunciation_diagnostics', False),
                             ('normalization_ruleset', 'unapproved-ruleset')]:
            changed = copy.deepcopy(request)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                cross.validate_request(changed)
        changed = copy.deepcopy(request)
        changed['model']['revision'] = 'main'
        with self.assertRaises(ValueError):
            cross.validate_request(changed)
        for field in ('request_id', 'decoder_av_version', 'pronunciation_diagnostics', 'normalization_ruleset'):
            changed = copy.deepcopy(request)
            changed.pop(field)
            with self.subTest(missing=field), self.assertRaises(ValueError):
                cross.validate_request(changed)

    def test_decoder_version_requires_exact_installed_distribution(self):
        with patch.object(cross.importlib.metadata, 'version', return_value='18.1.0') as version:
            self.assertEqual(cross.verify_decoder_version(), '18.1.0')
            version.assert_called_once_with('av')
        for observed in ('19.0.2', '18.0.0', '18.1.0.post1'):
            with patch.object(cross.importlib.metadata, 'version', return_value=observed):
                with self.subTest(observed=observed), self.assertRaises(ValueError):
                    cross.verify_decoder_version()
        with patch.object(cross.importlib.metadata, 'version',
                          side_effect=cross.importlib.metadata.PackageNotFoundError('av')):
            with self.assertRaisesRegex(ValueError, '^tb001_crosscheck_validation_failed$'):
                cross.verify_decoder_version()

    def test_wrong_decoder_blocks_both_modes_before_model_or_private_inspection(self):
        for mode in ('download-model', 'inspect'):
            argv = ['crosscheck', mode, '--model-path', '/unused-model']
            if mode == 'inspect': argv += ['--directory', '/unused-cipher', '--output', '/unused-output']
            with patch('sys.argv', argv), \
                 patch.object(cross.importlib.metadata, 'version', return_value='19.0.2'), \
                 patch.object(cross, 'download_model') as download, \
                 patch.object(cross, 'inspect') as inspect, \
                 patch.dict(os.environ, {cross.preview.PRIVATE_KEYS[0]: 'untouched-private-key'}):
                with self.assertRaises(ValueError): cross.main()
                download.assert_not_called(); inspect.assert_not_called()
                self.assertEqual(os.environ[cross.preview.PRIVATE_KEYS[0]], 'untouched-private-key')

    def test_verified_decoder_version_is_forwarded_to_private_summary_path(self):
        argv = ['crosscheck', 'inspect', '--model-path', '/unused-model',
                '--directory', '/unused-cipher', '--output', '/unused-output']
        with patch('sys.argv', argv), \
             patch.object(cross.importlib.metadata, 'version', return_value='18.1.0'), \
             patch.object(cross, 'inspect') as inspect:
            cross.main()
            inspect.assert_called_once_with('/unused-cipher', '/unused-model', '/unused-output',
                                            decoder_av_version='18.1.0')

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
            self.assertTrue(all(x['normalized_exact'] for x in results))
            self.assertEqual({i for i, _ in calls}, set(range(29))-{1})
            self.assertEqual(reviews, original)
            for _, kwargs in calls:
                self.assertEqual(kwargs, cross.TRANSCRIBE)
                self.assertNotIn('initial_prompt', kwargs)
            encoded = json.dumps(results, ensure_ascii=False)
            self.assertNotIn('통합', encoded)
            self.assertNotIn('recognized', encoded)
            self.assertNotIn('expected_spoken', encoded)
            self.assertNotIn('normalization_edits', encoded)
            self.assertNotIn('normalization_flags', encoded)
            self.assertNotIn('missing_critical"', encoded)

    def test_pronunciation_diagnostics_do_not_replace_raw_mismatch_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            reviews = self.reviews(directory)
            for row in reviews:
                row['expected_spoken'] = '교육 시간은 세 시간이며 인원은 스물다섯 명이다'
                row['critical_terms'] = ['세 시간', '스물다섯 명']
            original = copy.deepcopy(reviews)
            class Model:
                def transcribe(self, path, **kwargs):
                    return iter([SimpleNamespace(text='교육 시간은 3시간이며 인원은 25명이다')]), None
            results = cross.run_reviews(reviews, Model())
            self.assertEqual(reviews, original)
            for result in results:
                self.assertFalse(result['larger_exact'])
                self.assertGreater(result['larger_character_errors'], 0)
                self.assertGreater(result['larger_missing_critical_count'], 0)
                self.assertTrue(result['normalized_exact'])
                self.assertEqual(result['normalized_character_errors'], 0)
                self.assertEqual(result['normalized_missing_critical_count'], 0)
                self.assertEqual(result['normalization_review_flag_count'], 0)
            encoded = json.dumps(results, ensure_ascii=False)
            for private in ('교육 시간', '3시간', '25명', '스물다섯', 'recognized', 'expected_spoken', 'edits'):
                self.assertNotIn(private, encoded)

    def test_normalization_review_flags_and_missing_critical_remain_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            reviews = self.reviews(directory)
            class Model:
                def transcribe(self, path, **kwargs):
                    return iter([SimpleNamespace(text='통합 방위법 제일조')]), None
            with patch.object(cross, 'normalize', return_value=(
                    '통합 방위법 제일조', [], [{'reason': 'private-review-reason'}])):
                flagged = cross.run_reviews(reviews, Model())
            self.assertTrue(all(row['larger_exact'] for row in flagged))
            self.assertTrue(all(row['normalized_character_errors'] == 0
                and row['normalization_review_flag_count'] == 1
                and not row['normalized_exact'] for row in flagged))
            self.assertNotIn('private-review-reason', json.dumps(flagged))
            for row in reviews:
                row['critical_terms'] = ['추가 핵심어']
            missing = cross.run_reviews(reviews, Model())
            self.assertTrue(all(row['normalized_character_errors'] == 0
                and row['normalized_missing_critical_count'] == 1
                and not row['normalized_exact'] for row in missing))
            self.assertNotIn('추가 핵심어', json.dumps(missing, ensure_ascii=False))

    def test_lexical_mismatch_remains_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            class Model:
                def transcribe(self, path, **kwargs):
                    return iter([SimpleNamespace(text='통합 방입법 제1조')]), None
            results = cross.run_reviews(self.reviews(directory), Model())
            self.assertTrue(all(not x['larger_exact'] for x in results))
            self.assertTrue(all(x['larger_missing_critical_count'] == 1 for x in results))
            self.assertTrue(all(not x['normalized_exact'] for x in results))

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
