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
            cap.assert_called_once_with(2, jobs, 'mock-token')

    def test_reads_the_recorded_capacity_from_the_actions_job_log(self):
        jobs = [{'name': 'capacity', 'id': 4, 'conclusion': 'success', 'steps': [
            {'name': 'Count available course synthesis slots', 'conclusion': 'success'},
        ]}]
        with patch.object(capacity.subprocess, 'run') as cli:
            cli.return_value.stdout = '2026-11-16T03:38:34Z Reserved synthesis jobs: 7; Military Service slots: 33\n'
            self.assertEqual(capacity.published_cap(2, jobs, 'mock-token'), 33)
            self.assertTrue(cli.call_args.args[0][-1].endswith('/actions/jobs/4/logs'))

    def test_previous_capacity_pin_requires_the_matching_run_and_job(self):
        jobs = [{'name': 'capacity', 'id': 4, 'conclusion': 'success', 'steps': [
            {'name': 'Count available course synthesis slots', 'conclusion': 'success'},
        ]}]
        with patch.dict(capacity.os.environ, BORA_PRECEDING_CAPACITY='2:4:33,3:5:4'), \
                patch.object(capacity.subprocess, 'run') as cli:
            self.assertEqual(capacity.published_cap(2, jobs, 'mock-token'), 33)
            cli.assert_not_called()
            cli.return_value.stdout = 'Reserved synthesis jobs: 8; Military Service slots: 32\n'
            self.assertEqual(capacity.published_cap(3, jobs, 'mock-token'), 32)


class InventoryPaginationTests(unittest.TestCase):
    def read(self, pages):
        with patch.object(capacity, 'fetch_json', side_effect=pages):
            return capacity.list_items('mock?filter=latest', 'jobs', 'mock-token')

    @staticmethod
    def page(count, ids):
        return {'total_count': count, 'jobs': [{'id': i} for i in ids]}

    def test_short_page_is_not_complete_inventory(self):
        with self.assertRaises(capacity.RetrySnapshot):
            self.read([self.page(40, range(1, 3))])

    def test_complete_and_empty_inventory(self):
        self.assertEqual(len(self.read([self.page(103, range(1, 101)),
                                        self.page(103, range(101, 104))])), 103)
        self.assertEqual(self.read([self.page(0, [])]), [])

    def test_count_changes(self):
        with self.assertRaises(capacity.RetrySnapshot):
            self.read([self.page(101, range(1, 101)), self.page(102, [101, 102])])

    def test_duplicate_across_pages(self):
        with self.assertRaises(capacity.RetrySnapshot):
            self.read([self.page(101, range(1, 101)), self.page(101, [100])])

    def test_more_rows_than_count(self):
        with self.assertRaises(capacity.RetrySnapshot):
            self.read([self.page(1, [1, 2])])

    def test_missing_total_or_invalid_identity(self):
        with self.assertRaises(capacity.CapacityError):
            self.read([{'jobs': []}])
        with self.assertRaises(capacity.CapacityError):
            self.read([self.page(1, [True])])

    def test_page_limit(self):
        with self.assertRaises(capacity.CapacityError):
            self.read([self.page(1001, range(1, 101))])
        self.assertEqual(len(self.read([
            self.page(1000, range(i * 100 + 1, i * 100 + 101))
            for i in range(10)
        ])), 1000)


class SharedInventoryTests(unittest.TestCase):
    @staticmethod
    def run_row(run_id, workflow_id, path, state="waiting", attempt=1):
        return dict(id=run_id, workflow_id=workflow_id, path=path,
                    head_sha="a" * 40, head_branch="integration", event="push",
                    run_attempt=attempt, status=state,
                    repository={"full_name": capacity.REPOSITORY})

    @staticmethod
    def job_row(run_id, job_id, name, state="waiting", attempt=1):
        return dict(id=job_id, run_id=run_id, run_attempt=attempt,
                    head_sha="a" * 40, head_branch="integration", name=name,
                    status=state, conclusion="success" if state == "completed" else None)

    def inventory(self, runs, jobs):
        def read(url, field, token):
            if field == "workflow_runs":
                self.assertNotIn("/workflows/", url)
                state = url.rsplit("=", 1)[-1]
                return [r for r in runs if r["status"] == state]
            run_id = int(url.split("/runs/")[1].split("/")[0])
            return jobs.get(run_id, [])
        return read

    def test_keeps_all_workflows_and_empty_pending_plan(self):
        runs = [self.run_row(1, 11, ".github/workflows/bora-fp32-single-pass.yml"),
                self.run_row(2, 22, ".github/workflows/bora-fp32-compiled-pilot.yml"),
                self.run_row(3, 33, ".github/workflows/unknown.yml")]
        jobs = {1: [self.job_row(1, 101, "plan")],
                2: [self.job_row(2, 201, "render (0)", "queued")]}
        with patch.object(capacity, "list_items", side_effect=self.inventory(runs, jobs)):
            result = capacity.shared_inventory("mock-token")
        self.assertEqual([r[0][0] for r in result], [1, 2, 3])
        self.assertEqual(result[2][1], ())
        self.assertEqual(result[1][1][0][2], "queued")

    def test_empty_repository(self):
        with patch.object(capacity, "list_items", side_effect=self.inventory([], {})):
            self.assertEqual(capacity.shared_inventory("mock-token"), ())

    def test_cross_status_duplicate_or_stale_status_is_rejected(self):
        run = self.run_row(1, 11, "workflow", "waiting")
        with patch.object(capacity, "list_items", return_value=[run]):
            with self.assertRaises(capacity.RetrySnapshot):
                capacity._active_repository_runs("mock-token")

    def test_job_wrong_attempt_or_run_is_rejected(self):
        run = self.run_row(1, 11, "workflow", attempt=2)
        for job in (self.job_row(1, 101, "render (0)"),
                    self.job_row(2, 101, "render (0)", attempt=2)):
            with self.subTest(job=job):
                with patch.object(capacity, "list_items", side_effect=self.inventory([run], {1: [job]})):
                    with self.assertRaises(capacity.RetrySnapshot):
                        capacity.shared_inventory("mock-token")

    def test_duplicate_job_id_across_runs_is_rejected(self):
        runs = [self.run_row(1, 11, "workflow"), self.run_row(2, 22, "other")]
        jobs = {1: [self.job_row(1, 101, "plan")], 2: [self.job_row(2, 101, "plan")]}
        with patch.object(capacity, "list_items", side_effect=self.inventory(runs, jobs)):
            with self.assertRaises(capacity.RetrySnapshot):
                capacity.shared_inventory("mock-token")

    def test_unknown_job_state_fails_closed(self):
        run = self.run_row(1, 11, "workflow")
        job = self.job_row(1, 101, "render (0)", "unknown")
        with patch.object(capacity, "list_items", side_effect=self.inventory([run], {1: [job]})):
            with self.assertRaises(capacity.CapacityError):
                capacity.shared_inventory("mock-token")

    def test_changed_job_inventory_with_same_run_count_is_rejected(self):
        run = self.run_row(1, 11, "workflow")
        observations = iter([[self.job_row(1, 101, "render (0)")],
                             [self.job_row(1, 102, "render (0)")]])
        def read(url, field, token):
            if field == "jobs":
                return next(observations)
            return [run] if url.endswith("status=waiting") else []
        with patch.object(capacity, "list_items", side_effect=read):
            with self.assertRaises(capacity.RetrySnapshot):
                capacity.shared_inventory("mock-token")

    def test_run_changes_during_job_read_are_rejected(self):
        run = self.run_row(1, 11, "workflow")
        replacement = self.run_row(2, 11, "workflow")
        with patch.object(capacity, "_active_repository_runs",
                          side_effect=[{1: tuple((1, 11, "workflow", "a"*40, "integration", "push", 1, "waiting"))},
                                       {2: tuple((2, 11, "workflow", "a"*40, "integration", "push", 1, "waiting"))}]), \
                patch.object(capacity, "list_items", return_value=[]):
            with self.assertRaises(capacity.RetrySnapshot):
                capacity.shared_inventory("mock-token")

