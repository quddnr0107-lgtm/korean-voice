import copy
import unittest
import cbackend_sohee_benchmark as b


class PublicCBackendTests(unittest.TestCase):
    def rows(self):
        return [dict(fixture=i, process_wall_seconds=10.0, audio_seconds=5.0,
            natural_eos_observed=True, signal_valid=True) for i in range(2)]

    def test_same_public_fixtures_and_no_hidden_quantization_or_eos_assist(self):
        from basic_sohee_benchmark import FIXTURES
        self.assertEqual(b.FIXTURES, FIXTURES)
        args = b.command('bin', 'model', 'out.wav', 0)
        self.assertEqual(args[args.index('--eos-strategy') + 1], 'off')
        self.assertEqual(args[args.index('-s') + 1], 'sohee')
        self.assertEqual(args[args.index('-l') + 1], 'Korean')
        self.assertEqual(args[args.index('-j') + 1], '4')
        for forbidden in ('--int8', '--int4', '--emotion', '--rate', '--backend', '--instruct'):
            self.assertNotIn(forbidden, args)

    def test_optional_style_explicit(self):
        args = b.command('bin', 'model', 'out.wav', 1, 'Public style')
        self.assertEqual(args[-2:], ['--instruct', 'Public style'])

    def test_eos_missing_duplicate_and_cap_cannot_claim_complete(self):
        for log in ('no evidence', 'EOS at frame 1100\n', 'EOS at frame 0\n',
                    'EOS at frame 40\nEOS at frame 41\n'):
            self.assertFalse(b.eos_evidence(log)['natural_eos_observed'])
        self.assertTrue(b.eos_evidence('  EOS at frame 54\n')['natural_eos_observed'])

    def test_timing_ratio_not_quality_or_publication(self):
        rows = self.rows(); before = copy.deepcopy(rows)
        out = b.summarize(rows)
        self.assertEqual(out['process_seconds_per_audio_second'], 2.0)
        self.assertTrue(out['process_time_includes_each_model_load'])
        self.assertFalse(out['production_eligible'])
        self.assertFalse(out['human_listening_verified'])
        self.assertEqual(rows, before)

    def test_incomplete_or_signal_invalid_suppresses_throughput_claim(self):
        for key in ('natural_eos_observed', 'signal_valid'):
            rows = self.rows(); rows[0][key] = False
            self.assertIsNone(b.summarize(rows)['process_seconds_per_audio_second'])

    def test_coverage_and_numeric_timers_fail_closed(self):
        for mutation in (lambda r:r.pop(), lambda r:r[1].update(fixture=0),
                         lambda r:r[0].update(process_wall_seconds=float('nan')),
                         lambda r:r[0].update(audio_seconds=0),
                         lambda r:r[0].update(audio_seconds=True)):
            rows = self.rows(); mutation(rows)
            with self.assertRaises(ValueError): b.summarize(rows)


if __name__ == '__main__': unittest.main()
