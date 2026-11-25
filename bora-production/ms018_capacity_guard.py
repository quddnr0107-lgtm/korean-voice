"""Conservative one-worker admission check for the exact MS-018 request.

Count every outstanding job, including other courses and nonrender work.
Unknown runs and incomplete/changing inventories fail closed. This is
not a global atomic scheduler; the existing production capacity gate
also remains in force.
"""
import json
import os
from pathlib import Path

from ms018_recovery import validate_request_scope, REQUEST_ID
from wait_for_tb_capacity import check_context, fetch_json

API = 'https://api.github.com/repos/quddnr0107-lgtm/korean-voice/actions'
STATES = ('in_progress','queued','waiting','pending','requested')
KNOWN_OLDER_RUNS = {
    43817761505: 'c06f638092494066aead91dd955fac350b3f92d5',
    44055370981: '3e1a92d320859b29b673e9450621d067e22e4d14',
    43556247204: '0b393b277938db5e75b34a7c7181b03ceafb4bfc',
}
NONRENDER = frozenset(('.github/workflows/bora-execution-ci.yml',
                      '.github/workflows/source-security.yml'))


def require(ok, code):
    if not ok:
        raise ValueError(code)


def items(url, field, token):
    out, seen, total = [], set(), None
    for page in range(1, 11):
        response = fetch_json(f'{url}&per_page=100&page={page}', token)
        count, rows = response.get('total_count'), response.get(field)
        require(type(count) is int and 0 <= count <= 1000
                and isinstance(rows, list) and len(rows) <= 100,
                'ms018_capacity_inventory_shape')
        require(total is None or total == count, 'ms018_capacity_inventory_changed')
        total = count
        for row in rows:
            require(isinstance(row, dict) and type(row.get('id')) is int
                    and row['id'] > 0 and row['id'] not in seen,
                    'ms018_capacity_duplicate_identity')
            seen.add(row['id'])
            out.append(row)
        require(len(out) <= total, 'ms018_capacity_inventory_count')
        if len(out) == total:
            return out
        require(len(rows) == 100, 'ms018_capacity_inventory_incomplete')
    raise ValueError('ms018_capacity_inventory_limit')


def snapshot(token, own_id, own_sha):
    runs = {}
    for status in STATES:
        for row in items(f'{API}/runs?status={status}', 'workflow_runs', token):
            run_id = row['id']
            require(row.get('status') == status and run_id not in runs,
                    'ms018_capacity_run_changed')
            runs[run_id] = row
    require(own_id in runs and runs[own_id].get('head_sha') == own_sha,
            'ms018_capacity_current_run')
    result = []
    count = 0
    for run_id, row in sorted(runs.items()):
        if run_id == own_id:
            continue
        old = KNOWN_OLDER_RUNS.get(run_id)
        require((old is not None and row.get('head_sha') == old)
                or (row.get('path') in NONRENDER and row.get('head_sha') == own_sha),
                'ms018_capacity_unclassified_run')
        jobs = items(f'{API}/runs/{run_id}/jobs?filter=latest', 'jobs', token)
        require(bool(jobs), 'ms018_capacity_jobs_missing')
        attempt = row.get('run_attempt')
        require(type(attempt) is int and attempt > 0, 'ms018_capacity_attempt')
        if old is not None:
            require(any(j.get('name') == 'plan' and j.get('status') == 'completed'
                        and j.get('conclusion') == 'success' for j in jobs)
                    and all(j.get('status') == 'completed' for j in jobs
                            if j.get('name') in ('preflight','plan','capacity')),
                    'ms018_capacity_other_plan_unresolved')
        job_state = []
        for job in jobs:
            status = job.get('status')
            require(job.get('run_id') == run_id and job.get('run_attempt') == attempt
                    and status in (*STATES, 'completed')
                    and (status == 'completed') == bool(job.get('conclusion')),
                    'ms018_capacity_job_identity')
            count += status != 'completed'
            job_state.append((job['id'], status, job.get('conclusion')))
        result.append((run_id, row.get('head_sha'), row['status'], tuple(sorted(job_state))))
    require(count < 40, 'ms018_capacity_no_worker_available')
    return tuple(result), count


def main():
    token = check_context(os.environ)
    request = json.loads(Path(__file__).with_name('single-pass-request.json').read_text())
    require(request.get('request_id') == REQUEST_ID, 'ms018_capacity_request')
    validate_request_scope(request)
    require(os.environ.get('GITHUB_ACTOR') == 'quddnr0107-lgtm',
            'ms018_capacity_owner')
    own_id, own_sha = int(os.environ['GITHUB_RUN_ID']), os.environ['GITHUB_SHA']
    first = snapshot(token, own_id, own_sha)
    require(snapshot(token, own_id, own_sha) == first, 'ms018_capacity_snapshot_changed')
    print(json.dumps(dict(status='ms018_one_worker_snapshot_passed',
                          other_outstanding_jobs=first[1], requested_workers=1)), flush=True)


if __name__ == '__main__':
    main()
