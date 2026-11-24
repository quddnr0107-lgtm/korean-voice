"""Find available synthesis slots for a concurrent Military Service batch."""

import os
from pathlib import Path
import re
import subprocess
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
    seen = set()
    total = None
    for page in range(1, 11):
        response = fetch_json(f"{url}&per_page=100&page={page}", token)
        items = response.get(field)
        count = response.get("total_count")
        require(type(count) is int and count >= 0 and isinstance(items, list)
                and len(items) <= 100, "invalid_actions_inventory")
        require(count <= 1000, "actions_inventory_page_limit")
        if total is None:
            total = count
        elif total != count:
            raise RetrySnapshot
        for item in items:
            require(isinstance(item, dict) and type(item.get("id")) is int
                    and item["id"] > 0, "invalid_actions_inventory_identity")
            if item["id"] in seen:
                raise RetrySnapshot
            seen.add(item["id"])
        rows.extend(items)
        if len(rows) > total:
            raise RetrySnapshot
        if len(rows) == total:
            return rows
        if len(items) < 100:
            raise RetrySnapshot
    raise CapacityError("actions_inventory_page_limit")



def _active_repository_runs(token):
    """Project identities for every active workflow, including unknown pools."""
    runs = {}
    for state in ACTIVE_STATES:
        rows = list_items(f"{API}/runs?status={state}", "workflow_runs", token)
        for row in rows:
            run_id = row.get("id")
            workflow_id = row.get("workflow_id")
            attempt = row.get("run_attempt")
            repository = row.get("repository")
            sha = row.get("head_sha")
            path = row.get("path")
            branch = row.get("head_branch")
            event = row.get("event")
            require(type(run_id) is int and run_id > 0
                    and type(workflow_id) is int and workflow_id > 0
                    and type(attempt) is int and attempt > 0
                    and isinstance(repository, dict)
                    and repository.get("full_name") == REPOSITORY
                    and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha)
                    and all(isinstance(x, str) and x for x in (path, branch, event))
                    and row.get("status") in ACTIVE_STATES,
                    "invalid_shared_run_identity")
            if row["status"] != state or run_id in runs:
                raise RetrySnapshot
            runs[run_id] = (run_id, workflow_id, path, sha, branch, event,
                            attempt, state)
    # Bound the number of individual job inventory requests. Never truncate.
    require(len(runs) <= 100, "shared_active_run_limit")
    return runs


def _repository_inventory_once(token):
    runs = _active_repository_runs(token)
    inventory = []
    seen_jobs = set()
    for run_id, identity in sorted(runs.items()):
        _, _, _, sha, branch, _, attempt, _ = identity
        jobs = list_items(f"{API}/runs/{run_id}/jobs?filter=latest", "jobs", token)
        projected = []
        for job in jobs:
            job_id = job.get("id")
            name = job.get("name")
            state = job.get("status")
            conclusion = job.get("conclusion")
            require(type(job_id) is int and job_id > 0
                    and isinstance(name, str) and bool(name)
                    and state in (*ACTIVE_STATES, "completed")
                    and (conclusion is None or isinstance(conclusion, str)),
                    "invalid_shared_job_identity")
            if (job.get("run_id") != run_id or job.get("run_attempt") != attempt
                    or job.get("head_sha") != sha or job.get("head_branch") != branch
                    or job_id in seen_jobs):
                raise RetrySnapshot
            if (state == "completed") != bool(conclusion):
                raise RetrySnapshot
            seen_jobs.add(job_id)
            projected.append((job_id, name, state, conclusion))
        # Preserve an empty job inventory as an unknown pending plan, not zero slots.
        inventory.append((identity, tuple(sorted(projected))))
    if _active_repository_runs(token) != runs:
        raise RetrySnapshot
    return tuple(inventory)


def shared_inventory(token):
    """Read-only conservative snapshot; not an admission or reservation API.

    All workflows and branches are retained. Two identical observations detect
    visible churn but are not atomic. The caller must still classify every
    synthesis path and obtain an atomic global reservation before allocating.
    """
    first = _repository_inventory_once(token)
    if _repository_inventory_once(token) != first:
        raise RetrySnapshot
    return first


def published_cap(run_id, jobs, token):
    """Read the earlier capacity job's numeric allocation, when present."""
    for job in jobs:
        if job["name"] != "capacity" or job.get("conclusion") != "success":
            continue
        if not any(step["name"] == "Count available course synthesis slots"
                   and step.get("conclusion") == "success" for step in job.get("steps", [])):
            continue
        require(type(job["id"]) is int and job["id"] > 0, "invalid_capacity_job")
        verified = os.environ.get("BORA_PRECEDING_CAPACITY", "")
        for value in filter(None, verified.split(",")):
            pin = re.fullmatch(r"([1-9][0-9]*):([1-9][0-9]*):([1-9][0-9]*)", value)
            require(pin is not None, "invalid_preceding_capacity_pin")
            prior_run, prior_job, prior_cap = map(int, pin.groups())
            if (prior_run, prior_job) == (run_id, job["id"]):
                require(prior_cap <= WORKER_LIMIT, "invalid_preceding_worker_count")
                return prior_cap
        try:
            logs = subprocess.run(
                ["gh", "api", f"repos/{REPOSITORY}/actions/jobs/{job['id']}/logs"],
                env={**os.environ, "GH_TOKEN": token}, capture_output=True,
                text=True, timeout=45, check=True,
            ).stdout
        except (OSError, UnicodeError, subprocess.SubprocessError):
            raise RetrySnapshot from None
        slots = re.findall(r"Reserved synthesis jobs: [0-9]+; (?:Military Service|Defense Directive) slots: ([0-9]+)$",
                           logs, flags=re.MULTILINE)
        require(len(slots) == 1 and 1 <= int(slots[0]) <= WORKER_LIMIT,
                "invalid_published_worker_count")
        return int(slots[0])
    return WORKER_LIMIT


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
        open_jobs = [job for job in renders if job["status"] != "completed"]
        if any(job["status"] == "queued" for job in open_jobs):
            # Matrix jobs above max-parallel wait until an active worker ends.
            # The earlier capacity job bounds how many can run concurrently.
            reserved += min(len(open_jobs), published_cap(run_id, jobs, token))
        else:
            reserved += len(open_jobs)
    return reserved


def main():
    token = check_context(os.environ)
    current_run = int(os.environ["GITHUB_RUN_ID"])
    requested = int(os.environ["BORA_MAX_WORKERS"])
    label = {"MS": "Military Service", "DR": "Defense Directive"}.get(os.environ.get("BORA_COURSE"))
    require(label is not None, "unexpected_capacity_course")
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
            print(f"Reserved synthesis jobs: {reserved}; {label} slots: {slots}")
            return
        time.sleep(POLL_SECONDS)
    raise CapacityError("synthesis_capacity_wait_expired")


if __name__ == "__main__":
    main()
