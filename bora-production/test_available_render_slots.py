import unittest
from unittest.mock import patch

import available_render_slots as capacity


class AvailableRenderSlotsTests(unittest.TestCase):
    def test_reserves_waiting_workers_and_counts_each_run_once(self):
        def inventory(url, field, token):
            if field == 'workflow_runs':
                return [{'id': 1}, {'id': 2}]
            self.assertIn('/runs/2/jobs', url)
            return [
                {'name': 'render_grouped (MS-005, 0, 1)', 'status': 'in_progress'},
                {'name': 'render (0, MS-006, 1, 2)', 'status': 'waiting'},
                {'name': 'render_grouped (MS-007, 0, 3)', 'status': 'completed'},
                {'name': 'assemble (MS-005)', 'status': 'in_progress'},
            ]

        with patch.object(capacity, 'list_items', side_effect=inventory):
            self.assertEqual(capacity.reserved_slots(1, 'mock-token'), 2)

    def test_waits_for_another_active_runs_matrix(self):
        with patch.object(
            capacity, 'list_items', side_effect=[
                *([[{'id': 2}]] * len(capacity.ACTIVE_STATES)),
                [{'name': 'plan', 'status': 'in_progress'}],
            ],
        ):
            with self.assertRaises(capacity.RetrySnapshot):
                capacity.reserved_slots(1, 'mock-token')
