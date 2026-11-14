import copy
import unittest

import cpu_compile_long_benchmark as b


class LongTests(unittest.TestCase):
    def rows(self):
        rows, payloads = [], {}
        for index, (mode, fixture) in enumerate(b.SCHEDULE):
            rows.append(dict(call_index=index, mode=mode, **b.fixtures()[fixture],
                generation_args=b.generation_args(fixture),
                tts_seconds=(100.0, 130.0, 80.0, 90.0)[index], audio_seconds=20.0,
                sample_rate=24000, pcm_samples=480000, talker_sequences=[[7, 8, 9]],
                natural_eos=True, signal_valid=True, within_production_part_limit=True,
                compiled_graph_invocations_during_call=20 if mode == b.COMPILED else 0,
                new_graphs_compiled_during_call=3 if index == 1 else 0))
            payloads[index] = b'exact FLOAT bytes'+bytes([fixture])
            b.link_outputs(rows, payloads, fixture)
        return rows, payloads

    def test_production_styles_seeds_normalization_and_boundaries(self):
        fixtures = b.fixtures()
        self.assertEqual([f['fixture_mode'] for f in fixtures], ['explain', 'reading'])
        self.assertEqual([f['seed'] for f in fixtures], [5192, 7319])
        self.assertEqual([f['source_characters'] for f in fixtures], [170, 147])
        self.assertEqual([f['spoken_characters'] for f in fixtures], [170, 156])
        self.assertTrue(all(not f['normalization_review_flags'] for f in fixtures))
        self.assertIn('가상의 예문', fixtures[1]['source'])
        self.assertIn('제십일조 제이항', fixtures[1]['spoken'])
        self.assertIn('세 시간', fixtures[1]['spoken'])
        self.assertIn('스물다섯 명', fixtures[1]['spoken'])
        self.assertIn('아홉 시 삼십 분', fixtures[1]['spoken'])
        for index, fixture in enumerate(fixtures):
            args = b.generation_args(index)
            self.assertEqual({key: args[key] for key in b.SAMPLING}, b.SAMPLING)
            self.assertEqual(args['instruct'], b.STYLES[fixture['fixture_mode']])
            self.assertEqual(args['max_new_tokens'], 1100)
            self.assertEqual(args['speaker'], 'Sohee')
        request = b.request_value()
        self.assertEqual(request['schedule'], [list(row) for row in b.SCHEDULE])
        self.assertEqual((request['call_timeout_seconds'], request['worker_timeout_seconds']), (600, 1900))
        self.assertFalse(request['production']); self.assertFalse(request['secrets'])
        self.assertFalse(request['gpu'])

    def test_compiled_reading_is_pending_until_later_eager_reference(self):
        rows, payloads = self.rows()
        first_three = copy.deepcopy(rows[:3])
        b.link_outputs(first_three, payloads, 1)
        self.assertTrue(first_three[2]['comparison_pending'])
        self.assertIsNone(first_three[2]['exact_pcm_equal'])
        partial = b.summarize(first_three, {})
        self.assertFalse(partial['exact_public_outputs_equal'])
        self.assertFalse(partial['comparison_valid'])
        self.assertIsNone(partial['warm_reading_eager_over_compiled'])
        first_three.append(copy.deepcopy(rows[3])); b.link_outputs(first_three, payloads, 1)
        self.assertTrue(all(not row['comparison_pending'] for row in first_three))
        self.assertEqual(first_three[2]['comparison_reference_call_index'], 3)
        self.assertTrue(b.summarize(first_three, {})['comparison_valid'])

    def test_exact_pcm_bytes_sequences_rate_and_samples_are_independent_gates(self):
        for kind in ('pcm', 'sequence', 'rate', 'samples'):
            rows, payloads = self.rows()
            if kind == 'pcm': payloads[2] = b'different FLOAT bytes, including signed zero'
            if kind == 'sequence': rows[2]['talker_sequences'] = [[7, 9, 8]]
            if kind == 'rate': rows[2]['sample_rate'] = 16000
            if kind == 'samples': rows[2]['pcm_samples'] += 1
            b.link_outputs(rows, payloads, 1)
            report = b.summarize(rows, {})
            self.assertFalse(report['comparison_valid'], kind)
            self.assertIsNone(report['total_wall_ratio_including_compile_setup'])
            self.assertIsNone(report['warm_reading_eager_over_compiled'])

    def test_cold_compile_cost_and_warm_reading_ratio_are_separate(self):
        rows, _ = self.rows(); report = b.summarize(rows, {'backend_graphs_compiled': 3}, 2)
        self.assertTrue(report['comparison_valid'])
        self.assertTrue(report['warm_reading_speed_claim_valid'])
        self.assertEqual(report['total_eager_synthesis_seconds'], 190)
        self.assertEqual(report['total_compiled_synthesis_and_setup_seconds'], 212)
        self.assertEqual(report['observed_total_net_saved_seconds'], -22)
        self.assertFalse(report['actual_two_fixture_cost_recovered'])
        self.assertEqual(report['warm_reading_eager_over_compiled'], 90/80)
        self.assertEqual(report['pairs'][0]['compiled_seconds_including_setup'], 132)
        self.assertTrue(report['pairs'][0]['compiled_call_includes_first_compilation'])
        self.assertFalse(report['first_explain_pair_is_steady_state'])
        self.assertFalse(report['production_eligible'])
        self.assertFalse(report['break_even_forecast_performed'])
        rows[1]['tts_seconds'] = 100
        recovered = b.summarize(rows, {}, 2)
        self.assertTrue(recovered['actual_two_fixture_cost_recovered'])
        self.assertEqual(recovered['observed_total_net_saved_seconds'], 8)

    def test_new_graphs_reject_warm_claim_without_hiding_inclusive_timings(self):
        rows, _ = self.rows(); rows[2]['new_graphs_compiled_during_call'] = 1
        report = b.summarize(rows, {}, 2)
        self.assertTrue(report['comparison_valid'])
        self.assertFalse(report['warm_reading_speed_claim_valid'])
        self.assertIsNone(report['warm_reading_eager_over_compiled'])
        self.assertEqual(report['total_compiled_synthesis_and_setup_seconds'], 212)

    def test_signal_eos_limit_and_actual_execution_are_required(self):
        for mutation in (
            lambda rows: rows[0].update(signal_valid=False),
            lambda rows: rows[3].update(natural_eos=False),
            lambda rows: rows[1].update(within_production_part_limit=False),
            lambda rows: rows[2].update(compiled_graph_invocations_during_call=0),
            lambda rows: rows.pop(),
        ):
            rows, _ = self.rows(); mutation(rows)
            report = b.summarize(rows, {})
            self.assertFalse(report['comparison_valid'])
            self.assertIsNone(report['actual_two_fixture_cost_recovered'])

    def test_request_order_identity_and_finite_timings_are_enforced(self):
        for mutation in (
            lambda rows: rows[1].update(mode='eager'),
            lambda rows: rows[0].update(seed=5193),
            lambda rows: rows[0].update(source='unapproved source'),
            lambda rows: rows[0].update(tts_seconds=float('nan')),
            lambda rows: rows[0].update(exact_pcm_equal=1),
        ):
            rows, _ = self.rows(); mutation(rows)
            with self.assertRaises(ValueError): b.summarize(rows, {})


if __name__ == '__main__':
    unittest.main()
