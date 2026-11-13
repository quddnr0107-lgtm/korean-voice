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
        for log in ('no evidence', self.log(eos=1100, generated=1100),
                    self.log(eos=0, generated=0),
                    self.log() + 'EOS at frame 41\n',
                    self.log().replace('EOS strategy: off (suppress=2)\n', ''),
                    self.log(generated=55), self.log(strategy='v1'),
                    self.log() + 'EOS strategy: v1 (suppress=2)\n',
                    self.log() + 'Generated 54 frames (4.3s audio)\n',
                    self.log().replace('  EOS at frame 54', 'Text: EOS at frame 54')):
            self.assertFalse(b.eos_evidence(log)['natural_eos_observed'])
        self.assertTrue(b.eos_evidence(self.log())['natural_eos_observed'])

    def log(self, eos=54, generated=54, strategy='off', inline=False):
        prefix = '  Frame 50/1100 (4.0s audio)...  ' if inline else '  '
        return (f'EOS strategy: {strategy} (suppress=2)\n'
            f'{prefix}EOS at frame {eos}\n\n'
            f'  Generated {generated} frames (4.3s audio)\n')

    def test_actual_pinned_inline_progress_is_consistent_eos_evidence(self):
        out = b.eos_evidence(self.log(eos=56, generated=56, inline=True))
        self.assertTrue(out['natural_eos_observed'])
        self.assertEqual(out['eos_frame'], 56)
        self.assertEqual(out['generated_frames'], 56)
        self.assertFalse(out['eos_assistance_enabled'])

    def test_carriage_return_progress_and_malformed_duplicate_fail_closed(self):
        text = self.log(eos=65, generated=65, inline=True)
        self.assertTrue(b.eos_evidence(text.replace('\n', '\r\n'))['natural_eos_observed'])
        self.assertFalse(b.eos_evidence(text + 'Text: EOS at frame 65\n')['natural_eos_observed'])

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
