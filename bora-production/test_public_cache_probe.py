"""Synthetic timing evidence and tiny public files; no model inference."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import public_cache_probe as p
import fp32_renderer as original


class PublicCacheProbeTests(unittest.TestCase):
    def rows(self):
        common = dict(schema=p.SCHEMA, request_sha256='a'*64, manifest_sha256='b'*64,
            pinset_sha256='c'*64, run_id='123', run_attempt='1', commit='d'*40,
            file_count=15, pinned_bytes=5006371958, all_pinned_files_verified=True,
            cache_action_outcome='success', original_renderer_verifier_compatible=True,
            renderer_compatibility_verify_seconds=2.0)
        return (dict(common, phase='fill', fresh_download=True, cache_hit=False,
                     prepare_seconds=20.0, verify_seconds=5.0, cache_action_bracket_seconds=7.0),
                dict(common, phase='restore', fresh_download=False, cache_hit=True,
                     prepare_seconds=4.0, verify_seconds=5.0, cache_action_bracket_seconds=6.0),
                dict(common, phase='baseline', download_and_size_json_verify_seconds=28.0,
                     original_baseline_total_seconds=30.0, original_hf_metadata_preserved=True))

    def test_verified_comparison_keeps_compatibility_cost_separate(self):
        fill, restored, baseline = self.rows()
        before = copy.deepcopy((fill, restored, baseline))
        result = p.summarize(fill, restored, baseline)
        self.assertTrue(result['warm_cache_verified'])
        self.assertEqual(result['auxiliary_new_cold_prepare_verify_seconds'], 25)
        self.assertEqual(result['auxiliary_new_warm_restore_prepare_verify_seconds'], 15)
        self.assertEqual(result['original_production_prepare_seconds'], 30)
        self.assertEqual(result['proposed_warm_production_prepare_seconds'], 17)
        self.assertAlmostEqual(result['observed_prepare_reduction_pct'], 100*13/30)
        self.assertEqual(result['fill_with_cache_save_seconds'], 32)
        self.assertFalse(result['synthesis_started'])
        self.assertEqual((fill, restored, baseline), before)

    def test_negative_savings_are_not_hidden(self):
        fill, restored, baseline = self.rows(); restored['cache_action_bracket_seconds'] = 30
        result = p.summarize(fill, restored, baseline)
        self.assertTrue(result['warm_cache_verified'])
        self.assertEqual(result['observed_prepare_seconds_saved'], -11)
        self.assertFalse(result['cache_faster_than_original'])

    def test_new_cold_improvement_cannot_hide_regression_against_original(self):
        fill, restored, baseline = self.rows()
        fill['prepare_seconds'] = 120
        baseline['original_baseline_total_seconds'] = 12
        baseline['download_and_size_json_verify_seconds'] = 10
        result = p.summarize(fill, restored, baseline)
        self.assertGreater(result['auxiliary_new_path_prepare_seconds_saved'], 0)
        self.assertEqual(result['observed_prepare_seconds_saved'], -5)
        self.assertFalse(result['cache_faster_than_original'])

    def test_fallback_cache_miss_or_failed_action_cannot_claim_warm_speedup(self):
        for changes in ({'fresh_download': True}, {'cache_hit': False},
                        {'cache_action_outcome': 'failure'},
                        {'original_renderer_verifier_compatible': False}):
            fill, restored, baseline = self.rows(); restored.update(changes)
            result = p.summarize(fill, restored, baseline)
            self.assertFalse(result['warm_cache_verified'])
            self.assertIsNone(result['observed_prepare_reduction_pct'])
        fill, restored, baseline = self.rows(); fill['cache_action_outcome'] = 'failure'
        self.assertFalse(p.summarize(fill, restored, baseline)['warm_cache_verified'])

    def test_other_run_manifest_or_phase_cannot_be_combined(self):
        for changes in ({'run_id': '124'}, {'manifest_sha256': 'f'*64},
                        {'pinset_sha256': 'f'*64}, {'phase': 'fill'}, {'file_count': 14}):
            fill, restored, baseline = self.rows(); restored.update(changes)
            with self.assertRaises(ValueError): p.summarize(fill, restored, baseline)
        fill, restored, baseline = self.rows(); baseline['run_id'] = 'other'
        with self.assertRaises(ValueError): p.summarize(fill, restored, baseline)

    def test_invalid_clocks_and_timers_fail(self):
        for value in (True, 0, -1, float('nan'), float('inf')):
            fill, restored, baseline = self.rows(); restored['prepare_seconds'] = value
            with self.assertRaises(ValueError): p.summarize(fill, restored, baseline)
            fill, restored, baseline = self.rows(); baseline['original_baseline_total_seconds'] = value
            with self.assertRaises(ValueError): p.summarize(fill, restored, baseline)
        for markers in ({'a': True, 'b': 2}, {'a': 3, 'b': 2}, {'a': 2, 'b': 2}):
            with self.assertRaises(ValueError): p.interval(markers, 'a', 'b')
        self.assertEqual(p.interval({'a': 1, 'b': 1000000001}, 'a', 'b'), 1)

    def test_generated_metadata_passes_original_verifier_without_inference(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = {'models': {}}
            for name, filename, payload, revision in (
                    ('qwen', 'config.json', b'{}', original.MODEL_REVISION),
                    ('asr', 'model.bin', b'PUBLIC_ASR', original.ASR_REVISION)):
                sha = hashlib.sha256(payload).hexdigest()
                manifest['models'][name] = {'revision': revision,
                    'files': {filename: {'bytes': len(payload), 'sha256': sha}}}
                path = root/name/filename; path.parent.mkdir(parents=True); path.write_bytes(payload)
                metadata = root/name/'.cache/huggingface/download'/(filename+'.metadata')
                metadata.parent.mkdir(parents=True)
                metadata.write_text(revision+'\n'+sha+'\n0\n')
            with patch.object(original, 'MODEL_REQUIRED', {'config.json': 2}), \
                 patch.object(original, 'ASR_REQUIRED', {'model.bin': len(b'PUBLIC_ASR')}):
                p.verify_renderer_compatibility(root, manifest)
                manifest['models']['asr']['files']['model.bin']['sha256'] = '0'*64
                with self.assertRaisesRegex(ValueError, 'probe_original_verifier_hashes'):
                    p.verify_renderer_compatibility(root, manifest)

    def test_baseline_uses_original_download_and_verifiers_without_metadata_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def snapshot(repository, **kwargs):
                target = kwargs['local_dir']; target.mkdir(parents=True)
                (target/'original-hf-metadata').write_text('UPSTREAM_METADATA_UNCHANGED')
            def compatibility(runtime, manifest):
                for name in ('qwen', 'asr'):
                    self.assertEqual((runtime/name/'original-hf-metadata').read_text(),
                                     'UPSTREAM_METADATA_UNCHANGED')
            with patch.dict(os.environ, RUNNER_TEMP=td, GITHUB_RUN_ID='123',
                            GITHUB_RUN_ATTEMPT='1', GITHUB_SHA='d'*40,
                            GITHUB_OUTPUT=str(root/'outputs')), \
                 patch.dict(sys.modules, {'huggingface_hub': SimpleNamespace(snapshot_download=snapshot)}), \
                 patch.object(p.cache.pins, 'verify_assets') as size_checks, \
                 patch.object(p, 'verify_renderer_compatibility', side_effect=compatibility) as hashes, \
                 patch.object(p.cache, 'prepare', side_effect=AssertionError('new preparation forbidden')), \
                 patch('sys.argv', ['public_cache_probe.py', 'baseline', '--state',
                                    str(root/'bora-public-cache-probe.json')]):
                p.main()
            self.assertEqual(size_checks.call_count, 2)
            hashes.assert_called_once()
            summary=json.loads((root/'bora-public-cache-probe.json').read_text())
            self.assertTrue(summary['original_hf_metadata_preserved'])
            self.assertGreater(summary['original_baseline_total_seconds'],
                               summary['renderer_compatibility_verify_seconds'])


if __name__ == '__main__':
    unittest.main()
