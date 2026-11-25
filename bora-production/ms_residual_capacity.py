"""Use the existing conservative inventory reader for the separate residual batch."""
import json
import os
import time
from pathlib import Path
from wait_for_tb_capacity import check_context, RetrySnapshot
from ms018_capacity_guard import snapshot, KNOWN_OLDER_RUNS, require
from ms_residual_batch import REQUEST_ID, WORKERS, validate_request_scope

KNOWN = dict(KNOWN_OLDER_RUNS, **{})
KNOWN[45667020239] = '005bc65a9e4f9fc82c6038856d3f02792e31a701'


# GitHub can briefly omit the current run or return a short page while
# grouped workers start concurrently. Never admit on a partial read.
TRANSIENT_CODES = frozenset((
    'ms018_capacity_inventory_changed',
    'ms018_capacity_inventory_incomplete',
    'ms018_capacity_inventory_count',
    'ms018_capacity_duplicate_identity',
    'ms018_capacity_run_changed',
    'ms018_capacity_current_run',
    'residual_capacity_snapshot_changed',
))


def stable_snapshot(token, run, sha, *, attempts=6, sleep=time.sleep):
    for attempt in range(attempts):
        try:
            first = snapshot(token, run, sha, known_older_runs=KNOWN, workers=WORKERS)
            require(snapshot(token, run, sha, known_older_runs=KNOWN, workers=WORKERS) == first,
                    'residual_capacity_snapshot_changed')
            return first
        except (RetrySnapshot, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc) not in TRANSIENT_CODES:
                raise
            if attempt + 1 == attempts:
                raise
            # Stagger simultaneous workers without changing any capacity rule.
            worker = os.environ.get('BORA_BATCH_WORKER', '')
            offset = int(worker) % 17 if worker.isdecimal() else 0
            sleep(min(60, 5 * (2 ** attempt)) + offset)
    raise ValueError('residual_capacity_snapshot_exhausted')


def main():
    token = check_context(os.environ)
    request = json.loads(Path(__file__).with_name('single-pass-request.json').read_text())
    require(request.get('request_id') == REQUEST_ID, 'residual_capacity_request')
    validate_request_scope(request)
    require(os.environ.get('GITHUB_ACTOR') == 'quddnr0107-lgtm', 'residual_capacity_owner')
    run, sha = int(os.environ['GITHUB_RUN_ID']), os.environ['GITHUB_SHA']
    first = stable_snapshot(token, run, sha)
    print(json.dumps(dict(status='residual_parallel_capacity_passed',
                          other_outstanding_jobs=first[1], requested_workers=WORKERS)), flush=True)


if __name__ == '__main__':
    main()
