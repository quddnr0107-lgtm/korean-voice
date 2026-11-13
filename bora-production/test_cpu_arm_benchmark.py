import copy
import unittest

import cpu_arm_benchmark as arm
from fp32_renderer import SAMPLING, STYLES


class ArmTests(unittest.TestCase):
    def info(self, n):
        return f'ATen/Parallel:\nomp_get_max_threads() : {n}\nMKL not found\n'

    def rows(self):
        return [dict(fixture=i, seed=5192+i, threads_requested=n,
            native_threads=dict(intraop_threads=n, interop_threads=1, openmp_available=True),
            parallel_info=self.info(n), dtype='float32', device='cpu',
            tts_seconds=float(n*10), audio_seconds=5.0, natural_eos=True, signal_valid=True,
            wav_sha256='a'*64, pcm_f32le_sha256='b'*64, talker_sequence_sha256='c'*64,
            generation_args=arm.generation_args('production_style', arm.FIXTURES[i]))
            for i, order in enumerate(arm.ORDERS) for n in order]

    def test_request_original_sampler_arm_wheel_difference_and_order(self):
        arm.validate_request()
        self.assertEqual(arm.ORDERS, ((4, 2), (2, 4)))
        args = arm.generation_args('production_style', arm.FIXTURES[0])
        self.assertEqual({key: args[key] for key in SAMPLING}, SAMPLING)
        self.assertEqual(args['instruct'], STYLES['explain'])
        self.assertEqual(arm.EXPECTED_PACKAGES['torch'], '2.8.0+cpu')
        self.assertEqual(arm.EXPECTED_PACKAGES['torchaudio'], '2.8.0')

    def test_actual_arm_architecture_required(self):
        for name in ('aarch64', 'arm64', 'ARM64'):
            self.assertIn(arm.validate_machine(name), ('aarch64', 'arm64'))
        for name in ('x86_64', '', 'armv7l', None):
            with self.assertRaises(ValueError): arm.validate_machine(name)

    def test_arm_native_threads_do_not_assume_mkl_exists(self):
        observed = arm.native_threads(2, 2, 1, self.info(2), True)
        self.assertIsNone(observed['mkl_get_max_threads'])
        arm.native_threads(4, 4, 1, 'ATen native backend', False)
        for args in ((2, 4, 1, self.info(4), True), (4, 4, 1, '', True),
                     (2, 2, 2, self.info(2), True), (True, 1, 1, '', False)):
            with self.assertRaises(ValueError): arm.native_threads(*args)

    def test_duration_normalized_results_and_pcm_hash_comparison(self):
        rows = self.rows(); before = copy.deepcopy(rows)
        result = arm.summarize(rows)
        self.assertEqual(result['totals']['4']['tts_seconds'], 80)
        self.assertEqual(result['totals']['2']['tts_seconds'], 40)
        self.assertEqual(result['observed_arm_rtf_ratio_4_over_2'], 2)
        self.assertTrue(result['identical_output_by_fixture']['0']['pcm_f32le_sha256'])
        self.assertFalse(result['production_eligible'])
        self.assertEqual(rows, before)

    def test_partial_eos_and_signal_failure_suppress_speed_claims(self):
        for change in (lambda r:r.pop(), lambda r:r[0].update(natural_eos=False),
                       lambda r:r[1].update(signal_valid=False)):
            rows = self.rows(); change(rows); result = arm.summarize(rows)
            self.assertFalse(result['complete_natural_eos_and_signal'])
            self.assertIsNone(result['observed_arm_rtf_ratio_4_over_2'])
        for change in (lambda r:r.reverse(), lambda r:r[0].update(tts_seconds=float('nan')),
                       lambda r:r[0].update(pcm_f32le_sha256='bad')):
            rows = self.rows(); change(rows)
            with self.assertRaises(ValueError): arm.summarize(rows)


if __name__ == '__main__':
    unittest.main()
