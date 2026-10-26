"""Local prototype only; no operational secrets, files, network, or deployment."""
import base64,hashlib,json,os,re,struct
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
VERSION='admin-law-premium/v1'
CHUNK=65536
b64=lambda b:base64.b64encode(b).decode('ascii')
def unb64(s):return base64.b64decode(s,validate=True)
def jbytes(v):return json.dumps(v,separators=(',',':'),ensure_ascii=True).encode('ascii')
def context(m):return [VERSION,m['key_id'],m['namespace'],m['lesson_id'],m['kind'],m['object_id']]
def key_for(master,m):
 if len(master)!=32:raise ValueError('master must be 32 bytes')
 return HKDF(algorithm=hashes.SHA256(),length=32,salt=unb64(m['salt_b64']),info=jbytes(context(m))).derive(master)
def aad(m,i):return jbytes(context(m)+[i,m['plain_bytes'],m['chunk_bytes']])
def validate(m,blob):
 if m['version']!=VERSION or m['namespace']!='admin-law/v2':raise ValueError('version/namespace')
 if not re.fullmatch(r'AL-0(?:1[1-9]|[2-6][0-9]|7[0-8])',m['lesson_id']):raise ValueError('premium lesson only')
 if m['kind'] not in ('source','audio','private-metadata'):raise ValueError('kind')
 if not re.fullmatch(r'[a-z0-9-]{1,64}',m['key_id']):raise ValueError('key id')
 if not re.fullmatch(r'[a-f0-9]{32}',m['object_id']):raise ValueError('object id')
 if len(unb64(m['salt_b64']))!=32 or len(unb64(m['nonce_prefix_b64']))!=8:raise ValueError('salt/nonce')
 n=m['plain_bytes'];c=m['chunk_bytes']
 if type(n)!=int or not 0<n<=64*1024*1024 or c!=CHUNK:raise ValueError('size')
 count=(n+c-1)//c
 if len(m['cipher_sha256'])!=count or len(blob)!=n+16*count:raise ValueError('truncated/extra chunks')
 if any(not re.fullmatch(r'[a-f0-9]{64}',x) for x in m['cipher_sha256']):raise ValueError('hash encoding')
def seal(master,plain,lesson_id,kind,key_id='admin-law-premium-v1'):
 if not plain:raise ValueError('empty object')
 m={'version':VERSION,'key_id':key_id,'namespace':'admin-law/v2','lesson_id':lesson_id,'kind':kind,'object_id':os.urandom(16).hex(),'salt_b64':b64(os.urandom(32)),'nonce_prefix_b64':b64(os.urandom(8)),'plain_bytes':len(plain),'chunk_bytes':CHUNK,'cipher_sha256':[]}
 cipher=AESGCM(key_for(master,m));out=[]
 for i,start in enumerate(range(0,len(plain),CHUNK)):
  iv=unb64(m['nonce_prefix_b64'])+struct.pack('>I',i)
  ct=cipher.encrypt(iv,plain[start:start+CHUNK],aad(m,i));out.append(ct);m['cipher_sha256'].append(hashlib.sha256(ct).hexdigest())
 blob=b''.join(out);validate(m,blob);return m,blob
def open_chunk(master,m,blob,i):
 validate(m,blob)
 if not 0<=i<len(m['cipher_sha256']):raise ValueError('chunk index')
 start=i*(CHUNK+16);ct=blob[start:min(start+CHUNK+16,len(blob))]
 if hashlib.sha256(ct).hexdigest()!=m['cipher_sha256'][i]:raise ValueError('cipher hash')
 iv=unb64(m['nonce_prefix_b64'])+struct.pack('>I',i)
 return AESGCM(key_for(master,m)).decrypt(iv,ct,aad(m,i))
def parse_range(value,size):
 if value is None:return 0,size-1,200
 if ',' in value:raise ValueError('400: multiple ranges unsupported')
 q=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
 if not q or not any(q.groups()):raise ValueError('400: malformed range')
 a,b=q.groups()
 if not a:
  tail=int(b)
  if tail==0:raise ValueError('416: unsatisfiable')
  return max(0,size-tail),size-1,206
 start=int(a);end=min(int(b),size-1) if b else size-1
 if start>=size or end<start:raise ValueError('416: unsatisfiable')
 return start,end,206
def open_range(master,m,blob,value=None):
 validate(m,blob);a,b,status=parse_range(value,m['plain_bytes'])
 first,last=a//CHUNK,b//CHUNK
 # Authenticate all needed complete chunks before returning any response bytes.
 full=b''.join(open_chunk(master,m,blob,i) for i in range(first,last+1))
 data=full[a-first*CHUNK:b-first*CHUNK+1]
 return data,{'status':status,'content_length':len(data),'content_range':f"bytes {a}-{b}/{m['plain_bytes']}" if status==206 else None}
