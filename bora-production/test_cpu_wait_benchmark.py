import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cpu_wait_benchmark as b


class WaitTests(unittest.TestCase):
    def variants(self):
        cpu = b.hardware('model name : Test CPU\nflags : avx2\n', 4)
        result = {}
        for variant in b.VARIANTS:
            result[variant] = dict(variant=variant, cpu=cpu, model=b.MODEL,
                model_revision=b.REVISION, device='cpu', parameter_dtypes=['float32'],
                threads=4, interop_threads=1, fixtures=list(b.FIXTURES),
                suite=b.SUITE, fixture_modes=list(b.FIXTURE_MODES), fixture_order=[0, 1],
                wait_environment=b.PASSIVE if variant == 'passive' else {},
                native_threads=dict(intraop_threads=4, interop_threads=1),
                parallel_info='omp_get_max_threads() : 4\nmkl_get_max_threads() : 4\n',
                rows=[dict(variant=variant, fixture=i, voice_mode=b.FIXTURE_MODES[i], seed=5192+i, device='cpu', dtype='float32',
                    threads=4, interop_threads=1, natural_eos=True, signal_valid=True,
                    generation_args=b.generation_args(i),
                    tts_seconds=20.0 if variant == 'default' else 10.0,
                    audio_seconds=5.0, process_cpu_seconds=12.0,
                    wav_sha256='a'*64, raw_pcm_f32le_sha256='b'*64, talker_sequence_sha256='c'*64)
                    for i, text in enumerate(b.FIXTURES)])
        return result, cpu

    def test_environment_scrubbed_before_fresh_process_and_original_recipe_preserved(self):
        inherited = {'OMP_WAIT_POLICY': 'ACTIVE', 'GOMP_SPINCOUNT': '100',
            'KMP_BLOCKTIME': '999', 'KEEP': 'public'}
        before = copy.deepcopy(inherited)
        self.assertEqual(b.wait_values(b.policy_environment('default', inherited)), {})
        self.assertEqual(b.wait_values(b.policy_environment('passive', inherited)), b.PASSIVE)
        self.assertEqual(inherited, before)
        self.assertEqual(b.policy_environment('passive', inherited)['OMP_NUM_THREADS'], '4')
        b.validate_request()
        self.assertEqual(b.VARIANTS, ('passive', 'default'))
        self.assertEqual(b.FIXTURE_MODES, ('explain', 'reading'))
        for i, mode in enumerate(b.FIXTURE_MODES):
            kwargs = b.generation_args(i)
            self.assertEqual({key: kwargs[key] for key in b.SAMPLING}, b.SAMPLING)
            self.assertEqual(kwargs['instruct'], b.STYLES[mode])
            self.assertEqual(kwargs['text'], b.FIXTURES[i])
            self.assertEqual(kwargs['speaker'], 'Sohee')
            self.assertEqual(kwargs['language'], 'Korean')
            self.assertEqual(kwargs['max_new_tokens'], 1100)
        with self.assertRaises(ValueError): b.generation_args(True)

    def test_measured_duration_and_pcm_hash_not_container_hash_control_comparison(self):
        variants, cpu = self.variants()
        variants['passive']['rows'][0]['wav_sha256'] = 'd'*64
        before = copy.deepcopy(variants)
        report = b.compare_results(variants, cpu)
        self.assertEqual(report['observed_rtf_ratio_default_over_passive'], 2)
        self.assertTrue(all(report['identical_pcm_hashes_by_fixture'].values()))
        self.assertFalse(report['wait_hints_guaranteed_applied'])
        self.assertFalse(report['production_eligible'])
        self.assertEqual(variants, before)

    def test_mismatched_input_settings_hardware_or_native_threads_rejected(self):
        for change in (lambda v: v['passive'].update(cpu={}),
                lambda v: v['passive'].update(wait_environment={}),
                lambda v: v['passive']['rows'].pop(),
                lambda v: v['default']['rows'][0].update(tts_seconds=float('nan')),
                lambda v: v['passive']['rows'][1].update(generation_args={}),
                lambda v: v['default']['rows'][1].update(voice_mode='explain'),
                lambda v: v['passive'].update(parallel_info='omp_get_max_threads() : 2\n')):
            variants, cpu = self.variants()
            change(variants)
            with self.assertRaises(ValueError):
                b.compare_results(variants, cpu)

    def test_eos_or_bad_signal_suppresses_both_speed_claims(self):
        for field in ('natural_eos', 'signal_valid'):
            variants, cpu = self.variants()
            variants['passive']['rows'][0][field] = False
            result = b.compare_results(variants, cpu)
            self.assertIsNone(result['observed_rtf_ratio_default_over_passive'])
            self.assertIsNone(result['observed_pair_wall_ratio_default_over_passive'])

    def test_openmp_maps_reports_only_loaded_runtime_libraries(self):
        maps = '1-2 r-xp 000 00:00 0 /public/libgomp-a.so.1\n3-4 r-xp 0 00:0 0 /public/libiomp5.so\n5-6 r 0 0 0 /public/other.so\n'
        self.assertEqual(b.openmp_libraries(maps), ['/public/libgomp-a.so.1', '/public/libiomp5.so'])

    def test_worker_timeout_fails_closed_and_uses_isolated_environment(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td)/'out'
            with patch.object(b, 'hardware', return_value={}), \
                    patch.object(b.subprocess, 'run', side_effect=subprocess.TimeoutExpired('public', 600)) as child:
                self.assertEqual(b.run('/public/not-loaded', output), 1)
            report = json.loads((output/'summary.json').read_text())
            self.assertEqual(report['status'], 'wait_worker_timeout')
            self.assertIsNone(report['observed_rtf_ratio_default_over_passive'])
            self.assertEqual(child.call_args.kwargs['timeout'], 600)
            self.assertEqual(b.wait_values(child.call_args.kwargs['env']), b.PASSIVE)


if __name__ == '__main__':
    unittest.main()
