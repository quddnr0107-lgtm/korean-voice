"""Use the existing conservative inventory reader for the separate residual batch."""
import json
import os
from pathlib import Path
from wait_for_tb_capacity import check_context
from ms018_capacity_guard import snapshot, KNOWN_OLDER_RUNS, require
from ms_residual_batch import REQUEST_ID, WORKERS, validate_request_scope

KNOWN = dict(KNOWN_OLDER_RUNS, **{})
KNOWN[45667020239] = '005bc65a9e4f9fc82c6038856d3f02792e31a701'


def main():
    token = check_context(os.environ)
    request = json.loads(Path(__file__).with_name('single-pass-request.json').read_text())
    require(request.get('request_id') == REQUEST_ID, 'residual_capacity_request')
    validate_request_scope(request)
    require(os.environ.get('GITHUB_ACTOR') == 'quddnr0107-lgtm', 'residual_capacity_owner')
    run, sha = int(os.environ['GITHUB_RUN_ID']), os.environ['GITHUB_SHA']
    first = snapshot(token, run, sha, known_older_runs=KNOWN, workers=WORKERS)
    require(snapshot(token, run, sha, known_older_runs=KNOWN, workers=WORKERS) == first,
            'residual_capacity_snapshot_changed')
    print(json.dumps(dict(status='residual_parallel_capacity_passed',
                          other_outstanding_jobs=first[1], requested_workers=WORKERS)), flush=True)


if __name__ == '__main__':
    main()
