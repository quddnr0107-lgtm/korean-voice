"""Reserve at most 15 concurrent RL workers while the pinned TB run finishes."""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


REPOSITORY = "quddnr0107-lgtm/korean-voice"
BRANCH = "integrated-lectures-premium-20261028"
WORKFLOW = ".github/workflows/bora-fp32-single-pass.yml"
TB_RUN_ID = 43565543458
TB_WORKFLOW_ID = 418070199
TB_HEAD = "836500cd9250f38698235c729ae5553f41e7c262"
TB_ATTEMPT = 1
TB_RENDER_JOBS = 95
TB_UNIT_COUNT = 3665
TB_LESSONS = {f"TB-{n:03d}" for n in range(1, 26)}
TB_REMAINING_LIMIT = 25
MAX_JOBS = TB_RENDER_JOBS + len(TB_LESSONS) + 3
MAX_WAIT_SECONDS = 4 * 3600 + 45 * 60
POLL_SECONDS = 60
MAX_BODY = 4 * 1024 * 1024
JOB_STATES = {"queued", "requested", "waiting", "pending", "in_progress", "completed"}
SHARD_NAME = re.compile(r"render \(([0-9]+), (TB-[0-9]{3}), ([0-9]+), ([0-9]+)\)")
ASSEMBLY_NAME = re.compile(r"assemble \((TB-[0-9]{3})\)")
API_RUN = f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{TB_RUN_ID}"


class CapacityError(Exception):
    """A fixed validation code safe to record in public GitHub logs."""


class RetrySnapshot(Exception):
    """GitHub is unavailable or the run is changing between reads."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require(condition, code):
    if not condition:
        raise CapacityError(code)


def check_context(env):
    require(
        env.get("GITHUB_REPOSITORY") == REPOSITORY
        and env.get("GITHUB_REF") == "refs/heads/" + BRANCH
        and env.get("GITHUB_EVENT_NAME") == "push"
        and env.get("GITHUB_WORKFLOW_REF") == f"{REPOSITORY}/{WORKFLOW}@refs/heads/{BRANCH}",
        "unexpected_capacity_workflow",
    )
    require(
        not any(env.get(key) for key in (
            "ADMIN_LAW_PREMIUM_SOURCE_KEY_B64", "ADMIN_LAW_PREMIUM_MEDIA_KEY_B64",
        )),
        "capacity_gate_received_protected_key",
    )
    token = env.get("GITHUB_TOKEN")
    require(isinstance(token, str) and bool(token.strip()) and token == token.strip(),
            "missing_actions_read_token")
    return token


def fetch_json(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as handle:
            raw = handle.read(MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 500, 502, 503, 504) or (
            exc.code == 403 and (
                exc.headers.get("Retry-After") or exc.headers.get("X-RateLimit-Remaining") == "0"
            )
        ):
            raise RetrySnapshot from None
        raise CapacityError("actions_api_rejected_request") from None
    except (urllib.error.URLError, OSError):
        raise RetrySnapshot from None
    require(len(raw) <= MAX_BODY, "actions_api_response_limit")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RetrySnapshot from None
    require(isinstance(value, dict), "actions_api_response_shape")
    return value


def verify_run(run):
    repository = run.get("repository")
    require(
        isinstance(repository, dict)
        and repository.get("full_name") == REPOSITORY
        and run.get("id") == TB_RUN_ID
        and run.get("workflow_id") == TB_WORKFLOW_ID
        and run.get("path") == WORKFLOW
        and run.get("head_branch") == BRANCH
        and run.get("head_sha") == TB_HEAD
        and run.get("event") == "push"
        and run.get("run_attempt") == TB_ATTEMPT,
        "tb_predecessor_identity_mismatch",
    )
    require(run.get("status") in JOB_STATES, "unknown_tb_run_status")


def load_jobs(token):
    jobs = []
    max_pages = (MAX_JOBS + 99) // 100
    total = None
    for page in range(1, max_pages + 1):
        result = fetch_json(f"{API_RUN}/jobs?filter=latest&per_page=100&page={page}", token)
        count = result.get("total_count")
        page_jobs = result.get("jobs")
        require(type(count) is int and 0 <= count <= MAX_JOBS
                and isinstance(page_jobs, list) and len(page_jobs) <= 100,
                "tb_job_inventory_shape")
        if total is None:
            total = count
        elif total != count:
            raise RetrySnapshot
        jobs.extend(page_jobs)
        if len(jobs) >= count or len(page_jobs) < 100:
            break
    if len(jobs) != total:
        raise RetrySnapshot
    return jobs


def shard_ranges_complete(ranges):
    if len(ranges) != TB_RENDER_JOBS:
        return False
    by_lesson = {lesson: [] for lesson in TB_LESSONS}
    for (lesson, shard), (start, end) in ranges.items():
        by_lesson[lesson].append((shard, start, end))
    units = 0
    for rows in by_lesson.values():
        if not rows:
            return False
        next_start = 0
        for n, (shard, start, end) in enumerate(sorted(rows)):
            if shard != n or start != next_start or start >= end:
                return False
            next_start = end
        units += next_start
    return units == TB_UNIT_COUNT


def capacity(run_status, jobs):
    """Open only for a terminal run or a complete, verified matrix with capacity."""
    job_ids = set()
    render_ranges = {}
    assemblies = set()
    singleton_jobs = set()
    open_render = 0
    active_render = False
    active_assembly = False
    any_unfinished = False
    for job in jobs:
        require(isinstance(job, dict), "tb_job_shape")
        job_id, name, state = job.get("id"), job.get("name"), job.get("status")
        require(
            type(job_id) is int and job_id > 0 and isinstance(name, str)
            and state in JOB_STATES
            and job.get("run_id") == TB_RUN_ID and job.get("run_attempt") == TB_ATTEMPT,
            "tb_job_identity_mismatch",
        )
        if job_id in job_ids:
            raise RetrySnapshot
        job_ids.add(job_id)
        any_unfinished |= state != "completed"
        shard_name = SHARD_NAME.fullmatch(name)
        assembly_name = ASSEMBLY_NAME.fullmatch(name)
        if shard_name:
            end, lesson, shard, start = shard_name.groups()
            key = (lesson, int(shard))
            require(lesson in TB_LESSONS and 0 <= int(start) < int(end) <= TB_UNIT_COUNT,
                    "tb_render_shard_range")
            if key in render_ranges:
                raise RetrySnapshot
            render_ranges[key] = (int(start), int(end))
            open_render += state != "completed"
            active_render |= state == "in_progress"
        elif assembly_name:
            lesson = assembly_name[1]
            require(lesson in TB_LESSONS, "tb_assembly_lesson_scope")
            if lesson in assemblies:
                raise RetrySnapshot
            assemblies.add(lesson)
            active_assembly |= state == "in_progress"
        else:
            require(name in {"preflight", "plan", "publish"}, "unexpected_tb_job_name")
            if name in singleton_jobs:
                raise RetrySnapshot
            singleton_jobs.add(name)

    if run_status == "completed":
        if any_unfinished or not jobs:
            raise RetrySnapshot
        return True, open_render, len(render_ranges)
    # Phase order is pinned by TB_HEAD. Treat contradictory API snapshots as
    # temporary, never as capacity, even when a job list is changing mid-read.
    if active_render and active_assembly:
        raise RetrySnapshot
    complete_ranges = shard_ranges_complete(render_ranges)
    if len(render_ranges) == TB_RENDER_JOBS:
        require(complete_ranges, "tb_render_matrix_scope")
    return complete_ranges and open_render <= TB_REMAINING_LIMIT, open_render, len(render_ranges)


def snapshot(token):
    initial = fetch_json(API_RUN, token)
    verify_run(initial)
    jobs = load_jobs(token)
    final = fetch_json(API_RUN, token)
    verify_run(final)
    if final["status"] != initial["status"]:
        raise RetrySnapshot
    return capacity(final["status"], jobs)


def wait_for_capacity(token, *, wait_seconds=MAX_WAIT_SECONDS, poll_seconds=POLL_SECONDS):
    deadline = time.monotonic() + wait_seconds
    previous = None
    while True:
        try:
            ready, render_remaining, render_known = snapshot(token)
            status = {
                "status": "tb_capacity_ready" if ready else "waiting_for_tb_capacity",
                "tb_run": TB_RUN_ID,
                "tb_render_known": render_known,
                "tb_render_remaining": render_remaining,
            }
        except RetrySnapshot:
            ready = False
            status = {"status": "retry_tb_capacity_snapshot", "tb_run": TB_RUN_ID}
        if status != previous:
            print(json.dumps(status), flush=True)
            previous = status
        if ready:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CapacityError("tb_capacity_wait_timeout")
        time.sleep(min(poll_seconds, remaining))


def main():
    try:
        wait_for_capacity(check_context(os.environ))
    except CapacityError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
