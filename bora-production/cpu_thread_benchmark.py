"""Public FP32 Sohee same-model CPU thread trial; never production approval.

One model stays resident. Fixture zero uses 4/2/1 threads; fixture one uses 1/2/4.
Only intra-op thread count changes. Original explain instruction and Python
sampling remain identical; inter-op threads are fixed at one throughout.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import time

from basic_sohee_benchmark import FIXTURES, MODEL, REVISION, generation_args, observe_eos
from cpu_precision_benchmark import hardware

SCHEMA='sohee-public-cpu-thread/v1'
THREAD_COUNTS=(4,2,1)
ORDERS=(THREAD_COUNTS,tuple(reversed(THREAD_COUNTS)))


def require(ok):
    if not ok:raise ValueError('public_cpu_thread_benchmark_invalid')


def validate_request():
    obj=json.loads(Path(__file__).with_name('cpu-thread-request.json').read_text())
    require(obj==dict(schema=SCHEMA,request_id='public-sohee-fp32-threads-20261113-01',
        model_revision=REVISION,fixture_count=len(FIXTURES),thread_counts=list(THREAD_COUNTS),
        interop_threads=1,production=False,secrets=False))


def validate_native_threads(requested,actual,interop,parallel_info):
    require(type(requested) is int and requested in THREAD_COUNTS
        and type(actual) is int and actual==requested and type(interop) is int and interop==1
        and isinstance(parallel_info,str))
    observed={}
    for name in ('omp_get_max_threads','mkl_get_max_threads'):
        match=re.search(re.escape(name)+r'\(\)\s*:\s*([0-9]+)',parallel_info)
        require(match is not None)
        count=int(match[1]);require(count==requested);observed[name]=count
    return dict(intraop_threads=actual,interop_threads=interop,**observed)


def rss_snapshot():
    lines=Path('/proc/self/status').read_text().splitlines();out={}
    for key,target in (('VmRSS:','current_rss_kib'),('VmHWM:','peak_rss_kib')):
        line=next((l for l in lines if l.startswith(key)),None)
        require(line is not None and line.split()[-1]=='kB')
        out[target]=int(line.split()[1]);require(out[target]>0)
    return out


def summarize(rows):
    expected=[(fixture,threads) for fixture,order in enumerate(ORDERS) for threads in order]
    require(isinstance(rows,list) and len(rows)==len(expected)
        and [(r.get('fixture'),r.get('threads_requested')) for r in rows]==expected)
    for r in rows:
        require(type(r['fixture']) is int and r['seed']==5192+r['fixture']
            and r['threads_observed']==r['threads_requested'] and r['interop_threads']==1
            and r['dtype']=='float32' and r['device']=='cpu'
            and type(r['natural_eos']) is bool and type(r['signal_valid']) is bool
            and isinstance(r['wav_sha256'],str) and re.fullmatch(r'[a-f0-9]{64}',r['wav_sha256'])
            and isinstance(r['talker_sequence_sha256'],str)
            and re.fullmatch(r'[a-f0-9]{64}',r['talker_sequence_sha256'])
            and r['generation_args']==generation_args('production_style',FIXTURES[r['fixture']]))
        validate_native_threads(r['threads_requested'],r['threads_observed'],r['interop_threads'],r['parallel_info'])
        for key in ('tts_seconds','audio_seconds'):
            require(type(r[key]) in (int,float) and math.isfinite(r[key]) and r[key]>0)
    valid=all(r['natural_eos'] and r['signal_valid'] for r in rows)
    totals={}
    for n in THREAD_COUNTS:
        part=[r for r in rows if r['threads_requested']==n]
        tts=sum(r['tts_seconds'] for r in part);audio=sum(r['audio_seconds'] for r in part)
        totals[str(n)]=dict(tts_seconds=round(tts,6),audio_seconds=round(audio,6),
            synthesis_seconds_per_audio_second=round(tts/audio,6) if valid else None)
    baseline=totals['4']['synthesis_seconds_per_audio_second']
    return dict(schema=SCHEMA,status='timing_trial_no_quality_approval' if valid else 'incomplete_output_no_speed_claim',
        model=MODEL,model_revision=REVISION,device='cpu',dtype='float32',speaker='Sohee',
        interop_threads=1,thread_counts=list(THREAD_COUNTS),fixtures=list(FIXTURES),
        fixture_thread_orders=[list(x) for x in ORDERS],rows=rows,totals=totals,
        complete_natural_eos_and_signal=valid,
        observed_rtf_speedups_vs_4={str(n):round(baseline/totals[str(n)]['synthesis_seconds_per_audio_second'],4)
            if valid else None for n in THREAD_COUNTS},
        identical_wav_hashes_by_fixture={str(i):len({r['wav_sha256'] for r in rows if r['fixture']==i})==1
            for i in range(len(FIXTURES))},
        identical_talker_sequence_hashes_by_fixture={str(i):len({r['talker_sequence_sha256'] for r in rows if r['fixture']==i})==1
            for i in range(len(FIXTURES))},
        talker_sequence_hash_scope='Returned talker.generate sequences first row only; not full-codebook parity.',
        model_load_included_in_tts=False,asr_performed=False,dsp_performed=False,retries_performed=False,
        production_eligible=False,human_listening_verified=False,full_course_forecast_validated=False,
        caveat='One six-call trial on two short public fixtures, same resident FP32 model and sampler. Alternating thread order partially balances warmup but does not remove all order/cache/host-noise effects. Same thread settings can still produce different floating-point reductions; report WAV hash equality rather than assume it. This is raw synthesis throughput, not a full-course forecast or quality approval.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--model',required=True)
    p.add_argument('--output',required=True);args=p.parse_args();validate_request()
    require(not any(os.environ.get(n) for n in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')))
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',
        HF_HUB_DISABLE_IMPLICIT_TOKEN='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4')
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(4);torch.set_num_interop_threads(1)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    hw=hardware();started=time.perf_counter()
    model=Qwen3TTSModel.from_pretrained(str(Path(args.model).resolve()),device_map='cpu',dtype=torch.float32,
        attn_implementation='sdpa',low_cpu_mem_usage=True,local_files_only=True)
    model.model.eval();load_seconds=time.perf_counter()-started
    floating=sorted({str(x.dtype).removeprefix('torch.') for x in model.model.parameters() if x.is_floating_point()})
    require(floating==['float32'] and all(x.device.type=='cpu' for x in model.model.parameters()))
    after_load=rss_snapshot();rows=[]
    def deadline(*unused):raise TimeoutError('public_cpu_thread_sample_timeout')
    signal.signal(signal.SIGALRM,deadline)
    for i,text in enumerate(FIXTURES):
        for threads in ORDERS[i]:
            torch.set_num_threads(threads)
            actual=torch.get_num_threads();interop=torch.get_num_interop_threads()
            parallel_info=torch.__config__.parallel_info()
            native=validate_native_threads(threads,actual,interop,parallel_info)
            talker=model.model.talker;original=talker.generate;observed=[]
            eos_id=int(model.model.config.talker_config.codec_eos_token_id)
            def observe(*a,**kw):
                result=original(*a,**kw)
                tokens=result.sequences[0].tolist()
                observation=observe_eos(tokens,eos_id)
                observation['talker_sequence_sha256']=hashlib.sha256(json.dumps(tokens,separators=(',',':')).encode('ascii')).hexdigest()
                observed.append(observation);return result
            talker.generate=observe;torch.manual_seed(5192+i);signal.alarm(240)
            started=time.perf_counter()
            try:
                with torch.inference_mode():waves,sr=model.generate_custom_voice(**generation_args('production_style',text))
                elapsed=time.perf_counter()-started
            finally:signal.alarm(0);talker.generate=original
            require(len(waves)==1 and sr==24000 and len(observed)==1)
            wave=np.asarray(waves[0]);valid=bool(wave.ndim==1 and wave.size>0 and np.isfinite(wave).all()
                and np.max(np.abs(wave))<1 and np.sqrt(np.mean(wave.astype('float64')**2))>1e-5)
            target=out/f'fp32-threads-{threads}-fixture-{i}.wav';sf.write(target,wave,sr,subtype='FLOAT')
            row=dict(fixture=i,seed=5192+i,threads_requested=threads,threads_observed=actual,interop_threads=interop,
                dtype='float32',device='cpu',native_threads=native,parallel_info=parallel_info,
                tts_seconds=round(elapsed,6),audio_seconds=round(len(wave)/sr,6),signal_valid=valid,
                sample_rate=sr,**observed[0],wav_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                generation_args=generation_args('production_style',text),memory=rss_snapshot())
            rows.append(row);print(json.dumps(row,sort_keys=True),flush=True)
            (out/'partial-rows.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    report=summarize(rows)
    report.update(cpu=hw,model_load_seconds=round(load_seconds,3),remaining_floating_parameter_dtypes=floating,
        memory_after_load=after_load,memory_after_synthesis=rss_snapshot(),torch_version=torch.__version__,
        mkldnn_enabled=torch.backends.mkldnn.enabled,
        generation_config=json.loads((Path(args.model)/'generation_config.json').read_text()))
    (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,sort_keys=True),flush=True)


if __name__=='__main__':main()
