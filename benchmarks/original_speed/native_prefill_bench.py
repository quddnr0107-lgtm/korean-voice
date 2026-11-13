"""Public CLI screening of fixed Kim Bora C prefill options, never production approval.

Source, model, speaker, instruction, seeds and sampling stay fixed. This preserves
the existing native baseline semantics; it does NOT claim Python FP32 equivalence.
Untimed in-process census proofs are separate from uninstrumented timing trials.
The source repository's canonical benchmark procedure is not changed by this tool.
"""
from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import statistics
import subprocess
import time
import wave

SOURCE_SHA = 'ef339be58a778b062e1c14382347964552eae007'
MODEL = 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'
MODEL_REVISION = '0c0e3051f131929182e2c023b9537f8b1c68adfe'
ASSETS = {
    'model.safetensors': '38b1d5971bdbd982b561cccec982669a53b0537c3cf5e9bd4778ed07bb2f5137',
    'speech_tokenizer/model.safetensors': '836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258',
}
SOURCE_INPUTS = {
    'qwen_tts_sampling.c': '3326deb1e348cf9869cc94310c4e93ba844e4db303395950d07d14136723eba7',
    'qwen_tts_code_predictor.c': '1017b7c412c572674ddaafe5657fae952cf3c24606638cad4ff621d406f2b76f',
}
TEXTS = (
    '오늘 배운 내용을 잠깐 덮고, 자기 말로 한 번 설명해 보세요.',
    '지금은 조금 느려도 괜찮습니다. 한 문장씩 차분하게 읽어 보세요.',
)
INSTRUCT = ('Speak Korean to one learner. Ask the questions with genuine curiosity, '
    'then explain with clear, conversational phrasing. Keep a consistent speaking '
    'voice throughout the paragraph. Allow occasional quiet, unforced inhalations '
    'and small thoughtful pauses at natural phrase boundaries when the meaning '
    'calls for them. Keep the vocal affect appropriate to the subject, and keep '
    'the spoken words clear and complete.')
PROFILES = {
    'baseline': {},
    'rowpack_qkv': {'QWEN_PREFILL_ROWPACK': '1', 'QWEN_PREFILL_QKV_SHARE': '1'},
    # The bare wide driver has no in-process census hook at this source pin.
    # Pair chunk32 with rowpack so the actual B=32 row kernel is observable;
    # this arm is not a claim to isolate chunk size from row layout.
    'rowpack_chunk32': {'QWEN_PREFILL_ROWPACK': '1', 'QWEN_PREFILL_CHUNK': '32'},
    'combined': {'QWEN_PREFILL_ROWPACK': '1', 'QWEN_PREFILL_QKV_SHARE': '1', 'QWEN_PREFILL_CHUNK': '32'},
}
TOKEN_CAP = 1100
CALL_TIMEOUT = 180
TOTAL_SECONDS = 1200


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


def capture(args, *, cwd=None, env=None, timeout=20):
    return subprocess.check_output(args, cwd=cwd, env=env, timeout=timeout, text=True,
                                   stderr=subprocess.STDOUT).strip()


def child_environment(profile):
    # Do not inherit service tokens, arbitrary experimental controls, or native
    # sampling patches. Baseline really is this upstream build's default path.
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'TMPDIR')
           if k in os.environ}
    env.update(OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', ORT_DISABLE_TELEMETRY='1')
    env.update(PROFILES[profile])
    return env


def generation_command(binary, model, target, fixture):
    return [str(binary), '-d', str(model), '-s', 'sohee', '-l', 'Korean', '-j', '4',
            '--text', TEXTS[fixture], '--seed', str(5192 + fixture),
            '--temperature', '0.8', '--top-k', '50', '--top-p', '0.9',
            '--rep-penalty', '1.05', '--max-tokens', str(TOKEN_CAP),
            '--eos-strategy', 'off', '--instruct', INSTRUCT, '-o', str(target)]


def bounded_run(command, *, cwd, env, stdout, stderr, timeout):
    """Own and reap exactly this child/session, including timeout descendants."""
    started = time.perf_counter()
    with Path(stdout).open('wb') as out, Path(stderr).open('wb') as err:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=out, stderr=err,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        timed_out = False
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            returncode = proc.wait(timeout=10)
    return dict(returncode=returncode, timed_out=timed_out,
                process_wall_seconds=time.perf_counter() - started,
                known_child_pid=proc.pid, child_reaped=proc.returncode is not None)


def audio_evidence(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or not 44 < path.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError('invalid_output_wav')
    with wave.open(str(path), 'rb') as f:
        channels, width, rate, count = f.getnchannels(), f.getsampwidth(), f.getframerate(), f.getnframes()
        if (channels, width, rate) != (1, 2, 24000) or count <= 0:
            raise ValueError('unexpected_native_audio_format')
        pcm = f.readframes(count)
    if len(pcm) != count * width:
        raise ValueError('truncated_wav_pcm')
    values = array.array('h', pcm)
    if __import__('sys').byteorder != 'little':
        values.byteswap()
    rms = math.sqrt(sum((x / 32768.0) ** 2 for x in values) / count)
    peak = max(abs(x) for x in values) / 32768.0
    return dict(file=path.name, wav_sha256=sha(path), pcm_sha256=hashlib.sha256(pcm).hexdigest(),
                sample_count=count, sample_rate=rate, audio_seconds=count / rate,
                signal_valid=bool(math.isfinite(rms) and rms > 1e-6 and peak < 1),
                peak=peak, rms=rms, sample_format='PCM16_LE')


def log_evidence(log):
    text = log.replace('\r', '\n')
    eos = re.findall(r'\bEOS at frame ([0-9]+)', text)
    generated = re.findall(r'(?m)^\s*Generated ([0-9]+) frames \(', text)
    strategy = re.findall(r'(?m)^\s*EOS strategy: ([a-z0-9_]+) \(', text)
    natural = (len(eos) == len(generated) == len(strategy) == 1 and strategy[0] == 'off'
               and eos[0] == generated[0] and 0 < int(eos[0]) < TOKEN_CAP)
    one = lambda pattern: [float(x) for x in re.findall(pattern, text)]
    load = one(r'Model loaded in ([0-9.]+) ms')
    prefill = one(r'(?m)^\s*Prefill: ([0-9.]+) ms\s*$')
    synth = one(r'Audio: [0-9.]+s generated in ([0-9.]+)s \(')
    return dict(natural_eos_observed=natural, generated_frames=int(generated[0]) if len(generated) == 1 else None,
                eos_assistance_enabled=(strategy[0] != 'off') if len(strategy) == 1 else None,
                model_load_seconds=load[0] / 1000 if len(load) == 1 else None,
                prefill_seconds=prefill[0] / 1000 if len(prefill) == 1 else None,
                native_reported_generation_seconds=synth[0] if len(synth) == 1 else None)


def validate_proof(profile, census):
    """Requested flags cannot silently fall back and earn a performance label."""
    rows = census.get('rows', [])
    if census.get('dropped_ops') != 0 or not rows:
        return dict(path_verified=False, reason='census_missing_or_dropped')
    used = [r for r in rows if r.get('calls', 0) > 0]
    rowpack = sum(r['calls'] for r in used if r.get('path') == 'matmat_bf16_rows')
    shared = sum(r['calls'] for r in used if r.get('path') == 'bf16_rowpack_shared')
    wide = sum(r['calls'] for r in used if r.get('path') in ('matmat_bf16', 'matmat_bf16_rows') and r.get('B', 0) > 16)
    generic = sum(r['calls'] for r in used if r.get('comp') == 'talker' and r.get('path') == 'prefill_f32_sgemm')
    expects_rows = profile != 'baseline'
    expects_shared = profile in ('rowpack_qkv', 'combined')
    expects_wide = profile in ('rowpack_chunk32', 'combined')
    ok = (rowpack > 0) if expects_rows else (rowpack == 0)
    ok = ok and ((shared > 0) if expects_shared else (shared == 0))
    ok = ok and ((wide > 0) if expects_wide else (wide == 0)) and generic == 0
    return dict(path_verified=ok, rowpack_calls=rowpack, shared_qkv_pack_calls=shared,
                wide_bf16_calls=wide, talker_prefill_f32_sgemm_calls=generic,
                reason='requested_paths_observed' if ok else 'requested_path_fell_back_or_unexpected_leaf')


def avx512bf16_supported(caps, cpu_flags):
    return bool('SIMD=avx512bf16' in caps and
                ({'avx512bf16', 'avx512_bf16'} & set(cpu_flags.split())))


def summary(rows, profiles, skipped):
    pairs, totals = [], {}
    for name in PROFILES:
        rr = [r for r in rows if r['profile'] == name]
        complete = len(rr) == 4 and all(r.get('valid_complete') for r in rr)
        totals[name] = dict(completed_trials=len(rr), complete=complete,
            median_two_fixture_process_seconds=statistics.median(
                [sum(r['process_wall_seconds'] for r in rr if r['repeat'] == rep) for rep in range(2)]) if complete else None,
            median_two_fixture_prefill_seconds=statistics.median(
                [sum(r['prefill_seconds'] for r in rr if r['repeat'] == rep) for rep in range(2)])
                if complete and all(r.get('prefill_seconds') is not None for r in rr) else None)
    baseline_stability = []
    for fixture in range(2):
        rr = [r for r in rows if r['profile'] == 'baseline' and r['fixture'] == fixture]
        stable = (len(rr) == 2 and {r['repeat'] for r in rr} == {0, 1}
                  and all(r.get('valid_complete') for r in rr)
                  and all(rr[0].get(k) == rr[1].get(k)
                          for k in ('pcm_sha256', 'sample_count', 'sample_rate')))
        baseline_stability.append(dict(fixture=fixture, repeat_pcm_and_samples_stable=stable))
    baseline_stable = all(r['repeat_pcm_and_samples_stable'] for r in baseline_stability)
    for name in list(PROFILES)[1:]:
        matches = []
        for fi in range(2):
            for rep in range(2):
                a = next((r for r in rows if r['profile'] == 'baseline' and r['fixture'] == fi and r['repeat'] == rep), None)
                b = next((r for r in rows if r['profile'] == name and r['fixture'] == fi and r['repeat'] == rep), None)
                if a and b:
                    same = all(a.get(k) == b.get(k) for k in ('pcm_sha256', 'sample_count', 'sample_rate'))
                    matches.append(dict(fixture=fi, repeat=rep, pcm_exact=same,
                                        baseline_valid=a.get('valid_complete', False), candidate_valid=b.get('valid_complete', False)))
        parity = len(matches) == 4 and all(x['pcm_exact'] and x['baseline_valid'] and x['candidate_valid'] for x in matches)
        path_ok = profiles.get(name, {}).get('proof', {}).get('path_verified', False)
        eligible = parity and path_ok and baseline_stable and totals['baseline']['complete'] and totals[name]['complete']
        gain = (1 - totals[name]['median_two_fixture_process_seconds'] / totals['baseline']['median_two_fixture_process_seconds']) if eligible else None
        status = ('skipped_unsupported_cpu_or_path' if name in skipped else
                  'unstable_baseline_no_speed_claim' if totals['baseline']['complete'] and not baseline_stable else
                  'changed_audio_no_approval' if len(matches) == 4 and not parity else
                  'parity_passed_screening_only' if eligible else 'incomplete_no_speed_claim')
        pairs.append(dict(profile=name, comparisons=matches, exact_pcm_parity=parity,
                          requested_path_verified=path_ok, status=status,
                          process_time_reduction_fraction_if_exact=gain))
    return dict(schema='bora-original-native-prefill-screen/v1', profiles=totals, comparisons=pairs,
                baseline_repeats_stable=baseline_stable, baseline_repeat_checks=baseline_stability,
                skipped=skipped, production_eligible=False, human_listening_verified=False,
                scope='Independent CLI screening, not canonical server WAVE/SOAK or full-course performance. Native RNG/greedy CP differ from original Python. Exact parity only means this fixed C baseline.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'model'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--output', '--out', dest='out', required=True)
    args = parser.parse_args()
    source, model, out = (Path(x).resolve() for x in (args.source, args.model, args.out))
    if out.exists() or any(out == p or out.is_relative_to(p) for p in (source, model)):
        raise ValueError('output_must_be_fresh_and_outside_source_and_model')
    if any(os.environ.get(k) for k in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')):
        raise ValueError('public_benchmark_must_not_receive_private_keys')
    if capture(['git', 'rev-parse', 'HEAD'], cwd=source) != SOURCE_SHA or capture(['git', 'diff', 'HEAD', '--'], cwd=source):
        raise ValueError('native_source_revision_or_cleanliness_mismatch')
    for name, expected in SOURCE_INPUTS.items():
        if sha(source / name) != expected:
            raise ValueError('native_sampler_source_modified')
    config = json.loads((model / 'config.json').read_text())
    if config.get('tts_model_type') != 'custom_voice' or config.get('tts_model_size') != '1b7':
        raise ValueError('wrong_model')
    model_hashes = {name: sha(model / name) for name in ASSETS}
    if model_hashes != ASSETS:
        raise ValueError('model_weights_mismatch')
    binary = source / 'qwen_tts'
    if not binary.is_file() or binary.is_symlink() or not Path('/usr/bin/time').is_file():
        raise ValueError('binary_or_resource_timer_missing')
    out.mkdir(parents=True)
    cpuinfo = Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else ''
    cpu_model = next((s.split(':', 1)[1].strip() for s in cpuinfo.splitlines() if s.startswith('model name')), 'unknown')
    cpu_flags = next((s.split(':', 1)[1].strip() for s in cpuinfo.splitlines() if s.startswith('flags')), '')
    manifest = dict(schema='bora-original-native-run-manifest/v1', source_commit=SOURCE_SHA,
        source_diff_clean=True, source_inputs=SOURCE_INPUTS, binary_sha256=sha(binary),
        source_fingerprint=(source / '.source_fingerprint').read_text().strip() if (source / '.source_fingerprint').is_file() else None,
        compiler=capture(['cc', '--version']).splitlines()[0], model=MODEL, model_revision=MODEL_REVISION,
        model_hashes=model_hashes, cpu_model=cpu_model, cpu_flags=cpu_flags, visible_logical_cpus=os.cpu_count(),
        affinity=sorted(os.sched_getaffinity(0)), platform=platform.platform(),
        cpu_quota_verified=False, profiles={}, public_fixtures=list(TEXTS), instruction=INSTRUCT,
        seeds=[5192, 5193], threads=4, total_timeout_seconds=TOTAL_SECONDS, per_call_timeout_seconds=CALL_TIMEOUT,
        policy='Fixed upstream native BF16 + greedy code predictor. No CP sampling patch, no quantization, no DSP, no production writes.',
        canonical_native_benchmark_procedure_changed=False,
        numerical_equivalence_scope='Candidate versus same C baseline only; never Python FP32 equivalence.')
    rows, skipped = [], {}
    start = time.perf_counter()
    deadline = start + TOTAL_SECONDS
    def timeout():
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError('native_screen_total_budget_exhausted')
        return min(CALL_TIMEOUT, remaining)
    def save():
        atomic_json(out / 'run_manifest.json', manifest)
        atomic_json(out / 'rows.json', rows)
        atomic_json(out / 'summary.json', summary(rows, manifest['profiles'], skipped))
    def run_trial(name, fixture, repeat, proof=False):
        stem = f'{name}-proof' if proof else f'{name}-f{fixture}-r{repeat}'
        env = child_environment(name)
        if proof:
            env.update(QWEN_SHAPE_CENSUS='1', QWEN_CENSUS_JSON=str(out / (stem + '-census.json')))
        wav = out / (stem + '.wav')
        native_command = generation_command(binary, model, wav, fixture)
        resource_file = out / (stem + '-resources.json')
        command = ['/usr/bin/time', '-f', '{"max_rss_kib":%M,"user_cpu_seconds":%U,"system_cpu_seconds":%S}', '-o', str(resource_file), *native_command]
        result = bounded_run(command, cwd=source, env=env, stdout=out / (stem + '.stdout.log'),
                             stderr=out / (stem + '.stderr.log'), timeout=timeout())
        row = dict(profile=name, fixture=fixture, repeat=repeat, seed=5192 + fixture,
                   exact_command=command, native_command=native_command, exact_environment=env,
                   measurement='DIAGNOSTIC_UNTIMED_PATH_PROOF' if proof else 'UNINSTRUMENTED_CLI_SCREEN', **result)
        log = (out / (stem + '.stderr.log')).read_text(errors='replace')
        row.update(log_evidence(log))
        try:
            row.update(audio_evidence(wav))
        except Exception as error:
            row['audio_error'] = type(error).__name__ + ':' + str(error)
        try:
            row['resource_usage'] = json.loads(resource_file.read_text())
        except (OSError, ValueError):
            row['resource_usage'] = None
        row['decoded_pcm_frame_coverage_verified'] = bool(row.get('generated_frames') and
            row.get('sample_count') == row['generated_frames'] * 1920)
        row['valid_complete'] = bool(row['returncode'] == 0 and not row['timed_out'] and
            row.get('natural_eos_observed') and row.get('signal_valid') and row['decoded_pcm_frame_coverage_verified'])
        atomic_json(out / (stem + '.json'), row)
        return row
    try:
        for name in PROFILES:
            env = child_environment(name)
            caps = capture([str(binary), '-j', '4', '--caps'], cwd=source, env=env)
            (out / (name + '-capabilities.txt')).write_text(caps + '\n')
            dispatch_env = dict(env, QWEN_DISPATCH_JSON=str(out / (name + '-dispatch.json')))
            dispatch = capture([str(binary), '-j', '4', '--dispatch-map'], cwd=source, env=dispatch_env)
            (out / (name + '-dispatch.txt')).write_text(dispatch + '\n')
            entry = dict(environment=env, requested_flags=PROFILES[name],
                         caps_file=name + '-capabilities.txt', dispatch_file=name + '-dispatch.json')
            manifest['profiles'][name] = entry
            if not avx512bf16_supported(caps, cpu_flags):
                skipped[name] = 'requires_compiled_and_runtime_avx512bf16'
                entry['proof'] = dict(path_verified=False, reason=skipped[name])
                save()
                continue
            proof = run_trial(name, 0, -1, proof=True)
            census_path = out / (name + '-proof-census.json')
            proof_gate = validate_proof(name, json.loads(census_path.read_text())) if census_path.is_file() else dict(path_verified=False, reason='census_not_written')
            proof_gate['complete_audio'] = proof['valid_complete']
            proof_gate['path_verified'] = bool(proof_gate['path_verified'] and proof['valid_complete'])
            entry['proof'] = proof_gate
            if not proof_gate['path_verified']:
                skipped[name] = proof_gate['reason']
            save()
        if 'baseline' in skipped:
            skipped.update({name: 'baseline_path_unavailable' for name in PROFILES if name != 'baseline'})
        else:
            for repeat in range(2):
                for fixture in range(2):
                    order = list(PROFILES)
                    if (repeat + fixture) % 2:
                        order.reverse()
                    for name in order:
                        if name in skipped:
                            continue
                        rows.append(run_trial(name, fixture, repeat))
                        print(json.dumps({k: rows[-1].get(k) for k in ('profile', 'fixture', 'repeat', 'process_wall_seconds', 'prefill_seconds', 'sample_count', 'valid_complete')}), flush=True)
                        save()
        manifest['status'] = 'screen_complete'
    except Exception as error:
        manifest['status'] = 'screen_interrupted'
        manifest['error'] = dict(type=type(error).__name__, message=str(error))
        save()
        raise
    finally:
        manifest['elapsed_seconds'] = time.perf_counter() - start
        manifest['persistent_servers_started'] = False
        manifest['known_child_processes_reaped'] = all(r.get('child_reaped') for r in rows)
        save()
    print(json.dumps(summary(rows, manifest['profiles'], skipped), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
