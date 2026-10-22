#!/usr/bin/env python3
"""Frozen female-B recipe. Resume by chapter; publish only complete, verified ZIPs."""
import os
os.environ['ORT_DISABLE_TELEMETRY']='1'
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ.setdefault('OMP_NUM_THREADS','2')
os.environ.setdefault('OPENBLAS_NUM_THREADS','2')
import argparse, gzip, hashlib, importlib.util, io, json, pathlib, subprocess, sys, time, zipfile
import math, re, shutil, tempfile
HERE=pathlib.Path(__file__).resolve().parent
REPO='quddnr0107-lgtm/korean-voice'
def plan():
    with gzip.open(HERE/'corpus.json.gz','rt',encoding='utf-8') as f:return json.load(f)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def gh(*args,check=True):return subprocess.run(['gh',*args],capture_output=True,text=True,check=check)
def release_tag(version,bundle):return 'sulsul-b1-'+version+'-'+bundle.split(':')[0]
def asset_name(bundle):return bundle.split(':')[1].zfill(3)+'.zip'
class ApiError(RuntimeError):pass

def api(path, *, missing_ok=False, paginate=False):
    args=['api',path]
    if paginate:args.extend(['--paginate','--slurp'])
    result=gh(*args,check=False)
    if result.returncode:
        if missing_ok and re.search(r'\(HTTP 404\)\s*$',result.stderr.strip()):return None
        raise ApiError('GitHub lookup failed; no work is assumed missing: '+result.stderr.strip()[:240])
    try:return json.loads(result.stdout)
    except (ValueError,TypeError) as exc:raise ApiError('GitHub returned invalid JSON') from exc

def catalog(data):
    value=json.loads((HERE/'release-catalog.json').read_text())
    if value['schema']!=1 or value['version']!=data['version'] or value['recipe']!=data['recipe']['id'] or value['repository']!=REPO:raise ValueError('Pinned catalog does not match approved plan')
    expected={b['id'] for j in data['jobs'] for b in j['bundles']}
    if set(value['bundles'])!=expected:raise ValueError('Pinned catalog is incomplete')
    return value

def assets(release):
    # Embedded lists can be truncated; always request all pages for large books.
    items=release.get('assets',[])
    if len(items)>=100:
        pages=api('repos/'+REPO+'/releases/'+str(release['id'])+'/assets?per_page=100',paginate=True)
        if not isinstance(pages,list) or any(not isinstance(p,list) for p in pages):raise ApiError('Malformed asset pages')
        items=[a for page in pages for a in page]
    names=[a['name'] for a in items]
    if len(names)!=len(set(names)):raise ApiError('Duplicate release asset names')
    return {a['name']:a for a in items}

def matches(asset,pin):
    return asset is not None and asset.get('state')=='uploaded' and asset.get('size')==pin['bytes'] and asset.get('digest')=='sha256:'+pin['sha256'] and asset.get('browser_download_url')==pin['url']

def check_asset(asset,pin,bundle):
    if asset is None:return False
    if not matches(asset,pin):raise ValueError('Published bytes differ from frozen catalog: '+bundle)
    return True

def remote_bundle(data,bundle,pin):
    release=api('repos/'+REPO+'/releases/tags/'+release_tag(data['version'],bundle),missing_ok=True)
    if release is None:return None
    if release.get('draft'):raise ApiError('Draft audio release is not a completed release')
    item=assets(release).get(asset_name(bundle))
    check_asset(item,pin,bundle)
    return item

def inventory(data):
    pins=catalog(data)['bundles'];pages=api('repos/'+REPO+'/releases?per_page=100',paginate=True)
    if not isinstance(pages,list) or any(not isinstance(p,list) for p in pages):raise ApiError('Malformed release pages')
    releases={}
    for release in [r for page in pages for r in page]:
        tag=release['tag_name']
        if tag in releases:raise ApiError('Duplicate release tags')
        releases[tag]=release
    missing=[];complete=0;bytag={}
    for bundle,pin in pins.items():
        tag=release_tag(data['version'],bundle)
        if tag not in bytag:
            release=releases.get(tag)
            if release and release.get('draft'):raise ApiError('Draft audio release: '+tag)
            bytag[tag]=assets(release) if release else {}
        if check_asset(bytag[tag].get(asset_name(bundle)),pin,bundle):complete+=1
        else:missing.append(bundle)
    return {'version':data['version'],'complete':complete,'missing':missing,'archiveBytes':sum(p['bytes'] for p in pins.values())}

def failed_jobs(run_id,data):
    if not str(run_id).isdigit():raise ValueError('A numeric failed run ID is required')
    run=api('repos/'+REPO+'/actions/runs/'+str(run_id))
    if run.get('status')!='completed' or not run.get('path','').split('@')[0].endswith('.github/workflows/sulsul.yml'):raise ValueError('Run must be a completed Sulsul workflow')
    pages=api('repos/'+REPO+'/actions/runs/'+str(run_id)+'/jobs?filter=latest&per_page=100',paginate=True)
    valid={j['id'] for j in data['jobs']};failed=set()
    for page in pages:
        for job in page['jobs']:
            match=re.fullmatch(r'bake \((\d{3})\)',job['name'])
            if match and match[1] in valid and job.get('conclusion') in {'failure','cancelled','timed_out','action_required','startup_failure'}:failed.add(match[1])
    return failed

def select_jobs(data,report,mode='audit',job_ids='',failed_run_id=None):
    valid={j['id'] for j in data['jobs']};selected=valid
    if job_ids:
        selected={x.strip() for x in job_ids.split(',') if x.strip()}
        if not selected or not selected<=valid:raise ValueError('Unknown or empty job selector')
    if mode=='audit':return []
    if mode=='retry-failed':selected &= failed_jobs(failed_run_id,data)
    elif mode!='retry-missing':raise ValueError('Unknown operation')
    missing=set(report['missing'])
    return [j['id'] for j in data['jobs'] if j['id'] in selected and any(b['id'] in missing for b in j['bundles'])]

def validate_archive(archive,bundle,data,pin):
    if not archive.is_file() or archive.stat().st_size!=pin['bytes'] or digest(archive)!=pin['sha256']:
        raise ValueError('Exact original ZIP required; no regeneration/repacking allowed: '+bundle['id'])
    expected={i['key'] for i in bundle['items']}
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or set(names)!={'manifest.json',*(k+'.mp3' for k in expected)} or z.testzip() is not None:raise ValueError('ZIP member set/CRC mismatch')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('schema')!=1 or manifest.get('version')!=data['version'] or manifest.get('recipe')!=data['recipe']['id'] or manifest.get('bundle')!=bundle['id'] or set(manifest.get('files',{}))!=expected:raise ValueError('ZIP manifest mismatch')
        for key,record in manifest['files'].items():
            content=z.read(key+'.mp3')
            if record.get('version')!=data['version'] or record.get('bytes')!=len(content) or record.get('sha256')!=hashlib.sha256(content).hexdigest() or not math.isfinite(record.get('seconds',0)) or record.get('seconds',0)<=0:raise ValueError('Invalid MP3 record: '+key)
    return manifest

def publish_original(archive,bundle,data,pin):
    validate_archive(archive,bundle,data,pin)
    if remote_bundle(data,bundle['id'],pin) is not None:return False
    tag=release_tag(data['version'],bundle['id'])
    release=api('repos/'+REPO+'/releases/tags/'+tag,missing_ok=True)
    if release is None:
        result=gh('release','create',tag,'--repo',REPO,'--target',catalog(data)['sourceCommit'],'--prerelease','--title','쉬운술술성경 여성 B · '+tag.rsplit('-',1)[1],'--notes','Frozen approved female-B audio assets. Exact-byte restoration only.',check=False)
        if result.returncode and api('repos/'+REPO+'/releases/tags/'+tag,missing_ok=True) is None:raise ApiError('Release creation failed')
    with tempfile.TemporaryDirectory(prefix='sulsul-upload-') as tmp:
        upload=pathlib.Path(tmp)/asset_name(bundle['id']);shutil.copyfile(archive,upload)
        result=gh('release','upload',tag,str(upload),'--repo',REPO,check=False)
    # A failed/lost upload response is reconciled with the remote bytes first.
    if remote_bundle(data,bundle['id'],pin) is None:raise ApiError('Upload incomplete; retain original ZIP checkpoint. '+result.stderr[:200])
    return True

def restore(args,data):
    pins=catalog(data)['bundles'];job=next((j for j in data['jobs'] if j['id']==args.job),None)
    if job is None:raise ValueError('Unknown job')
    started=time.monotonic();restored=skipped=0
    for bundle in job['bundles']:
        if args.bundle and bundle['id']!=args.bundle:continue
        if time.monotonic()-started>args.budget_minutes*60:raise TimeoutError('Soft time budget reached; completed releases remain intact')
        pin=pins[bundle['id']]
        if remote_bundle(data,bundle['id'],pin) is not None:
            skipped+=1;print('Verified published; skip',bundle['id'],flush=True);continue
        archive=args.output/data['version']/(bundle['id'].replace(':','-')+'.zip')
        validate_archive(archive,bundle,data,pin)
        if not args.publish:print('Validated original ZIP; no upload',bundle['id'],flush=True);continue
        restored+=int(publish_original(archive,bundle,data,pin))
    return {'restored':restored,'skipped':skipped,'generated':0}

def load_engine(assets,engine):
    import numpy as np
    sys.path.insert(0,str(HERE/'vendor'))
    import helper
    spec=importlib.util.spec_from_file_location('sulsul_synth',engine)
    synth=importlib.util.module_from_spec(spec);spec.loader.exec_module(synth)
    tts=helper.load_text_to_speech(str(assets/'onnx'),False)
    style,_=synth.load_blend(helper,str(assets/'voice_styles'),'F4:0.6,F2:0.4')
    return np,synth,tts,style

def render(item,state):
    np,synth,tts,style=state;sr=tts.sample_rate
    np.random.seed(int(item['key'][:8],16))
    units=synth.units_from_plan(synth.plan(item['text'],'calm',None,1))
    if not units:raise ValueError('Empty speech unit')
    units[-1]['hard']=True;units[-1]['pause_ms']=max(units[-1]['pause_ms'],600)
    pieces=[]
    for u in units:
        speed=1.06*u['rate']*(.95 if u['emph'] else 1)
        wav,_=tts._infer([u['text']],['ko'],style,16,float(np.clip(speed,.7,1.6)))
        w=synth.fade(synth.trim(np.asarray(wav,dtype=np.float32).reshape(-1),sr),sr)
        if not np.isfinite(w).all() or len(w)<sr*.08:raise ValueError('Invalid waveform')
        pieces.extend([w,np.zeros(int(sr*u['pause_ms']/1000),dtype=np.float32)])
    out=np.concatenate(pieces);peak=np.abs(out).max()
    if not np.isfinite(peak) or peak<1e-5:raise ValueError('Silent waveform')
    return (out/peak*10**(-2/20)).astype(np.float32),sr

def bake(args,data):
    """Compatibility entry point: frozen published edition permits restoration only."""
    return restore(args,data)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description='Audit or restore exact completed female-B releases; never regenerate this edition.')
    ap.add_argument('--matrix',action='store_true');ap.add_argument('--audit',action='store_true');ap.add_argument('--job');ap.add_argument('--bundle')
    ap.add_argument('--mode',choices=['audit','retry-missing','retry-failed'],default='audit');ap.add_argument('--job-ids',default='');ap.add_argument('--failed-run-id')
    ap.add_argument('--output',type=pathlib.Path,default=pathlib.Path('sulsul-output'));ap.add_argument('--publish',action='store_true');ap.add_argument('--report',type=pathlib.Path)
    ap.add_argument('--budget-minutes',type=float,default=320)
    args=ap.parse_args();data=plan()
    if args.job:
        if args.mode=='audit':ap.error('Restoration requires explicit --mode retry-missing or retry-failed')
        report=inventory(data)
        selected=select_jobs(data,report,args.mode,args.job_ids,args.failed_run_id)
        if args.job not in selected:print(json.dumps({'restored':0,'generated':0,'reason':'No selected missing work'}))
        else:print(json.dumps(restore(args,data)))
    else:
        report=inventory(data);selected=select_jobs(data,report,args.mode,args.job_ids,args.failed_run_id);report['matrix']={'job':selected}
        if args.report:
            args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report['matrix'] if args.matrix else report))
