"""Course-by-course FP32 production. Private processing is offline and sealed.

Network transport is a separate keyless command. Public manifests contain only
IDs, counts and fingerprints; source, ASR text and candidate audio stay sealed.
"""
from __future__ import annotations
import argparse
from collections import Counter
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
import tempfile
import time
import traceback
import zipfile
import zlib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.append(str(ROOT / 'research/reading-quality'))
import lecture_crypto as crypto
from lecture_pack import seal_part, open_part, make_pack_manifest, validate_pack
from namespace_guard import validate_snapshot
from execution_profiles import request_filename, workflow_filename, validate_profile, validate_request

REPO = 'quddnr0107-lgtm/korean-voice'
BRANCH = 'integrated-lectures-premium-20261028'
KEY_ID = 'integrated-lectures-media-v1'
LIMITS = {'TB':25, 'RL':34, 'DR':78, 'MS':166}
MAX_UNIT = 32 * 1024 * 1024
MAX_BUNDLE = 512 * 1024 * 1024
HASH = re.compile('[a-f0-9]{64}')


def enc(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def identity(value):
    return sha(enc(value))


def check(value, code):
    if not value:
        raise ValueError(code)


def read_json(path, cap=16*1024*1024):
    path = Path(path)
    check(path.is_file() and not path.is_symlink() and path.stat().st_size <= cap, 'json_file_bounds')
    return json.loads(path.read_bytes())


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    check(not path.is_symlink(), 'output_symlink')
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.pending-', delete=False) as f:
        temp = Path(f.name)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def quiet():
    sys.stdout.flush(); sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    dev = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(dev,1); os.dup2(dev,2)
        yield
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        os.dup2(saved[0],1); os.dup2(saved[1],2)
        for fd in saved + [dev]:
            os.close(fd)


def config(profile='eager'):
    campaign = read_json(HERE/'campaign.json')
    recipe = read_json(HERE/'recipe.json')
    request = read_json(HERE/request_filename(profile))
    validate_request(request, profile)
    check(campaign.get('schema') == 'bora-fp32-campaign/v1' and campaign.get('authorized') is True,
          'campaign_authorization')
    check(campaign.get('repository') == REPO and campaign.get('branch') == BRANCH
          and campaign.get('environment') == 'admin-law-premium-production'
          and campaign.get('courses') == LIMITS and campaign.get('delete_existing') is False
          and campaign.get('new_paid_services') is False, 'campaign_scope')
    check(campaign.get('recipe_sha256') == identity(recipe), 'recipe_pin')
    check(request.get('schema') == 'bora-fp32-request/v1'
          and request.get('campaign_sha256') == identity(campaign), 'request_pin')
    check(request.get('course') in LIMITS and type(request.get('retry_blocked')) is bool,
          'request_course')
    expected_ids = {f'{course}-{n:03d}' for course,count in LIMITS.items() for n in range(1,count+1)}
    rows = campaign.get('inputs', [])
    check(len(rows)==303 and {r['id'] for r in rows} == expected_ids, 'campaign_inventory')
    for row in rows:
        ident = row['id']; crypto.namespace_for(ident)
        folder = 'lecture-premium' if not ident.startswith('MS-') else (
            'lecture-military-a' if int(ident[3:])<=83 else 'lecture-military-b')
        check(row.get('source_directory') == folder and row.get('file') == ident+'.sealed.json'
              and HASH.fullmatch(row.get('cipher_sha256','')), 'source_inventory_pin')
    course_ids = [f"{request['course']}-{n:03d}" for n in range(1,LIMITS[request['course']]+1)]
    selected = request.get('lesson_ids', course_ids)
    check(isinstance(selected,list) and selected and len(selected)==len(set(selected))
          and set(selected)<=set(course_ids), 'request_lessons')
    if profile == 'single_pass' and 'selected_shards' in request:
        subset = request['selected_shards']
        check(isinstance(subset,list) and 0<len(subset)<=256 and
              all(isinstance(item,dict) and set(item)=={'lesson_id','shard'} and
                  item['lesson_id'] in selected and type(item['shard']) is int and
                  0<=item['shard']<=999 for item in subset), 'single_pass_shard_scope')
    execution_shard_chars(recipe, request)  # Validate scheduling before key access.
    return campaign, recipe, request, sorted(selected)


def protected_keys(media=False, profile='eager'):
    check(os.environ.get('GITHUB_REPOSITORY')==REPO
          and os.environ.get('GITHUB_REF')=='refs/heads/'+BRANCH
          and os.environ.get('GITHUB_EVENT_NAME')=='push'
          and os.environ.get('GITHUB_WORKFLOW_REF')==REPO+'/.github/workflows/'+workflow_filename(profile)+'@refs/heads/'+BRANCH,
          'protected_workflow_context')
    validate_snapshot(os.getuid(), os.getuid(), socket.if_nameindex(),
        Path('/proc/net/route').read_text(), os.readlink('/proc/self/ns/net'),
        os.environ.get('BORA_HOST_NETNS',''), Path('/proc/net/ipv6_route').read_text())
    check(os.geteuid()==os.getuid() and os.getuid()!=0, 'unprivileged_namespace')
    names = ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')
    values = [os.environ.pop(name, '') for name in names]
    keys = [base64.b64decode(v, validate=True) if v else b'' for v in values]
    del values
    check(len(keys[0])==32 and (not media or len(keys[1])==32), 'protected_key_bounds')
    check(not keys[1] or keys[0]!=keys[1], 'separate_key_domains')
    def deny(*args, **kwargs):
        raise RuntimeError('outbound_forbidden')
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    return keys


def source_plan(ident, campaign, source_key):
    from planning import build_plan
    row = next(r for r in campaign['inputs'] if r['id']==ident)
    bundle = read_json(ROOT/row['source_directory']/'inputs'/row['file'])
    cipher = base64.b64decode(bundle['cipher_b64'], validate=True)
    check(sha(cipher)==row['cipher_sha256'], 'source_cipher_pin')
    compressed = crypto.open_input(source_key, read_json(HERE/'receiver-public.json'),
        read_json(HERE/'receiver-wrapped.json'), bundle['record'], cipher, ident)
    inflater=zlib.decompressobj()
    raw=inflater.decompress(compressed, 16*1024*1024+1)
    check(len(raw)<=16*1024*1024 and inflater.eof and not inflater.unused_data
          and not inflater.unconsumed_tail, 'source_expansion')
    payload=json.loads(raw)
    check(set(payload)=={'version','course','source_sha256','lesson','recipe'}
          and payload['version']=='integrated-lecture-input/v1'
          and crypto.namespace_for(ident)=='integrated-lectures/v1/'+payload['course']
          and payload['lesson']['id']==ident
          and payload['recipe']==read_json(ROOT/row['source_directory']/'recipe.json'), 'source_payload_scope')
    chunks=payload['lesson']['chunks']; cursor=0
    check(isinstance(chunks,list) and chunks, 'source_chunks')
    for index,chunk in enumerate(chunks):
        check(chunk['index']==index and chunk['start_cp']==cursor
              and chunk['end_cp']==cursor+len(chunk['text'])
              and sha(chunk['text'].encode())==chunk['sha256'], 'source_chunk_coverage')
        cursor=chunk['end_cp']
    source=''.join(c['text'] for c in chunks)
    check(sha(source.encode())==payload['lesson']['source_sha256'], 'source_plain_pin')
    return build_plan(ident, source), payload


def execution_shard_chars(recipe, request):
    """Execution-only fan-out; never change recipe/plan/audio fingerprints."""
    default = recipe['shard_chars']
    target = request.get('execution_shard_chars', default)
    check(type(default) is int and type(target) is int and 200 <= target <= default,
          'execution_shard_chars_bounds')
    return target


def pending_units(rows, begin, end, retry_blocked):
    # rows must come from authenticated restore(), never a public shard report.
    return [i for i in range(begin, end) if i not in rows or
            (retry_blocked and rows[i]['receipt'].get('status') != 'passed')]


def shards(plan, target=4000):
    rows=[]; start=0; chars=0
    for index,unit in enumerate(plan['units']):
        size=len(unit['source'])
        if chars and chars+size>target:
            rows.append(dict(lesson_id=plan['lesson_id'],shard=len(rows),start=start,end=index))
            start=index; chars=0
        chars+=size
    rows.append(dict(lesson_id=plan['lesson_id'],shard=len(rows),start=start,end=len(plan['units'])))
    return rows


def public_plan(args, profile='eager'):
    campaign,recipe,request,ids=config(profile)
    check(HASH.fullmatch(args.settings_sha or ''), 'preflight_settings_hash')
    source_key,_=protected_keys(profile=profile)
    target_chars=execution_shard_chars(recipe,request)
    records=[]; matrix=[]; review_counts=Counter(); costs={}
    for ident in ids:
        plan,_=source_plan(ident,campaign,source_key)
        review_counts.update(flag for unit in plan['units'] for flag in set(unit.get('review',[])+unit.get('review_flags',[])))
        groups=shards(plan,target_chars)
        for group in groups:
            # Private cost estimate: favor long spoken input plus per-unit work.
            # Only IDs/ranges, never manuscript or per-unit text, leave this job.
            costs[(ident,group['shard'])]=sum(
                max(len(u['source']),len(u.get('spoken',u['source'])))+32
                for u in plan['units'][group['start']:group['end']])
        records.append(dict(lesson_id=ident,source_sha256=plan['source_sha256'],
            plan_sha256=identity(plan),unit_count=len(plan['units']),paragraph_count=len(plan['paragraphs']),
            review_units=sum(bool(u.get('review') or u.get('review_flags')) for u in plan['units']),shards=groups))
        matrix.extend(groups)
    selected=request.get('selected_shards')
    if selected is not None:
        check(isinstance(selected,list) and selected, 'selected_shards')
        wanted={(v['lesson_id'],v['shard']) for v in selected}
        check(len(wanted)==len(selected) and wanted<={(v['lesson_id'],v['shard']) for v in matrix}, 'selected_shards')
        matrix=[v for v in matrix if (v['lesson_id'],v['shard']) in wanted]
    check(0<len(matrix)<=256, 'matrix_requires_lesson_batch')
    matrix.sort(key=lambda x:(-costs[(x['lesson_id'],x['shard'])],x['lesson_id'],x['shard']))
    manifest=dict(schema='bora-fp32-public-plan/v1',course=request['course'],
        campaign_sha256=identity(campaign),recipe_sha256=identity(recipe),
        request_sha256=identity(request),render_settings_sha256=args.settings_sha,lessons=records)
    if profile == 'single_pass':
        manifest['complete_course'] = len(ids) == LIMITS[request['course']]
    atomic(args.output,enc(manifest))
    unresolved=sum(v['review_units'] for v in records)
    print(json.dumps(dict(status='sources_authenticated',course=request['course'],lessons=len(ids),
        shards=len(matrix),units=sum(v['unit_count'] for v in records),
        review_units=unresolved,review_counts=dict(review_counts))),flush=True)
    # Resolve manuscript pronunciation before spending a full matrix of CPU.
    check(unresolved==0, 'source_review_required_before_render')
    if args.github_output:
        with open(args.github_output,'a') as f:
            f.write('matrix='+enc(dict(include=matrix)).decode()+'\n')
            f.write('lessons='+enc(dict(include=[dict(lesson_id=i) for i in ids])).decode()+'\n')
            f.write('course='+request['course']+'\n')
            if profile == 'single_pass':
                f.write('complete_course='+str(manifest['complete_course']).lower()+'\n')


def matched_plan(args, campaign, recipe, request, key):
    public=read_json(args.plan)
    check(public.get('schema')=='bora-fp32-public-plan/v1' and public.get('campaign_sha256')==identity(campaign)
          and public.get('recipe_sha256')==identity(recipe) and public.get('request_sha256')==identity(request)
          and public.get('course')==request['course'], 'public_plan_pin')
    check(args.lesson_id in [r['lesson_id'] for r in public['lessons']], 'planned_lesson_scope')
    plan,payload=source_plan(args.lesson_id,campaign,key)
    row=next(r for r in public['lessons'] if r['lesson_id']==args.lesson_id)
    check(row['source_sha256']==plan['source_sha256'] and row['plan_sha256']==identity(plan)
          and row['unit_count']==len(plan['units']) and row['shards']==shards(plan,execution_shard_chars(recipe,request)), 'private_plan_reproduction')
    return public,plan,payload,row


def seal_unit(media_key,plan,index,receipt,directory,recipe_hash,settings_hash):
    receipt=dict(receipt,lesson_id=plan['lesson_id'],unit_index=index,
                 plan_sha256=identity(plan),recipe_sha256=recipe_hash)
    check(receipt.get('render_settings_sha256')==settings_hash, 'receipt_settings_pin')
    plain=io.BytesIO()
    with zipfile.ZipFile(plain,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        z.writestr('receipt.json',enc(receipt))
        for field in ('file','review_file'):
            if field in receipt:
                filename=receipt[field]
                check(filename in ('final.wav','review.wav'), 'unit_audio_name')
                path=Path(directory)/filename
                check(not path.is_symlink() and 0<path.stat().st_size<=16*1024*1024, 'unit_audio_bounds')
                expected=receipt['sha256' if field=='file' else 'review_sha256']
                check(sha(path.read_bytes())==expected, 'unit_audio_integrity')
                z.write(path,filename)
    check(len(plain.getvalue())<=MAX_UNIT, 'unit_checkpoint_size')
    manifest,cipher=crypto.seal(media_key,plain.getvalue(),plan['lesson_id'],'checkpoint',KEY_ID)
    return dict(version='bora-fp32-unit-envelope/v1',lesson_id=plan['lesson_id'],unit_index=index,
        plan_sha256=identity(plan),recipe_sha256=recipe_hash,render_settings_sha256=settings_hash,
        manifest=manifest,cipher_b64=base64.b64encode(cipher).decode('ascii'))


def open_unit(envelope,media_key,plan,recipe_hash,settings_hash,destination):
    check(envelope.get('version')=='bora-fp32-unit-envelope/v1'
          and envelope.get('lesson_id')==plan['lesson_id']
          and type(envelope.get('unit_index')) is int
          and 0<=envelope['unit_index']<len(plan['units']), 'checkpoint_scope')
    # A different source/configuration is ineligible for reuse, never silently mixed.
    if (envelope.get('plan_sha256')!=identity(plan) or envelope.get('recipe_sha256')!=recipe_hash
            or envelope.get('render_settings_sha256')!=settings_hash):
        return None
    index=envelope['unit_index']; unit=plan['units'][index]
    cipher=base64.b64decode(envelope['cipher_b64'],validate=True)
    plain,_=crypto.open_range(media_key,envelope['manifest'],cipher,expected_id=plan['lesson_id'],
                             expected_kind='checkpoint',expected_key_id=KEY_ID)
    with zipfile.ZipFile(io.BytesIO(plain)) as z:
        names=z.namelist()
        check(len(names)==len(set(names)) and 'receipt.json' in names
              and set(names)<={'receipt.json','final.wav','review.wav'}
              and sum(v.file_size for v in z.infolist())<=MAX_UNIT, 'checkpoint_zip_bounds')
        receipt=json.loads(z.read('receipt.json'))
        expected=dict(lesson_id=plan['lesson_id'],unit_index=index,plan_sha256=identity(plan),
            recipe_sha256=recipe_hash,render_settings_sha256=settings_hash,unit_id=unit['id'],
            source_sha256=unit['source_sha256'],spoken_sha256=sha(unit['spoken'].encode()),
            unit_key=identity(dict(unit=unit,render_settings_sha256=settings_hash)))
        check(all(receipt.get(k)==v for k,v in expected.items()), 'authenticated_checkpoint_identity')
        out=Path(destination)/f'u{index:05d}'
        out.mkdir(parents=True,exist_ok=True,mode=0o700)
        for field,hash_field in (('file','sha256'),('review_file','review_sha256')):
            if field in receipt:
                name=receipt[field]
                check(name in ('final.wav','review.wav') and name in names, 'checkpoint_audio_name')
                audio=z.read(name)
                check(sha(audio)==receipt[hash_field], 'checkpoint_audio_hash')
                atomic(out/name,audio)
    return dict(unit_index=index,receipt=receipt,path=out/receipt.get('file','review.wav'))


def restore(directory,media_key,plan,recipe_hash,settings_hash,work,*,unit_range=None,profile='eager'):
    """Restore requested private units after checking the entire public bundle.

    Probe/render may request a validated shard range to avoid decrypting and
    writing unrelated PCM. The default remains full restoration for assembly.
    Public inventory is never a QA approval or a substitute for authenticating
    each requested unit through open_unit().
    """
    if unit_range is not None:
        check(isinstance(unit_range,tuple) and len(unit_range)==2
              and all(type(v) is int for v in unit_range)
              and 0<=unit_range[0]<unit_range[1]<=len(plan['units']), 'resume_unit_range')
    restored={}
    for bundle in sorted(Path(directory).glob(plan['lesson_id']+'-s*-r*-a*.zip')):
        check(not bundle.is_symlink() and bundle.stat().st_size<=MAX_BUNDLE, 'resume_bundle_bounds')
        if unit_range is not None:
            # Validate every member, public pin, ciphertext hash and report
            # count before using any untrusted index to skip private work.
            from transport import validate_bundle
            if profile == 'single_pass':
                report=validate_bundle(bundle,plan['lesson_id'].split('-',1)[0],profile=profile)
            else:
                report=validate_bundle(bundle,plan['lesson_id'].split('-',1)[0])
            if (report['plan_sha256']!=identity(plan) or report['recipe_sha256']!=recipe_hash
                    or report['render_settings_sha256']!=settings_hash):
                continue
            check(report['lesson_id']==plan['lesson_id']
                  and 0<=report['start']<report['end']<=len(plan['units']), 'resume_report_scope')
        with zipfile.ZipFile(bundle) as z:
            names=z.namelist()
            check(len(names)==len(set(names)) and sum(i.file_size for i in z.infolist())<=MAX_BUNDLE
                  and all(n=='report.json' or re.fullmatch('unit-[0-9]{5}\\.sealed\\.json',n) for n in names),
                  'resume_bundle_members')
            for name in names:
                if name=='report.json': continue
                check(z.getinfo(name).file_size<=MAX_UNIT*2, 'envelope_size')
                envelope=json.loads(z.read(name))
                index=envelope.get('unit_index')
                if unit_range is not None and not unit_range[0]<=index<unit_range[1]:
                    continue
                if index in restored and restored[index]['receipt'].get('status')=='passed':
                    continue
                row=open_unit(envelope,media_key,plan,recipe_hash,settings_hash,work)
                if row: restored[index]=row
    return restored


def alarm(signum,frame):
    raise TimeoutError('unit_deadline')


def terminate(signum,frame):
    raise InterruptedError('termination_requested')


def resume_probe(args, profile='eager'):
    """Check encrypted reuse offline BEFORE heavy runtime/model preparation."""
    campaign,recipe,request,ids=config(profile)
    check_lesson_scope(args, request, ids, profile)
    source_key,media_key=protected_keys(media=True,profile=profile)
    public,plan,_,row=matched_plan(args,campaign,recipe,request,source_key)
    del source_key
    check(type(args.shard) is int and 0<=args.shard<len(row['shards']), 'shard_scope')
    group=row['shards'][args.shard]; begin,end=group['start'],group['end']
    with tempfile.TemporaryDirectory(prefix='bora-probe-',dir=os.environ.get('RUNNER_TEMP')) as td:
        restored=restore(args.resume_dir,media_key,plan,identity(recipe),
                         public['render_settings_sha256'],Path(td)/'restored',unit_range=(begin,end),
                         profile=profile)
        rows={i:v for i,v in restored.items() if begin<=i<end}
        pending=pending_units(rows,begin,end,request['retry_blocked'])
        passed=sum(v['receipt'].get('status')=='passed' for v in rows.values())
        result=dict(schema='bora-fp32-resume-probe/v1',lesson_id=plan['lesson_id'],
                    shard=args.shard,start=begin,end=end,passed=passed,
                    blocked=len(rows)-passed,pending=len(pending),
                    needs_synthesis=bool(pending),request_sha256=identity(request),
                    plan_sha256=identity(plan),recipe_sha256=identity(recipe),
                    render_settings_sha256=public['render_settings_sha256'])
    # Plaintext temporary files and key-bearing child process are not retained.
    atomic(args.output,enc(result))
    if args.github_output:
        with open(args.github_output,'a') as f:
            f.write('needs_synthesis='+str(bool(pending)).lower()+'\n')
    print(json.dumps(result),flush=True)


def check_lesson_scope(args, request, ids, profile):
    if profile == 'compiled':
        check(args.lesson_id in ids, 'compiled_lesson_scope')
    if profile == 'single_pass':
        check(args.lesson_id in ids, 'single_pass_lesson_scope')
        selected = request.get('selected_shards')
        if selected is not None and hasattr(args, 'shard'):
            check({'lesson_id': args.lesson_id, 'shard': args.shard} in selected,
                  'single_pass_selected_shard')


def make_renderer(recipe, profile='eager'):
    """Import and load heavy dependencies only when synthesis is required."""
    validate_profile(profile)
    if profile == 'single_pass':
        from single_pass_renderer import SinglePassRenderer
        return SinglePassRenderer(os.environ['BORA_MODEL'],
                                  max_new_tokens=recipe['max_new_tokens'], max_attempts=1)
    if profile == 'compiled':
        from compiled_fp32_renderer import CompiledFP32Renderer as Renderer
    else:
        from fp32_renderer import FP32Renderer as Renderer
    return Renderer(os.environ['BORA_MODEL'],os.environ['BORA_ASR'],max_attempts=recipe['max_attempts'])


def close_renderer(renderer):
    close=getattr(renderer,'close',None)
    if callable(close):
        close()


def render(args, profile='eager'):
    campaign,recipe,request,ids=config(profile)
    check_lesson_scope(args, request, ids, profile)
    source_key,media_key=protected_keys(media=True,profile=profile)
    public,plan,_,row=matched_plan(args,campaign,recipe,request,source_key)
    del source_key
    check(type(args.shard) is int and 0<=args.shard<len(row['shards']), 'shard_scope')
    shard=row['shards'][args.shard]; begin,end=shard['start'],shard['end']
    output=Path(args.output); output.mkdir(parents=True,exist_ok=True,mode=0o700)
    check(not list(output.iterdir()), 'fresh_cipher_output')
    settings_hash=public['render_settings_sha256']; recipe_hash=identity(recipe)
    run=os.environ.get('GITHUB_RUN_ID',''); attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
    check(run.isdigit() and attempt.isdigit(), 'run_identity')
    name=f'{plan["lesson_id"]}-s{args.shard:03d}-r{run}-a{attempt}.zip'
    started=time.monotonic(); timed_out=0; failed=False
    signal.signal(signal.SIGTERM,terminate)
    envelopes={}
    with tempfile.TemporaryDirectory(prefix='bora-private-',dir=os.environ.get('RUNNER_TEMP')) as td:
        work=Path(td)
        restored=restore(args.resume_dir,media_key,plan,recipe_hash,settings_hash,work/'restored',
                         unit_range=(begin,end),profile=profile)
        rows={i:v for i,v in restored.items() if begin<=i<end}
        pending=pending_units(rows,begin,end,request['retry_blocked'])
        renderer=None
        try:
            if pending:
                with quiet():
                    renderer=make_renderer(recipe,profile)
                check(renderer.settings_sha256==settings_hash, 'preflight_renderer_fingerprint')
                signal.signal(signal.SIGALRM,alarm)
                for index in pending:
                    if time.monotonic()-started > recipe['soft_minutes']*60-recipe['unit_timeout_seconds']:
                        break
                    target=work/f'new-{index:05d}'
                    try:
                        signal.alarm(recipe['unit_timeout_seconds'])
                        with quiet(): receipt=renderer.render_unit(plan['units'][index],target)
                        envelope=seal_unit(media_key,plan,index,receipt,target,recipe_hash,settings_hash)
                        # Encrypt and fsync immediately after every completed unit.
                        path=work/f'unit-{index:05d}.sealed.json'
                        atomic(path,enc(envelope)); envelopes[index]=path
                        rows[index]=dict(receipt=receipt)
                    except TimeoutError:
                        timed_out+=1
                        break
                    finally:
                        signal.alarm(0)
                    print(json.dumps(dict(lesson_id=plan['lesson_id'],shard=args.shard,
                        unit_index=index,status=receipt['status'])),flush=True)
        except BaseException:
            failed=True
            raise
        finally:
            try:
                if renderer is not None:
                    with quiet(): close_renderer(renderer)
            finally:
                passed=sum(v['receipt'].get('status')=='passed' for v in rows.values())
                blocked=len(rows)-passed
                report=dict(schema='bora-fp32-shard-report/v1',lesson_id=plan['lesson_id'],shard=args.shard,
                    start=begin,end=end,passed=passed,blocked=blocked,pending=end-begin-len(rows),
                    timed_out=timed_out,recipe_sha256=recipe_hash,plan_sha256=identity(plan),
                    render_settings_sha256=settings_hash,run_id=run,run_attempt=attempt)
                temp=output/(name+'.pending')
                with zipfile.ZipFile(temp,'w',zipfile.ZIP_STORED) as z:
                    z.writestr('report.json',enc(report))
                    for index,path in sorted(envelopes.items()): z.write(path,path.name)
                temp.replace(output/name)
                print(json.dumps(report),flush=True)
    check(not failed, 'render_failed')


def assemble(args, profile='eager'):
    campaign,recipe,request,ids=config(profile)
    check_lesson_scope(args, request, ids, profile)
    source_key,media_key=protected_keys(media=True,profile=profile)
    public,plan,payload,_=matched_plan(args,campaign,recipe,request,source_key)
    del source_key
    settings_hash=public['render_settings_sha256']; recipe_hash=identity(recipe)
    with tempfile.TemporaryDirectory(prefix='bora-assemble-',dir=os.environ.get('RUNNER_TEMP')) as td:
        work=Path(td)
        restored=restore(args.resume_dir,media_key,plan,recipe_hash,settings_hash,work/'restored',
                         profile=profile)
        check(set(restored)==set(range(len(plan['units']))) and
              all(v['receipt'].get('status')=='passed' for v in restored.values()), 'lesson_not_complete')
        from assemble import assemble_lesson
        assembly_policy = {'profile': profile} if profile == 'single_pass' else {}
        with quiet():
            metadata=assemble_lesson(plan,list(restored.values()),work/'assembled',
                expected_recipe_sha256=recipe_hash,expected_render_settings_sha256=settings_hash,
                title=payload['lesson'].get('title'),
                unit_gap_ms=recipe['unit_gap_ms'],paragraph_gap_ms=recipe['paragraph_gap_ms'],
                max_part_seconds=recipe['max_part_seconds'], **assembly_policy)
        pack_id=os.urandom(16).hex(); offset=0; parts=[]; ciphertext=[]
        for part in metadata['audio_parts']:
            raw=(work/'assembled'/part['file_name']).read_bytes()
            check(sha(raw)==part['audio_sha256'],'assembled_part_hash')
            manifest,cipher=seal_part(media_key,raw,plan['lesson_id'],pack_id,part['index'],
                part['start_sample'],part['end_sample'],offset,KEY_ID)
            check(open_part(media_key,manifest,cipher,expected_id=plan['lesson_id'],
                expected_part_index=part['index'],expected_pack_id=pack_id)==raw,'pack_roundtrip')
            parts.append(manifest); ciphertext.append(cipher); offset+=len(cipher)
            part.pop('file_name')
        audio=b''.join(ciphertext)
        audio_manifest=make_pack_manifest(plan['lesson_id'],pack_id,parts,audio)
        validate_pack(audio_manifest,audio)
        metadata.update(audio_pack_sha256=sha(audio),sealed_input_hash=identity(payload),
            course=payload['course'],lesson_source=payload['lesson'],narration_text=plan['source'])
        content_manifest,content=crypto.seal(media_key,enc(metadata),plan['lesson_id'],'private-metadata',KEY_ID)
        recovered,_=crypto.open_range(media_key,content_manifest,content,expected_id=plan['lesson_id'],
            expected_kind='private-metadata',expected_key_id=KEY_ID)
        check(recovered==enc(metadata),'metadata_roundtrip')
        output=Path(args.output); output.mkdir(parents=True,exist_ok=True,mode=0o700)
        check(not list(output.iterdir()),'fresh_assembly_output')
        atomic(output/f"{plan['lesson_id']}.audio.{pack_id}.bin",audio)
        atomic(output/f"{plan['lesson_id']}.content.{content_manifest['object_id']}.bin",content)
        atomic(output/(plan['lesson_id']+'.descriptor.json'),enc(dict(version='integrated-lecture-output/v2',
            lesson_id=plan['lesson_id'],audio=audio_manifest,content=content_manifest)))
        print(json.dumps(dict(status='lesson_assembled_and_sealed',lesson_id=plan['lesson_id'],
            duration_s=metadata['validation']['duration_s'],units=len(plan['units']),parts=len(parts))))


def make_index(args, profile='eager'):
    check(args.course in LIMITS,'index_course')
    validate_profile(profile)
    check(profile!='compiled','compiled_course_index_forbidden')
    directory=Path(args.directory)
    if profile == 'single_pass':
        from transport import tag_for
        tag=tag_for(args.course, True, profile=profile)
    else:
        tag='bora-fp32-'+args.course.lower()+'-v1-20261112'
    expected={f'{args.course}-{i:03d}' for i in range(1,LIMITS[args.course]+1)}
    check({p.name.removesuffix('.descriptor.json') for p in directory.glob('*.descriptor.json')}==expected,
          'course_not_complete')
    rows=[]
    for ident in sorted(expected):
        path=directory/(ident+'.descriptor.json'); d=read_json(path)
        check(set(d)=={'version','lesson_id','audio','content'} and d['version']=='integrated-lecture-output/v2'
              and d['lesson_id']==ident, 'index_descriptor')
        audio=directory/f"{ident}.audio.{d['audio']['object_id']}.bin"
        content=directory/f"{ident}.content.{d['content']['object_id']}.bin"
        check(audio.is_file() and content.is_file() and not audio.is_symlink() and not content.is_symlink(),
              'index_missing_objects')
        validate_pack(d['audio'],audio.read_bytes())
        content_bytes=content.read_bytes()
        crypto.validate(d['content'],content_bytes)
        crypto._expected(d['content'],ident,'private-metadata',KEY_ID)
        for index,expected_hash in enumerate(d['content']['cipher_sha256']):
            begin=index*(crypto.CHUNK+16)
            check(sha(content_bytes[begin:begin+crypto.CHUNK+16])==expected_hash, 'index_content_hash')
        rows.append(dict(id=ident,descriptor_url=f'https://github.com/{REPO}/releases/download/{tag}/{ident}.descriptor.json',sha256=sha(path.read_bytes())))
    raw=enc(dict(version='integrated-lecture-index/v2',release_tag=tag,count=len(rows),lessons=rows))
    atomic(directory/'index.json',raw)
    print(json.dumps(dict(status='complete_course_index',course=args.course,count=len(rows),index_sha256=sha(raw))))


def preflight(args, profile='eager'):
    _,recipe,request,_=config(profile)
    check(not any(os.environ.get(v) for v in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')),
          'keyless_preflight')
    from planning import build_plan
    from assemble import assemble_lesson
    source='안녕하세요. 오늘은 차근차근 설명하겠습니다.'
    plan=build_plan('TB-002' if profile=='compiled' else 'TB-001',source)
    assembly_policy = {'profile': profile} if profile == 'single_pass' else {}
    with tempfile.TemporaryDirectory(prefix='bora-public-',dir=os.environ.get('RUNNER_TEMP')) as td:
        work=Path(td)
        with quiet():
            renderer=None
            try:
                renderer=make_renderer(recipe,profile)
                entries=[]
                for index,unit in enumerate(plan['units']):
                    receipt=renderer.render_unit(unit,work/f'unit-{index}')
                    check(receipt['status']=='passed','public_smoke_not_passed')
                    receipt.update(lesson_id=plan['lesson_id'],unit_index=index,plan_sha256=identity(plan),recipe_sha256=identity(recipe))
                    entries.append(dict(unit_index=index,receipt=receipt,path=work/f'unit-{index}'/'final.wav'))
                assemble_lesson(plan,entries,work/'assembled',expected_recipe_sha256=identity(recipe),
                    expected_render_settings_sha256=renderer.settings_sha256,unit_gap_ms=180,
                    paragraph_gap_ms=350, **assembly_policy)
            finally:
                close_renderer(renderer)
    result=dict(status='public_runtime_smoke_passed',course=request['course'],
        source_key_used=False,private_manuscript_read=False,render_settings_sha256=renderer.settings_sha256)
    if profile == 'single_pass':
        result.update(execution_profile=request['execution_profile'], asr_performed=False,
                      max_attempts=1)
    atomic(args.output,enc(result)); print(json.dumps(result))


def main(profile='eager'):
    validate_profile(profile)
    os.umask(0o077)
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('preflight'); p.add_argument('--output',required=True)
    p=sub.add_parser('plan'); p.add_argument('--output',required=True); p.add_argument('--github-output'); p.add_argument('--settings-sha',required=True)
    for name in ('render','assemble','probe'):
        p=sub.add_parser(name); p.add_argument('--lesson-id',required=True); p.add_argument('--plan',required=True)
        p.add_argument('--resume-dir',required=True); p.add_argument('--output',required=True)
        if name in ('render','probe'): p.add_argument('--shard',required=True,type=int)
        if name=='probe': p.add_argument('--github-output')
    p=sub.add_parser('index'); p.add_argument('--directory',required=True); p.add_argument('--course',required=True)
    args=parser.parse_args()
    if args.command=='index':
        check(profile!='compiled','compiled_course_index_forbidden')
        if profile=='eager':
            make_index(args)
        else:
            _,_,request,_=config(profile)
            check(args.course==request['course'],'single_pass_index_course_scope')
            make_index(args, profile=profile)
    else:
        {'preflight':preflight,'plan':public_plan,'probe':resume_probe,'render':render,'assemble':assemble}[args.command](args,profile=profile)


if __name__=='__main__':
    try:
        main()
    except BaseException as exc:
        # No exception message, transcript or local variable enters public logs.
        locations=[dict(file=Path(f.filename).name,line=f.lineno) for f in traceback.extract_tb(exc.__traceback__)
                   if Path(f.filename).parent==HERE]
        print(json.dumps(dict(status='failed',error_type=type(exc).__name__,locations=locations[-6:])),flush=True)
        sys.exit(1)

