"""Pure v2 segmented MP3 encryption, metadata validation, and pack construction.
No IO, network, environment access, operational keys, or playback dependencies.
Each independently decodable MP3 part is authenticated with its exact placement.
"""
import base64,copy,hashlib,json,os,re,struct
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from lecture_crypto import namespace_for

PART_VERSION='integrated-lecture-audio-part/v2'
PACK_VERSION='integrated-lecture-pack/v2'
KEY_ID='integrated-lectures-media-v1'
SAMPLE_RATE=44100
CHUNK=65536
MAX_PART_BYTES=2*1024*1024
MAX_PART_SAMPLES=60*SAMPLE_RATE
MAX_PACK_BYTES=128*1024*1024
MAX_PARTS=1440
MAX_TOTAL_SAMPLES=SAMPLE_RATE*86400
MAX_SAFE_INTEGER=2**53-1
PART_FIELDS={'version','kind','key_id','namespace','lesson_id','object_id','pack_id','part_index','start_sample','end_sample','sample_rate','pack_offset','cipher_bytes','plain_bytes','chunk_bytes','salt_b64','nonce_prefix_b64','cipher_sha256'}
PACK_FIELDS={'version','kind','key_id','namespace','lesson_id','object_id','sample_rate','total_samples','pack_bytes','pack_sha256','parts'}

def enc(value):return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')
def digest(data):return hashlib.sha256(data).hexdigest()
def _hex(value,size):return isinstance(value,str) and re.fullmatch('[a-f0-9]{'+str(size)+'}',value) is not None
def _b64(value):return base64.b64encode(value).decode('ascii')
def _unb(value,size):
    if not isinstance(value,str):raise ValueError('base64 type')
    raw=base64.b64decode(value,validate=True)
    if len(raw)!=size or _b64(raw)!=value:raise ValueError('base64 size/canonical encoding')
    return raw

def _integer(value,low=0,high=MAX_SAFE_INTEGER):
    if type(value) is not int or not low<=value<=high:raise ValueError('integer bound')
def _bytes(value,size=None):
    if not isinstance(value,bytes) or (size is not None and len(value)!=size):raise ValueError('bytes type/size')
def _schema(value,fields):
    if not isinstance(value,dict) or set(value)!=fields:raise ValueError('schema')

def part_context(m):
    # Exact ordered contract, shared by HKDF info and AES-GCM AAD.
    return [PART_VERSION,m['key_id'],m['namespace'],m['lesson_id'],m['kind'],m['object_id'],m['pack_id'],m['part_index'],m['start_sample'],m['end_sample'],m['sample_rate'],m['pack_offset'],m['cipher_bytes'],m['plain_bytes']]
def part_aad(m,chunk_index):return enc(part_context(m)+[chunk_index,m['chunk_bytes']])
def _part_key(master,m):
    _bytes(master,32)
    return HKDF(algorithm=hashes.SHA256(),length=32,salt=_unb(m['salt_b64'],32),info=enc(part_context(m))).derive(master)

def _validate_part_header(m):
    _schema(m,PART_FIELDS)
    if m['version']!=PART_VERSION or m['kind']!='audio-part' or m['key_id']!=KEY_ID:raise ValueError('part version/kind/key')
    if m['namespace']!=namespace_for(m['lesson_id']):raise ValueError('part namespace')
    if not _hex(m['object_id'],32) or not _hex(m['pack_id'],32):raise ValueError('part object identity')
    _integer(m['part_index'],0,MAX_PARTS-1)
    _integer(m['start_sample'],0,MAX_TOTAL_SAMPLES-1);_integer(m['end_sample'],1,MAX_TOTAL_SAMPLES)
    if not 0<m['end_sample']-m['start_sample']<=MAX_PART_SAMPLES:raise ValueError('part duration')
    if type(m['sample_rate']) is not int or m['sample_rate']!=SAMPLE_RATE:raise ValueError('sample rate')
    if type(m['chunk_bytes']) is not int or m['chunk_bytes']!=CHUNK:raise ValueError('chunk size')
    _integer(m['plain_bytes'],1,MAX_PART_BYTES)
    count=(m['plain_bytes']+CHUNK-1)//CHUNK
    _integer(m['cipher_bytes'],17,MAX_PART_BYTES+16*((MAX_PART_BYTES+CHUNK-1)//CHUNK))
    if m['cipher_bytes']!=m['plain_bytes']+16*count:raise ValueError('cipher size')
    _integer(m['pack_offset'],0,MAX_PACK_BYTES-17)
    if m['pack_offset']+m['cipher_bytes']>MAX_PACK_BYTES:raise ValueError('pack placement cap')
    _unb(m['salt_b64'],32);_unb(m['nonce_prefix_b64'],8)
    if not isinstance(m['cipher_sha256'],list) or len(m['cipher_sha256'])!=count or any(not _hex(x,64) for x in m['cipher_sha256']):raise ValueError('cipher hash list')

def _check_cipher_hashes(m,cipher):
    if len(cipher)!=m['cipher_bytes']:raise ValueError('cipher length')
    view=memoryview(cipher)
    for i,h in enumerate(m['cipher_sha256']):
        if digest(view[i*(CHUNK+16):(i+1)*(CHUNK+16)])!=h:raise ValueError('cipher chunk digest')

def validate_part(m,cipher=None):
    """Validate a public part header and, when provided, its ciphertext bytes."""
    _validate_part_header(m)
    if cipher is not None:
        _bytes(cipher);_check_cipher_hashes(m,cipher)

def seal_part(master,plain,lesson_id,pack_id,part_index,start_sample,end_sample,pack_offset,key_id=KEY_ID):
    _bytes(master,32);_bytes(plain)
    if not 0<len(plain)<=MAX_PART_BYTES:raise ValueError('part plaintext cap')
    count=(len(plain)+CHUNK-1)//CHUNK
    m={'version':PART_VERSION,'kind':'audio-part','key_id':key_id,'namespace':namespace_for(lesson_id),'lesson_id':lesson_id,'object_id':os.urandom(16).hex(),'pack_id':pack_id,'part_index':part_index,'start_sample':start_sample,'end_sample':end_sample,'sample_rate':SAMPLE_RATE,'pack_offset':pack_offset,'cipher_bytes':len(plain)+16*count,'plain_bytes':len(plain),'chunk_bytes':CHUNK,'salt_b64':_b64(os.urandom(32)),'nonce_prefix_b64':_b64(os.urandom(8)),'cipher_sha256':['0'*64]*count}
    _validate_part_header(m)
    cipher=AESGCM(_part_key(master,m));prefix=_unb(m['nonce_prefix_b64'],8);parts=[]
    for i,start in enumerate(range(0,len(plain),CHUNK)):
        part=cipher.encrypt(prefix+struct.pack('>I',i),plain[start:start+CHUNK],part_aad(m,i))
        parts.append(part);m['cipher_sha256'][i]=digest(part)
    blob=b''.join(parts);validate_part(m,blob);return m,blob

def open_part(master,part_manifest,cipher,*,expected_id,expected_part_index,expected_pack_id):
    m=part_manifest;validate_part(m,cipher)
    _integer(expected_part_index,0,2**32-1)
    if not _hex(expected_pack_id,32):raise ValueError('expected pack id')
    if m['lesson_id']!=expected_id or m['namespace']!=namespace_for(expected_id) or m['part_index']!=expected_part_index or m['pack_id']!=expected_pack_id:raise ValueError('expected part context')
    a=AESGCM(_part_key(master,m));prefix=_unb(m['nonce_prefix_b64'],8);plain=[]
    # Do not return any plaintext until every required AEAD chunk authenticates.
    for i in range(len(m['cipher_sha256'])):
        start=i*(CHUNK+16);ct=cipher[start:start+CHUNK+16]
        plain.append(a.decrypt(prefix+struct.pack('>I',i),ct,part_aad(m,i)))
    result=b''.join(plain)
    if len(result)!=m['plain_bytes']:raise ValueError('plaintext size')
    return result

def validate_pack(m,blob=None):
    """Strict complete coverage: indices, timeline and byte offsets start at zero.
    With blob=None this checks manifest structure, not ciphertext authenticity.
    With bytes, verifies the whole-pack digest plus all per-AEAD-chunk digests.
    AEAD authentication itself is provided by open_part with the secret key.
    """
    _schema(m,PACK_FIELDS)
    if m['version']!=PACK_VERSION or m['kind']!='audio-pack' or m['key_id']!=KEY_ID:raise ValueError('pack version/kind/key')
    if m['namespace']!=namespace_for(m['lesson_id']) or not _hex(m['object_id'],32):raise ValueError('pack identity')
    if type(m['sample_rate']) is not int or m['sample_rate']!=SAMPLE_RATE:raise ValueError('pack sample rate')
    _integer(m['total_samples'],1,MAX_TOTAL_SAMPLES);_integer(m['pack_bytes'],17,MAX_PACK_BYTES)
    if not _hex(m['pack_sha256'],64) or not isinstance(m['parts'],list) or not 1<=len(m['parts'])<=MAX_PARTS:raise ValueError('pack digest/parts')
    offset=sample=0;objects=set()
    for i,p in enumerate(m['parts']):
        validate_part(p)
        if p['lesson_id']!=m['lesson_id'] or p['namespace']!=m['namespace'] or p['key_id']!=m['key_id'] or p['pack_id']!=m['object_id']:raise ValueError('pack/part scope')
        if p['part_index']!=i or p['start_sample']!=sample or p['pack_offset']!=offset:raise ValueError('part gap/overlap/order')
        if p['object_id'] in objects:raise ValueError('duplicate part object id')
        objects.add(p['object_id']);sample=p['end_sample'];offset+=p['cipher_bytes']
    if sample!=m['total_samples'] or offset!=m['pack_bytes']:raise ValueError('pack totals')
    if blob is not None:
        _bytes(blob)
        if len(blob)!=m['pack_bytes'] or digest(blob)!=m['pack_sha256']:raise ValueError('pack digest/length')
        view=memoryview(blob)
        for p in m['parts']:_check_cipher_hashes(p,view[p['pack_offset']:p['pack_offset']+p['cipher_bytes']])

def make_pack_manifest(lesson_id,packid,parts,blob):
    _bytes(blob)
    if not isinstance(parts,list) or not parts:raise ValueError('parts required')
    m={'version':PACK_VERSION,'kind':'audio-pack','key_id':KEY_ID,'namespace':namespace_for(lesson_id),'lesson_id':lesson_id,'object_id':packid,'sample_rate':SAMPLE_RATE,'total_samples':parts[-1]['end_sample'],'pack_bytes':len(blob),'pack_sha256':digest(blob),'parts':copy.deepcopy(parts)}
    validate_pack(m,blob);return m
