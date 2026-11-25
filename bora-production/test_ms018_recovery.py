"""Synthetic regression for exact unit-only seed recovery. No model or keys."""
import copy
import unittest
from unittest.mock import patch
import ms018_recovery as recovery


class MS018RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.plan = dict(lesson_id='MS-018', units=[{} for _ in range(52)])
        self.settings = dict(seeds=[5192], engine='qwen-tts-python', dtype='float32',
                             sampling={'max_new_tokens': 1100})
        self.plan_pin = recovery.identity(self.plan)
        self.settings_pin = recovery.identity(self.settings)
        self.pins = patch.multiple(recovery, PLAN=self.plan_pin, SETTINGS=self.settings_pin)
        self.pins.start()
        self.addCleanup(self.pins.stop)
        self.rows = {i: {'receipt': {'status': 'passed'}} for i in range(52)}
        self.rows[50] = {'receipt': dict(status='blocked_requires_review', review_flags=[],
            natural_eos=True, render_settings=copy.deepcopy(self.settings),
            render_settings_sha256=self.settings_pin,
            candidates=[dict(seed=5192, natural_eos=True,
                             signal=dict(blocked=True, reasons=['clipped_waveform']))])}

    def resolve(self, rows=None, replacement=None):
        return recovery.resolve(self.plan, recovery.RECIPE, self.settings_pin,
                                self.rows if rows is None else rows,
                                lambda _: {} if replacement is None else replacement)

    def test_only_exact_failed_unit_pending(self):
        rows, settings = self.resolve()
        self.assertEqual(set(rows), set(range(52)) - {50})
        self.assertEqual(settings['seeds'], [7319])
        self.assertEqual(self.settings['seeds'], [5192])
        self.assertTrue(recovery.is_replacement(settings, recovery.identity(settings)))
        self.assertNotEqual(recovery.identity(settings), self.settings_pin)
        for i in rows:
            self.assertIs(rows[i], self.rows[i])

    def test_passed_original_never_regenerated(self):
        rows = copy.deepcopy(self.rows)
        rows[50]['receipt']['status'] = 'passed'
        result, settings = self.resolve(rows)
        self.assertEqual(result, rows)
        self.assertIsNone(settings)

    def test_missing_or_failed_other_unit_rejected(self):
        for status in ('missing', 'blocked_requires_review'):
            rows = copy.deepcopy(self.rows)
            if status == 'missing':
                del rows[49]
            else:
                rows[49]['receipt']['status'] = status
            with self.assertRaises(ValueError):
                self.resolve(rows)

    def test_wrong_failure_or_review_rejected(self):
        for reason in ('silent_waveform', 'invalid_waveform'):
            rows = copy.deepcopy(self.rows)
            rows[50]['receipt']['candidates'][0]['signal']['reasons'] = [reason]
            with self.assertRaisesRegex(ValueError, 'ms018_original_failure_scope'):
                self.resolve(rows)
        rows = copy.deepcopy(self.rows)
        rows[50]['receipt']['review_flags'] = ['unresolved']
        with self.assertRaises(ValueError):
            self.resolve(rows)

    def test_replacement_reused_without_another_attempt(self):
        _, settings = self.resolve()
        row = {'receipt': dict(status='passed', seed=7319, render_settings=settings,
                               render_settings_sha256=recovery.identity(settings))}
        result, _ = self.resolve(replacement={50: row})
        self.assertIs(result[50], row)
        with self.assertRaises(ValueError):
            self.resolve(replacement={51: row})
        row['receipt']['status'] = 'blocked_requires_review'
        with self.assertRaisesRegex(ValueError, 'already_failed'):
            self.resolve(replacement={50: row})

    def test_settings_drift_not_compatible(self):
        settings = recovery.replacement_settings(self.settings)
        settings['sampling']['max_new_tokens'] = 1200
        self.assertFalse(recovery.is_replacement(settings, recovery.identity(settings)))

    def test_scope_cannot_extend_to_other_plan(self):
        with self.assertRaises(ValueError):
            recovery.scope(dict(self.plan, lesson_id='MS-022'), recovery.RECIPE, self.settings_pin)


if __name__ == '__main__':
    unittest.main()
