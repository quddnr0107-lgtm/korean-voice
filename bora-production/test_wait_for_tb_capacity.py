import unittest
from unittest.mock import patch

import wait_for_tb_capacity as gate


def tb_job(number, name, status):
    return {
        "id": number,
        "name": name,
        "status": status,
        "run_id": gate.TB_RUN_ID,
        "run_attempt": gate.TB_ATTEMPT,
    }


def tb_jobs(remaining):
    jobs = [
        tb_job(1, "preflight", "completed"),
        tb_job(2, "plan", "completed"),
    ]
    for lesson_no in range(1, 26):
        start = 0
        # First 20 lessons have 4 shards each and the last 5 have 3.
        for shard in range(4 if lesson_no <= 20 else 3):
            end = start + (39 if len(jobs) - 2 < 55 else 38)
            name = f"render ({end}, TB-{lesson_no:03d}, {shard}, {start})"
            running = len(jobs) - 2 >= (gate.TB_RENDER_JOBS - remaining)
            jobs.append(tb_job(len(jobs) + 1, name, "waiting" if running else "completed"))
            start = end
    return jobs


def tb_run():
    return {
        "id": gate.TB_RUN_ID,
        "workflow_id": gate.TB_WORKFLOW_ID,
        "path": gate.WORKFLOW,
        "head_branch": gate.BRANCH,
        "head_sha": gate.TB_HEAD,
        "event": "push",
        "run_attempt": gate.TB_ATTEMPT,
        "repository": {"full_name": gate.REPOSITORY},
        "status": "in_progress",
    }


class CapacityTests(unittest.TestCase):
    def test_all_active_waiting_and_queued_render_jobs_count_against_limit(self):
        jobs = tb_jobs(26)
        jobs[-2]["status"] = "queued"
        jobs[-1]["status"] = "in_progress"
        self.assertEqual(gate.capacity("in_progress", jobs), (False, 26, 95))
        jobs[-1]["status"] = "completed"
        self.assertEqual(gate.capacity("in_progress", jobs), (True, 25, 95))

    def test_incomplete_inventory_cannot_open_before_95_shards_are_known(self):
        self.assertEqual(gate.capacity("in_progress", tb_jobs(0)[:-1]), (False, 0, 94))
        with self.assertRaises(gate.CapacityError):
            jobs = tb_jobs(0)
            jobs[5]["name"] = jobs[5]["name"][:-2] + "8)"
            gate.capacity("in_progress", jobs)

    def test_terminal_run_may_finish_with_reduced_but_not_unfinished_inventory(self):
        self.assertEqual(gate.capacity("completed", tb_jobs(0)[:2]), (True, 0, 0))
        with self.assertRaises(gate.RetrySnapshot):
            gate.capacity("completed", tb_jobs(1))

    def test_never_accept_unexpected_head_attempt_or_repository(self):
        gate.verify_run(tb_run())
        for field, value in (("head_sha", "bad"), ("run_attempt", 2), ("repository", {})):
            with self.subTest(field=field), self.assertRaises(gate.CapacityError):
                gate.verify_run(dict(tb_run(), **{field: value}))

    def test_paginates_and_rejects_changing_inventory_count(self):
        jobs = tb_jobs(0) + [
            tb_job(98 + i, f"assemble (TB-{i+1:03d})", "waiting") for i in range(25)
        ]
        with patch.object(gate, "fetch_json", side_effect=[
            {"total_count": 122, "jobs": jobs[:100]},
            {"total_count": 122, "jobs": jobs[100:]},
        ]) as fetch:
            self.assertEqual(gate.load_jobs("unused"), jobs)
            self.assertTrue(fetch.call_args_list[1].args[0].endswith("page=2"))
        with patch.object(gate, "fetch_json", side_effect=[
            {"total_count": 122, "jobs": jobs[:100]},
            {"total_count": 123, "jobs": jobs[100:]},
        ]), self.assertRaises(gate.RetrySnapshot):
            gate.load_jobs("unused")


if __name__ == "__main__":
    unittest.main()
