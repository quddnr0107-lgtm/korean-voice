"""One-shot memory-only key transfer primitives; operational plaintext keys never serialized.
Uses X25519/HKDF-SHA256/AES-256-GCM. Public envelopes are safe to persist;
receiver private keys and unsealed MEDIA exist only in the runner process.
"""
import base64, hashlib, json, re, secrets, time
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
VERSION='yebijun-media-transfer/v1'
REPO='quddnr0107-lgtm/yebijun-muhanhoidok234'
WORKER='yebijun-sync'
SECRET='INTEGRATED_LECTURE_MEDIA_KEY_B64'
RECEIVER_WORKFLOW_REF=REPO+'/.github/workflows/integrated-media-register.yml@refs/heads/codex/integrated-media-register-20261028'
MAX_PACKET=4096
HEADER_FIELDS={'version','receiver_repo','receiver_run_id','receiver_run_attempt','receiver_workflow_ref','receiver_workflow_sha','receiver_pub_b64','nonce','issued_at','expires_at','worker','secret'}
PACKET_FIELDS={'header','sender_pub_b64','salt_b64','iv_b64','cipher_b64'}
class TransferError(Exception): pass

def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise TransferError('duplicate JSON field')
        result[key]=value
    return result

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
def b64(value): return base64.b64encode(value).decode('ascii')
def unb(value,size):
    if not isinstance(value,str): raise TransferError('encoding')
    try: raw=base64.b64decode(value,validate=True)
    except Exception: raise TransferError('encoding') from None
    if len(raw)!=size or b64(raw)!=value: raise TransferError('encoding')
    return raw

def public_bytes(key): return key.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
def validate_header(h,now):
    if not isinstance(h,dict) or set(h)!=HEADER_FIELDS: raise TransferError('header schema')
    if h['version']!=VERSION or h['receiver_repo']!=REPO or h['worker']!=WORKER or h['secret']!=SECRET: raise TransferError('target')
    if not isinstance(h['receiver_run_id'],str) or not re.fullmatch(r'[1-9][0-9]{0,19}',h['receiver_run_id']): raise TransferError('run')
    if not isinstance(h['receiver_run_attempt'],str) or not re.fullmatch(r'[1-9][0-9]{0,5}',h['receiver_run_attempt']): raise TransferError('attempt')
    if not isinstance(h['nonce'],str) or not re.fullmatch('[a-f0-9]{64}',h['nonce']): raise TransferError('nonce')
    if h['receiver_workflow_ref']!=RECEIVER_WORKFLOW_REF or not isinstance(h['receiver_workflow_sha'],str) or not re.fullmatch('[a-f0-9]{40}',h['receiver_workflow_sha']):raise TransferError('workflow scope')
    unb(h['receiver_pub_b64'],32)
    if type(h['issued_at']) is not int or type(h['expires_at']) is not int or not 0<h['expires_at']-h['issued_at']<=540 or now<h['issued_at']-5 or now>=h['expires_at']: raise TransferError('expired')
    return h

def create_receiver(run_id,attempt,now=None,workflow_sha=None):
    now=int(time.time()) if now is None else now
    private=X25519PrivateKey.generate()
    h={'version':VERSION,'receiver_repo':REPO,'receiver_run_id':str(run_id),'receiver_run_attempt':str(attempt),'receiver_workflow_ref':RECEIVER_WORKFLOW_REF,'receiver_workflow_sha':workflow_sha,'receiver_pub_b64':b64(public_bytes(private.public_key())),'nonce':secrets.token_hex(32),'issued_at':now,'expires_at':now+540,'worker':WORKER,'secret':SECRET}
    validate_header(h,now);return private,h

def _derive(shared,h,sender_pub,salt):
    return HKDF(algorithm=hashes.SHA256(),length=32,salt=salt,info=VERSION.encode()+b'\0'+canonical(h)+sender_pub+unb(h['receiver_pub_b64'],32)).derive(shared)

def seal_packet(media,h,now=None):
    now=int(time.time()) if now is None else now;validate_header(h,now)
    if not isinstance(media,(bytes,bytearray)) or len(media)!=32: raise TransferError('media size')
    sender=X25519PrivateKey.generate();sp=public_bytes(sender.public_key());salt=secrets.token_bytes(32);iv=secrets.token_bytes(12)
    key=_derive(sender.exchange(X25519PublicKey.from_public_bytes(unb(h['receiver_pub_b64'],32))),h,sp,salt)
    packet={'header':dict(h),'sender_pub_b64':b64(sp),'salt_b64':b64(salt),'iv_b64':b64(iv)}
    packet['cipher_b64']=b64(AESGCM(key).encrypt(iv,bytes(media),canonical(packet)))
    raw=canonical(packet)
    if len(raw)>MAX_PACKET: raise TransferError('packet size')
    return raw

def open_packet(private,expected,raw,now=None):
    now=int(time.time()) if now is None else now;validate_header(expected,now)
    if not isinstance(raw,bytes) or len(raw)>MAX_PACKET: raise TransferError('packet size')
    try: p=json.loads(raw,object_pairs_hook=unique_object)
    except Exception: raise TransferError('packet JSON') from None
    if not isinstance(p,dict) or set(p)!=PACKET_FIELDS or p['header']!=expected: raise TransferError('packet scope')
    if b64(public_bytes(private.public_key()))!=expected['receiver_pub_b64']: raise TransferError('receiver mismatch')
    sp=unb(p['sender_pub_b64'],32);salt=unb(p['salt_b64'],32);iv=unb(p['iv_b64'],12);cipher=unb(p['cipher_b64'],48)
    aad={k:v for k,v in p.items() if k!='cipher_b64'}
    try:
        key=_derive(private.exchange(X25519PublicKey.from_public_bytes(sp)),expected,sp,salt)
        return bytearray(AESGCM(key).decrypt(iv,cipher,canonical(aad)))
    except Exception: raise TransferError('packet authentication') from None

def confirm_old_media(media,manifest,cipher):
    """Caller must pin original public manifest and ciphertext first. No plaintext return."""
    m=manifest
    if m.get('version')!='admin-law-premium/v1' or m.get('namespace')!='admin-law/v3/source' or m.get('lesson_id')!='AL-011' or m.get('kind')!='audio' or m.get('key_id')!='admin-law-premium-media-v1' or m.get('chunk_bytes')!=65536: raise TransferError('proof scope')
    if len(cipher)!=min(m['plain_bytes'],65536)+16 or hashlib.sha256(cipher).hexdigest()!=m['cipher_sha256'][0]: raise TransferError('proof ciphertext')
    context=[m['version'],m['key_id'],m['namespace'],m['lesson_id'],m['kind'],m['object_id']]
    try:
        key=HKDF(algorithm=hashes.SHA256(),length=32,salt=unb(m['salt_b64'],32),info=canonical(context)).derive(bytes(media))
        plain=bytearray(AESGCM(key).decrypt(unb(m['nonce_prefix_b64'],8)+b'\0\0\0\0',cipher,canonical(context+[0,m['plain_bytes'],65536])))
        plain[:]=b'\0'*len(plain)
    except Exception: raise TransferError('media key confirmation') from None
    return True

class Receiver:
    def __init__(self,run_id,attempt,now=None,workflow_sha=None): self._private,self.header=create_receiver(run_id,attempt,now,workflow_sha);self._used=False
    def consume(self,raw,now=None):
        if self._used: raise TransferError('already consumed')
        self._used=True
        return open_packet(self._private,self.header,raw,now)
