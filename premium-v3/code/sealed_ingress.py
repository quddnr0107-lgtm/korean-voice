"""One-session input sealing. Pure helpers, no environment/files/network access."""
import base64,hashlib,json,os,re
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey,X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
VERSION='admin-law-v3-ingress/v1'
REPO='quddnr0107-lgtm/korean-voice'
def enc(v):return json.dumps(v,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()
def b64(v):return base64.b64encode(v).decode()
def unb(v):return base64.b64decode(v,validate=True)
def digest(v):return hashlib.sha256(v).hexdigest()
def derive(key,salt,label):return HKDF(algorithm=hashes.SHA256(),length=32,salt=salt,info=enc([VERSION,label])).derive(key)
def raw_private(k):return k.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
def raw_public(k):return k.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
def validate_public(p):
 if set(p)!={'version','repository','commit','run_id','session_id','public_key_b64','public_sha256'}:raise ValueError('receiver schema')
 if p['version']!=VERSION or p['repository']!=REPO or not re.fullmatch('[a-f0-9]{40}',p['commit']) or not re.fullmatch('[0-9]{1,20}',p['run_id']) or not re.fullmatch('[a-f0-9]{32}',p['session_id']):raise ValueError('receiver identity')
 pub=unb(p['public_key_b64'])
 if len(pub)!=32 or digest(pub)!=p['public_sha256']:raise ValueError('receiver public pin')
 return pub

def bootstrap(source_key,commit,run_id):
 if len(source_key)!=32:raise ValueError('source key size')
 private=X25519PrivateKey.generate();pub=raw_public(private.public_key())
 p={'version':VERSION,'repository':REPO,'commit':commit,'run_id':str(run_id),'session_id':os.urandom(16).hex(),'public_key_b64':b64(pub),'public_sha256':digest(pub)};validate_public(p)
 salt=os.urandom(32);nonce=os.urandom(12)
 sealed=AESGCM(derive(source_key,salt,'receiver-wrap')).encrypt(nonce,raw_private(private),enc(p))
 w={'public':p,'salt_b64':b64(salt),'nonce_b64':b64(nonce),'sealed_private_b64':b64(sealed)}
 return p,w

def unwrap(source_key,p,w):
 validate_public(p)
 if set(w)!={'public','salt_b64','nonce_b64','sealed_private_b64'} or w['public']!=p:raise ValueError('wrapper identity')
 salt=unb(w['salt_b64']);nonce=unb(w['nonce_b64']);ct=unb(w['sealed_private_b64'])
 if len(source_key)!=32 or len(salt)!=32 or len(nonce)!=12 or len(ct)!=48:raise ValueError('wrapper size')
 k=X25519PrivateKey.from_private_bytes(AESGCM(derive(source_key,salt,'receiver-wrap')).decrypt(nonce,ct,enc(p)))
 if raw_public(k.public_key())!=validate_public(p):raise ValueError('unwrapped identity')
 return k

def header(p,track,lesson_id,sender,salt,nonce,bundle_id):
 validate_public(p)
 if track not in ('easy','source') or not re.fullmatch(r'AL-0(?:1[1-9]|[2-6][0-9]|7[0-8])',lesson_id):raise ValueError('premium track/id')
 return {'version':VERSION,'receiver':p,'namespace':'admin-law/v3/'+track,'track':track,'lesson_id':lesson_id,'bundle_id':bundle_id,'sender_public_b64':b64(sender),'salt_b64':b64(salt),'nonce_b64':b64(nonce)}

def seal_input(p,track,lesson_id,plain):
 if not 0<len(plain)<=2*1024*1024:raise ValueError('input size')
 public=validate_public(p);sender=X25519PrivateKey.generate();salt=os.urandom(32);nonce=os.urandom(12)
 h=header(p,track,lesson_id,raw_public(sender.public_key()),salt,nonce,os.urandom(16).hex())
 key=derive(sender.exchange(X25519PublicKey.from_public_bytes(public)),salt,'input-seal')
 ct=AESGCM(key).encrypt(nonce,plain,enc(h))
 return {'header':h,'cipher_sha256':digest(ct)},ct

def open_input(source_key,p,w,record,ct,*,expected_track,expected_id):
 if set(record)!={'header','cipher_sha256'} or digest(ct)!=record['cipher_sha256'] or not 16<len(ct)<=2*1024*1024+16:raise ValueError('cipher pin')
 h=record['header']
 if set(h)!={'version','receiver','namespace','track','lesson_id','bundle_id','sender_public_b64','salt_b64','nonce_b64'} or h['receiver']!=p:raise ValueError('input header')
 if h['track']!=expected_track or h['lesson_id']!=expected_id or not re.fullmatch('[a-f0-9]{32}',h['bundle_id']):raise ValueError('expected input')
 sender=unb(h['sender_public_b64']);salt=unb(h['salt_b64']);nonce=unb(h['nonce_b64'])
 if len(sender)!=32 or len(salt)!=32 or len(nonce)!=12 or h!=header(p,expected_track,expected_id,sender,salt,nonce,h['bundle_id']):raise ValueError('input context')
 receiver=unwrap(source_key,p,w);key=derive(receiver.exchange(X25519PublicKey.from_public_bytes(sender)),salt,'input-seal')
 return AESGCM(key).decrypt(nonce,ct,enc(h))
