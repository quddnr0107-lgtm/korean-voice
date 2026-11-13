"""Public CPU FP32 vs selective-MLP dynamic INT8 trial, never production.

The original Python generate_custom_voice sampling, style, model, seeds and CPU
threads remain the same. Only the 84 Talker decoder MLP linear modules change.
Codec, speaker/text embeddings, attention, heads and CodePredictor are untouched.
"""
from __future__ import annotations
import argparse
import gc
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import hashlib

from basic_sohee_benchmark import FIXTURES, MODEL, REVISION, THREADS, generation_args, observe_eos
from cpu_precision_benchmark import hardware

SCHEMA='sohee-public-cpu-linear-quant/v1'
VARIANTS=('fp32_baseline','fp32_mlp_dynamic_int8')
LAYER_COUNT=28
PROJECTIONS=('gate_proj','up_proj','down_proj')


def require(ok):
    if not ok:raise ValueError('public_cpu_linear_quant_benchmark_invalid')


def expected_mlp_names():
    return {f'talker.model.layers.{i}.mlp.{p}' for i in range(LAYER_COUNT) for p in PROJECTIONS}


def validate_quant_inventory(variant,inventory):
    require(variant in VARIANTS and isinstance(inventory,list)
        and all(isinstance(r,dict) and isinstance(r.get('name'),str) for r in inventory))
    names=[r['name'] for r in inventory]
    require(len(names)==len(set(names)))
    if variant=='fp32_baseline':require(not names)
    else:
        require(set(names)==expected_mlp_names())
        require(all(r.get('class')=='torch.ao.nn.quantized.dynamic.modules.linear.Linear'
            and r.get('weight_dtype')=='torch.qint8' for r in inventory))
    return len(names)


def rss_snapshot(status=None):
    if status is None:status=Path('/proc/self/status').read_text()
    out={}
    for key,target in (('VmRSS:','current_rss_kib'),('VmHWM:','peak_rss_kib')):
        line=next((l for l in status.splitlines() if l.startswith(key)),None)
        require(line is not None and line.split()[-1]=='kB')
        value=int(line.split()[1]);require(value>0);out[target]=value
    return out


def validate_request():
    value=json.loads(Path(__file__).with_name('cpu-linear-quant-request.json').read_text())
    require(value==dict(schema=SCHEMA,request_id='public-sohee-fp32-mlp-int8-20261113-01',
        model_revision=REVISION,fixture_count=len(FIXTURES),threads=THREADS,
        quantized_modules=84,production=False,secrets=False))


def row_totals(rows,variant):
    require(isinstance(rows,list) and len(rows)==len(FIXTURES)
        and [r.get('fixture') for r in rows]==list(range(len(FIXTURES))))
    for i,r in enumerate(rows):
        require(type(r['fixture']) is int and r['seed']==5192+i and r['variant']==variant
            and type(r['natural_eos']) is bool and type(r['signal_valid']) is bool)
        for key in ('tts_seconds','audio_seconds'):
            require(type(r[key]) in (int,float) and math.isfinite(r[key]) and r[key]>0)
    valid=all(r['natural_eos'] and r['signal_valid'] for r in rows)
    tts=sum(r['tts_seconds'] for r in rows);audio=sum(r['audio_seconds'] for r in rows)
    return dict(tts_seconds=round(tts,6),audio_seconds=round(audio,6),
        complete_natural_eos_and_signal=valid,
        synthesis_seconds_per_audio_second=round(tts/audio,6) if valid else None)


def compare_results(variants,hw):
    require(set(variants)==set(VARIANTS))
    totals={}
    for variant,v in variants.items():
        require(v.get('variant')==variant and v.get('model_revision')==REVISION
            and v.get('threads')==THREADS and v.get('device')=='cpu'
            and v.get('fixtures')==list(FIXTURES) and v.get('cpu')==hw
            and v.get('remaining_floating_parameter_dtypes')==['float32']
            and v.get('generation_args')==[generation_args('production_style',t) for t in FIXTURES]
            and v.get('quantized_module_count')==validate_quant_inventory(variant,v.get('quantized_modules')))
        totals[variant]=row_totals(v['rows'],variant)
    valid=all(t['complete_natural_eos_and_signal'] for t in totals.values())
    return dict(schema=SCHEMA,status='timing_trial_no_quality_equivalence' if valid else 'incomplete_output_no_speed_claim',
        model=MODEL,model_revision=REVISION,speaker='Sohee',device='cpu',threads=THREADS,cpu=hw,
        fixtures=list(FIXTURES),style='existing_explain_instruction',
        sampling='unchanged existing production-style Python sampling with 1100-token safety cap',
        variants=variants,totals=totals,
        observed_rtf_speedup=round(totals[VARIANTS[0]]['synthesis_seconds_per_audio_second']/
            totals[VARIANTS[1]]['synthesis_seconds_per_audio_second'],4) if valid else None,
        quantized_module_counts={n:v['quantized_module_count'] for n,v in variants.items()},
        memory={n:v['memory'] for n,v in variants.items()},
        asr_performed=False,dsp_performed=False,retries_performed=False,
        production_eligible=False,quality_equivalence_verified=False,
        human_listening_verified=False,full_course_forecast_validated=False,
        caveat='One FP32-then-INT8 trial on two short public fixtures. Same Python sampler does not guarantee identical token samples after lossy quantization. Model load and quantization time are excluded from TTS timers. Current RSS and high-water RSS differ; high-water includes FP32 loading. ASR and listening review are still required.')


def dump(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def observed_model_manifest(model_path,required):
    """Observation only: this is not an independent trusted model hash pin."""
    root=Path(model_path).resolve()
    require(root.is_dir() and isinstance(required,dict) and required)
    files=[]
    for name in sorted(required):
        require(isinstance(name,str) and not Path(name).is_absolute() and '..' not in Path(name).parts)
        path=root/name
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root))
        with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        files.append(dict(path=name,bytes=path.stat().st_size,sha256=digest))
    return dict(schema='qwen-observed-model-sha256/v1',model=MODEL,model_revision=REVISION,
        basis='Observed bytes after revision-pinned download and existing validation; not an independent trust anchor.',
        independent_trust_anchor=False,required_file_count=len(required),files=files)


def module_inventory(module):
    return [dict(name=n,**{'class':type(m).__module__+'.'+type(m).__name__})
        for n,m in module.named_modules()]


def worker(model_path,out,variant):
    require(variant in VARIANTS)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
        OMP_NUM_THREADS=str(THREADS),MKL_NUM_THREADS=str(THREADS),OPENBLAS_NUM_THREADS=str(THREADS))
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(THREADS);torch.set_num_interop_threads(1)
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter()
    model=Qwen3TTSModel.from_pretrained(str(Path(model_path).resolve()),
        device_map='cpu',dtype=torch.float32,attn_implementation='sdpa',
        low_cpu_mem_usage=True,local_files_only=True)
    model.model.eval();load_seconds=time.perf_counter()-started
    memory={'after_fp32_load':rss_snapshot()}
    expected=expected_mlp_names();modules=dict(model.model.named_modules())
    require(all(n in modules and type(modules[n]) is torch.nn.Linear for n in expected))
    del modules
    gc.collect()
    quant_seconds=0.0
    if variant=='fp32_mlp_dynamic_int8':
        require('x86' in torch.backends.quantized.supported_engines)
        torch.backends.quantized.engine='x86'
        started=time.perf_counter()
        torch.ao.quantization.quantize_dynamic(model.model,qconfig_spec=expected,
            dtype=torch.qint8,inplace=True)
        quant_seconds=time.perf_counter()-started
        gc.collect()
    quantized=[]
    for name,module in model.model.named_modules():
        if isinstance(module,torch.ao.nn.quantized.dynamic.Linear):
            weight=module.weight()
            quantized.append(dict(name=name,**{'class':type(module).__module__+'.'+type(module).__name__},
                weight_dtype=str(weight.dtype),weight_shape=list(weight.shape)))
    quant_count=validate_quant_inventory(variant,quantized)
    floating=sorted({str(p.dtype).removeprefix('torch.') for p in model.model.parameters() if p.is_floating_point()})
    require(floating==['float32'] and all(p.device.type=='cpu' for p in model.model.parameters()))
    memory['after_optional_quantization']=rss_snapshot()
    inventory=module_inventory(model.model)
    dump(out/'module-inventory.json',inventory)
    print(json.dumps(dict(variant=variant,quantized_module_count=quant_count,
        remaining_floating_parameter_dtypes=floating,memory=memory)),flush=True)
    def deadline(*unused):raise TimeoutError('public_cpu_linear_quant_sample_timeout')
    signal.signal(signal.SIGALRM,deadline)
    rows=[]
    for i,text in enumerate(FIXTURES):
        talker=model.model.talker;original=talker.generate;observed=[]
        eos_id=int(model.model.config.talker_config.codec_eos_token_id)
        def observe(*a,**kw):
            result=original(*a,**kw)
            observed.append(observe_eos(result.sequences[0].tolist(),eos_id));return result
        talker.generate=observe;torch.manual_seed(5192+i);signal.alarm(240)
        started=time.perf_counter()
        try:
            with torch.inference_mode():waves,sr=model.generate_custom_voice(**generation_args('production_style',text))
            elapsed=time.perf_counter()-started
        finally:signal.alarm(0);talker.generate=original
        require(len(waves)==1 and sr==24000 and len(observed)==1)
        wave=np.asarray(waves[0]);valid=bool(wave.ndim==1 and wave.size>0 and
            np.isfinite(wave).all() and np.max(np.abs(wave))<1.0 and
            np.sqrt(np.mean(wave.astype('float64')**2))>1e-5)
        target=out/f'{variant}-{i}.wav';sf.write(target,wave,sr,subtype='FLOAT')
        row=dict(variant=variant,fixture=i,seed=5192+i,tts_seconds=round(elapsed,6),
            audio_seconds=round(len(wave)/sr,6),signal_valid=valid,
            sample_rate=sr,**observed[0],wav_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        rows.append(row);print(json.dumps(row,sort_keys=True),flush=True)
    memory['after_synthesis']=rss_snapshot()
    report=dict(variant=variant,device='cpu',threads=THREADS,
        remaining_floating_parameter_dtypes=floating,model=MODEL,model_revision=REVISION,
        fixtures=list(FIXTURES),cpu=hardware(),rows=rows,model_load_seconds=round(load_seconds,3),
        quantization_seconds=round(quant_seconds,3),quantized_module_count=quant_count,
        quantized_modules=quantized,module_inventory=inventory,memory=memory,
        quantized_engine=torch.backends.quantized.engine,torch_version=torch.__version__,
        mkldnn_enabled=torch.backends.mkldnn.enabled,
        generation_args=[generation_args('production_style',t) for t in FIXTURES],
        generation_config=json.loads((Path(model_path)/'generation_config.json').read_text()))
    dump(out/'result.json',report)


def run(model_path,out):
    require(not any(os.environ.get(n) for n in (
        'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')))
    hw=hardware();out=Path(out);out.mkdir(parents=True,exist_ok=False);variants={}
    for variant in VARIANTS:
        try:
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'worker',
                '--model',str(Path(model_path).resolve()),'--output',str(out/variant),'--variant',variant],
                check=False,timeout=600)
        except subprocess.TimeoutExpired:
            report=dict(schema=SCHEMA,status='quant_worker_timeout',failed_variant=variant,
                cpu=hw,completed_variants=variants,observed_rtf_speedup=None,production_eligible=False);break
        if result.returncode:
            report=dict(schema=SCHEMA,status='quant_worker_failed',failed_variant=variant,
                returncode=result.returncode,cpu=hw,completed_variants=variants,
                observed_rtf_speedup=None,production_eligible=False);break
        variants[variant]=json.loads((out/variant/'result.json').read_text())
    else:report=compare_results(variants,hw)
    dump(out/'summary.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('variants','completed_variants')},ensure_ascii=False,sort_keys=True),flush=True)
    return 0 if report['status']=='timing_trial_no_quality_equivalence' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('run','worker'));p.add_argument('--model',required=True)
    p.add_argument('--output',required=True);p.add_argument('--variant',choices=VARIANTS)
    args=p.parse_args();validate_request()
    if args.mode=='worker':require(args.variant in VARIANTS);worker(args.model,args.output,args.variant);return 0
    require(args.variant is None);return run(args.model,args.output)


if __name__=='__main__':raise SystemExit(main())
