"""Keyless ciphertext-only delivery. CLI calls are explicit; imports never do IO.
Workflow serializes runs and same-ID publishers; distinct IDs may publish concurrently. Never clobber/delete.
"""
import argparse, hashlib, io, json, os, re, shutil, subprocess, tempfile, zipfile
from pathlib import Path
import lecture_crypto as crypto
import lecture_pack as pack
REPO=crypto.REPO
TAG='integrated-lectures-v2-20261028'
KEY_ID='integrated-lectures-media-v1'
IDS={f'{p}-{i:03}' for p,(_,n) in crypto.BOUNDS.items() for i in range(1,n+1)}
MAX_JSON=2*1024*1024
MAX_INDEX=128*1024
MAX_DESCRIPTOR=512*1024
MAX_ASSETS=1000
MAX_TOTAL_ASSETS=960
MAX_ASSETS_PER_ID=7
ARTIFACT_ID=12698829253
ARTIFACT_SHA='9b946ad7ab96a75627ecc685e5503e21b31d9302135f2458d80142d8c117567f'
RECEIVER_HASHES={'receiver-public.json':'b0dde8e8106b2325e47b51dc2535fb618d4a9c1e9fa62ee61b90d8da32e9ae2a','receiver-wrapped.json':'e28c9edd2f478bbb81cb113126261f1b392f07cf03a053777b2e238e679a2cfa'}
sha=crypto.digest

def pairs(items):
    out={}
    for k,v in items:
        if k in out:raise ValueError('duplicate JSON key')
        out[k]=v
    return out

def loads(b):return json.loads(b,object_pairs_hook=pairs)
def encoded(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
def descriptor_name(ident):
    if ident not in IDS:raise ValueError('lesson scope')
    return ident+'.descriptor.json'
def media_name(ident,key,manifest):
    descriptor_name(ident)
    if key not in ('audio','content') or not isinstance(manifest,dict) or not isinstance(manifest.get('object_id'),str) or not re.fullmatch('[a-f0-9]{32}',manifest['object_id']):raise ValueError('media filename')
    return ident+'.'+key+'.'+manifest['object_id']+'.bin'
def check_descriptor(d,ident):
    descriptor_name(ident)
    if not isinstance(d,dict) or set(d)!={'version','lesson_id','audio','content'} or d['version']!='integrated-lecture-output/v2' or d['lesson_id']!=ident:raise ValueError('descriptor schema')
    audio=d['audio'];pack.validate_pack(audio)
    if audio['lesson_id']!=ident or audio['namespace']!=crypto.namespace_for(ident) or audio['key_id']!=KEY_ID:raise ValueError('audio pack scope')
    m=d['content'];crypto._validate_media_header(m)
    if m['lesson_id']!=ident or m['namespace']!=crypto.namespace_for(ident) or m['kind']!='private-metadata' or m['key_id']!=KEY_ID:raise ValueError('descriptor media scope')
    if len(m['cipher_sha256'])!=(m['plain_bytes']+crypto.CHUNK-1)//crypto.CHUNK or any(not isinstance(x,str) or not re.fullmatch('[a-f0-9]{64}',x) for x in m['cipher_sha256']):raise ValueError('descriptor hashes')
    return d
def names(ident,descriptor):
    check_descriptor(descriptor,ident)
    return {descriptor_name(ident),*(media_name(ident,k,descriptor[k]) for k in ('audio','content'))}
def checkpoint_name(name):
    q=re.fullmatch(r'((?:TB|DR|RL)-[0-9]{3})\.checkpoint\.([0-9]{1,20})\.([0-9]{1,6})\.(bin|json)',name)
    if not q or q[1] not in IDS or int(q[2])<1 or int(q[3])<1:raise ValueError('checkpoint filename')
    return q[1],int(q[2]),int(q[3]),q[4]
def asset_kind(name):
    q=re.fullmatch(r'((?:TB|DR|RL)-[0-9]{3})\.(?:descriptor\.json|(?:audio|content)\.[a-f0-9]{32}\.bin)',name)
    if q and q[1] in IDS:return 'complete'
    checkpoint_name(name);return 'checkpoint'
def cap_for(name):
    if name=='index.json':return MAX_INDEX
    kind=asset_kind(name)
    if name.endswith('.descriptor.json'):return MAX_DESCRIPTOR
    if name.endswith('.json'):return MAX_JSON
    if '.audio.' in name:return pack.MAX_PACK_BYTES
    n=crypto.MAX_CHECKPOINT if kind=='checkpoint' else crypto.MAX_PRIVATE_METADATA
    return n+16*((n+crypto.CHUNK-1)//crypto.CHUNK)
def validate_asset_capacity(existing_names,new_names=()):
    """Reserve before upload; no implicit deletion. Per-ID bounds are what keep
    concurrent distinct-ID publishers below the global cap despite snapshots.
    137 * 7 committed/orphan/checkpoint assets + one index = 960 <= 1000.
    """
    all_names=set(existing_names)|set(new_names)
    if len(all_names)>MAX_TOTAL_ASSETS or len(all_names)>MAX_ASSETS:raise ValueError('release asset capacity; explicit cleanup authorization required')
    counts={}
    for name in all_names:
        if name=='index.json':continue
        asset_kind(name);ident=name.split('.',1)[0];counts[ident]=counts.get(ident,0)+1
        if counts[ident]>MAX_ASSETS_PER_ID:raise ValueError('lesson asset capacity; explicit cleanup authorization required')
    return counts

def read(path,cap):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=cap:raise ValueError('file gate')
    b=path.read_bytes()
    if not 0<len(b)<=cap:raise ValueError('file changed')
    return b

def validate_queue(root):
    root=Path(root);path=root/'queue.json' if root.is_dir() else root;q=loads(read(path,MAX_JSON))
    if set(q)!={'version','count','lessons'} or q['version']!='integrated-lecture-queue/v1' or type(q['count']) is not int or q['count']!=137 or not isinstance(q['lessons'],list) or len(q['lessons'])!=137:raise ValueError('queue schema')
    found=[]
    for row in q['lessons']:
        if set(row)!={'id','file','cipher_sha256'} or row['id'] not in IDS or row['file']!=row['id']+'.sealed.json' or not re.fullmatch('[a-f0-9]{64}',row['cipher_sha256']):raise ValueError('queue item')
        found.append(row['id'])
    if set(found)!=IDS or len(set(found))!=137:raise ValueError('queue coverage')
    return q

def check_media(m,blob,ident,kind):
    if kind=='audio':
        pack.validate_pack(m,blob)
        if m['lesson_id']!=ident or m['namespace']!=crypto.namespace_for(ident) or m['key_id']!=KEY_ID:raise ValueError('audio pack scope')
        return
    crypto.validate(m,blob)
    if m['lesson_id']!=ident or m['namespace']!=crypto.namespace_for(ident) or m['kind']!=kind or m['key_id']!=KEY_ID:raise ValueError('media scope')
    for i,h in enumerate(m['cipher_sha256']):
        if sha(blob[i*(crypto.CHUNK+16):(i+1)*(crypto.CHUNK+16)])!=h:raise ValueError('cipher chunk digest')

def checked_complete(directory,ident):
    directory=Path(directory)
    d=check_descriptor(loads(read(directory/descriptor_name(ident),MAX_DESCRIPTOR)),ident)
    for k,kind in [('audio','audio'),('content','private-metadata')]:
        n=media_name(ident,k,d[k]);check_media(d[k],read(directory/n,cap_for(n)),ident,kind)
    return d
def checked_checkpoint(directory,stem,ident):
    meta_name=stem+'.json';bin_name=stem+'.bin'
    if checkpoint_name(meta_name)[0]!=ident:raise ValueError('checkpoint identity')
    m=loads(read(Path(directory)/meta_name,MAX_JSON))
    check_media(m,read(Path(directory)/bin_name,cap_for(bin_name)),ident,'checkpoint')
    return m

def output_gate(directory,ident,*,run_id=None,attempt=None):
    directory=Path(directory);descriptor_name(ident)
    if directory.is_symlink() or not directory.is_dir():raise ValueError('output directory')
    ps=list(directory.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in ps):raise ValueError('output file type')
    actual={p.name for p in ps}
    if descriptor_name(ident) in actual:
        d=check_descriptor(loads(read(directory/descriptor_name(ident),MAX_DESCRIPTOR)),ident)
        if actual!=names(ident,d):raise ValueError('complete output allowlist')
        checked_complete(directory,ident);return 'complete',sorted(actual)
    if len(actual)!=2:raise ValueError('output allowlist')
    info=[checkpoint_name(n) for n in actual]
    if any(x[0]!=ident for x in info) or len({x[:3] for x in info})!=1 or {x[3] for x in info}!={'bin','json'}:raise ValueError('checkpoint pair')
    _,rid,att,_=info[0]
    if run_id is not None and str(rid)!=str(run_id):raise ValueError('checkpoint run')
    if attempt is not None and str(att)!=str(attempt):raise ValueError('checkpoint attempt')
    stem=next(iter(actual)).rsplit('.',1)[0];checked_checkpoint(directory,stem,ident)
    return 'checkpoint',sorted(actual)

def gh(*args,json_output=False,bytes_output=False,timeout=180):
    # Deliberately do not forward source/media keys or unrelated environment vars.
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','GH_TOKEN')}
    if not env.get('GH_TOKEN'):raise ValueError('delivery token unavailable')
    r=subprocess.run(['gh',*args],env=env,check=True,stdout=subprocess.PIPE if json_output or bytes_output else subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout)
    return loads(r.stdout) if json_output else (r.stdout if bytes_output else None)

class Release:
    def __init__(self,prepare=False):
        if prepare:
            pages=gh('api',f'repos/{REPO}/releases?per_page=100','--paginate','--slurp',json_output=True)
            matches=[r for page in pages for r in page if r['tag_name']==TAG]
            if len(matches)>1:raise ValueError('duplicate release')
            if not matches:
                target=os.environ.get('GITHUB_SHA','')
                if not re.fullmatch('[a-f0-9]{40}',target):raise ValueError('target commit')
                gh('release','create',TAG,'--repo',REPO,'--title',TAG,'--notes','Encrypted AI-generated premium lecture audio (AI 합성 음성). No plaintext manuscripts or publicly playable MP3 files.','--target',target,'--latest=false')
        r=gh('api',f'repos/{REPO}/releases/tags/{TAG}',json_output=True)
        if r['tag_name']!=TAG or r['draft'] or type(r['id']) is not int:raise ValueError('release scope')
        pages=gh('api',f'repos/{REPO}/releases/{r["id"]}/assets?per_page=100','--paginate','--slurp',json_output=True)
        self.assets={}
        for page in pages:
            for a in page:
                n=a['name']
                if n in self.assets:raise ValueError('duplicate asset')
                if a.get('state','uploaded')!='uploaded' or a['size']==0:raise ValueError('asset upload incomplete')
                if type(a['size']) is not int or not 0<a['size']<=cap_for(n):raise ValueError('asset cap')
                self.assets[n]=a
        validate_asset_capacity(self.assets)
        if 'index.json' in self.assets and not all(descriptor_name(i) in self.assets for i in IDS):raise ValueError('premature index')
    def download(self,selected,out):
        selected=set(selected);out=Path(out)
        if not selected<=set(self.assets):raise ValueError('missing assets')
        out.mkdir(parents=True,exist_ok=True)
        if out.is_symlink() or any(out.iterdir()):raise ValueError('download destination')
        if not selected:return
        args=['release','download',TAG,'--repo',REPO,'--dir',str(out)]
        for n in sorted(selected):args+=['--pattern',n]
        gh(*args,timeout=900)
        if {p.name for p in out.iterdir()}!=selected:raise ValueError('download allowlist')
        for n in selected:
            a=self.assets[n];b=read(out/n,cap_for(n))
            if len(b)!=a['size'] or (a.get('digest') and a['digest']!='sha256:'+sha(b)):raise ValueError('asset integrity')
    def upload(self,path):
        path=Path(path)
        if path.name in self.assets:raise ValueError('immutable asset')
        validate_asset_capacity(self.assets,{path.name})
        b=read(path,cap_for(path.name))
        gh('release','upload',TAG,'--repo',REPO,str(path),timeout=900)
        self.assets[path.name]={'size':len(b),'digest':'sha256:'+sha(b)}

def download_complete(remote,ident,dest):
    """Resolve the stable descriptor first, then fetch only its referenced objects.
    An existing descriptor is a commit pointer: missing references are corruption,
    never a reason to fall back to checkpoints or unrelated orphan objects.
    """
    dn=descriptor_name(ident);dest=Path(dest)
    if dn not in remote.assets:raise ValueError('missing committed descriptor')
    dest.mkdir(parents=True,exist_ok=True)
    if dest.is_symlink() or any(dest.iterdir()):raise ValueError('complete destination')
    with tempfile.TemporaryDirectory(prefix='lecture-resolve-') as td:
        root=Path(td);allfiles=root/'all';remote.download({dn},allfiles)
        d=check_descriptor(loads(read(allfiles/dn,MAX_DESCRIPTOR)),ident);needed=names(ident,d)
        if not needed<=set(remote.assets):raise ValueError('committed incomplete lesson')
        blobs=root/'blobs';remote.download(needed-{dn},blobs)
        for p in blobs.iterdir():p.rename(allfiles/p.name)
        checked_complete(allfiles,ident)
        for n in needed:shutil.copyfile(allfiles/n,dest/n)
    return d

def fetch(remote,ident,dest):
    dn=descriptor_name(ident);available=set(remote.assets)
    if dn in available:
        download_complete(remote,ident,dest);return {'kind':'complete','lesson_id':ident}
    # Versioned audio/content objects without a descriptor are uncommitted
    # orphans. Ignore them; a retry may safely create fresh object IDs.
    candidates=[]
    for n in available:
        if n.startswith(ident+'.checkpoint.') and n.endswith('.json'):
            _,rid,att,_=checkpoint_name(n);stem=n[:-5]
            if stem+'.bin' in available:candidates.append((rid,att,stem))
    if candidates:
        _,_,stem=max(candidates);remote.download({stem+'.json',stem+'.bin'},dest);checked_checkpoint(dest,stem,ident)
        return {'kind':'checkpoint','lesson_id':ident,'stem':stem}
    dest=Path(dest);dest.mkdir(parents=True,exist_ok=True)
    if dest.is_symlink() or any(dest.iterdir()):raise ValueError('fetch destination')
    return {'kind':'none','lesson_id':ident}

def publish(remote,ident,directory,*,run_id=None,attempt=None):
    directory=Path(directory);dn=descriptor_name(ident)
    if directory.is_symlink() or not directory.is_dir():raise ValueError('output directory')
    empty=not any(directory.iterdir())
    if not empty:kind,files=output_gate(directory,ident,run_id=run_id,attempt=attempt)
    if dn in remote.assets:
        # A committed descriptor is immutable. Verify the complete remote object
        # before accepting empty-output reuse or skipping a failure checkpoint.
        with tempfile.TemporaryDirectory(prefix='lecture-committed-') as td:
            existing=Path(td)/'existing';download_complete(remote,ident,existing)
            if empty:return {'kind':'complete','status':'already_complete','lesson_id':ident}
            if kind=='checkpoint':return {'kind':'complete','status':'checkpoint-not-needed','lesson_id':ident}
            if read(existing/dn,MAX_DESCRIPTOR)!=read(directory/dn,MAX_DESCRIPTOR):raise ValueError('immutable descriptor conflict')
            for n in files:
                if read(existing/n,cap_for(n))!=read(directory/n,cap_for(n)):raise ValueError('immutable conflict')
        return {'kind':'complete','status':'reused','lesson_id':ident}
    if empty:raise ValueError('empty output without completed remote')
    validate_asset_capacity(remote.assets,files)
    existing=set(files)&set(remote.assets)
    with tempfile.TemporaryDirectory(prefix='lecture-cipher-compare-') as td:
        before=Path(td)/'existing';remote.download(existing,before)
        for n in existing:
            if read(before/n,cap_for(n))!=read(directory/n,cap_for(n)):raise ValueError('immutable object conflict')
    # Unreferenced objects belonging to older object IDs are never overwritten,
    # but also never prevent a new attempt from uploading its own fresh IDs.
    binary=[n for n in files if n.endswith('.bin')]
    for n in binary:
        if n not in existing:remote.upload(directory/n)
    refreshed=Release()
    with tempfile.TemporaryDirectory(prefix='lecture-before-commit-') as td:
        verified=Path(td)/'verified';refreshed.download(binary,verified)
        for n in binary:
            if read(verified/n,cap_for(n))!=read(directory/n,cap_for(n)):raise ValueError('pre-commit object mismatch')
    # Only after remote binary bytes have been verified do we write the JSON
    # commit pointer. No --clobber or delete is ever used.
    for n in files:
        if not n.endswith('.json'):continue
        if n in refreshed.assets:
            with tempfile.TemporaryDirectory(prefix='lecture-json-compare-') as td:
                before=Path(td)/'before';refreshed.download({n},before)
                if read(before/n,cap_for(n))!=read(directory/n,cap_for(n)):raise ValueError('immutable descriptor conflict')
        else:refreshed.upload(directory/n)
    refreshed=Release()
    with tempfile.TemporaryDirectory(prefix='lecture-cipher-verify-') as td:
        verify=Path(td)/'verified'
        if kind=='complete':download_complete(refreshed,ident,verify)
        else:refreshed.download(files,verify)
        output_gate(verify,ident,run_id=run_id,attempt=attempt)
        for n in files:
            if read(verify/n,cap_for(n))!=read(directory/n,cap_for(n)):raise ValueError('post-upload mismatch')
    return {'kind':kind,'status':'reused' if len(existing)==len(files) else 'published','lesson_id':ident}

def fetch_receiver(dest):
    dest=Path(dest)
    if dest.exists() and (dest.is_symlink() or not dest.is_dir() or any(dest.iterdir())):raise ValueError('receiver destination')
    raw=gh('api',f'repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip',bytes_output=True)
    if len(raw)>2048 or sha(raw)!=ARTIFACT_SHA:raise ValueError('receiver archive pin')
    data={}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        members=z.infolist()
        if len(members)!=2 or {x.filename for x in members}!=set(RECEIVER_HASHES):raise ValueError('receiver filenames')
        if sum(x.file_size for x in members)>2048 or any(x.is_dir() or x.file_size>2048 or x.flag_bits&1 or (x.external_attr>>16)&0o170000==0o120000 for x in members):raise ValueError('receiver archive size/type')
        for x in members:
            b=z.read(x)
            if sha(b)!=RECEIVER_HASHES[x.filename]:raise ValueError('receiver file pin')
            data[x.filename]=b
    crypto.validate_public(loads(data['receiver-public.json']))
    dest.mkdir(parents=True,exist_ok=True)
    for n,b in data.items(): (dest/n).write_bytes(b)
    return {'status':'receiver-verified'}

def finalize(remote,queue):
    validate_queue(queue);validate_asset_capacity(remote.assets)
    complete={ident for ident in IDS if descriptor_name(ident) in remote.assets}
    if complete!=IDS:
        if 'index.json' in remote.assets:raise ValueError('premature index')
        return {'status':'incomplete','complete':len(complete),'required':137,'missing_ids':sorted(IDS-complete)}
    rows=[]
    # Verify actual ciphertext and all per-chunk digests for every lesson before
    # publishing any course index. Use bounded temporary storage, one lesson at a time.
    with tempfile.TemporaryDirectory(prefix='lecture-finalize-') as td:
        root=Path(td)
        for ident in sorted(IDS):
            lesson=root/ident;d=download_complete(remote,ident,lesson)
            prefix=f'https://github.com/{REPO}/releases/download/{TAG}/'
            descriptor_bytes=read(lesson/descriptor_name(ident),MAX_DESCRIPTOR)
            rows.append({'id':ident,'descriptor_url':prefix+descriptor_name(ident),'sha256':sha(descriptor_bytes)})
            for p in lesson.iterdir():p.unlink()
            lesson.rmdir()
        index={'version':'integrated-lecture-index/v2','release_tag':TAG,'count':137,'lessons':rows}
        raw=encoded(index)
        if len(raw)>MAX_INDEX:raise ValueError('index cap')
        if 'index.json' in remote.assets:
            old=root/'old-index';remote.download({'index.json'},old)
            if read(old/'index.json',MAX_INDEX)!=raw:raise ValueError('immutable index conflict')
            return {'status':'reused','complete':137}
        validate_asset_capacity(remote.assets,{'index.json'})
        path=root/'index.json';path.write_bytes(raw);remote.upload(path)
        refreshed=Release();verify=root/'verify-index';refreshed.download({'index.json'},verify)
        if read(verify/'index.json',MAX_INDEX)!=raw:raise ValueError('index verification')
    return {'status':'published','complete':137}

def selected_ids(queue, remote, only_id=''):
    if not only_id:return [row['id'] for row in queue['lessons']]
    if only_id not in IDS:raise ValueError('diagnostic lesson scope')
    if descriptor_name(only_id) in remote.assets:raise ValueError('diagnostic refuses completed lesson')
    candidates=[]
    for name in remote.assets:
        if name.startswith(only_id+'.checkpoint.') and name.endswith('.json'):
            checkpoint_name(name)
            if name[:-5]+'.bin' in remote.assets:candidates.append(name)
    if not candidates:raise ValueError('diagnostic requires existing encrypted checkpoint')
    return [only_id]

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='mode',required=True)
    for mode in ['prepare','finalize']:
        a=sub.add_parser(mode);a.add_argument('--queue',required=True)
        if mode=='prepare':a.add_argument('--only-id',default='')
    a=sub.add_parser('fetch');a.add_argument('--id',required=True);a.add_argument('--destination','--directory',dest='directory',required=True)
    for mode in ['publish','gate']:
        a=sub.add_parser(mode);a.add_argument('--id',required=True);a.add_argument('--directory',required=True)
    a=sub.add_parser('receiver',aliases=['fetchreceiver']);a.add_argument('--destination','--directory',dest='directory',required=True)
    args=p.parse_args()
    if os.environ.get('GITHUB_REPOSITORY')!=REPO:raise ValueError('repository scope')
    if args.mode=='prepare':
        queue=validate_queue(args.queue);remote=Release(prepare=True)
        ids=selected_ids(queue,remote,args.only_id)
        matrix={'include':[{'id':ident} for ident in ids]}
        output=os.environ.get('GITHUB_OUTPUT')
        if not output:raise ValueError('missing workflow output')
        with open(output,'a',encoding='utf-8') as f:f.write('matrix='+json.dumps(matrix,separators=(',',':'))+'\ncount='+str(len(ids))+'\nqueue_sha256='+sha(read(Path(args.queue),MAX_JSON))+'\n')
        result={'status':'prepared','lessons':len(ids),'diagnostic':bool(args.only_id)}
    elif args.mode=='finalize':result=finalize(Release(),args.queue)
    elif args.mode in ('receiver','fetchreceiver'):result=fetch_receiver(args.directory)
    elif args.mode=='fetch':result=fetch(Release(),args.id,args.directory)
    elif args.mode=='publish':
        rid=os.environ.get('GITHUB_RUN_ID','');att=os.environ.get('GITHUB_RUN_ATTEMPT','')
        if not re.fullmatch('[0-9]{1,20}',rid) or not re.fullmatch('[0-9]{1,6}',att) or int(rid)<1 or int(att)<1:raise ValueError('run scope')
        result=publish(Release(),args.id,args.directory,run_id=rid,attempt=att)
    else:
        kind,files=output_gate(args.directory,args.id,run_id=os.environ.get('GITHUB_RUN_ID'),attempt=os.environ.get('GITHUB_RUN_ATTEMPT'));result={'kind':kind,'files':files}
    print(json.dumps(result,separators=(',',':')))
    return 1 if result.get('status')=='incomplete' else 0
def safe_delivery_failure(exc):
    # No stderr, command arguments, arbitrary exception messages or local values.
    import traceback
    kind=type(exc).__name__
    if kind not in {'ValueError','OSError','TimeoutExpired','CalledProcessError','KeyError','TypeError'}:kind='OtherError'
    result={'status':'failed','stage':'cipher-delivery','error_type':kind,'code_locations':[{'file':Path(f.filename).name,'line':f.lineno} for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename).name in {'delivery.py','lecture_pack.py','lecture_crypto.py'}][-4:]}
    reasons={'asset upload incomplete','duplicate asset','asset cap','asset integrity','missing assets','download allowlist','immutable asset','immutable object conflict','pre-commit object mismatch','immutable descriptor conflict','post-upload mismatch','lesson asset capacity; explicit cleanup authorization required','release asset capacity; explicit cleanup authorization required'}
    if type(exc) is ValueError and str(exc) in reasons:result['reason']=str(exc)
    if isinstance(exc,subprocess.CalledProcessError):result['exit_code']=int(exc.returncode)
    return result

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:print(json.dumps(safe_delivery_failure(exc),separators=(',',':')));raise SystemExit(1)
