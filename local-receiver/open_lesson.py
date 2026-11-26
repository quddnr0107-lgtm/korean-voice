"""Open exactly one authenticated lesson to anonymous memory descriptors for rendering."""
from contextlib import contextmanager,ExitStack
import hashlib,io,json,os,re,stat,zipfile
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CONTEXT=b'muhan/v4-283-private/v1/'

def open_zip(key,ident,ciphertext):
    if not re.fullmatch(r'(?:TB|RL|DR|MS)-[0-9]{3}',ident) or len(ciphertext)<=412:
        raise ValueError('owner envelope format')
    label=CONTEXT+ident.encode()
    wrapped=key.decrypt(ciphertext[:384],padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                     algorithm=hashes.SHA256(),label=label))
    return zipfile.ZipFile(io.BytesIO(AESGCM(wrapped).decrypt(ciphertext[384:396],ciphertext[396:],label)))

@contextmanager
def lesson(archive,filename,archive_sha,ident,key_path):
    path=Path(archive);key_path=Path(key_path)
    if any(p.is_symlink() or not p.is_file() for p in [path,key_path]) or key_path.stat().st_mode & 0o077:
        raise ValueError('workspace key or archive access scope')
    with path.open('rb') as f:
        if hashlib.file_digest(f,'sha256').hexdigest()!=archive_sha:raise ValueError('outer archive digest')
    key=serialization.load_pem_private_key(key_path.read_bytes(),password=None)
    with zipfile.ZipFile(path) as outer:
        expected=ident+'.v4-encrypted.bin'
        if filename!=expected or outer.namelist().count(expected)!=1:
            raise ValueError('exact lesson member required')
        item=outer.getinfo(expected)
        if stat.S_ISLNK(item.external_attr>>16) or item.file_size>256*1024*1024:
            raise ValueError('owner envelope member type or size')
        cipher=outer.read(item)
    with open_zip(key,ident,cipher) as z, ExitStack() as stack:
        meta=json.loads(z.read('metadata.json')); desc=json.loads(z.read('descriptor.json'))
        if meta['id']!=ident or desc['lesson_id']!=ident:
            raise ValueError('archive identity mismatch')
        names=[f'part-{i:03d}.mp3' for i in range(len(desc['audio']['parts']))]
        if len(z.namelist())!=len(names)+2 or set(z.namelist())!={'metadata.json','descriptor.json',*names}:
            raise ValueError('archive part shape')
        targets=[]
        for name in names:
            f=stack.enter_context(os.fdopen(os.memfd_create('v4-audio-part',flags=os.MFD_CLOEXEC),'w+b'))
            f.write(z.read(name));f.seek(0);targets.append(f.fileno())
        yield meta,desc,targets
