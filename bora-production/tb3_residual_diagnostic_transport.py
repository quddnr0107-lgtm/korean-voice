"""GET-only exact input transport. No source/media keys permitted."""
import hashlib,json,os,re,subprocess,sys,zipfile
from pathlib import Path
REPO='quddnr0107-lgtm/korean-voice'
PIN='836500cd9250f38698235c729ae5553f41e7c262'
ARTIFACT=14101669700
DIGEST='sha256:bcb72e0019eb926b94dbc8479396d5545eb66d689d44f3c2bffd2b6c8e171e4d'
RUN=43565543458

def require(ok):
    if not ok: raise ValueError('pinned_input_mismatch')
def api(path,target=None):
    cmd=['gh','api','--method','GET',f'/repos/{REPO}/{path}']
    if target is None:
        return json.loads(subprocess.check_output(cmd,stderr=subprocess.DEVNULL))
    with target.open('xb') as f:
        subprocess.run(cmd+['-H','Accept: application/octet-stream'],stdout=f,stderr=subprocess.DEVNULL,check=True)
def sha(path):
    with path.open('rb') as f: return 'sha256:'+hashlib.file_digest(f,'sha256').hexdigest()
def main():
    require(not any(os.environ.get(x) for x in ('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')))
    os.umask(0o077)
    root=Path(sys.argv[1]);root.mkdir(mode=0o700,exist_ok=False)
    manifest=json.loads(Path(__file__).with_name('tb3_residual-cipher-pins.json').read_text())
    require(manifest['run_id']==RUN and manifest['release_id']==445068693)
    raw=Path(__file__).with_name('tb25-original-public-plan.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest()=='407d577e0c81173e0c7a7b897cba46f966e9111e7c5b58450a9a3065b67bcf6a')
    plan=json.loads(raw)
    require(plan['course']=='TB' and plan['schema']=='bora-fp32-public-plan/v1')
    (root/'public-plan.json').write_bytes(raw)
    release=api('releases/445068693')
    require(release['id']==445068693 and release['draft'] is True
            and release['tag_name']=='bora-fp32-tb-single-pass-staging-20261114')
    sys.path.insert(0,str(Path(sys.argv[2]).resolve()))
    from transport import validate_bundle
    lessons=set(manifest['lesson_ids'])
    require(lessons=={'TB-005','TB-013','TB-018'} and len(manifest['assets'])==13)
    for asset in manifest['assets']:
        name=asset['name'];m=re.fullmatch(r'(TB-\d{3})-s(\d{3})-r43565543458-a1.zip',name)
        require(m is not None and m[1] in lessons)
        live=api(f'releases/assets/{asset["id"]}')
        require(all(live[k]==asset[k] for k in ('id','name','size','digest')))
        folder=root/m[1];folder.mkdir(mode=0o700,exist_ok=True)
        target=folder/name;api(f'releases/assets/{asset["id"]}',target)
        require(target.stat().st_size==asset['size'] and sha(target)==asset['digest'])
        report=validate_bundle(target,'TB',profile='single_pass')
        require(str(report['run_id'])==str(RUN) and str(report['run_attempt'])=='1')
    print(json.dumps({'status':'exact_cipher_inputs_verified','lessons':len(lessons),'bundles':len(manifest['assets'])}))
if __name__=='__main__':
    try: main()
    except BaseException:
        print('{"status":"pinned_input_failed"}');sys.exit(1)
