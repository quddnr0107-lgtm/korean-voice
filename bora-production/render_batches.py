"""Schedule a bounded group of immutable shards per protected worker."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import time


MAX_SHARDS_PER_WORKER = 6
WALL_SECONDS = 270 * 60
# Reserve enough time to avoid starting a network stage or renderer when it can
# no longer finish within the worker's soft end. The separate 360m workflow job
# limit leaves time for the last keyless checkpoint upload.
MIN_SECONDS_TO_START = 25 * 60
LESSON_ID = re.compile(r'(?:TB|DR|RL|MS)-[0-9]{3}')


def group_render_work(shards, costs, worker_cap):
    """Assign longest work first, without exposing estimated character costs."""
    groups = [[] for _ in range(min(worker_cap, len(shards)))]
    if not groups or len(shards) > len(groups) * MAX_SHARDS_PER_WORKER:
        raise ValueError('render_workers_require_smaller_lesson_batch')
    order = sorted(shards, key=lambda row: (-costs[row['lesson_id'], row['shard']],
                                             row['lesson_id'], row['shard']))
    loads = [0] * len(groups)
    for row in order:
        worker = min((i for i, group in enumerate(groups) if len(group) < MAX_SHARDS_PER_WORKER),
                     key=lambda i: (loads[i], len(groups[i]), i))
        groups[worker].append({'lesson_id': row['lesson_id'], 'shard': row['shard']})
        loads[worker] += costs[row['lesson_id'], row['shard']]
    return [{'worker': i + 1, 'items': group} for i, group in enumerate(groups)]


def assignment(items, worker, slot, course, deadline, now):
    """Return a strictly validated slot and why it could not start, if any."""
    if (type(worker) is not int or not 1 <= worker <= 40
            or type(slot) is not int or not 1 <= slot <= MAX_SHARDS_PER_WORKER
            or course not in ('TB', 'DR', 'RL', 'MS')
            or type(items) is not list or not 1 <= len(items) <= MAX_SHARDS_PER_WORKER):
        raise ValueError('render_worker_assignment')
    seen = set()
    for row in items:
        if (type(row) is not dict or set(row) != {'lesson_id', 'shard'}
                or not isinstance(row['lesson_id'], str) or not LESSON_ID.fullmatch(row['lesson_id'])
                or row['lesson_id'].split('-')[0] != course
                or type(row['shard']) is not int or not 0 <= row['shard'] <= 999
                or (row['lesson_id'], row['shard']) in seen):
            raise ValueError('render_worker_item')
        seen.add((row['lesson_id'], row['shard']))
    if slot > len(items):
        return None
    if deadline - now > WALL_SECONDS:
        raise ValueError('render_worker_deadline')
    return {**items[slot - 1], 'deferred': deadline - now < MIN_SECONDS_TO_START}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('start', 'slot'))
    parser.add_argument('--slot', type=int)
    args = parser.parse_args()
    output = Path(os.environ['GITHUB_OUTPUT'])
    now = time.monotonic_ns() // 10**9
    if args.command == 'start':
        with output.open('a', encoding='utf-8') as out:
            print(f'deadline={now + WALL_SECONDS}', file=out)
        return

    row = assignment(json.loads(os.environ['BORA_BATCH_ITEMS']), int(os.environ['BORA_BATCH_WORKER']),
                     args.slot, os.environ['BORA_COURSE'], int(os.environ['BORA_BATCH_DEADLINE']), now)
    if row is None:
        return
    with output.open('a', encoding='utf-8') as out:
        print(f'slot={args.slot}', file=out)
        print('lesson=' + row['lesson_id'], file=out)
        print(f'shard={row["shard"]}', file=out)
        print(f'deferred={str(row["deferred"]).lower()}', file=out)


if __name__ == '__main__':
    main()
