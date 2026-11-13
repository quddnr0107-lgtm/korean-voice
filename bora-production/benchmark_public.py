"""One bounded keyless option-3 timing experiment; never reads production inputs.

Only self-authored public fixture text is rendered. Reports contain counts,
fingerprints and timings, not audio or ASR transcripts. Production gates and
request.json are untouched. A successful experiment is not a course release.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ARMS = {'serial': list(range(8)), **{f'p{i}': [2*i, 2*i+1] for i in range(4)}}
# Original synthetic copy, not copied from any operating manuscript.
PARAGRAPHS = (
    '안녕하세요. 오늘도 차근차근 시작하겠습니다.',
    '창문을 열면 시원한 바람이 들어옵니다.',
    '책상 위에 연필과 공책이 놓여 있습니다.',
    '어려운 내용은 천천히 다시 살펴보겠습니다.',
    '친구와 함께 공원을 걸으며 이야기를 나눕니다.',
    '준비가 끝나면 다음 순서로 넘어가겠습니다.',
    '아침에는 가벼운 운동으로 몸을 풀어 주세요.',
    '오늘 배운 내용을 짧게 정리해 보겠습니다.',
)
SCHEMA = 'bora-public-parallel-timing/v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                    separators=(',', ':')).encode('ascii')).hexdigest()


def require(value, code):
    if not value:
        raise ValueError(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def seconds(end, start):
    return (datetime.fromisoformat(end.replace('Z', '+00:00')) -
            datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.pending')
    tmp.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2)+'\n')
    tmp.replace(path)


def hardware():
    cpu = Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else ''
    matches = re.findall(r'^model name\s*:\s*(.+)$', cpu, re.M)
    return dict(cpu_models=sorted(set(matches)), logical_cpus=os.cpu_count(),
                affinity_cpus=len(os.sched_getaffinity(0)), machine=platform.machine())


def run(args):
    require(args.arm in ARMS, 'unknown_arm')
    require(not any(os.environ.get(n) for n in (
        'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64',
        'GITHUB_TOKEN', 'GH_TOKEN', 'HF_TOKEN')), 'benchmark_must_be_keyless')
    request = json.loads((HERE/'benchmark-request.json').read_text())
    recipe = json.loads((HERE/'recipe.json').read_text())
    require(request['schema'] == SCHEMA and request['arms'] == list(ARMS)
            and request['fixture_sha256'] == digest(PARAGRAPHS)
            and request['max_workers'] == 5, 'benchmark_request_scope')
    require(recipe['selection'] == 3 and recipe['speaker'] == 'Sohee'
            and recipe['dtype'] == 'float32' and recipe['threads'] == 4
            and recipe['max_attempts'] == 2, 'unchanged_option3_required')
    from planning import build_plan
    from fp32_renderer import FP32Renderer, quiet_native
    plan = build_plan('TB-025', '\n\n'.join(PARAGRAPHS))
    require(len(plan['units']) == 8 and plan['review_unit_count'] == 0,
            'public_fixture_plan_must_have_eight_clean_units')
    report = dict(schema=SCHEMA, arm=args.arm, request_id=request['request_id'],
        request_sha256=digest(request), recipe_sha256=digest(recipe),
        fixture_sha256=digest(PARAGRAPHS), plan_sha256=digest(plan),
        source_kind='original_public_synthetic_fixture', production_keys_used=False,
        production_inputs_read=False, unit_indices=ARMS[args.arm], units=[],
        job_first_step=os.environ['BORA_BENCH_JOB_START'], started_at=now(),
        run_id=os.environ['GITHUB_RUN_ID'], head_sha=os.environ['GITHUB_SHA'],
        hardware=hardware(), complete=False)
    started = time.perf_counter()
    def timeout(_signum, _frame):
        raise TimeoutError('bounded_public_unit_timeout')
    signal.signal(signal.SIGALRM, timeout)
    try:
        loading = time.perf_counter()
        with quiet_native():
            renderer = FP32Renderer(os.environ['BORA_MODEL'], os.environ['BORA_ASR'],
                                    max_attempts=recipe['max_attempts'])
        report['model_load_s'] = time.perf_counter()-loading
        report['render_settings_sha256'] = renderer.settings_sha256
        report['synthesis_started_at'] = now()
        generation = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='bora-public-bench-',
                                         dir=os.environ['RUNNER_TEMP']) as td:
            for index in ARMS[args.arm]:
                one = time.perf_counter()
                signal.alarm(300)
                try:
                    with quiet_native():
                        receipt = renderer.render_unit(plan['units'][index], Path(td)/str(index))
                finally:
                    signal.alarm(0)
                candidates = receipt.get('candidates', [])
                row = dict(index=index, seconds=time.perf_counter()-one,
                    status=receipt['status'], unit_key=receipt['unit_key'],
                    source_sha256=receipt['source_sha256'], spoken_sha256=receipt['spoken_sha256'],
                    candidate_seeds=[c['seed'] for c in candidates],
                    selected_seed=receipt.get('seed'), audio_sha256=receipt.get('sha256'),
                    character_errors=receipt.get('asr', {}).get('character_errors'),
                    natural_eos=receipt.get('natural_eos'),
                    tts_seconds=sum(float(c.get('tts_seconds', 0)) for c in candidates))
                report['units'].append(row)
                print(json.dumps(dict(status='public_benchmark_unit', arm=args.arm,
                                      index=index, result=row['status'], seconds=row['seconds'])), flush=True)
        report['render_s'] = time.perf_counter()-generation
        report['complete'] = True
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        signal.alarm(0)
        report['ended_at'] = now()
        report['kernel_s'] = time.perf_counter()-started
        report['setup_s'] = seconds(report['started_at'], report['job_first_step'])
        report['job_first_step_to_render_end_s'] = seconds(report['ended_at'], report['job_first_step'])
        report['passed'] = sum(u['status'] == 'passed' for u in report['units'])
        report['blocked'] = len(report['units'])-report['passed']
        save(args.output, report)
        print(json.dumps(dict(status='public_benchmark_worker', **report)), flush=True)


def summarize(rows):
    require(len(rows) == 5 and Counter(r['arm'] for r in rows) == Counter(ARMS.keys()),
            'exactly_five_distinct_arms_required')
    require(all(r.get('complete') is True for r in rows), 'incomplete_benchmark')
    for key in ('request_sha256', 'recipe_sha256', 'fixture_sha256', 'plan_sha256',
                'render_settings_sha256', 'run_id', 'head_sha'):
        require(len({r[key] for r in rows}) == 1, 'mismatched_'+key)
    for r in rows:
        require(r['source_kind'] == 'original_public_synthetic_fixture'
                and r['production_keys_used'] is False and r['production_inputs_read'] is False,
                'non_public_benchmark_forbidden')
        require([u['index'] for u in r['units']] == ARMS[r['arm']], 'missing_or_duplicate_units')
        require(all(math.isfinite(float(u['seconds'])) and u['seconds'] > 0 for u in r['units']),
                'invalid_unit_timing')
    serial = next(r for r in rows if r['arm'] == 'serial')
    parallel = [r for r in rows if r['arm'] != 'serial']
    s = {u['index']: u for u in serial['units']}
    p = {u['index']: u for r in parallel for u in r['units']}
    require(set(s) == set(p) == set(range(8)), 'unpaired_fixture_coverage')
    for i in range(8):
        for key in ('unit_key', 'source_sha256', 'spoken_sha256'):
            require(s[i][key] == p[i][key], 'unpaired_input_identity')
    work_equal = all(s[i]['candidate_seeds'] == p[i]['candidate_seeds']
                     and s[i]['selected_seed'] == p[i]['selected_seed'] for i in s)
    outcomes_equal = all(s[i]['status'] == p[i]['status']
                        and s[i]['character_errors'] == p[i]['character_errors']
                        and s[i]['natural_eos'] == p[i]['natural_eos'] for i in s)
    all_pass = all(u['status'] == 'passed' for r in rows for u in r['units'])
    same_hardware = len({digest(r['hardware']) for r in rows}) == 1
    serial_s = serial['kernel_s']
    parallel_s = seconds(max(r['ended_at'] for r in parallel), min(r['started_at'] for r in parallel))
    serial_setup_s = serial['job_first_step_to_render_end_s']
    parallel_setup_s = seconds(max(r['ended_at'] for r in parallel), min(r['job_first_step'] for r in parallel))
    require(min(serial_s, parallel_s, serial_setup_s, parallel_setup_s) > 0, 'invalid_wall_clock')
    eligible = work_equal and outcomes_equal and all_pass and same_hardware
    return dict(schema=SCHEMA, status='measured_comparable_pilot' if eligible else 'timing_with_caveats',
        sample_units=8, sequential_workers=1, parallel_workers=4, repetitions=1,
        fixture_sha256=serial['fixture_sha256'], render_settings_sha256=serial['render_settings_sha256'],
        run_id=serial['run_id'], head_sha=serial['head_sha'],
        serial_kernel_s=serial_s, parallel_observed_kernel_envelope_s=parallel_s,
        serial_setup_inclusive_s=serial_setup_s, parallel_observed_setup_inclusive_s=parallel_setup_s,
        observed_kernel_time_reduction_pct=100*(1-parallel_s/serial_s),
        observed_setup_inclusive_time_reduction_pct=100*(1-parallel_setup_s/serial_setup_s),
        comparable_setup_inclusive_time_reduction_pct=100*(1-parallel_setup_s/serial_setup_s) if eligible else None,
        kernel_speed_ratio=serial_s/parallel_s,
        setup_inclusive_speed_ratio=serial_setup_s/parallel_setup_s,
        aggregate_worker_setup_inclusive_s=sum(r['job_first_step_to_render_end_s'] for r in rows),
        same_candidate_work=work_equal, same_quality_outcomes=outcomes_equal,
        all_units_passed=all_pass, matching_hardware=same_hardware,
        same_output_audio_hashes=all(s[i]['audio_sha256'] == p[i]['audio_sha256'] for i in s),
        serial_passed=serial['passed'], parallel_passed=sum(r['passed'] for r in parallel),
        limitations=['One short public fixture pilot, not all 303 lectures.',
            'Measured elapsed time includes repeated candidate work; no quality gate was relaxed.',
            'Kernel envelope includes real start skew; setup-inclusive excludes queue before first step.',
            'Final assembly, protected-environment approval, production checkpoint transport and deployment are excluded.',
            'Five independent standard runners are used; parallelism may consume more aggregate runner time.'])


def queue_metrics(rows):
    token = os.environ.get('GITHUB_TOKEN', '')
    require(token and re.fullmatch(r'[0-9]+', os.environ.get('GITHUB_RUN_ID', '')), 'actions_read_context')
    require(os.environ.get('GITHUB_REPOSITORY') == 'quddnr0107-lgtm/korean-voice', 'repository_scope')
    url = ('https://api.github.com/repos/quddnr0107-lgtm/korean-voice/actions/runs/'
           + os.environ['GITHUB_RUN_ID'] + '/jobs?per_page=100')
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer '+token,
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    jobs = {j['name']: j for j in data['jobs'] if j['name'] in {'public-bench-'+a for a in ARMS}}
    require(len(jobs) == 5 and all(j['conclusion'] == 'success' for j in jobs.values()), 'benchmark_jobs_incomplete')
    serial = jobs['public-bench-serial']; parallel = [jobs['public-bench-'+a] for a in ARMS if a != 'serial']
    ss = seconds(serial['completed_at'], serial['created_at'])
    ps = seconds(max(j['completed_at'] for j in parallel), min(j['created_at'] for j in parallel))
    require(min(ss, ps) > 0, 'invalid_queue_timing')
    return dict(source='GitHub Actions job created_at/completed_at', serial_queue_to_complete_s=ss,
        parallel_queue_to_complete_s=ps, observed_queue_inclusive_time_reduction_pct=100*(1-ps/ss),
        aggregate_runner_execution_s=sum(seconds(j['completed_at'],j['started_at']) for j in jobs.values()))


def collect(args):
    paths = sorted(Path(args.directory).glob('**/benchmark-*.json'))
    rows = [json.loads(p.read_text()) for p in paths]
    result = summarize(rows)
    try:
        result['queue_inclusive'] = queue_metrics(rows)
    except Exception as exc:
        result['queue_inclusive'] = {'status':'unavailable','error_type':type(exc).__name__}
    save(args.output, result)
    print(json.dumps(result, sort_keys=True), flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(
            '# Public option-3 parallel timing pilot\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command', required=True)
    p=sub.add_parser('run'); p.add_argument('--arm', choices=ARMS, required=True); p.add_argument('--output', required=True)
    p=sub.add_parser('collect'); p.add_argument('--directory', required=True); p.add_argument('--output', required=True)
    args=parser.parse_args()
    {'run':run,'collect':collect}[args.command](args)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status':'benchmark_failed','error_type':type(exc).__name__}),flush=True)
        raise SystemExit(1) from None
