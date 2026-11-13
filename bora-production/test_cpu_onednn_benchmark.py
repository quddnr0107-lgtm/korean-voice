import copy
from types import SimpleNamespace
import unittest
import cpu_onednn_benchmark as b


class OneDNNTests(unittest.TestCase):
    def rows(self):
        return [dict(fixture=i, mode=mode, seed=5192+i, dtype='float32', device='cpu',
            natural_eos=True, signal_valid=True, mkldnn_enabled_observed=mode == 'enabled',
            backend_before=dict(enabled=mode == 'enabled', deterministic=False,
                allow_tf32=True, float32_matmul_precision='highest'),
            backend_after=dict(enabled=mode == 'enabled', deterministic=False,
                allow_tf32=True, float32_matmul_precision='highest'),
            threads_observed=4, interop_threads=1,
            parallel_info='omp_get_max_threads() : 4\nmkl_get_max_threads() : 4\n',
            tts_seconds=10.0 if mode == 'enabled' else 8.0, talker_seconds=7.0,
            audio_seconds=4.0, wav_sha256='a'*64, pcm_float32_sha256='b'*64,
            talker_sequence_sha256='c'*64,
            generation_args=b.generation_args('production_style', b.FIXTURES[i]))
            for i, order in enumerate(b.ORDERS) for mode in order]

    def test_request_and_recipe_keep_original_sampling_and_ab_ba_order(self):
        b.validate_request()
        self.assertEqual(b.ORDERS, (('enabled', 'disabled'), ('disabled', 'enabled')))
        args = b.generation_args('production_style', b.FIXTURES[0])
        self.assertEqual(args['speaker'], 'Sohee')
        self.assertEqual(args['temperature'], .8)
        self.assertIn('instruct', args)

    def test_only_enabled_changes_and_other_precision_flags_are_preserved(self):
        backend = SimpleNamespace(enabled=True, deterministic=True, allow_tf32=False)
        torch = SimpleNamespace(backends=SimpleNamespace(mkldnn=backend),
            get_float32_matmul_precision=lambda: 'highest')
        original = b.backend_flags(torch)
        self.assertEqual(b.set_enabled_only(torch, False, original), dict(original, enabled=False))
        self.assertTrue(backend.deterministic)
        self.assertFalse(backend.allow_tf32)
        b.set_enabled_only(torch, True, original)
        self.assertEqual(b.backend_flags(torch), original)
        backend.allow_tf32 = True
        with self.assertRaises(ValueError): b.set_enabled_only(torch, False, original)
        backend.allow_tf32 = None
        unsupported = b.backend_flags(torch)
        self.assertIsNone(unsupported['allow_tf32'])
        for enabled in (False, True):
            self.assertIsNone(b.set_enabled_only(torch, enabled, unsupported)['allow_tf32'])
            self.assertIsNone(backend.allow_tf32)
        diagnostic = b.backend_diagnostic(torch)
        self.assertEqual(diagnostic['allow_tf32'],
            dict(python_type='builtins.NoneType', observed_repr='None'))
        rows = self.rows()
        for row in rows:
            row['backend_before']['allow_tf32'] = None
            row['backend_after']['allow_tf32'] = None
        self.assertTrue(b.summarize(rows)['complete_natural_eos_and_signal'])

    def test_measured_time_audio_pcm_and_inputs_preserved(self):
        rows = self.rows(); before = copy.deepcopy(rows)
        report = b.summarize(rows)
        self.assertEqual(report['totals']['enabled']['tts_seconds'], 20)
        self.assertEqual(report['totals']['disabled']['tts_seconds'], 16)
        self.assertEqual(report['observed_rtf_ratio_enabled_over_disabled'], 1.25)
        self.assertTrue(all(report['identical_pcm_float32_hashes_by_fixture'].values()))
        self.assertEqual(rows, before)
        self.assertFalse(report['production_eligible'])

    def test_partial_eos_or_signal_failure_suppresses_all_speed_claims(self):
        for change in (lambda r: r.pop(), lambda r: r[0].update(natural_eos=False),
                lambda r: r[2].update(signal_valid=False)):
            rows = self.rows(); change(rows)
            report = b.summarize(rows)
            self.assertFalse(report['complete_natural_eos_and_signal'])
            self.assertIsNone(report['observed_rtf_ratio_enabled_over_disabled'])
            self.assertIsNone(report['observed_pair_wall_ratio_enabled_over_disabled'])
            self.assertTrue(all(t['synthesis_seconds_per_audio_second'] is None
                for t in report['totals'].values()))

    def test_wrong_mode_native_threads_flags_or_nonfinite_time_rejected(self):
        def flags_change(rows):
            rows[1]['backend_before']['allow_tf32'] = False
            rows[1]['backend_after']['allow_tf32'] = False
        for change in (lambda r: r.reverse(),
                lambda r: r[0].update(mkldnn_enabled_observed=False),
                lambda r: r[0].update(threads_observed=2),
                lambda r: r[0].update(tts_seconds=float('nan')), flags_change):
            rows = self.rows(); change(rows)
            with self.assertRaises(ValueError): b.summarize(rows)

    def test_pcm_and_wav_hashes_are_compared_separately(self):
        rows = self.rows(); rows[1]['wav_sha256'] = 'd'*64
        report = b.summarize(rows)
        self.assertFalse(report['identical_wav_hashes_by_fixture']['0'])
        self.assertTrue(report['identical_pcm_float32_hashes_by_fixture']['0'])
        rows[1]['pcm_float32_sha256'] = 'e'*64
        self.assertFalse(b.summarize(rows)['identical_pcm_float32_hashes_by_fixture']['0'])


if __name__ == '__main__':
    unittest.main()

