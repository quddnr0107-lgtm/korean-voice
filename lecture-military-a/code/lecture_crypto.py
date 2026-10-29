"""Pure premium-lecture cryptography. No file/env/network access or secret storage.

Production receiver is pinned to the approved existing public receiver. Only its
legacy private-key wrapping uses the old KDF/AAD; new input and media formats
have distinct domains. IDs follow the existing manifests, including hyphens.
"""
import base64
import hashlib
import json
import os
import re
import struct
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

INGRESS_VERSION = 'lecture-ingress/v1'
LEGACY_INGRESS_VERSION = 'admin-law-v3-ingress/v1'
MEDIA_VERSION = 'integrated-lectures-media/v1'
REPO = 'quddnr0107-lgtm/korean-voice'
CHUNK = 65536
MAX_INPUT = 2 * 1024 * 1024
MAX_MEDIA = 128 * 1024 * 1024
MAX_PRIVATE_METADATA = 8 * 1024 * 1024
MAX_CHECKPOINT = 1024 * 1024 * 1024
PINNED_PUBLIC = {'commit':'21a528e9d7619d61528406eba50991d3c271d931','public_key_b64':'Ea42z0bGTzGzHxVqkk38uauwCmhMI6/YIb56aUK7vwg=','public_sha256':'47b8a1993581ff309a8d68cabec2070bebde30d4a5ff258071a91b43f7f3c7f4','repository':REPO,'run_id':'40424627162','session_id':'8265e63081b962715df68ca17ef090a0','version':LEGACY_INGRESS_VERSION}
BOUNDS = {'TB':('tongbang',25),'DR':('directive',78),'RL':('reserve-law',34),'MS':('military-service',166)}

def enc(v):
    return json.dumps(v, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')
def b64(v): return base64.b64encode(v).decode('ascii')
def unb(v):
    if not isinstance(v,str): raise ValueError('base64 type')
    return base64.b64decode(v,validate=True)
def digest(v): return hashlib.sha256(v).hexdigest()
def raw_public(k): return k.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
def _bytes(v,n=None):
    if not isinstance(v,bytes) or (n is not None and len(v)!=n): raise ValueError('byte size/type')
def _keys(v,names):
    if not isinstance(v,dict) or set(v)!=set(names): raise ValueError('schema')
def _hex(v,n): return isinstance(v,str) and re.fullmatch('[a-f0-9]{'+str(n)+'}',v) is not None

def namespace_for(lesson_id):
    q=re.fullmatch(r'(TB|DR|RL|MS)-([0-9]{3})',lesson_id) if isinstance(lesson_id,str) else None
    if not q: raise ValueError('lesson id')
    name,limit=BOUNDS[q[1]]
    if not 1<=int(q[2])<=limit: raise ValueError('lesson bound')
    if q[1]=='MS':name='military-service-a' if int(q[2])<=83 else 'military-service-b'
    return 'integrated-lectures/v1/'+name

def validate_public(public):
    _keys(public,PINNED_PUBLIC)
    if public!=PINNED_PUBLIC or public['repository']!=REPO or public['version']!=LEGACY_INGRESS_VERSION: raise ValueError('receiver pin')
    if not _hex(public['commit'],40) or not _hex(public['session_id'],32) or not re.fullmatch('[0-9]{1,20}',public['run_id']): raise ValueError('receiver identity')
    key=unb(public['public_key_b64'])
    if len(key)!=32 or digest(key)!=public['public_sha256']: raise ValueError('receiver key pin')
    return key

def _derive(material,salt,domain,label):
    return HKDF(algorithm=hashes.SHA256(),length=32,salt=salt,info=enc([domain,label])).derive(material)

def _unwrap_legacy(source_key,public,wrapped):
    _bytes(source_key,32);validate_public(public)
    _keys(wrapped,('public','salt_b64','nonce_b64','sealed_private_b64'))
    if wrapped['public']!=public: raise ValueError('wrapper identity')
    salt,nonce,ct=(unb(wrapped[k]) for k in ('salt_b64','nonce_b64','sealed_private_b64'))
    if (len(salt),len(nonce),len(ct))!=(32,12,48): raise ValueError('wrapper size')
    # Deliberately exact legacy HKDF and AAD: existing receiver must remain usable.
    key=_derive(source_key,salt,LEGACY_INGRESS_VERSION,'receiver-wrap')
    private=X25519PrivateKey.from_private_bytes(AESGCM(key).decrypt(nonce,ct,enc(public)))
    if raw_public(private.public_key())!=validate_public(public): raise ValueError('unwrapped identity')
    return private

def _header(public,lesson_id,sender,salt,nonce,bundle):
    validate_public(public);_bytes(sender,32);_bytes(salt,32);_bytes(nonce,12)
    if not _hex(bundle,32): raise ValueError('bundle id')
    return {'version':INGRESS_VERSION,'receiver':dict(public),'namespace':namespace_for(lesson_id),'lesson_id':lesson_id,'bundle_id':bundle,'sender_public_b64':b64(sender),'salt_b64':b64(salt),'nonce_b64':b64(nonce)}

def seal_input(public,lesson_id,plain):
    _bytes(plain)
    if not 0<len(plain)<=MAX_INPUT: raise ValueError('input size')
    receiver=validate_public(public);namespace_for(lesson_id)
    sender=X25519PrivateKey.generate();salt=os.urandom(32);nonce=os.urandom(12)
    h=_header(public,lesson_id,raw_public(sender.public_key()),salt,nonce,os.urandom(16).hex())
    key=_derive(sender.exchange(X25519PublicKey.from_public_bytes(receiver)),salt,INGRESS_VERSION,'input-seal')
    ct=AESGCM(key).encrypt(nonce,plain,enc(h))
    return {'header':h,'cipher_sha256':digest(ct)},ct

def open_input(source_key,public,wrapped,record,cipher,expected_id):
    namespace_for(expected_id);_bytes(cipher);_keys(record,('header','cipher_sha256'))
    if not 16<len(cipher)<=MAX_INPUT+16 or digest(cipher)!=record['cipher_sha256']: raise ValueError('cipher pin')
    h=record['header'];_keys(h,('version','receiver','namespace','lesson_id','bundle_id','sender_public_b64','salt_b64','nonce_b64'))
    if h['lesson_id']!=expected_id or h['receiver']!=public: raise ValueError('expected input')
    sender,salt,nonce=(unb(h[k]) for k in ('sender_public_b64','salt_b64','nonce_b64'))
    if h!=_header(public,expected_id,sender,salt,nonce,h['bundle_id']): raise ValueError('input context')
    receiver=_unwrap_legacy(source_key,public,wrapped)
    key=_derive(receiver.exchange(X25519PublicKey.from_public_bytes(sender)),salt,INGRESS_VERSION,'input-seal')
    return AESGCM(key).decrypt(nonce,cipher,enc(h))

MEDIA_FIELDS=('version','key_id','namespace','lesson_id','kind','object_id','salt_b64','nonce_prefix_b64','plain_bytes','chunk_bytes','cipher_sha256')
def context(m): return [MEDIA_VERSION,m['key_id'],m['namespace'],m['lesson_id'],m['kind'],m['object_id']]
def _validate_media_header(m):
    _keys(m,MEDIA_FIELDS)
    if m['version']!=MEDIA_VERSION or m['namespace']!=namespace_for(m['lesson_id']): raise ValueError('version/namespace')
    if m['kind'] not in ('audio','private-metadata','checkpoint'): raise ValueError('kind')
    if not isinstance(m['key_id'],str) or not re.fullmatch('[a-z0-9-]{1,64}',m['key_id']): raise ValueError('key id')
    if not _hex(m['object_id'],32): raise ValueError('object id')
    if len(unb(m['salt_b64']))!=32 or len(unb(m['nonce_prefix_b64']))!=8: raise ValueError('salt/nonce')
    limit=MAX_CHECKPOINT if m['kind']=='checkpoint' else (MAX_PRIVATE_METADATA if m['kind']=='private-metadata' else MAX_MEDIA)
    if type(m['plain_bytes']) is not int or not 0<m['plain_bytes']<=limit: raise ValueError('size')
    if type(m['chunk_bytes']) is not int or m['chunk_bytes']!=CHUNK: raise ValueError('chunk size')
    if not isinstance(m['cipher_sha256'],list): raise ValueError('hash list')

def validate(m,blob):
    _validate_media_header(m);_bytes(blob)
    count=(m['plain_bytes']+CHUNK-1)//CHUNK
    if len(m['cipher_sha256'])!=count or len(blob)!=m['plain_bytes']+16*count: raise ValueError('truncated/extra chunks')
    if any(not _hex(x,64) for x in m['cipher_sha256']): raise ValueError('hash encoding')

def key_for(master,m):
    _bytes(master,32)
    return HKDF(algorithm=hashes.SHA256(),length=32,salt=unb(m['salt_b64']),info=enc(context(m))).derive(master)
def aad(m,i): return enc(context(m)+[i,m['plain_bytes'],m['chunk_bytes']])

def seal(master,plain,lesson_id,kind,key_id,*,namespace=None):
    _bytes(master,32);_bytes(plain);expected=namespace_for(lesson_id)
    if namespace is not None and namespace!=expected: raise ValueError('namespace mismatch')
    m={'version':MEDIA_VERSION,'key_id':key_id,'namespace':expected,'lesson_id':lesson_id,'kind':kind,'object_id':os.urandom(16).hex(),'salt_b64':b64(os.urandom(32)),'nonce_prefix_b64':b64(os.urandom(8)),'plain_bytes':len(plain),'chunk_bytes':CHUNK,'cipher_sha256':[]}
    _validate_media_header(m);a=AESGCM(key_for(master,m));out=[];prefix=unb(m['nonce_prefix_b64'])
    for i,start in enumerate(range(0,len(plain),CHUNK)):
        ct=a.encrypt(prefix+struct.pack('>I',i),plain[start:start+CHUNK],aad(m,i));out.append(ct);m['cipher_sha256'].append(digest(ct))
    blob=b''.join(out);validate(m,blob);return m,blob

def _expected(m,expected_id,expected_kind=None,expected_key_id=None):
    # Checkpoints are never consumable through the default audio/metadata path.
    # Recovery must explicitly request expected_kind='checkpoint'.
    if m['kind']=='checkpoint' and expected_kind!='checkpoint': raise ValueError('checkpoint recovery opt-in required')
    if m['lesson_id']!=expected_id or m['namespace']!=namespace_for(expected_id): raise ValueError('expected media identity')
    if expected_kind is not None and m['kind']!=expected_kind: raise ValueError('expected media kind')
    if expected_key_id is not None and m['key_id']!=expected_key_id: raise ValueError('expected media key')

def open_chunk(master,m,blob,i,*,expected_id,expected_kind=None,expected_key_id=None):
    validate(m,blob);_expected(m,expected_id,expected_kind,expected_key_id)
    if type(i) is not int or not 0<=i<len(m['cipher_sha256']): raise ValueError('chunk index')
    start=i*(CHUNK+16);ct=blob[start:min(start+CHUNK+16,len(blob))]
    if digest(ct)!=m['cipher_sha256'][i]: raise ValueError('cipher hash')
    return AESGCM(key_for(master,m)).decrypt(unb(m['nonce_prefix_b64'])+struct.pack('>I',i),ct,aad(m,i))

def parse_range(value,size,*,max_size=None):
    limit=MAX_MEDIA if max_size is None else max_size
    if type(limit) is not int or limit not in (MAX_MEDIA,MAX_CHECKPOINT): raise ValueError('range limit')
    if type(size) is not int or not 0<size<=limit: raise ValueError('size')
    if value is None: return 0,size-1,200
    if not isinstance(value,str) or len(value)>128: raise ValueError('400: malformed range')
    if ',' in value: raise ValueError('400: multiple ranges unsupported')
    q=re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})',value)
    if not q or not any(q.groups()): raise ValueError('400: malformed range')
    a,b=q.groups()
    if not a:
        tail=int(b)
        if tail==0: raise ValueError('416: unsatisfiable')
        return max(0,size-tail),size-1,206
    start=int(a);end=min(int(b),size-1) if b else size-1
    if start>=size or end<start: raise ValueError('416: unsatisfiable')
    return start,end,206

def open_range(master,m,blob,value=None,*,expected_id,expected_kind=None,expected_key_id=None):
    validate(m,blob);_expected(m,expected_id,expected_kind,expected_key_id)
    a,b,status=parse_range(value,m['plain_bytes'],max_size=MAX_CHECKPOINT if m['kind']=='checkpoint' else MAX_MEDIA);first,last=a//CHUNK,b//CHUNK
    # Authenticate each needed whole chunk before returning any plaintext bytes.
    full=b''.join(open_chunk(master,m,blob,i,expected_id=expected_id,expected_kind=expected_kind,expected_key_id=expected_key_id) for i in range(first,last+1))
    data=full[a-first*CHUNK:b-first*CHUNK+1]
    return data,{'status':status,'content_length':len(data),'content_range':f"bytes {a}-{b}/{m['plain_bytes']}" if status==206 else None}
