"""Public, same-runner CPU FP32/BF16 timing trial; never production approval.

Model weights, two public fixtures, default Sohee generation, seeds and four
threads stay the same. Only requested floating-point precision differs. Each
precision runs in a separate sequential subprocess so two models never coexist.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from basic_sohee_benchmark import CAP, FIXTURES, MODEL, REVISION, THREADS, generation_args, observe_eos

SCHEMA='sohee-public-cpu-precision/v1'
BF16_FLAGS=frozenset(('avx512_bf16','amx_bf16'))
DTYPES=('float32','bfloat16')


def require(ok):
    if not ok:raise ValueError('public_cpu_precision_benchmark_invalid')


def hardware(cpuinfo=None, cpu_count=None):
    if cpuinfo is None:cpuinfo=Path('/proc/cpuinfo').read_text()
    require(isinstance(cpuinfo,str))
    flags=set();models=set()
    for line in cpuinfo.splitlines():
        key,sep,value=line.partition(':')
        if not sep:continue
        if key.strip() in ('flags','Features'):flags.update(value.split())
        if key.strip()=='model name':models.add(value.strip())
    found=sorted(flags & BF16_FLAGS)
    return dict(cpu_models=sorted(models),logical_cpus=os.cpu_count() if cpu_count is None else cpu_count,
        bf16_instruction_flags=found,bf16_instruction_available=bool(found),
        cpu_flags=sorted(flags))


def validate_request():
    obj=json.loads(Path(__file__).with_name('cpu-precision-request.json').read_text())
    require(obj==dict(schema=SCHEMA,request_id='public-sohee-fp32-bf16-cpu-20261113-01',
        model_revision=REVISION,fixture_count=len(FIXTURES),threads=THREADS,
        production=False,secrets=False))


def validate_rows(rows,dtype):
    require(dtype in DTYPES and isinstance(rows,list) and len(rows)==len(FIXTURES)
        and [r.get('fixture') for r in rows]==list(range(len(FIXTURES))))
    for i,r in enumerate(rows):
        require(type(r['fixture']) is int and r['seed']==5192+i and r['dtype']==dtype
            and type(r['natural_eos']) is bool and type(r['signal_valid']) is bool)
        for key in ('tts_seconds','audio_seconds'):
            require(type(r[key]) in (int,float) and math.isfinite(r[key]) and r[key]>0)
    valid=all(r['natural_eos'] and r['signal_valid'] for r in rows)
    tts=sum(r['tts_seconds'] for r in rows);audio=sum(r['audio_seconds'] for r in rows)
    return dict(tts_seconds=round(tts,6),audio_seconds=round(audio,6),
        complete_natural_eos_and_signal=valid,
        synthesis_seconds_per_audio_second=round(tts/audio,6) if valid else None)


def compare_results(variants,hw):
    require(hw.get('bf16_instruction_available') is True
        and set(variants)==set(DTYPES))
    totals={}
    for dtype,v in variants.items():
        require(v.get('dtype')==dtype and v.get('model_revision')==REVISION
            and v.get('threads')==THREADS and v.get('device')=='cpu'
            and v.get('fixtures')==list(FIXTURES) and v.get('cpu')==hw
            and v.get('parameter_dtypes')==[dtype])
        totals[dtype]=validate_rows(v['rows'],dtype)
    valid=all(t['complete_natural_eos_and_signal'] for t in totals.values())
    return dict(schema=SCHEMA,status='timing_trial_no_quality_equivalence' if valid else 'incomplete_output_no_speed_claim',
        model=MODEL,model_revision=REVISION,cpu=hw,device='cpu',threads=THREADS,
        fixtures=list(FIXTURES),speaker='Sohee',generation='library defaults except shared max_new_tokens=1100',
        variants=variants,totals=totals,
        observed_rtf_speedup=round(totals['float32']['synthesis_seconds_per_audio_second']/
            totals['bfloat16']['synthesis_seconds_per_audio_second'],4) if valid else None,
        asr_performed=False,dsp_performed=False,retries_performed=False,
        production_eligible=False,human_listening_verified=False,
        quality_equivalence_verified=False,full_course_forecast_validated=False,
        caveat='One sequential FP32-then-BF16 trial on two short public fixtures; order and warmup effects are not controlled. Same-runner instruction support is necessary but does not guarantee BF16 speed. Audio, pronunciation and naturalness equivalence require separate review.')


def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def worker(model_path,out,dtype):
    require(dtype in DTYPES)
    hw=hardware()
    require(dtype!='bfloat16' or hw['bf16_instruction_available'])
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        OMP_NUM_THREADS=str(THREADS),OPENBLAS_NUM_THREADS=str(THREADS))
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(THREADS);torch.set_num_interop_threads(1)
    target_dtype={'float32':torch.float32,'bfloat16':torch.bfloat16}[dtype]
    start=time.perf_counter()
    model=Qwen3TTSModel.from_pretrained(str(Path(model_path).resolve()),
        device_map='cpu',dtype=target_dtype,attn_implementation='sdpa',
        low_cpu_mem_usage=True,local_files_only=True)
    load_seconds=time.perf_counter()-start
    observed_dtypes=sorted({str(p.dtype).removeprefix('torch.')
        for p in model.model.parameters() if p.is_floating_point()})
    require(observed_dtypes==[dtype] and all(p.device.type=='cpu' for p in model.model.parameters()))
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    def deadline(*unused):raise TimeoutError('public_cpu_precision_sample_timeout')
    signal.signal(signal.SIGALRM,deadline)
    rows=[]
    for i,text in enumerate(FIXTURES):
        talker=model.model.talker;original=talker.generate;observed=[]
        eos_id=int(model.model.config.talker_config.codec_eos_token_id)
        def observe(*a,**kw):
            result=original(*a,**kw)
            observed.append(observe_eos(result.sequences[0].tolist(),eos_id))
            return result
        talker.generate=observe;torch.manual_seed(5192+i);signal.alarm(240)
        started=time.perf_counter()
        try:
            with torch.inference_mode():waves,sr=model.generate_custom_voice(**generation_args('plain_defaults',text))
            elapsed=time.perf_counter()-started
        finally:
            signal.alarm(0);talker.generate=original
        require(len(waves)==1 and sr==24000 and len(observed)==1)
        wave=np.asarray(waves[0]);valid=bool(wave.ndim==1 and wave.size>0 and
            np.isfinite(wave).all() and np.max(np.abs(wave))<1.0 and
            np.sqrt(np.mean(wave.astype('float64')**2))>1e-5)
        target=out/f'{dtype}-{i}.wav';sf.write(target,wave,sr,subtype='FLOAT')
        row=dict(dtype=dtype,fixture=i,seed=5192+i,tts_seconds=round(elapsed,6),
            audio_seconds=round(len(wave)/sr,6),signal_valid=valid,
            sample_rate=sr,**observed[0],wav_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        rows.append(row);print(json.dumps(row,sort_keys=True),flush=True)
    report=dict(dtype=dtype,device='cpu',threads=THREADS,parameter_dtypes=observed_dtypes,
        model=MODEL,model_revision=REVISION,fixtures=list(FIXTURES),cpu=hw,rows=rows,
        model_load_seconds=round(load_seconds,3),totals=validate_rows(rows,dtype),
        torch_version=torch.__version__,mkldnn_enabled=torch.backends.mkldnn.enabled,
        torch_cpu_capability=torch.backends.cpu.get_cpu_capability(),
        generation_config=json.loads((Path(model_path)/'generation_config.json').read_text()))
    dump(out/'result.json',report)


def run(model_path,out):
    require(not any(os.environ.get(n) for n in (
        'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')))
    hw=hardware();out=Path(out);out.mkdir(parents=True,exist_ok=False)
    dump(out/'hardware.json',hw)
    if not hw['bf16_instruction_available']:
        report=dict(schema=SCHEMA,status='unsupported_cpu_bf16_instructions',cpu=hw,
            bf16_attempted=False,observed_rtf_speedup=None,production_eligible=False)
    else:
        variants={}
        for dtype in DTYPES:
            try:
                result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'worker',
                    '--model',str(Path(model_path).resolve()),'--output',str(out/dtype),'--dtype',dtype],
                    check=False,timeout=600)
            except subprocess.TimeoutExpired:
                report=dict(schema=SCHEMA,status='precision_worker_timeout',failed_dtype=dtype,
                    cpu=hw,completed_variants=variants,observed_rtf_speedup=None,
                    production_eligible=False)
                break
            if result.returncode:
                report=dict(schema=SCHEMA,status='precision_worker_failed',failed_dtype=dtype,
                    returncode=result.returncode,cpu=hw,completed_variants=variants,
                    observed_rtf_speedup=None,production_eligible=False)
                break
            variants[dtype]=json.loads((out/dtype/'result.json').read_text())
        else:report=compare_results(variants,hw)
    dump(out/'summary.json',report);print(json.dumps(report,ensure_ascii=False,sort_keys=True),flush=True)
    return 0 if report['status'] in ('timing_trial_no_quality_equivalence','unsupported_cpu_bf16_instructions') else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('probe','run','worker'))
    p.add_argument('--model');p.add_argument('--output');p.add_argument('--dtype',choices=DTYPES)
    args=p.parse_args();validate_request()
    if args.mode=='probe':
        hw=hardware();print(json.dumps(hw,sort_keys=True),flush=True)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('supported='+str(hw['bf16_instruction_available']).lower()+'\n')
        return 0
    require(bool(args.model) and bool(args.output))
    if args.mode=='worker':require(args.dtype in DTYPES);worker(args.model,args.output,args.dtype);return 0
    require(args.dtype is None);return run(args.model,args.output)


if __name__=='__main__':raise SystemExit(main())
