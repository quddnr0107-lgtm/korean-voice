import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cpu_compile_benchmark as b
from fp32_renderer import SAMPLING, STYLES


class CompileTests(unittest.TestCase):
    def counts(self):
        return dict(backend_compile_attempts=1, backend_graphs_compiled=1,
            compiled_graph_invocations=20, predictor_forward_calls=20)

    def rows(self):
        return [dict(mode=mode, fixture=i, seed=5192+i, tts_seconds=10.0,
            audio_seconds=5.0, signal_valid=True, natural_eos=True,
            wav_sha256='a'*64, pcm_sha256='b'*64, talker_sequence_sha256='c'*64,
            generation_args=b.generation_args('production_style', b.FIXTURES[i]),
            compiled_graph_invocations_during_call=10 if mode == 'compiled_predictor' else 0,
            new_graphs_compiled_during_call=1 if mode == 'compiled_predictor' and i == 0 else 0)
            for mode in b.MODES for i in range(2)]

    def test_request_and_voice_controls_remain_original(self):
        b.validate_request()
        args = b.generation_args('production_style', b.FIXTURES[0])
        self.assertEqual({k: args[k] for k in SAMPLING}, SAMPLING)
        self.assertEqual(args['instruct'], STYLES['explain'])
        self.assertEqual(args['speaker'], 'Sohee')

    def test_backend_requires_actual_returned_code_invocation(self):
        counts = dict.fromkeys(self.counts(), 0)
        backend = b.tracked_backend(lambda graph, inputs: lambda value: value+1, counts)
        result = backend(object(), [])
        self.assertEqual(counts['backend_graphs_compiled'], 1)
        self.assertEqual(counts['compiled_graph_invocations'], 0)
        self.assertEqual(result(4), 5)
        self.assertEqual(counts['compiled_graph_invocations'], 1)
        with self.assertRaises(RuntimeError):
            b.tracked_backend(lambda graph, inputs: (_ for _ in ()).throw(RuntimeError()), counts)(None, [])
        self.assertEqual(counts['backend_graphs_compiled'], 1)

    def test_forward_patch_only_preserves_arguments_and_restores_after_error(self):
        original = lambda value, scale=1: value*scale
        predictor = SimpleNamespace(forward=original)
        counts = dict.fromkeys(self.counts(), 0); seen = []
        def compiler(target, **kwargs):
            seen.append((target, kwargs)); return target
        with self.assertRaises(RuntimeError):
            with b.compiled_forward(predictor, compiler, 'backend', counts):
                self.assertEqual(predictor.forward(3, scale=2), 6)
                raise RuntimeError()
        self.assertIs(predictor.forward, original)
        self.assertEqual(counts['predictor_forward_calls'], 1)
        self.assertEqual(seen, [(original, dict(backend='backend', fullgraph=False, dynamic=True))])

    def test_first_compile_cost_included_and_second_not_assumed_steady(self):
        rows, counts = self.rows(), self.counts(); before = copy.deepcopy(rows)
        rows[2]['tts_seconds'] = 30
        out = b.summarize(rows, counts)
        self.assertEqual(out['totals']['compiled_predictor']['tts_seconds'], 40)
        self.assertEqual(out['observed_pair_wall_ratio_eager_over_compiled'], .5)
        self.assertTrue(out['second_call_proven_steady_state'])
        rows[3]['new_graphs_compiled_during_call'] = 1
        self.assertFalse(b.summarize(rows, counts)['second_call_proven_steady_state'])
        self.assertEqual(before[0], rows[0]); self.assertFalse(out['production_eligible'])

    def test_eager_fallback_partial_eos_or_invalid_timing_cannot_claim_speed(self):
        for mutation in (lambda r,c:r.pop(), lambda r,c:r[2].update(natural_eos=False),
                lambda r,c:c.update(compiled_graph_invocations=0),
                lambda r,c:r[3].update(compiled_graph_invocations_during_call=0)):
            rows, counts = self.rows(), self.counts(); mutation(rows, counts)
            self.assertIsNone(b.summarize(rows, counts)['observed_rtf_ratio_eager_over_compiled'])
        rows = self.rows(); rows[0]['tts_seconds'] = float('nan')
        with self.assertRaises(ValueError): b.summarize(rows, self.counts())

    def test_failed_worker_and_public_diagnostic_preserve_baseline_and_bound_chain(self):
        report = b.summarize(self.rows()[:2], self.counts())
        before = copy.deepcopy(report)
        failed = b.failed_worker_report(report, -9)
        self.assertEqual(failed['rows'], before['rows'])
        self.assertEqual(failed['worker_returncode'], -9)
        self.assertEqual(failed['status'], 'compile_worker_failed')
        self.assertIsNone(failed['observed_rtf_ratio_eager_over_compiled'])
        self.assertEqual(report, before)
        inner = RuntimeError('C++ header failure: '+'X'*20000+' final stderr: missing include')
        outer = ValueError('public compile wrapper'); outer.__cause__ = inner
        diagnostic = b.public_error_report(outer)
        self.assertEqual([row['type'] for row in diagnostic['exception_chain']], ['ValueError', 'RuntimeError'])
        self.assertEqual(diagnostic['exception_chain'][1]['module'], 'builtins')
        self.assertIn('C++ header failure:', diagnostic['exception_chain'][1]['message'])
        self.assertIn('final stderr: missing include', diagnostic['exception_chain'][1]['message'])
        self.assertTrue(diagnostic['exception_chain'][1]['message_truncated'])
        self.assertTrue(all(len(row['message']) <= 2000 for row in diagnostic['exception_chain']))
        self.assertLessEqual(len(diagnostic['traceback']), 12000)
        current = outer
        for _ in range(6):
            child = RuntimeError('nested error'); current.__context__ = child; current = child
        outer.__cause__ = None
        self.assertEqual(len(b.public_error_report(outer)['exception_chain']), 4)
        self.assertTrue(b.public_error_report(outer)['chain_limit_reached'])
        inner.__cause__ = outer; outer.__cause__ = inner
        self.assertTrue(b.public_error_report(outer)['chain_cycle_detected'])
        with patch.dict(os.environ, ADMIN_LAW_PREMIUM_SOURCE_KEY_B64='synthetic'):
            with self.assertRaises(RuntimeError): b.public_error_report(outer)


if __name__ == '__main__':
    unittest.main()
