import copy
import unittest
from types import SimpleNamespace

import cpu_compile_resident_benchmark as b


class ResidentTests(unittest.TestCase):
    def rows(self):
        return [dict(call_index=i, phase=phase, mode=mode, fixture=fixture,
            seed=5192+fixture, generation_args=b.base.generation_args(
                'production_style', b.base.FIXTURES[fixture]),
            tts_seconds=30.0 if phase == 'cold' and fixture == 0 else
                8.0 if mode == b.COMPILED else 10.0,
            audio_seconds=5.0, natural_eos=True, signal_valid=True,
            exact_pcm_equal=True, exact_sequence_equal=True, exact_rate_and_samples=True,
            compiled_graph_invocations_during_call=20 if mode == b.COMPILED else 0,
            new_graphs_compiled_during_call=3 if phase == 'cold' and fixture == 0 else 0)
            for i, (phase, mode, fixture) in enumerate(b.SCHEDULE)]

    def test_one_compiled_callable_survives_mode_changes_and_errors(self):
        original = lambda value: value+1
        predictor = SimpleNamespace(forward=original)
        masks = [lambda value: value, lambda value: value]
        module = SimpleNamespace(**dict(zip(b.base.MASK_ALIASES, masks)))
        compilations = []; disables = []; counts = {'predictor_forward_calls': 0}
        def compiler(fn, **kwargs):
            compilations.append((fn, kwargs)); return fn
        def disable(fn, recursive):
            disables.append((fn, recursive)); return lambda value: fn(value)
        resident = b.ResidentForward(predictor, module, compiler, disable, 'backend', counts)
        retained = resident.compiled
        for mode in ('eager', b.COMPILED, b.COMPILED, 'eager', b.COMPILED):
            with resident.select(mode):
                self.assertEqual(predictor.forward(2), 3)
                if mode == b.COMPILED:
                    self.assertIs(predictor.forward, retained)
            self.assertIs(predictor.forward, original)
            for name, mask in zip(b.base.MASK_ALIASES, masks):
                self.assertIs(getattr(module, name), mask)
        with self.assertRaises(RuntimeError):
            with resident.select(b.COMPILED):
                raise RuntimeError('synthetic')
        self.assertIs(predictor.forward, original)
        self.assertEqual(len(compilations), 1); self.assertEqual(len(disables), 2)
        self.assertEqual(counts['predictor_forward_calls'], 3)
        self.assertEqual(compilations[0][1], dict(backend='backend', fullgraph=False, dynamic=True))

    def test_costs_include_cold_compilation_and_setup_without_forecast(self):
        rows = self.rows(); report = b.summarize(rows, {'backend_graphs_compiled': 3}, 2.0)
        self.assertTrue(report['speed_claim_valid'])
        self.assertEqual(report['measured_eager_seconds'], 80)
        self.assertEqual(report['measured_compiled_seconds'], 64)
        self.assertEqual(report['measured_wall_ratio_eager_over_compiled'], 1.25)
        self.assertEqual(report['measured_pair_median_saved_seconds'], 2)
        self.assertEqual(report['total_eager_synthesis_seconds'], 100)
        self.assertEqual(report['total_compiled_synthesis_and_setup_seconds'], 104)
        self.assertEqual(report['observed_total_net_saved_seconds'], -4)
        self.assertIsNone(report['observed_break_even_completed_calls'])
        self.assertEqual([p['completed_calls'] for p in report['cumulative_cost_checkpoints']], [4, 8, 12, 16, 20])
        self.assertFalse(report['break_even_forecast_performed'])
        self.assertFalse(report['production_eligible'])
        rows[2]['tts_seconds'] = 8
        recovered = b.summarize(rows, {}, 2)
        self.assertEqual(recovered['observed_break_even_completed_calls'], 4)

    def test_strict_output_new_graph_and_missing_execution_gates(self):
        for key in ('exact_pcm_equal', 'exact_sequence_equal', 'exact_rate_and_samples',
                    'natural_eos', 'signal_valid'):
            rows = self.rows(); rows[5][key] = False
            report = b.summarize(rows, {})
            self.assertFalse(report['speed_claim_valid'], key)
            self.assertIsNone(report['measured_wall_ratio_eager_over_compiled'])
            self.assertIsNone(report['observed_break_even_completed_calls'])
        for mutation in (
            lambda rows: rows[5].update(new_graphs_compiled_during_call=1),
            lambda rows: rows[5].update(compiled_graph_invocations_during_call=0),
            lambda rows: rows.pop(),
            lambda rows: rows.__delitem__(slice(12, None)),
        ):
            rows = self.rows(); mutation(rows)
            self.assertFalse(b.summarize(rows, {})['speed_claim_valid'])

    def test_partial_evidence_preserved_and_bad_schedule_or_timing_rejected(self):
        rows = self.rows(); before = copy.deepcopy(rows)
        report = b.summarize(rows[:4], {}, 2)
        self.assertEqual(report['rows'], before[:4])
        self.assertEqual(len(report['cumulative_cost_checkpoints']), 1)
        self.assertFalse(report['speed_claim_valid'])
        self.assertIsNone(report['observed_break_even_completed_calls'])
        self.assertEqual(rows, before)
        for mutation in (
            lambda r: r[0].update(tts_seconds=float('nan')),
            lambda r: r[0].update(seed=1),
            lambda r: r[0].update(fixture=1),
            lambda r: r[0].update(exact_pcm_equal=1),
        ):
            bad = self.rows(); mutation(bad)
            with self.assertRaises(ValueError): b.summarize(bad, {})

    def test_fixed_request_preserves_model_voice_scope_and_call_limits(self):
        request = b.request_value()
        self.assertEqual(request['model_revision'], b.base.REVISION)
        self.assertEqual(request['compile_target_path'], b.base.COMPILE_TARGET_PATH)
        self.assertEqual(request['call_timeout_seconds'], 360)
        self.assertEqual(request['worker_timeout_seconds'], 1760)
        self.assertEqual(request['request_id'], 'public-sohee-fp32-predictor-resident-20261113-02')
        self.assertEqual(len(b.SCHEDULE), 20)
        self.assertEqual(b.SCHEDULE[4:12], b.SCHEDULE[12:20])
        self.assertEqual(request['schedule'], [list(row) for row in b.SCHEDULE])
        self.assertFalse(request['production']); self.assertFalse(request['secrets'])
        self.assertFalse(request['gpu'])


if __name__ == '__main__':
    unittest.main()
