"""Find available synthesis slots for a concurrent Military Service batch."""

import os
from pathlib import Path
import re
import time

from wait_for_tb_capacity import (
    BRANCH, MAX_WAIT_SECONDS, POLL_SECONDS, REPOSITORY, TB_WORKFLOW_ID,
    CapacityError, RetrySnapshot, check_context, fetch_json, require,
)


API = f"https://api.github.com/repos/{REPOSITORY}/actions"
ACTIVE_STATES = ("in_progress", "queued", "waiting", "pending", "requested")
RENDER = re.compile(r"render(?:_grouped)? \(")
WORKER_LIMIT = 40


def list_items(url, field, token):
    rows = []
    for page in range(1, 11):
        response = fetch_json(f"{url}&per_page=100&page={page}", token)
        items = response.get(field)
        require(isinstance(items, list), "invalid_actions_inventory")
        rows.extend(items)
        if len(items) < 100:
            return rows
    raise CapacityError("actions_inventory_page_limit")


def reserved_slots(current_run, token):
    other_runs = set()
    for status in ACTIVE_STATES:
        runs = list_items(
            f"{API}/workflows/{TB_WORKFLOW_ID}/runs?branch={BRANCH}&status={status}",
            "workflow_runs", token,
        )
        for run in runs:
            run_id = run.get("id")
            require(type(run_id) is int and run_id > 0, "invalid_active_run")
            if run_id != current_run:
                other_runs.add(run_id)

    reserved = 0
    for run_id in sorted(other_runs):
        jobs = list_items(f"{API}/runs/{run_id}/jobs?filter=latest", "jobs", token)
        renders = [job for job in jobs if RENDER.match(job["name"])]
        if not renders and not any(
            job["name"] == "plan" and job["status"] == "completed"
            and job["conclusion"] != "success" for job in jobs
        ):
            # An active run can still be planning its synthesis matrix.
            raise RetrySnapshot
        reserved += sum(job["status"] != "completed" for job in renders)
    return reserved


def main():
    token = check_context(os.environ)
    current_run = int(os.environ["GITHUB_RUN_ID"])
    requested = int(os.environ["BORA_MAX_WORKERS"])
    require(1 <= requested <= WORKER_LIMIT, "invalid_requested_worker_count")
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            reserved = reserved_slots(current_run, token)
        except RetrySnapshot:
            time.sleep(POLL_SECONDS)
            continue
        slots = min(requested, WORKER_LIMIT - reserved)
        if slots > 0:
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                print(f"parallelism={slots}", file=output)
            print(f"Reserved synthesis jobs: {reserved}; Military Service slots: {slots}")
            return
        time.sleep(POLL_SECONDS)
    raise CapacityError("synthesis_capacity_wait_expired")


if __name__ == "__main__":
    main()
