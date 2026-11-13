import copy
import types
import unittest

import python_history_bench as bench


class InnerHistoryTests(unittest.TestCase):
    def test_only_inner_history_flag_changes_and_arguments_are_not_mutated(self):
        calls = []
        def original(*args, **kwargs):
            calls.append((args, kwargs))
            return types.SimpleNamespace(hidden_states=None if not kwargs['output_hidden_states'] else ('history',))
        observations = {}
        wrapper = bench.inner_history_wrapper(original, True, observations)
        options = dict(output_hidden_states=True, return_dict_in_generate=True,
            do_sample=True, top_p=.9, top_k=50, temperature=.9, max_new_tokens=15)
        before = copy.deepcopy(options)
        result = wrapper('embedding', **options)
        self.assertIsNone(result.hidden_states)
        self.assertEqual(options, before)
        expected = dict(before, output_hidden_states=False)
        self.assertEqual(calls, [(('embedding',), expected)])
        self.assertEqual(observations, dict(calls=1, history_disabled_calls=1))

    def test_baseline_forwards_everything_unchanged(self):
        seen = []
        def original(**kwargs):
            seen.append(kwargs)
            return types.SimpleNamespace(hidden_states=('history',))
        options = dict(output_hidden_states=True, return_dict_in_generate=True, top_k=50)
        observations = {}
        bench.inner_history_wrapper(original, False, observations)(**options)
        self.assertEqual(seen, [options])
        self.assertEqual(observations, dict(calls=1, history_disabled_calls=0))

    def test_caller_contract_change_fails_before_running_original(self):
        def forbidden(**kwargs):
            self.fail('original must not run')
        for options in ({'output_hidden_states': False, 'return_dict_in_generate': True},
                        {'output_hidden_states': True, 'return_dict_in_generate': False}):
            with self.assertRaises(ValueError):
                bench.inner_history_wrapper(forbidden, True, {})(**options)

    def test_flag_that_did_not_disable_collection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'inner_history_still_returned'):
            bench.inner_history_wrapper(lambda **kw: types.SimpleNamespace(hidden_states=('not disabled',)),
                True, {})(output_hidden_states=True, return_dict_in_generate=True)

    def test_trial_order_is_balanced(self):
        self.assertEqual(bench.trial_order(2), [
            (0, 0, 'baseline'), (0, 0, 'inner_history_off'),
            (0, 1, 'inner_history_off'), (0, 1, 'baseline'),
            (1, 0, 'inner_history_off'), (1, 0, 'baseline'),
            (1, 1, 'baseline'), (1, 1, 'inner_history_off')])

    def rows(self):
        return [dict(repeat=r, fixture=f, profile=p, status='ok', natural_eos=True,
            signal_valid=True, pcm_f32_sha256='pcm'+str(f), codec_sha256='codes'+str(f),
            codec_shape=[50, 16], samples=100, sample_rate=24000,
            generate_custom_voice_seconds=10 if p == 'baseline' else 8)
            for r, f, p in bench.trial_order(2)]

    def test_exact_match_and_actual_fixed_text_timings_enable_comparison(self):
        result = bench.summarize(self.rows(), 2)
        self.assertTrue(result['exact_pcm_and_codec_equal'])
        self.assertAlmostEqual(result['observed_time_reduction_pct'], 20)
        self.assertFalse(result['production_eligible'])

    def test_missing_changed_audio_changed_codes_or_no_eos_cannot_claim_speedup(self):
        for mutate in (lambda r: r.pop(),
                       lambda r: r[1].update(pcm_f32_sha256='changed'),
                       lambda r: r[1].update(codec_sha256='changed'),
                       lambda r: r[1].update(natural_eos=False),
                       lambda r: r[4].update(pcm_f32_sha256='baseline nondeterminism')):
            rows = self.rows(); mutate(rows)
            result = bench.summarize(rows, 2)
            self.assertFalse(result['exact_pcm_and_codec_equal'])
            self.assertIsNone(result['observed_time_reduction_pct'])

    def test_duplicate_trials_and_unexpected_profiles_are_rejected(self):
        rows = self.rows()
        with self.assertRaises(ValueError):bench.summarize(rows + [rows[0]], 2)
        rows[0]['profile'] = 'alternate_voice'
        with self.assertRaises(ValueError):bench.summarize(rows, 2)

    def test_token_cap_is_never_natural_completion(self):
        self.assertTrue(bench.eos_observation([1, 2, 7], 7, 10)['natural_eos'])
        self.assertFalse(bench.eos_observation([1, 2, 7], 7, 3)['natural_eos'])


if __name__ == '__main__':
    unittest.main()
