"""Bounded public CPU comparison, not production rendering or quality approval.

Plain uses library generation defaults except a shared 1100-token safety cap.
Production-style changes both instruction AND sampling; this is not a controlled
estimate of the instruction's effect alone. Two fixtures use AB/BA call order.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import time

MODEL = 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'
REVISION = '0c0e3051f131929182e2c023b9537f8b1c68adfe'
CAP = 1100
THREADS = 4
CASES = ('plain_defaults', 'production_style')
FIXTURES = (
    '오늘 배운 내용을 잠깐 덮고, 자기 말로 한 번 설명해 보세요.',
    '지금은 조금 느려도 괜찮습니다. 한 문장씩 차분하게 읽어 보세요.',
)
STYLE = 'Speak Korean to one learner. Ask the questions with genuine curiosity, then explain with clear, conversational phrasing. Keep a consistent speaking voice throughout the paragraph. Allow occasional quiet, unforced inhalations and small thoughtful pauses at natural phrase boundaries when the meaning calls for them. Keep the vocal affect appropriate to the subject, and keep the spoken words clear and complete.'
SAMPLING = dict(non_streaming_mode=True, do_sample=True, temperature=0.8,
    top_p=0.9, top_k=50, repetition_penalty=1.05,
    subtalker_dosample=True, subtalker_temperature=0.9,
    subtalker_top_p=1.0, subtalker_top_k=50)


def require(ok):
    if not ok:
        raise ValueError('public_basic_sohee_benchmark_invalid')


def generation_args(case, text):
    require(case in CASES and text in FIXTURES)
    args = dict(text=text, language='Korean', speaker='Sohee', max_new_tokens=CAP)
    if case == 'production_style':
        args.update(SAMPLING)
        args['instruct'] = STYLE
    return args


def observe_eos(tokens, eos_id):
    require(isinstance(tokens, list) and tokens
        and all(type(t) is int for t in tokens) and type(eos_id) is int)
    return dict(sequence_tokens=len(tokens), eos_seen=eos_id in tokens,
        cap_reached=len(tokens) >= CAP,
        natural_eos=eos_id in tokens and tokens[-1] == eos_id and len(tokens) < CAP)


def summarize(rows):
    require(isinstance(rows, list) and len(rows) == len(CASES) * len(FIXTURES))
    require({(r['case'], r['fixture']) for r in rows}
        == {(c, i) for c in CASES for i in range(len(FIXTURES))})
    for r in rows:
        require(type(r['fixture']) is int and r['case'] in CASES
            and r['seed'] == 5192 + r['fixture'])
        for key in ('tts_seconds', 'audio_seconds'):
            require(type(r[key]) in (int, float) and math.isfinite(r[key]) and r[key] > 0)
        require(type(r['natural_eos']) is bool and type(r['signal_valid']) is bool)
    valid = all(r['natural_eos'] and r['signal_valid'] for r in rows)
    totals = {}
    for case in CASES:
        selected = [r for r in rows if r['case'] == case]
        tts = sum(r['tts_seconds'] for r in selected)
        audio = sum(r['audio_seconds'] for r in selected)
        totals[case] = dict(tts_seconds=round(tts, 3), audio_seconds=round(audio, 3),
            synthesis_seconds_per_audio_second=round(tts / audio, 5) if valid else None)
    return dict(schema='basic-sohee-public-cpu-benchmark/v1', model=MODEL,
        requested_model_revision=REVISION, device='cpu', dtype='float32', threads=THREADS,
        speaker='Sohee', public_fixtures=list(FIXTURES), rows=rows, totals=totals,
        complete_natural_eos_and_signal=valid,
        model_load_included_in_tts=False, asr_performed=False, dsp_performed=False,
        retries_performed=False, human_listening_verified=False,
        production_eligible=False, full_course_forecast_validated=False,
        scope='One AB/BA trial on two short public fixtures. Plain omits instruction and sampling overrides except the shared 1100-token safety cap. Production-style changes instruction and sampling together. No content accuracy or full-course speed claim.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', required=True)
    p.add_argument('--out', '--output', dest='out', required=True)
    args = p.parse_args()
    require(not any(os.environ.get(n) for n in (
        'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64', 'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')))
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        OMP_NUM_THREADS=str(THREADS), OPENBLAS_NUM_THREADS=str(THREADS))
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(THREADS)
    torch.set_num_interop_threads(1)
    load_start = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(str(Path(args.model).resolve()),
        device_map='cpu', dtype=torch.float32, attn_implementation='sdpa',
        low_cpu_mem_usage=True, local_files_only=True)
    load_seconds = time.perf_counter() - load_start
    require(all(p.dtype == torch.float32 and p.device.type == 'cpu'
        for p in model.model.parameters() if p.is_floating_point()))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    def timeout(*unused):
        raise TimeoutError('public_sample_cpu_deadline')
    signal.signal(signal.SIGALRM, timeout)
    for i, text in enumerate(FIXTURES):
        order = CASES if i == 0 else tuple(reversed(CASES))
        for case in order:
            observed = []
            talker = model.model.talker
            original = talker.generate
            eos_id = int(model.model.config.talker_config.codec_eos_token_id)
            def observe(*a, **kw):
                result = original(*a, **kw)
                observed.append(observe_eos(result.sequences[0].tolist(), eos_id))
                return result
            talker.generate = observe
            seed = 5192 + i
            torch.manual_seed(seed)
            signal.alarm(600)
            started = time.perf_counter()
            try:
                with torch.inference_mode():
                    waves, sr = model.generate_custom_voice(**generation_args(case, text))
                elapsed = time.perf_counter() - started
            finally:
                signal.alarm(0)
                talker.generate = original
            require(len(waves) == 1 and sr == 24000 and len(observed) == 1)
            wave = np.asarray(waves[0])
            signal_valid = bool(wave.ndim == 1 and wave.size > 0 and
                np.isfinite(wave).all() and np.max(np.abs(wave)) < 1.0 and
                np.sqrt(np.mean(wave.astype('float64') ** 2)) > 1e-5)
            target = out / f'{case}-{i}.wav'
            sf.write(target, wave, sr, subtype='FLOAT')
            rows.append(dict(case=case, fixture=i, seed=seed, tts_seconds=round(elapsed, 6),
                audio_seconds=round(len(wave) / sr, 6), sample_rate=sr,
                signal_valid=signal_valid, **observed[0],
                wav_sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
            print(json.dumps(rows[-1], sort_keys=True), flush=True)
    report = summarize(rows)
    report.update(model_load_seconds=round(load_seconds, 3), cpu_count=os.cpu_count(),
        platform=platform.platform(),
        pinned_generation_config=json.loads((Path(args.model)/'generation_config.json').read_text()))
    (out / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
