"""Read-only re-recognition of five sealed TB-001 review recordings.

The production model, voice, checkpoints and pass/fail receipts are never changed.
No text is supplied to ASR as a prompt. Only fixed numeric counters leave offline.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import tempfile
import time

import recovery_diagnostics as d

HERE = Path(__file__).resolve().parent
WORKFLOW = d.REPO+'/.github/workflows/bora-checkpoint-recheck.yml@refs/heads/'+d.BRANCH
TARGETS = (43,47,52,53,55)
PADDING_MS = (0,500,1500)
SCHEMA = 'bora-sealed-asr-boundary-recheck/v1'


def require(value):
    if not value: raise ValueError('sealed_recheck_validation_failed')


def request():
    value=json.loads((HERE/'recheck-request.json').read_text())
    require(value == dict(schema=SCHEMA, incident=d.INCIDENT, unit_indices=list(TARGETS),
        padding_ms=list(PADDING_MS), purpose='read-only-existing-audio-asr-check',
        promote_units=False, resynthesize=False))
    return value


def validate_context(env,uid,euid):
    require(env.get('GITHUB_REPOSITORY')==d.REPO
        and env.get('GITHUB_REF')=='refs/heads/'+d.BRANCH
        and env.get('GITHUB_EVENT_NAME')=='push'
        and env.get('GITHUB_WORKFLOW_REF')==WORKFLOW
        and uid==euid and uid!=0)


def validate_targets(restored):
    require(all(index in restored for index in TARGETS))
    for index in TARGETS:
        receipt=restored[index]['receipt']
        require(receipt.get('status')=='blocked_requires_review'
            and receipt.get('review_file')=='review.wav'
            and receipt.get('review_only') is True
            and receipt.get('review_natural_eos') is True
            and not receipt.get('review_flags'))
        candidates=[c for c in receipt['candidates'] if c.get('sha256')==receipt['review_sha256']]
        require(len(candidates)==1 and candidates[0].get('natural_eos') is True
            and candidates[0].get('signal',{}).get('blocked') is False
            and isinstance(candidates[0].get('asr',{}).get('recognized'),str))
    return list(TARGETS)


def summarize(rows):
    require(isinstance(rows,list) and len(rows)==len(TARGETS)
        and [r['unit_index'] for r in rows]==list(TARGETS))
    for row in rows:
        require(set(row)=={'unit_index','checks','raw_matches_saved_recognition'})
        require(type(row['raw_matches_saved_recognition']) is bool)
        require([x['padding_ms'] for x in row['checks']]==list(PADDING_MS))
        for item in row['checks']:
            require(set(item)=={'padding_ms','character_errors','missing_critical_count','seconds'}
                and type(item['character_errors']) is int and item['character_errors']>=0
                and type(item['missing_critical_count']) is int and item['missing_critical_count']>=0
                and type(item['seconds']) in (int,float)
                and math.isfinite(item['seconds']) and item['seconds']>=0)
    exact=lambda x:x['character_errors']==0 and x['missing_critical_count']==0
    return dict(schema=SCHEMA,status='diagnostic_only_no_promotion',incident=d.INCIDENT,
        recordings_checked=len(rows),recognition_passes=len(rows)*len(PADDING_MS),
        exact_by_padding_ms={str(p):sum(exact(r['checks'][i]) for r in rows) for i,p in enumerate(PADDING_MS)},
        exact_in_both_padded_checks=sum(all(exact(x) for x in r['checks'][1:]) for r in rows),
        raw_recognition_reproduced=sum(r['raw_matches_saved_recognition'] for r in rows),
        rows=rows, production_status_changed=False, checkpoint_write_performed=False,
        synthesis_started=False, production_eligible=False,human_listening_verified=False,
        independent_asr_model_used=False,
        scope='Same pinned ASR with silent boundary padding; diagnostic sensitivity, not proof of speech correctness.')


def download_asr():
    from download_models import require_keyless, ASR_REPOSITORY, ASR_REVISION, ASR_REQUIRED, verify_assets
    require_keyless()
    os.environ.update(HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',DO_NOT_TRACK='1')
    from huggingface_hub import snapshot_download
    root=Path(os.environ['RUNNER_TEMP'])/'bora-recheck-asr'
    snapshot_download(ASR_REPOSITORY,revision=ASR_REVISION,local_dir=root,
        allow_patterns=list(ASR_REQUIRED),max_workers=4)
    verify_assets(root,ASR_REQUIRED)
    with open(os.environ['GITHUB_ENV'],'a') as f:f.write('BORA_ASR='+str(root)+'\n')
    print('{"status":"same_pinned_asr_downloaded","tts_model_downloaded":false}')


def inspect(directory,output):
    validate_context(os.environ,os.getuid(),os.geteuid())
    from namespace_guard import validate_snapshot
    validate_snapshot(os.getuid(),os.getuid(),socket.if_nameindex(),Path('/proc/net/route').read_text(),
        os.readlink('/proc/self/ns/net'),os.environ.get('BORA_HOST_NETNS',''),Path('/proc/net/ipv6_route').read_text())
    values=[os.environ.pop(n,'') for n in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')]
    source_key,media_key=[base64.b64decode(v,validate=True) for v in values]
    del values
    require(len(source_key)==len(media_key)==32 and source_key!=media_key)
    def deny(*args,**kwargs):raise RuntimeError('outbound_forbidden')
    socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
    import runtime as r
    from fp32_renderer import require_guard,verify_assets,ASR_REQUIRED,ASR_REVISION
    from pilot_render import canonical,transcript_score,signal_stats,file_hash
    require_guard()
    campaign,recipe,_,_=r.config()
    require(r.identity(recipe)==d.INCIDENT['recipe_sha256'])
    report=d.check_bundle(directory)
    with r.quiet():plan,_=r.source_plan(d.INCIDENT['lesson_id'],campaign,source_key)
    del source_key
    require(r.identity(plan)==report['plan_sha256'])
    def timeout(*args):raise TimeoutError('asr_recheck_deadline')
    signal.signal(signal.SIGALRM,timeout)
    rows=[]
    with tempfile.TemporaryDirectory(prefix='bora-private-recheck-',dir=os.environ['RUNNER_TEMP']) as temp:
        with r.quiet():
            restored=r.restore(directory,media_key,plan,d.INCIDENT['recipe_sha256'],
                d.INCIDENT['render_settings_sha256'],Path(temp))
        del media_key
        targets=validate_targets(restored)
        sample=restored[targets[0]]['receipt']['render_settings']
        require(sample['asr_revision']==ASR_REVISION)
        asset_hashes=verify_assets(Path(os.environ['BORA_ASR']),ASR_REQUIRED,ASR_REVISION,sample['asr_sha256'])
        require(all(restored[i]['receipt']['render_settings']['asr_sha256']==asset_hashes for i in targets))
        with r.quiet():
            import numpy as np
            import soundfile as sf
            import onnxruntime as ort
            from scipy.signal import resample_poly
            from faster_whisper import WhisperModel
            ort.disable_telemetry_events()
            model=WhisperModel(os.environ['BORA_ASR'],device='cpu',compute_type='int8',
                cpu_threads=1,num_workers=1,local_files_only=True)
        for index in targets:
            record=restored[index];receipt=record['receipt'];unit=plan['units'][index]
            path=record['path']
            require(file_hash(path)==receipt['review_sha256'])
            with r.quiet():wave,sr=sf.read(path,dtype='float32')
            require(sr==24000 and len(wave)==receipt['review_sample_count'] and not signal_stats(wave,sr)['blocked'])
            original=next(c for c in receipt['candidates'] if c.get('sha256')==receipt['review_sha256'])['asr']['recognized']
            audio=resample_poly(wave,16000//math.gcd(16000,sr),sr//math.gcd(16000,sr)).astype('float32')
            row=dict(unit_index=index,checks=[],raw_matches_saved_recognition=False)
            for padding in PADDING_MS:
                padded=np.pad(audio,(padding*16,padding*16)) if padding else audio
                started=time.monotonic();signal.alarm(180)
                try:
                    with r.quiet():
                        segments,_=model.transcribe(padded,language='ko',beam_size=5,temperature=0,
                            condition_on_previous_text=False,vad_filter=False,word_timestamps=True)
                        recognized=' '.join(s.text.strip() for s in segments)
                        score=transcript_score(unit['spoken'],recognized,unit.get('critical_terms',[]),max_cer=0.0)
                    if padding==0:row['raw_matches_saved_recognition']=canonical(recognized)==canonical(original)
                    row['checks'].append(dict(padding_ms=padding,character_errors=score['character_errors'],
                        missing_critical_count=len(score['missing_critical']),seconds=round(time.monotonic()-started,3)))
                finally:signal.alarm(0)
            rows.append(row)
        del model,restored
    result=summarize(rows)
    d.dump(output,result)
    print(json.dumps(result,sort_keys=True),flush=True)


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('download','model','inspect'))
    parser.add_argument('--directory');parser.add_argument('--output')
    args=parser.parse_args();request()
    if args.mode=='download':
        require(args.directory is not None and args.output is None);d.download(args.directory)
    elif args.mode=='model':
        require(args.directory is None and args.output is None);download_asr()
    else:
        require(args.directory is not None and args.output is not None);inspect(args.directory,args.output)


if __name__=='__main__':
    try:main()
    except Exception:
        print('{"status":"failed","reason":"sealed_recheck_validation_failed"}')
        raise SystemExit(1) from None
