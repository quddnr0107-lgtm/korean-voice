"""Regression tests for transient Actions inventory changes; no network."""
import unittest
from unittest.mock import patch, Mock
import ms_residual_capacity as guard
from wait_for_tb_capacity import RetrySnapshot


class StableResidualCapacityTests(unittest.TestCase):
    def test_current_run_temporarily_missing_then_two_complete_reads(self):
        good = ((('run',),), 17)
        sleep = Mock()
        with patch.object(guard, 'snapshot', side_effect=[
            ValueError('ms018_capacity_current_run'), good, good
        ]) as read:
            self.assertEqual(guard.stable_snapshot('fake', 1, 'a'*40, sleep=sleep), good)
        self.assertEqual(read.call_count, 3)
        sleep.assert_called_once()

    def test_short_inventory_and_transport_retry_then_success(self):
        good = ((), 17)
        with patch.object(guard, 'snapshot', side_effect=[
            ValueError('ms018_capacity_inventory_incomplete'), RetrySnapshot(), good, good
        ]):
            self.assertEqual(guard.stable_snapshot('fake', 1, 'a'*40, sleep=Mock()), good)

    def test_changed_pair_requires_two_new_matching_reads(self):
        a, b = ((), 17), ((), 16)
        with patch.object(guard, 'snapshot', side_effect=[a, b, b, b]) as read:
            self.assertEqual(guard.stable_snapshot('fake', 1, 'a'*40, sleep=Mock()), b)
        self.assertEqual(read.call_count, 4)

    def test_persistent_incomplete_inventory_fails_closed(self):
        with patch.object(guard, 'snapshot', side_effect=ValueError('ms018_capacity_inventory_incomplete')) as read:
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                guard.stable_snapshot('fake', 1, 'a'*40, attempts=3, sleep=Mock())
        self.assertEqual(read.call_count, 3)

    def test_capacity_and_unknown_run_are_not_retried(self):
        for code in ('ms018_capacity_no_worker_available', 'ms018_capacity_unclassified_run',
                     'ms018_capacity_job_identity', 'ms018_capacity_inventory_shape'):
            with self.subTest(code=code), patch.object(guard, 'snapshot', side_effect=ValueError(code)) as read:
                with self.assertRaisesRegex(ValueError, code):
                    guard.stable_snapshot('fake', 1, 'a'*40, sleep=Mock())
                self.assertEqual(read.call_count, 1)


if __name__ == '__main__':
    unittest.main()
