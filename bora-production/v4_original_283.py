"""Same protected originals; sealed only to the scoped private video workspace."""
import argparse,base64,hashlib,io,json,os,re,shutil,socket,zipfile
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import lecture_crypto as crypto
import lecture_pack as pack
import ms147_owner_export as ms
from namespace_guard import validate_snapshot
import transport as tr

HERE=Path(__file__).resolve().parent
RECIPIENT_SHA='66b76134571be5758aecb26f858f0de040adf4b6b22abcb01eaf044fe4a297b6'
CONTEXT=b'muhan/v4-283-private/v1/'
PUBLISHED={
 'TB':(445262138,'bora-fp32-tb-single-pass-v1-20261114','174fce697dc12f9aa3d6da7d6e98ddd8e9c1a3c79ac7e6ba39ef1b9de2dd1290'),
 'RL':(445262582,'bora-fp32-rl-single-pass-v1-20261114','4be4c78bfbb45affd31ecdad418b750397b6fe37856fa563caa4b8f1a99131ef'),
 'DR':(445842493,'bora-fp32-dr-single-pass-v1-20261114','96010d3c26f0b27180162c2cda9488f1a81622defdff57593e3167202181984d'),
 'RL-027':(450980390,'bora-fp32-rl027-art15p3-v2-20261119','2370ff56cce9c4ffa23d53fae1ec9c9580d6d4c28dc035187816bba524331fc7'),
}

def plan():
    ids=[f'TB-{i:03d}' for i in range(2,26)]
    ids += tr.course_ids('RL') + tr.course_ids('DR') + [x for g in ms.plan().values() for x in g]
    if len(ids)!=283 or len(set(ids))!=283:
        raise ValueError('wrong video inventory')
    return {f'v4-{i//16+1:02d}':ids[i:i+16] for i in range(0,len(ids),16)}

def recipient():
    raw=(HERE/'v4-original-283-receiver.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=RECIPIENT_SHA:
        raise ValueError('private workspace recipient drift')
    c=tr._json(raw)
    if set(c)!={'schema','recipient_public_key'} or c['schema']!='v4-original-283-workspace-receiver/v1':
        raise ValueError('recipient config shape')
    k=serialization.load_der_public_key(base64.b64decode(c['recipient_public_key'],validate=True))
    if not isinstance(k,rsa.RSAPublicKey) or k.key_size!=3072:
        raise ValueError('recipient key shape')
    return k

def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def fetch(gh, asset, target, cap):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',asset.get('digest','')):
        raise ValueError('asset digest missing')
    tr._fetch_final_object(gh,asset,target,cap)

def published_index(gh, assets, path, bucket):
    release,tag,expected=PUBLISHED[bucket]
    fetch(gh,assets['index.json'],path,tr.INDEX_CAP)
    if sha(path)!=expected:raise ValueError('published index drift')
    data=tr._json(path.read_bytes())
    ids=tr.course_ids(bucket) if bucket!='RL-027' else ['RL-027']
    if (data['version']!='integrated-lecture-index/v2' or data['release_tag']!=tag or
        data['count']!=len(ids) or len(data['lessons'])!=len(ids)):
        raise ValueError('published index scope')
    entries={}
    for row in data['lessons']:
        if set(row)!={'id','descriptor_url','sha256'} or row['id'] in entries:
            raise ValueError('published index row')
        ident=row['id']
        tr._asset_url(row['descriptor_url'],host='github.com',path=f'/{tr.REPOSITORY}/releases/download/{tag}/{ident}.descriptor.json')
        entries[ident]=row['sha256']
    if set(entries)!=set(ids):raise ValueError('published index coverage')
    return entries

def download(input_dir, ids, gh=None):
    gh=gh or tr.GitHubBackend()
    input_dir.mkdir(mode=0o700)
    cache={}
    for ident in ids:
        bucket=ident if ident=='RL-027' else ident[:2]
        if bucket not in cache:
            if bucket=='MS':
                release,tag,draft=ms.RELEASE,ms.TAG,True
            else:
                release,tag,_=PUBLISHED[bucket]
                draft=False
            actual=gh.release(tag)
            if not actual or actual['id']!=release or actual['tag_name']!=tag or actual['draft'] is not draft:
                raise ValueError('release identity changed')
            assets=tr._assets(gh,actual)
            if bucket=='MS':
                expected={i for xs in ms.plan().values() for i in xs}
                pins={i:{} for i in expected}
                for name,a in assets.items():
                    i=name.partition('.')[0]
                    if i in expected:
                        pins[i][name]=[a['id'],a['size'],a['digest'].removeprefix('sha256:')]
                index=ms.validate_manifest(ms.canonical(pins))
            else:
                index=published_index(gh,assets,input_dir/(bucket+'.index.json'),bucket)
            cache[bucket]=(assets,index)
        assets,index=cache[bucket]
        stage=input_dir/ident
        stage.mkdir(mode=0o700)
        desc=ident+'.descriptor.json'
        fetch(gh,assets[desc],stage/desc,tr.DESCRIPTOR_CAP)
        if sha(stage/desc)!=(index[ident][desc][2] if bucket=='MS' else index[ident]):
            raise ValueError('incorrect descriptor')
        _,names=tr._read_descriptor(stage/desc,ident)
        for k,n in names.items():
            fetch(gh,assets[n],stage/n,tr.FINAL_OBJECT_CAPS[k])
        tr.validate_final_directory(stage,ident[:2],partial=True,profile='single_pass')
    for x in input_dir.glob('*.index.json'):x.unlink()

def recover(directory, ident, key):
    tr.validate_final_directory(directory,ident[:2],partial=True,profile='single_pass')
    path=directory/(ident+'.descriptor.json')
    d,names=tr._read_descriptor(path,ident)
    a=d['audio']
    audio=(directory/names['audio']).read_bytes()
    raw,_=crypto.open_range(key,d['content'],(directory/names['content']).read_bytes(),
                           expected_id=ident,expected_kind='private-metadata',expected_key_id=tr.KEY_ID)
    rec=tr._json(raw)
    if (rec['id']!=ident or rec['audio_pack_sha256']!=a['pack_sha256'] or
        rec['total_samples']!=a['total_samples'] or rec['sample_rate']!=a['sample_rate'] or
        not isinstance(rec['narration_text'],str) or not rec['segments']):
        raise ValueError('original audio metadata mismatch')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_STORED) as z:
        z.writestr('metadata.json',raw)
        z.writestr('descriptor.json',path.read_bytes())
        for p in a['parts']:
            i,o=p['part_index'],p['pack_offset']
            mp3=pack.open_part(key,p,audio[o:o+p['cipher_bytes']],expected_id=ident,
                               expected_part_index=i,expected_pack_id=a['object_id'])
            z.writestr(f'part-{i:03d}.mp3',mp3)
    return out.getvalue()

def seal(public,ident,payload):
    label=CONTEXT+ident.encode('ascii')
    key=AESGCM.generate_key(bit_length=256)
    wrapped=public.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                           algorithm=hashes.SHA256(),label=label))
    nonce=os.urandom(12)
    return wrapped+nonce+AESGCM(key).encrypt(nonce,payload,label)

def export(source,dest,ids,uid,host):
    validate_snapshot(os.getuid(),uid,socket.if_nameindex(),Path('/proc/net/route').read_text(),
                      os.readlink('/proc/self/ns/net'),host,Path('/proc/net/ipv6_route').read_text())
    public=recipient()
    key=base64.b64decode(os.environ.pop('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64'),validate=True)
    if len(key)!=32 or source.is_symlink() or {p.name for p in source.iterdir()}!=set(ids):
        raise ValueError('private batch input shape')
    dest.mkdir(mode=0o700)
    for ident in ids:
        path=source/ident
        if path.is_symlink():raise ValueError('malformed lesson directory')
        package=seal(public,ident,recover(path,ident,key))
        with (dest/(ident+'.v4-encrypted.bin')).open('xb') as f:f.write(package)
        shutil.rmtree(path)
        print(f'{ident}: authenticated original sealed for private video workspace',flush=True)

def main():
    p=argparse.ArgumentParser();sp=p.add_subparsers(dest='action',required=True)
    sp.add_parser('check')
    for verb in ('download','export'):
        a=sp.add_parser(verb);a.add_argument('group',choices=plan())
        a.add_argument('input',type=Path)
        if verb=='export':
            a.add_argument('output',type=Path);a.add_argument('uid',type=int);a.add_argument('host')
    a=p.parse_args()
    if a.action=='check':recipient();print('Verified: 283 originals, 18 bundles, one private receiver.')
    elif a.action=='download':download(a.input,plan()[a.group])
    else:export(a.input,a.output,plan()[a.group],a.uid,a.host)
if __name__=='__main__':main()
