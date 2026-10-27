"""Anonymous immutable premium release verification; no keys or writes to remote services."""
import base64,concurrent.futures,hashlib,json,os,re,sys,urllib.parse,urllib.request
from pathlib import Path
REPO='quddnr0107-lgtm/korean-voice'
BRANCH='refs/heads/admin-law-v3-premium-verify-20261027'
HOSTS={'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}
TAGS={'source':'admin-law-v3-source-premium-20261027','easy':'admin-law-v3-easy-r2-premium-20261027'}
CAP=64*1024*1024;INDEX_CAP=1024*1024;CHUNK=65536
MKEYS={'version','key_id','namespace','lesson_id','kind','object_id','salt_b64','nonce_prefix_b64','plain_bytes','chunk_bytes','cipher_sha256'}
sha=lambda b:hashlib.sha256(b).hexdigest()
def unique(pairs):
 d={}
 for k,v in pairs:
  if k in d:raise ValueError('duplicate JSON')
  d[k]=v
 return d
def loads(raw):return json.loads(raw,object_pairs_hook=unique)
def safe_url(url):
 u=urllib.parse.urlsplit(url)
 if u.scheme!='https' or u.hostname not in HOSTS or u.username or u.password or u.port not in (None,443) or u.fragment:raise ValueError('URL policy')
 return url
class Redirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  safe_url(newurl)
  return super().redirect_request(req,fp,code,msg,headers,newurl)
def fetch(url,cap,range_value=None):
 safe_url(url);headers={'Accept-Encoding':'identity'}
 if range_value:headers['Range']=range_value
 # An isolated opener has no cookie jar, authentication handler, or token headers.
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirect())
 with opener.open(urllib.request.Request(url,headers=headers),timeout=60) as r:
  safe_url(r.geturl());body=r.read(cap+1)
  if len(body)>cap or r.headers.get('Content-Encoding','identity') not in ('','identity'):raise ValueError('body bound/encoding')
  cl=r.headers.get('Content-Length')
  if cl is not None and (not cl.isdecimal() or int(cl)!=len(body)):raise ValueError('content length')
  return r.status,dict(r.headers.items()),body

def scope_checked(scope):
 if set(scope)!={'track','index_sha256','prefix'} or scope['track'] not in TAGS:raise ValueError('scope')
 if not re.fullmatch('[a-f0-9]{64}',scope['index_sha256']):raise ValueError('scope pin')
 prefix=f'https://github.com/{REPO}/releases/download/{TAGS[scope["track"]]}/'
 if scope['prefix']!=prefix:raise ValueError('prefix')
 return 'admin-law/v3/'+scope['track']
def descriptor(m,namespace,ident,kind):
 if set(m)!=MKEYS or m['version']!='admin-law-premium/v1' or m['key_id']!='admin-law-premium-media-v1' or m['namespace']!=namespace or m['lesson_id']!=ident or m['kind']!=kind:raise ValueError('descriptor context')
 if not re.fullmatch('[a-f0-9]{32}',m['object_id']):raise ValueError('object id')
 if len(base64.b64decode(m['salt_b64'],validate=True))!=32 or len(base64.b64decode(m['nonce_prefix_b64'],validate=True))!=8:raise ValueError('salt/nonce')
 n=m['plain_bytes']
 if type(n)!=int or not 0<n<=CAP or type(m['chunk_bytes'])!=int or m['chunk_bytes']!=CHUNK or not isinstance(m['cipher_sha256'],list):raise ValueError('size')
 count=(n+CHUNK-1)//CHUNK
 if len(m['cipher_sha256'])!=count or any(not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h) for h in m['cipher_sha256']):raise ValueError('chunk hashes')
 size=n+16*count
 if size>CAP or (kind=='private-metadata' and n>INDEX_CAP):raise ValueError('consumer object bound')
 return size

def parse_index(raw,scope):
 if len(raw)>INDEX_CAP or sha(raw)!=scope['index_sha256']:raise ValueError('index digest')
 m=loads(raw);namespace='admin-law/v3/'+scope['track']
 if set(m)!={'version','namespace','lessons'} or m['version']!='admin-law-premium-index/v1' or m['namespace']!=namespace or not isinstance(m['lessons'],list) or len(m['lessons'])!=68:raise ValueError('index schema')
 jobs=[]
 for n,row in enumerate(m['lessons'],11):
  ident=f'AL-{n:03}'
  if set(row)!={'id','audio','content'} or row['id']!=ident:raise ValueError('exact68 ids')
  for key,kind in [('audio','audio'),('content','private-metadata')]:
   obj=row[key]
   if set(obj)!={'manifest','cipher_url'} or obj['cipher_url']!=scope['prefix']+ident+'.'+key+'.bin':raise ValueError('cipher URL')
   size=descriptor(obj['manifest'],namespace,ident,kind)
   jobs.append((n,key,obj['cipher_url'],obj['manifest'],size))
 return jobs

def verify_object(job,request=fetch):
 number,key,url,m,size=job
 status,_,body=request(url,CAP)
 if status!=200 or len(body)!=size:raise ValueError('full response')
 for i,h in enumerate(m['cipher_sha256']):
  if sha(body[i*(CHUNK+16):(i+1)*(CHUNK+16)])!=h:raise ValueError('cipher chunk digest')
 for spec,start,end in [('bytes=0-15',0,15),('bytes=-16',size-16,size-1)]:
  status,headers,part=request(url,16,spec);headers={k.lower():v for k,v in headers.items()}
  if status!=206 or headers.get('content-range')!=f'bytes {start}-{end}/{size}' or len(part)!=16 or part!=body[start:end+1]:raise ValueError('Range evidence')
 return {'number':number,'kind_code':0 if key=='audio' else 1,'bytes':size,'cipher_sha256':sha(body),'chunks':len(m['cipher_sha256']),'range_checks':2,'range_status':206}

def main():
 if os.environ.get('GITHUB_REPOSITORY')!=REPO or os.environ.get('GITHUB_REF')!=BRANCH or os.environ.get('GITHUB_RUN_ATTEMPT')!='1':raise ValueError('run scope')
 if any(os.environ.get(k) for k in ('GH_TOKEN','GITHUB_TOKEN','ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')):raise ValueError('unexpected credential')
 scope=loads(Path(sys.argv[1]).read_bytes());scope_checked(scope)
 status,_,raw=fetch(scope['prefix']+'index.json',INDEX_CAP)
 if status!=200:raise ValueError('index status')
 jobs=parse_index(raw,scope)
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(verify_object,jobs))
 result={'verified_objects':len(results),'verified_lessons':68,'range_checks':2*len(results),'index_sha256':sha(raw),'code_commit':os.environ['GITHUB_SHA'],'objects':results}
 Path(os.environ['VERIFY_OUTPUT']).write_text(json.dumps(result,separators=(',',':')));print(json.dumps({'verified_objects':len(results),'range_checks':2*len(results),'index_sha256':sha(raw)}))
if __name__=='__main__':
 try:main()
 except Exception:print('{"failed":true}');sys.exit(1)
