"""Rebundle only already-approved ciphertext; no decryption or production access."""
import argparse,hashlib,json,os,re,shutil,stat,subprocess,tempfile,time,zipfile
from pathlib import Path
import v4_original_283 as original

RUN=45762532584
REPO='quddnr0107-lgtm/korean-voice'
MAX=440_000_000
REPACK_IF_ABOVE=500_000_000

def api(path,*opts,out=None):
    names=('PATH','HOME','LANG','GITHUB_TOKEN','SSL_CERT_FILE','SSL_CERT_DIR')
    env={k:os.environ[k] for k in names if k in os.environ}
    p=subprocess.run(['gh','api',f'/repos/{REPO}/{path}',*opts],env=env,stdout=out or subprocess.PIPE,
                     stderr=subprocess.PIPE,timeout=320,check=True)
    return p.stdout

def artifact(group):
    name='v4-private-original-'+group
    for _ in range(90):
        raw=api(f'actions/runs/{RUN}/artifacts?name={name}&per_page=10')
        hits=[x for x in json.loads(raw)['artifacts'] if x['name']==name and not x['expired']]
        if len(hits)>1:raise ValueError('duplicate parent artifact name')
        if hits:return hits[0]
        r=json.loads(api(f'actions/runs/{RUN}'))
        if r['status']=='completed':raise ValueError('upstream ended without exact encrypted group')
        time.sleep(9)
    raise ValueError('upstream did not produce this group')

def split(source, root, ids, expected_bytes, digest):
    if not re.fullmatch('sha256:[0-9a-f]{64}',digest or '') or source.stat().st_size!=expected_bytes:
        raise ValueError('upstream exact byte/digest binding')
    with source.open('rb') as f:
        if 'sha256:'+hashlib.file_digest(f,'sha256').hexdigest()!=digest:
            raise ValueError('upstream digest mismatch')
    allowed=[ident+'.v4-encrypted.bin' for ident in ids]
    with zipfile.ZipFile(source) as z:
        ee=z.infolist()
        if len(ee)!=len(allowed) or {e.filename for e in ee}!=set(allowed):
            raise ValueError('original group members mismatch')
        if any(e.is_dir() or stat.S_ISLNK(e.external_attr>>16) or not 412<e.file_size<=256*1024**2 for e in ee):
            raise ValueError('unexpected ciphertext entry')
        stage=1; current=0
        for member in allowed:
            size=z.getinfo(member).file_size
            if current+size>MAX:stage+=1;current=0
            if stage>6:raise ValueError('supported chunk envelope exceeded')
            parent=root/f'{stage:02d}'
            parent.mkdir(exist_ok=True,mode=0o700)
            with z.open(member) as src,(parent/member).open('xb') as dst:
                shutil.copyfileobj(src,dst,1024*1024)
            current+=size
    return stage

def main():
    p=argparse.ArgumentParser()
    p.add_argument('group',choices=original.plan())
    p.add_argument('out',type=Path)
    x=p.parse_args()
    if x.out.exists():raise ValueError('repack destination already exists')
    row=artifact(x.group)
    if row['size_in_bytes']<=REPACK_IF_ABOVE:
        print('0');return
    if type(row['id']) is not int or row['id']<=0:
        raise ValueError('invalid artifact ID')
    os.umask(0o077)
    x.out.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(dir=x.out.parent,prefix='.original-encrypted') as d:
        file=Path(d)/'original.zip'
        with file.open('xb') as writer:api(f'actions/artifacts/{row["id"]}/zip','-H','Accept: application/octet-stream',out=writer)
        print(split(file,x.out,original.plan()[x.group],row['size_in_bytes'],row['digest']))

if __name__=='__main__':main()
