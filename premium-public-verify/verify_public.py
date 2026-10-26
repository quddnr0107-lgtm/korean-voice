"""Unauthenticated, bounded public ciphertext verification. No key/decryption."""
import hashlib,json,re,subprocess,tempfile
from pathlib import Path
PREFIX='https://github.com/quddnr0107-lgtm/korean-voice/releases/download/admin-law-v2-premium-20261026/'
IDS={f'AL-{n:03}' for n in range(11,79)}
FIELDS={'version','key_id','namespace','lesson_id','kind','object_id','salt_b64','nonce_prefix_b64','plain_bytes','chunk_bytes','cipher_sha256'}
MAX_PLAIN=64*1024*1024
# Strict download budget: ciphertext exceeding 64MiB fails closed.
MAX_CIPHER=64*1024*1024
sha=lambda x:hashlib.sha256(x).hexdigest()
def fetch(url,directory,name,cap,byte_range=None):
 body=directory/(name+'.body');headers=directory/(name+'.headers')
 args=['curl','--silent','--show-error','--fail','--location','--max-redirs','5','--proto','=https','--proto-redir','=https','--connect-timeout','20','--max-time','120','--max-filesize',str(cap),'-D',str(headers),'-o',str(body),'-w','%{http_code}',url]
 if byte_range:args += ['-H','Range: bytes='+byte_range]
 r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=125,check=True)
 b=body.read_bytes()
 if len(b)>cap:raise ValueError('download cap')
 # Never retain signed redirect Location or raw headers in the proof artifact.
 blocks=re.split(rb'\r?\n\r?\n',headers.read_bytes());last=[x for x in blocks if x.startswith(b'HTTP/')][-1]
 fields={}
 for line in last.splitlines()[1:]:
  if b':' in line:
   k,v=line.split(b':',1);fields[k.decode('ascii').lower()]=v.decode('ascii').strip()
 headers.unlink();body.unlink()
 return int(r.stdout),fields,b

def manifest(m,ident,kind):
 import base64
 if set(m)!=FIELDS:raise ValueError('manifest schema')
 if (m['version'],m['namespace'],m['key_id'],m['lesson_id'],m['kind'])!=('admin-law-premium/v1','admin-law/v2','admin-law-premium-media-v1',ident,kind):raise ValueError('manifest scope')
 if not re.fullmatch('[a-f0-9]{32}',m['object_id']):raise ValueError('object id')
 for k,n in [('salt_b64',32),('nonce_prefix_b64',8)]:
  if len(base64.b64decode(m[k],validate=True))!=n:raise ValueError('encoding')
 n=m['plain_bytes']
 if type(n)!=int or not 0<n<=MAX_PLAIN or type(m['chunk_bytes'])!=int or m['chunk_bytes']!=65536:raise ValueError('size')
 if kind=='private-metadata' and n>1048576:raise ValueError('metadata size')
 if not isinstance(m['cipher_sha256'],list) or len(m['cipher_sha256'])!=(n+65535)//65536 or any(not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h) for h in m['cipher_sha256']):raise ValueError('chunks')
 return n+16*len(m['cipher_sha256'])

def validate_index(index):
 if set(index)!={'version','namespace','lessons'} or index['version']!='admin-law-premium-index/v1' or index['namespace']!='admin-law/v2':raise ValueError('index schema')
 if not isinstance(index['lessons'],list) or len(index['lessons'])!=68:raise ValueError('lesson count')
 seen=set()
 for lesson in index['lessons']:
  if set(lesson)!={'id','audio','content'}:raise ValueError('lesson schema')
  ident=lesson['id']
  if ident not in IDS or ident in seen:raise ValueError('lesson scope')
  seen.add(ident)
  for field,kind in [('audio','audio'),('content','private-metadata')]:
   item=lesson[field]
   if set(item)!={'manifest','cipher_url'} or item['cipher_url']!=PREFIX+ident+'.'+field+'.bin':raise ValueError('url scope')
   manifest(item['manifest'],ident,kind)
 if seen!=IDS:raise ValueError('id set')

def main():
 out=Path('proof');out.mkdir(exist_ok=False)
 with tempfile.TemporaryDirectory() as tmp:
  tmp=Path(tmp);status,_,raw=fetch(PREFIX+'index.json',tmp,'index',2*1024*1024)
  if status!=200:raise ValueError('index status')
  index=json.loads(raw);validate_index(index);proof={'index_sha256':sha(raw),'index_bytes':len(raw),'lessons':68,'objects':[]}
  for lesson in index['lessons']:
   for field in ('audio','content'):
    item=lesson[field];m=item['manifest'];expected=m['plain_bytes']+16*len(m['cipher_sha256'])
    status,_,blob=fetch(item['cipher_url'],tmp,'full',MAX_CIPHER)
    if status!=200 or len(blob)!=expected:raise ValueError('cipher length/status')
    for n,h in enumerate(m['cipher_sha256']):
     if sha(blob[n*65552:(n+1)*65552])!=h:raise ValueError('cipher hash')
    entry={'id':lesson['id'],'kind':field,'cipher_bytes':len(blob),'cipher_sha256':sha(blob),'chunk_hashes_verified':len(m['cipher_sha256']),'ranges':[]}
    # Metadata can be shorter than 64KiB; late then means its final sixteen bytes.
    for start in (0,min(65536,len(blob)-16)):
     end=start+15;status,headers,part=fetch(item['cipher_url'],tmp,'range',1024,f'{start}-{end}')
     expected_range=f'bytes {start}-{end}/{len(blob)}'
     if status!=206 or headers.get('content-range')!=expected_range or len(part)!=16 or part!=blob[start:end+1]:raise ValueError('range semantics')
     entry['ranges'].append({'status':status,'content_range':expected_range,'bytes':16,'sha256':sha(part),'matches_full_cipher':True})
    proof['objects'].append(entry)
  (out/'index.json').write_bytes(raw);(out/'verification.json').write_text(json.dumps(proof,separators=(',',':')))
 print('Verified 68 lessons / 136 ciphertext objects; no decryption performed.')
if __name__=='__main__':
 try:main()
 except Exception:raise SystemExit('Public ciphertext verification failed; no body or signed URL logged.')
