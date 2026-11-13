import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cpu_arm_wait_benchmark as b
from basic_sohee_benchmark import FIXTURES
from fp32_renderer import SAMPLING, STYLES


class ArmWaitTests(unittest.TestCase):
    def variants(self):
        cpu = dict(cpu_models=['Test ARM'], logical_cpus=4)
        results = {}
        for variant in b.VARIANTS:
            results[variant] = dict(variant=variant, cpu=cpu, machine='aarch64', model=b.MODEL,
                model_revision=b.REVISION, device='cpu', parameter_dtypes=['float32'],
                threads=4, interop_threads=1, fixtures=list(FIXTURES), fixture_order=[0, 1],
                installed_packages=dict(b.EXPECTED_PACKAGES),
                wait_environment=b.PASSIVE if variant == 'passive' else {},
                native_threads=dict(intraop_threads=4, interop_threads=1, openmp_available=True),
                parallel_info='omp_get_max_threads() : 4\nMKL not found\n',
                rows=[dict(variant=variant, fixture=i, seed=5192+i, device='cpu', dtype='float32',
                    threads=4, interop_threads=1, natural_eos=True, signal_valid=True,
                    generation_args=b.generation_args('production_style', text),
                    tts_seconds=20.0 if variant == 'default' else 10.0,
                    audio_seconds=5.0, process_cpu_seconds=12.0,
                    wav_sha256='a'*64, raw_pcm_f32le_sha256='b'*64, talker_sequence_sha256='c'*64)
                    for i, text in enumerate(FIXTURES)])
        return results, cpu

    def test_original_generic_fixtures_sampler_and_preimport_environment(self):
        b.validate_request(); self.assertEqual(b.VARIANTS, ('default', 'passive'))
        self.assertEqual(b.FIXTURES, FIXTURES)
        inherited = dict(OMP_WAIT_POLICY='ACTIVE', GOMP_SPINCOUNT='999', KMP_BLOCKTIME='999', KEEP='public')
        before = copy.deepcopy(inherited)
        self.assertEqual(b.wait_values(b.policy_environment('default', inherited)), {})
        self.assertEqual(b.wait_values(b.policy_environment('passive', inherited)), b.PASSIVE)
        self.assertEqual(inherited, before)
        for text in FIXTURES:
            args = b.generation_args('production_style', text)
            self.assertEqual({key: args[key] for key in SAMPLING}, SAMPLING)
            self.assertEqual(args['instruct'], STYLES['explain'])

    def test_same_arm_runner_duration_and_pcm_comparison(self):
        variants, cpu = self.variants(); before = copy.deepcopy(variants)
        report = b.compare_results(variants, cpu)
        self.assertEqual(report['observed_pair_wall_ratio_default_over_passive'], 2)
        self.assertEqual(report['observed_rtf_ratio_default_over_passive'], 2)
        self.assertTrue(all(report['identical_pcm_hashes_by_fixture'].values()))
        self.assertFalse(report['wait_hints_guaranteed_applied'])
        self.assertFalse(report['production_eligible'])
        self.assertEqual(variants, before)

    def test_bad_settings_hardware_threads_and_timing_rejected(self):
        for change in (lambda v:v['passive'].update(machine='x86_64'),
                lambda v:v['default'].update(installed_packages={}),
                lambda v:v['default'].update(parallel_info='omp_get_max_threads() : 2\n'),
                lambda v:v['default'].update(cpu={}),
                lambda v:v['passive'].update(wait_environment={}),
                lambda v:v['passive']['rows'][0].update(tts_seconds=float('nan')),
                lambda v:v['passive']['rows'][0].update(generation_args={})):
            variants, cpu = self.variants(); change(variants)
            with self.assertRaises(ValueError): b.compare_results(variants, cpu)

    def test_eos_or_signal_failure_suppresses_speed_comparison(self):
        for field in ('natural_eos', 'signal_valid'):
            variants, cpu = self.variants(); variants['passive']['rows'][0][field] = False
            report = b.compare_results(variants, cpu)
            self.assertIsNone(report['observed_rtf_ratio_default_over_passive'])
            self.assertIsNone(report['observed_pair_wall_ratio_default_over_passive'])

    def test_timeout_records_failure_and_fresh_default_environment(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td)/'out'
            with patch.object(b, 'validate_machine', return_value='aarch64'), \
                    patch.object(b, 'hardware', return_value={}), \
                    patch.object(b.subprocess, 'run', side_effect=subprocess.TimeoutExpired('public', 600)) as child:
                self.assertEqual(b.run('/public/not-loaded', output), 1)
            report = json.loads((output/'summary.json').read_text())
            self.assertEqual(report['status'], 'arm_wait_worker_timeout')
            self.assertIsNone(report['observed_rtf_ratio_default_over_passive'])
            self.assertEqual(child.call_args.kwargs['timeout'], 600)
            self.assertEqual(b.wait_values(child.call_args.kwargs['env']), {})


if __name__ == '__main__':
    unittest.main()
