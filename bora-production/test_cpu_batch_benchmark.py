import copy
import unittest

import cpu_batch_benchmark as batch
from fp32_renderer import SAMPLING, STYLES


class CpuBatchTests(unittest.TestCase):
    def test_exact_production_recipe_and_official_explicit_batch_lists(self):
        batch.validate_request()
        for mode in batch.MODES:
            kwargs = batch.generation_args(mode, 0 if mode == 'serial' else None)
            self.assertEqual({key: kwargs[key] for key in SAMPLING}, SAMPLING)
            self.assertEqual(kwargs['max_new_tokens'], 1100)
            self.assertEqual(kwargs['instruct'], STYLES['explain'] if mode == 'serial' else [STYLES['explain']] * 2)
            self.assertEqual(kwargs['speaker'], 'Sohee' if mode == 'serial' else ['Sohee'] * 2)
            self.assertEqual(kwargs['language'], 'Korean' if mode == 'serial' else ['Korean'] * 2)
            self.assertEqual(kwargs['text'], batch.FIXTURES[0] if mode == 'serial' else list(batch.FIXTURES))
        self.assertNotIn('seed', batch.generation_args('batch2'))

    def test_eos_uses_observed_padding_including_repeated_eos_not_guessed_zero(self):
        for tokens, pad in (([3, 4, 2150, 0, 0], 0), ([3, 4, 2150, 2150, 2150], 2150),
                ([3, 4, 2150], 2150)):
            result = batch.row_eos(tokens, 2150, pad, 2, 1920, 3840)
            self.assertTrue(result['natural_eos'])
            self.assertEqual(result['first_eos_index'], 2)
        self.assertFalse(batch.row_eos([3, 4, 2150, 0], 2150, 2150, 2, 1920, 3840)['natural_eos'])
        self.assertFalse(batch.row_eos([3, 4, 2150], 2150, None, 2, 1920, 3840)['natural_eos'])

    def test_missing_eos_cap_wrong_codec_count_and_pcm_length_fail_closed(self):
        for tokens, frames, samples in (([3, 4, 5], 3, 5760), ([2150], 0, 0),
                ([3] * 1099 + [2150], 1099, 1099 * 1920),
                ([3, 4, 2150], 3, 5760), ([3, 4, 2150], 2, 3800),
                ([3, 4, 2150, 9], 2, 3840)):
            self.assertFalse(batch.row_eos(tokens, 2150, 0, frames, 1920, samples)['natural_eos'])
        # A completed short row can be padded while another row reaches the cap.
        self.assertTrue(batch.row_eos([3, 4, 2150] + [0] * 1097, 2150, 0, 2, 1920, 3840)['natural_eos'])

    def records(self):
        calls = [dict(id='serial-0', mode='serial', fixtures=[0], mode_seed=5192, tts_seconds=12.0),
            dict(id='serial-1', mode='serial', fixtures=[1], mode_seed=5192, tts_seconds=10.0),
            dict(id='batch2-0', mode='batch2', fixtures=[0, 1], mode_seed=5192, tts_seconds=14.0)]
        rows = [dict(mode=mode, fixture=i, text=batch.FIXTURES[i], mode_seed=5192,
            call_id=f'{mode}-{i}' if mode == 'serial' else 'batch2-0', audio_seconds=4.0,
            natural_eos=True, signal_valid=True) for mode in batch.MODES for i in range(2)]
        return calls, rows

    def test_shared_batch_timer_counted_once_and_seed_identity_not_claimed(self):
        calls, rows = self.records(); before = copy.deepcopy((calls, rows))
        report = batch.summarize(calls, rows)
        self.assertEqual(report['totals']['serial']['tts_seconds'], 22.0)
        self.assertEqual(report['totals']['batch2']['tts_seconds'], 14.0)
        self.assertEqual(report['observed_pair_wall_ratio_serial_over_batch'], round(22 / 14, 6))
        self.assertEqual(report['model_load_count'], 1)
        self.assertFalse(report['production_eligible'])
        self.assertFalse(report['per_unit_seed_equivalence_claimed'])
        self.assertEqual((calls, rows), before)

    def test_incomplete_or_invalid_measurement_suppresses_speed_claim(self):
        for mutation in (lambda c,r:r.pop(), lambda c,r:r[0].update(natural_eos=False),
                lambda c,r:r[2].update(signal_valid=False)):
            calls, rows = self.records(); mutation(calls, rows)
            report = batch.summarize(calls, rows)
            self.assertIsNone(report['observed_pair_wall_ratio_serial_over_batch'])
            self.assertIsNone(report['observed_rtf_ratio_serial_over_batch'])
        for mutation in (lambda c,r:c[0].update(tts_seconds=float('nan')),
                lambda c,r:r[0].update(audio_seconds=0), lambda c,r:r[0].update(fixture=1)):
            calls, rows = self.records(); mutation(calls, rows)
            with self.assertRaises(ValueError): batch.summarize(calls, rows)


if __name__ == '__main__':
    unittest.main()
