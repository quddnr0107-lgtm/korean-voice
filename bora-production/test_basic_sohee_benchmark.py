import copy
import unittest
import basic_sohee_benchmark as b


class BasicBenchmarkTests(unittest.TestCase):
    def rows(self):
        return [dict(case=c, fixture=i, seed=5192+i, tts_seconds=20.0,
            audio_seconds=5.0, natural_eos=True, signal_valid=True)
            for i in range(2) for c in b.CASES]

    def test_plain_omits_instruction_and_all_sampling_overrides(self):
        x=b.generation_args('plain_defaults', b.FIXTURES[0])
        self.assertEqual(set(x), {'text','language','speaker','max_new_tokens'})
        self.assertEqual(x['speaker'],'Sohee')
        self.assertEqual(x['max_new_tokens'],1100)

    def test_production_comparator_keeps_exact_current_sampling(self):
        x=b.generation_args('production_style',b.FIXTURES[0])
        for key,value in b.SAMPLING.items():self.assertEqual(x[key],value)
        self.assertEqual(x['instruct'],b.STYLE)

    def test_summary_uses_decoded_audio_not_fixed_duration(self):
        rows=self.rows();before=copy.deepcopy(rows)
        out=b.summarize(rows)
        self.assertEqual(out['totals']['plain_defaults']['tts_seconds'],40)
        self.assertEqual(out['totals']['plain_defaults']['audio_seconds'],10)
        self.assertEqual(out['totals']['plain_defaults']['synthesis_seconds_per_audio_second'],4)
        self.assertEqual(rows,before)
        self.assertFalse(out['production_eligible'])
        self.assertFalse(out['full_course_forecast_validated'])

    def test_missing_duplicate_and_invalid_timers_fail(self):
        for mutate in (lambda x:x.pop(),lambda x:x.__setitem__(0,dict(x[1])),
                lambda x:x[0].update(tts_seconds=float('nan')),
                lambda x:x[0].update(audio_seconds=0),
                lambda x:x[0].update(seed=7)):
            x=self.rows();mutate(x)
            with self.assertRaises(ValueError):b.summarize(x)

    def test_truncation_and_signal_failure_do_not_create_throughput_claim(self):
        for field in ('natural_eos','signal_valid'):
            x=self.rows();x[0][field]=False
            out=b.summarize(x)
            self.assertFalse(out['complete_natural_eos_and_signal'])
            self.assertIsNone(out['totals']['plain_defaults']['synthesis_seconds_per_audio_second'])

    def test_eos_at_cap_or_followed_by_token_is_not_natural(self):
        self.assertTrue(b.observe_eos([1,2,9],9)['natural_eos'])
        self.assertFalse(b.observe_eos([9,2],9)['natural_eos'])
        self.assertFalse(b.observe_eos([1]*1099+[9],9)['natural_eos'])


if __name__ == '__main__':unittest.main()
