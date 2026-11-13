import copy
import unittest
import asr_timestamp_benchmark as b


class TimestampBenchmarkTests(unittest.TestCase):
    def rows(self):
        return [dict(fixture=i, word_timestamps=setting, seconds=2.0 if setting else 1.0,
            audio_seconds=5.0, character_errors=0, missing_critical_count=0,
            canonical_sha256='a' * 64, word_count=3 if setting else 0)
            for i in range(4) for setting in (True, False)]

    def test_parity_uses_exact_transcript_and_both_gate_dimensions(self):
        result = b.summarize(self.rows())
        self.assertTrue(result['canonical_and_gate_parity'])
        self.assertEqual(result['equivalent_transcript_timing_reduction_pct'], 50.0)
        self.assertFalse(result['production_eligible'])
        self.assertFalse(result['synthesis_started'])

    def test_missing_duplicate_and_boolean_indices_rejected(self):
        for change in (lambda r:r.pop(), lambda r:r.__setitem__(1, copy.deepcopy(r[0])),
                       lambda r:r[0].update(fixture=True)):
            rows = self.rows(); change(rows)
            with self.assertRaises(ValueError): b.summarize(rows)

    def test_bad_timers_cannot_make_a_speedup(self):
        for value in (0, -1, float('inf'), float('nan'), True):
            rows = self.rows(); rows[0]['seconds'] = value
            with self.assertRaises(ValueError): b.summarize(rows)

    def test_transcript_change_cannot_be_claimed_equivalent(self):
        rows = self.rows(); rows[1]['canonical_sha256'] = 'b' * 64
        result = b.summarize(rows)
        self.assertFalse(result['canonical_and_gate_parity'])
        self.assertIsNone(result['equivalent_transcript_timing_reduction_pct'])

    def test_gate_change_and_error_count_change_are_retained(self):
        for changes in ({'character_errors':1}, {'missing_critical_count':1}):
            rows = self.rows(); rows[1].update(changes)
            result = b.summarize(rows)
            self.assertFalse(result['pairs'][0]['exact_gate_outcome_equal'])
            self.assertFalse(result['canonical_and_gate_parity'])

    def test_false_timestamps_does_not_require_words_and_inputs_are_immutable(self):
        rows = self.rows(); before = copy.deepcopy(rows)
        result = b.summarize(rows)
        self.assertEqual(rows, before)
        self.assertEqual(result['rows'][1]['word_count'], 0)
        rows[1]['audio_seconds'] = 4.0
        with self.assertRaises(ValueError): b.summarize(rows)


if __name__ == '__main__': unittest.main()
