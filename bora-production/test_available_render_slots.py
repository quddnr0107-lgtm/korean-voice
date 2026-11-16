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

    def test_matrix_backlog_is_bounded_by_the_verified_worker_cap(self):
        jobs = [
            {'name': 'render_grouped (MS-005, 0, 1)', 'status': 'in_progress'},
            *[{'name': f'render_grouped (MS-006, 0, {index})', 'status': 'queued'}
              for index in range(2, 5)],
        ]
        with patch.object(capacity, 'list_items', side_effect=[
            *([[{'id': 2}]] * len(capacity.ACTIVE_STATES)), jobs,
        ]), patch.object(capacity, 'published_cap', return_value=2) as cap:
            self.assertEqual(capacity.reserved_slots(1, 'mock-token'), 2)
            cap.assert_called_once_with(jobs, 'mock-token')

    def test_reads_the_recorded_capacity_from_the_actions_job_log(self):
        jobs = [{'name': 'capacity', 'id': 4, 'conclusion': 'success', 'steps': [
            {'name': 'Count available course synthesis slots', 'conclusion': 'success'},
        ]}]
        with patch.object(capacity.subprocess, 'run') as cli:
            cli.return_value.stdout = '2026-11-16T03:38:34Z Reserved synthesis jobs: 7; Military Service slots: 33\n'
            self.assertEqual(capacity.published_cap(jobs, 'mock-token'), 33)
            self.assertTrue(cli.call_args.args[0][-1].endswith('/actions/jobs/4/logs'))
