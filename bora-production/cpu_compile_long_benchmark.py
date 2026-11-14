"""Four public production-sized units: explain E/C(cold), reading C(warm)/E.

Private sources, production activation and forecasts are forbidden. A reading
compiled row remains unverified until its later eager reference is available.
"""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import cpu_compile_benchmark as base
from cpu_compile_resident_benchmark import ResidentForward
from fp32_renderer import SAMPLING, STYLES
from tts_normalize import RULESET, normalize

SCHEMA = 'sohee-public-cpu-compile-long/v1'
COMPILED = base.COMPILED_MODE
CALL_SECONDS, WORKER_SECONDS, CAP = 600, 1900, 1100
SCHEDULE = (('eager', 0), (COMPILED, 0), (COMPILED, 1), ('eager', 1))
SOURCES = (
    '오늘은 조문을 읽을 때 문장을 어디에서 나누어야 하는지 살펴보겠습니다. 먼저 주체를 찾고, 다음으로 조건과 예외를 구분하세요. 긴 문장을 한 번에 외우려고 하기보다는, 주체와 행동을 연결해서 이해하는 것이 좋습니다. 책을 덮고 핵심을 자기 말로 설명한 뒤, 원문을 다시 펼쳐 빠뜨린 부분을 확인해 보세요.',
    '다음은 숫자 낭독을 확인하기 위한 가상의 예문입니다. 제11조 제2항에 따라 교육 시간은 3시간으로 정한다. 제3항에 따라 인원은 25명으로 한다. 제출일은 2026년 11월 14일이며, 시작 시각은 09:30이다. 제2호부터 제4호까지의 사항을 차례로 확인한다.',
)
FIXTURE_MODES, SEEDS = ('explain', 'reading'), (5192, 7319)


def fixtures():
    result = []
    for index, source in enumerate(SOURCES):
        spoken, edits, flags = normalize(source)
        base.require(not flags and 140 <= len(source) <= 180 and len(spoken) <= 180)
        result.append(dict(fixture=index, source=source, spoken=spoken,
            source_characters=len(source), spoken_characters=len(spoken),
            source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            spoken_sha256=hashlib.sha256(spoken.encode()).hexdigest(),
            fixture_mode=FIXTURE_MODES[index], seed=SEEDS[index],
            normalization_ruleset=RULESET, normalization_edits=edits,
            normalization_review_flags=flags,
            fixture_scope='Independently authored public test example; fictional clauses are not legal advice or an actual provision.'))
    return result


def generation_args(index):
    fixture = fixtures()[index]
    return dict(SAMPLING, text=fixture['spoken'], language='Korean', speaker='Sohee',
        max_new_tokens=CAP, instruct=STYLES[fixture['fixture_mode']])


def request_value():
    return dict(schema=SCHEMA,
        request_id='public-sohee-fp32-predictor-long-20261114-01',
        model_revision=base.REVISION, fixture_count=2, fixtures=fixtures(),
        threads=4, interop_threads=1, call_timeout_seconds=CALL_SECONDS,
        worker_timeout_seconds=WORKER_SECONDS, max_new_tokens=CAP,
        compile_scope=base.COMPILE_SCOPE, compile_target_path=base.COMPILE_TARGET_PATH,
        eager_mask_aliases=list(base.MASK_ALIASES), compile_threads=1,
        maximum_compiled_graphs=base.MAX_GRAPHS,
        schedule=[list(item) for item in SCHEDULE],
        production=False, secrets=False, gpu=False)


def validate_request():
    base.require(json.loads(Path(__file__).with_name(
        'cpu-compile-long-request.json').read_text()) == request_value())


def link_outputs(rows, pcm_bytes, fixture):
    """Fill exact comparisons only when an eager fixture reference exists."""
    selected = [row for row in rows if row['fixture'] == fixture]
    reference = next((row for row in selected if row['mode'] == 'eager'), None)
    for row in selected:
        pending = reference is None
        row.update(comparison_pending=pending,
            comparison_reference_call_index=reference['call_index'] if reference else None,
            exact_pcm_equal=None if pending else
                pcm_bytes[row['call_index']] == pcm_bytes[reference['call_index']],
            exact_sequence_equal=None if pending else
                row['talker_sequences'] == reference['talker_sequences'],
            exact_rate_and_samples=None if pending else
                (row['sample_rate'], row['pcm_samples']) ==
                (reference['sample_rate'], reference['pcm_samples']))


def summarize(rows, counts, setup_seconds=0.0):
    base.require(len(rows) <= len(SCHEDULE)
        and [(row['mode'], row['fixture']) for row in rows] == list(SCHEDULE[:len(rows)]))
    base.require(type(setup_seconds) in (int, float) and math.isfinite(setup_seconds) and setup_seconds >= 0)
    for index, row in enumerate(rows):
        fixture = fixtures()[row['fixture']]
        base.require(row['call_index'] == index and row['seed'] == fixture['seed']
            and row['fixture_mode'] == fixture['fixture_mode']
            and row['source'] == fixture['source'] and row['spoken'] == fixture['spoken']
            and row['generation_args'] == generation_args(row['fixture']))
        for key in ('tts_seconds', 'audio_seconds'):
            base.require(type(row[key]) in (int, float) and math.isfinite(row[key]) and row[key] > 0)
        for key in ('natural_eos', 'signal_valid', 'within_production_part_limit', 'comparison_pending'):
            base.require(type(row[key]) is bool)
        for key in ('exact_pcm_equal', 'exact_sequence_equal', 'exact_rate_and_samples'):
            base.require(row[key] is None if row['comparison_pending'] else type(row[key]) is bool)
        for key in ('compiled_graph_invocations_during_call', 'new_graphs_compiled_during_call'):
            base.require(type(row[key]) is int and row[key] >= 0)
    base.require(all(type(value) is int and value >= 0 for value in counts.values()))
    complete = len(rows) == len(SCHEDULE)
    exact = bool(rows) and all(not row['comparison_pending'] and row['exact_pcm_equal']
        and row['exact_sequence_equal'] and row['exact_rate_and_samples'] for row in rows)
    signal_ok = bool(rows) and all(row['natural_eos'] and row['signal_valid']
        and row['within_production_part_limit'] for row in rows)
    compiled = [row for row in rows if row['mode'] == COMPILED]
    executed = bool(compiled) and all(row['compiled_graph_invocations_during_call'] > 0 for row in compiled)
    valid = complete and exact and signal_ok and executed
    reading_c = next((row for row in rows if row['fixture'] == 1 and row['mode'] == COMPILED), None)
    reading_e = next((row for row in rows if row['fixture'] == 1 and row['mode'] == 'eager'), None)
    warm_valid = valid and reading_c is not None and reading_c['new_graphs_compiled_during_call'] == 0
    eager_total = sum(row['tts_seconds'] for row in rows if row['mode'] == 'eager')
    compiled_total = setup_seconds+sum(row['tts_seconds'] for row in compiled)
    pairs = []
    for fixture in range(2):
        selected = [row for row in rows if row['fixture'] == fixture]
        eager = next((row for row in selected if row['mode'] == 'eager'), None)
        candidate = next((row for row in selected if row['mode'] == COMPILED), None)
        if eager and candidate:
            compiled_cost = candidate['tts_seconds']+(setup_seconds if fixture == 0 else 0)
            pairs.append(dict(fixture=fixture, fixture_mode=FIXTURE_MODES[fixture], seed=SEEDS[fixture],
                eager_seconds=eager['tts_seconds'], compiled_seconds_including_setup=compiled_cost,
                compiled_call_includes_first_compilation=fixture == 0,
                compiled_new_graphs=candidate['new_graphs_compiled_during_call'],
                net_saved_seconds=eager['tts_seconds']-compiled_cost))
    return dict(schema=SCHEMA,
        status='complete_exact_long_public_outputs' if valid else 'partial_or_unvalidated_no_speed_claim',
        complete=complete, exact_public_outputs_equal=exact,
        all_natural_eos_and_signal_valid=signal_ok, compiled_execution_observed=executed,
        comparison_valid=valid, warm_reading_speed_claim_valid=warm_valid,
        rows=rows, fixture_manifest=fixtures(), schedule=[list(item) for item in SCHEDULE],
        compile_counters=dict(counts), compile_wrapper_setup_seconds=setup_seconds,
        pairs=pairs, total_eager_synthesis_seconds=eager_total,
        total_compiled_synthesis_and_setup_seconds=compiled_total,
        observed_total_net_saved_seconds=eager_total-compiled_total,
        total_wall_ratio_including_compile_setup=eager_total/compiled_total if valid else None,
        actual_two_fixture_cost_recovered=compiled_total <= eager_total if valid else None,
        warm_reading_eager_seconds=reading_e['tts_seconds'] if reading_e else None,
        warm_reading_compiled_seconds=reading_c['tts_seconds'] if reading_c else None,
        warm_reading_eager_over_compiled=reading_e['tts_seconds']/reading_c['tts_seconds'] if warm_valid else None,
        first_explain_pair_is_steady_state=False,
        model=base.MODEL, model_revision=base.REVISION, dtype='float32', device='cpu',
        speaker='Sohee', threads=4, interop_threads=1,
        compile_scope=base.COMPILE_SCOPE, compile_target_path=base.COMPILE_TARGET_PATH,
        retained_compiled_callable=True, dynamo_reset_between_calls=False,
        production_eligible=False, promotion_performed=False,
        human_listening_verified=False, asr_performed=False, dsp_performed=False,
        full_course_forecast_validated=False, break_even_forecast_performed=False,
        model_load_included_in_synthesis_totals=False,
        caveat='One AB/BA comparison on two independently authored public units within the180-character production boundary. Explain uses seed5192 and includes cold compilation; reading uses seed7319 and is warm only if no new graphs. Exact PCM and full returned sequences prove these outputs, not all content, styles/seeds combinations or pronunciation correctness. Downloads, common model load, saving and validation are outside synthesis totals. No private pipeline or production activation.')


def worker(model_path, output):
    from download_models import require_keyless
    require_keyless()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4',
        TORCHINDUCTOR_COMPILE_THREADS='1', MAX_JOBS='1')
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    from torch._dynamo.backends.registry import lookup_backend
    from torch._dynamo.utils import counters
    import torch._inductor.config as inductor_config
    from fp32_renderer import verify_assets, MODEL_REQUIRED
    torch.set_num_threads(4); torch.set_num_interop_threads(1)
    torch._dynamo.config.suppress_errors = False
    torch._dynamo.config.recompile_limit = base.MAX_GRAPHS
    inductor_config.compile_threads = 1
    out = Path(output); rows = []; pcm_bytes = {}; setup_seconds = 0.0
    counts = dict(backend_compile_attempts=0, backend_graphs_compiled=0,
        compiled_graph_invocations=0, predictor_forward_calls=0)
    metadata = dict(cpu=base.hardware(), torch_version=torch.__version__,
        compiler_version=subprocess.run(['g++', '--version'], capture_output=True,
            text=True, check=True).stdout.splitlines()[0],
        native_threads=base.validate_native_threads(4, torch.get_num_threads(),
            torch.get_num_interop_threads(), torch.__config__.parallel_info()),
        model_assets_sha256=verify_assets(model_path, MODEL_REQUIRED, base.REVISION))
    started = time.perf_counter(); trial_started = started
    base.dump(out/'summary.json', dict(summarize(rows, counts), **metadata, status='loading_original_fp32_model'))
    def save(**extra):
        report = dict(summarize(rows, counts, setup_seconds), **metadata,
            worker_elapsed_seconds=time.perf_counter()-trial_started, **extra)
        base.dump(out/'summary.json', report)
        return report
    def deadline(*unused):
        raise TimeoutError('public_long_compile_call_timeout')
    signal.signal(signal.SIGALRM, deadline); index = -1
    try:
        model = Qwen3TTSModel.from_pretrained(str(Path(model_path).resolve()),
            device_map='cpu', dtype=torch.float32, attn_implementation='sdpa',
            low_cpu_mem_usage=True, local_files_only=True)
        model.model.eval(); metadata['model_load_seconds'] = time.perf_counter()-started
        base.require(all(p.dtype == torch.float32 and p.device.type == 'cpu'
            for p in model.model.parameters() if p.is_floating_point()))
        predictor = model.model.talker.code_predictor; target = predictor.model
        original_outer = predictor.forward; modeling = inspect.getmodule(type(predictor))
        base.require(modeling is not None)
        metadata['original_predictor_source_sha256'] = base.digest(inspect.getsourcefile(type(predictor)))
        base.require(metadata['original_predictor_source_sha256'] == base.MODELING_SHA256)
        metadata['memory_after_load'] = base.rss_snapshot(); resident = None
        for index, (mode, fixture_index) in enumerate(SCHEDULE):
            fixture = fixtures()[fixture_index]
            base.require(predictor.forward == original_outer)
            if mode == COMPILED and resident is None:
                started = time.perf_counter()
                resident = ResidentForward(target, modeling, torch.compile, torch.compiler.disable,
                    base.tracked_backend(lookup_backend('inductor'), counts), counts)
                setup_seconds = time.perf_counter()-started
                save(status='resident_wrapper_ready_before_cold_call')
            before = dict(counts); observed = []
            talker = model.model.talker; original_generate = talker.generate
            eos = int(model.model.config.talker_config.codec_eos_token_id)
            def observe(*args, **kwargs):
                result = original_generate(*args, **kwargs)
                sequences = result.sequences.tolist()
                base.require(len(sequences) == 1)
                observed.append(dict(base.observe_eos(sequences[0], eos), talker_sequences=sequences,
                    talker_sequence_sha256=hashlib.sha256(json.dumps(sequences,
                        separators=(',', ':')).encode()).hexdigest()))
                return result
            talker.generate = observe
            torch.manual_seed(fixture['seed']); signal.alarm(CALL_SECONDS)
            started = time.perf_counter()
            try:
                context = resident.select(mode) if resident else contextlib.nullcontext()
                with context, torch.inference_mode():
                    waves, rate = model.generate_custom_voice(**generation_args(fixture_index))
                elapsed = time.perf_counter()-started
            finally:
                signal.alarm(0); talker.generate = original_generate
            base.require(len(waves) == 1 and len(observed) == 1 and rate == 24000)
            wave = np.asarray(waves[0], dtype='float32')
            valid = bool(wave.ndim == 1 and wave.size > 0 and np.isfinite(wave).all()
                and np.max(np.abs(wave)) < 1 and np.sqrt(np.mean(wave.astype('float64')**2)) > 1e-5)
            wav = out/f'{index:02d}-{mode}-{fixture["fixture_mode"]}.wav'
            sf.write(wav, wave, rate, subtype='FLOAT')
            pcm_bytes[index] = wave.astype('<f4', copy=False).tobytes()
            row = dict(call_index=index, mode=mode, **fixture,
                tts_seconds=elapsed, audio_seconds=len(wave)/rate, sample_rate=rate,
                pcm_samples=len(wave), signal_valid=valid, within_production_part_limit=len(wave)/rate <= 60,
                **observed[0], wav_file=wav.name, wav_sha256=base.digest(wav),
                pcm_sha256=hashlib.sha256(pcm_bytes[index]).hexdigest(),
                generation_args=generation_args(fixture_index),
                compiled_graph_invocations_during_call=counts['compiled_graph_invocations']-before['compiled_graph_invocations'],
                new_graphs_compiled_during_call=counts['backend_graphs_compiled']-before['backend_graphs_compiled'],
                predictor_forward_calls_during_call=counts['predictor_forward_calls']-before['predictor_forward_calls'],
                dynamo_counters=base.dynamo_counts(counters), memory=base.rss_snapshot())
            rows.append(row); link_outputs(rows, pcm_bytes, fixture_index)
            for changed in rows:
                if changed['fixture'] == fixture_index:
                    base.dump(out/f'row-{changed["call_index"]:02d}.json', changed)
            save(); print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)
            base.require(valid and row['natural_eos'] and row['within_production_part_limit'])
            base.require(all(r['comparison_pending'] or (r['exact_pcm_equal']
                and r['exact_sequence_equal'] and r['exact_rate_and_samples']) for r in rows))
            base.require(predictor.forward == original_outer)
        report = save(memory_after_synthesis=base.rss_snapshot())
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if report['comparison_valid'] and report['warm_reading_speed_claim_valid'] else 1
    except Exception as error:
        signal.alarm(0); diagnostic = out/f'error-call-{index:02d}.json'
        base.dump(diagnostic, base.public_error_report(error))
        save(status='long_trial_failed', failed_call_index=index,
            error_type=type(error).__name__, error_file=diagnostic.name,
            error_sha256=base.digest(diagnostic), comparison_valid=False,
            warm_reading_speed_claim_valid=False, warm_reading_eager_over_compiled=None,
            total_wall_ratio_including_compile_setup=None, actual_two_fixture_cost_recovered=None)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args(); validate_request()
    from download_models import require_keyless, verify_assets, QWEN_REQUIRED
    require_keyless(); model, output = Path(args.model).resolve(), Path(args.output).resolve()
    if args.worker:
        return worker(model, output)
    verify_assets(model, QWEN_REQUIRED); output.mkdir(parents=True, exist_ok=False)
    base.dump(output/'summary.json', dict(summarize([], {}), status='worker_starting'))
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
        '--model', str(model), '--output', str(output), '--worker'], start_new_session=True)
    try:
        result = process.wait(timeout=WORKER_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
        result = 1
        report = json.loads((output/'summary.json').read_text())
        report.update(status='long_worker_timeout', worker_timeout_seconds=WORKER_SECONDS)
        base.dump(output/'summary.json', report)
    if result:
        report = json.loads((output/'summary.json').read_text())
        report.update(worker_returncode=result, comparison_valid=False,
            warm_reading_speed_claim_valid=False, warm_reading_eager_over_compiled=None,
            total_wall_ratio_including_compile_setup=None, actual_two_fixture_cost_recovered=None)
        if report.get('status') not in ('long_worker_timeout', 'long_trial_failed'):
            report.update(status='long_worker_failed', last_recorded_status=report.get('status'))
        base.dump(output/'summary.json', report)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return result


if __name__ == '__main__':
    raise SystemExit(main())
