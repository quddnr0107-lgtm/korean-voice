"""Anonymous immutable premium release verification; no keys or writes to remote services."""
import contextvars,functools,socket,ssl,threading,urllib.error,http.client
import base64,concurrent.futures,hashlib,json,os,re,sys,urllib.parse,urllib.request
from pathlib import Path
REPO='quddnr0107-lgtm/korean-voice'
BRANCH='refs/heads/admin-law-v3-premium-verify-20261027'
HOSTS={'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}
TAGS={'source':'admin-law-v3-source-premium-20261027','easy':'admin-law-v3-easy-r2-premium-20261027'}
CAP=64*1024*1024;INDEX_CAP=1024*1024;CHUNK=65536
MKEYS={'version','key_id','namespace','lesson_id','kind','object_id','salt_b64','nonce_prefix_b64','plain_bytes','chunk_bytes','cipher_sha256'}
sha=lambda b:hashlib.sha256(b).hexdigest()
# Diagnostic context is per worker; never stringify exceptions, URLs or response bodies.
_CONTEXT=contextvars.ContextVar('safe_diagnostic',default={})
STAGES={'run-context','index-fetch','index-parse','object-full','object-range'}
EXCEPTIONS=(urllib.error.HTTPError,urllib.error.URLError,ssl.SSLError,socket.timeout,
            TimeoutError,ConnectionError,FileNotFoundError,PermissionError,
            json.JSONDecodeError,ValueError,KeyError,TypeError,OSError,http.client.HTTPException)
class DiagnosticError(ValueError):
 def __init__(self,report):self.report=report;super().__init__('sanitized verification failure')
def mark(stage,number=None,kind_code=None):
 if stage not in STAGES:raise ValueError('diagnostic stage')
 d={'stage':stage}
 if type(number)==int and 11<=number<=78:d['number']=number
 if type(kind_code)==int and kind_code in (0,1):d['kind_code']=kind_code
 _CONTEXT.set(d)
def status_seen(status):
 d=dict(_CONTEXT.get())
 if type(status)==int and 100<=status<=599:d['http_status']=status
 _CONTEXT.set(d)
def numeric_headers(headers,status=None):
 # Never retain headers; only bounded decimal fields with fixed output keys.
 lower={k.lower():v for k,v in headers.items()}
 d=dict(_CONTEXT.get())
 cl=lower.get('content-length','')
 if isinstance(cl,str) and re.fullmatch('[0-9]{1,19}',cl):d['content_length']=int(cl)
 cr=lower.get('content-range','')
 match=re.fullmatch(r'bytes ([0-9]{1,19})-([0-9]{1,19})/([0-9]{1,19})',cr) if isinstance(cr,str) else None
 if match:
  for key,value in zip(('range_start','range_end','range_total'),match.groups()):d[key]=int(value)
 retry=lower.get('retry-after','')
 if status in (403,429) and isinstance(retry,str) and re.fullmatch('[0-9]{1,10}',retry):d['retry_after_s']=int(retry)
 _CONTEXT.set(d)
def safe_exception(e):
 d=dict(_CONTEXT.get());d['failed']=True
 d['exception_class']=next((t.__name__ for t in EXCEPTIONS if isinstance(e,t)),'OtherError')
 if isinstance(e,urllib.error.HTTPError):
  numeric_headers(e.headers or {},e.code);d=dict(_CONTEXT.get());d.update(failed=True,exception_class='HTTPError')
  if type(e.code)==int and 100<=e.code<=599:d['http_status']=e.code
 # URLError.reason can contain a URL/body. Only a fixed recognized class is emitted.
 if isinstance(e,urllib.error.URLError) and not isinstance(e,urllib.error.HTTPError):
  d['reason_class']=next((t.__name__ for t in (ssl.SSLError,socket.gaierror,TimeoutError,ConnectionError,OSError) if isinstance(e.reason,t)),'OtherError')
 return d
def diagnostic(fn):
 @functools.wraps(fn)
 def wrapped(*a,**kw):
  try:return fn(*a,**kw)
  except DiagnosticError:raise
  except Exception as e:raise DiagnosticError(safe_exception(e)) from None
 return wrapped

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
@diagnostic
def fetch(url,cap,range_value=None):
 safe_url(url);headers={'Accept-Encoding':'identity'}
 if range_value:headers['Range']=range_value
 # An isolated opener has no cookie jar, authentication handler, or token headers.
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirect())
 with opener.open(urllib.request.Request(url,headers=headers),timeout=60) as r:
  status_seen(r.status);numeric_headers(r.headers,r.status)
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

@diagnostic
def verify_object(job,request=fetch,probe_suffix=False):
 number,key,url,m,size=job
 mark('object-full',number,0 if key=='audio' else 1)
 status,_,body=request(url,CAP)
 status_seen(status)
 if status!=200 or len(body)!=size:raise ValueError('full response')
 for i,h in enumerate(m['cipher_sha256']):
  if sha(body[i*(CHUNK+16):(i+1)*(CHUNK+16)])!=h:raise ValueError('cipher chunk digest')
 probe={}
 if probe_suffix and number==11 and key=='audio':
  mark('object-range',number,0);_CONTEXT.set({**_CONTEXT.get(),'range_code':2})
  try:
   ps,ph,pb=request(url,16,'bytes=-16');status_seen(ps);numeric_headers(ph,ps);ph={k.lower():v for k,v in ph.items()}
  except (DiagnosticError,urllib.error.HTTPError) as error:
   ps=error.report.get('http_status') if isinstance(error,DiagnosticError) else error.code
   if ps!=501:raise
   probe={'suffix_probe_status':501,'suffix_verified':0}
  else:
   if ps!=206 or ph.get('content-range')!=f'bytes {size-16}-{size-1}/{size}' or len(pb)!=16 or pb!=body[-16:]:raise ValueError('suffix capability evidence')
   probe={'suffix_probe_status':206,'suffix_verified':1}
 for spec,start,end in [('bytes=0-15',0,15),(f'bytes={size-16}-{size-1}',size-16,size-1)]:
  mark('object-range',number,0 if key=='audio' else 1)
  _CONTEXT.set({**_CONTEXT.get(),'range_code':0 if spec=='bytes=0-15' else 1})
  status,headers,part=request(url,16,spec);status_seen(status);numeric_headers(headers,status);headers={k.lower():v for k,v in headers.items()}
  if status!=206 or headers.get('content-range')!=f'bytes {start}-{end}/{size}' or len(part)!=16 or part!=body[start:end+1]:raise ValueError('Range evidence')
 return {'number':number,'kind_code':0 if key=='audio' else 1,'bytes':size,'cipher_sha256':sha(body),'chunks':len(m['cipher_sha256']),'range_checks':2,'range_status':206,**probe}

def verify_batch(jobs,request=fetch):
 # At most8 submitted/inflight objects; stop new HTTP before cancelling queued work.
 stop=threading.Event();lock=threading.Lock();errors=[]
 def checked_request(*a,**kw):
  if stop.is_set():raise concurrent.futures.CancelledError()
  return request(*a,**kw)
 def work(job):
  try:
   if stop.is_set():raise concurrent.futures.CancelledError()
   return verify_object(job,request=checked_request)
  except Exception as error:
   with lock:
    if not errors:errors.append(error)
    stop.set()
   raise
 pool=concurrent.futures.ThreadPoolExecutor(max_workers=8);pending={};results={};iterator=iter(enumerate(jobs))
 try:
  for _ in range(min(8,len(jobs))):
   i,job=next(iterator);pending[pool.submit(work,job)]=i
  while pending:
   done,_=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
   if errors:raise errors[0]
   for future in done:results[pending.pop(future)]=future.result()
   for _ in done:
    if stop.is_set():raise errors[0]
    try:i,job=next(iterator)
    except StopIteration:break
    pending[pool.submit(work,job)]=i
  return [results[i] for i in range(len(jobs))]
 except Exception:
  stop.set()
  for future in pending:future.cancel()
  if errors:raise errors[0]
  raise
 finally:pool.shutdown(wait=True,cancel_futures=True)

@diagnostic
def main():
 mark('run-context')
 if os.environ.get('GITHUB_REPOSITORY')!=REPO or os.environ.get('GITHUB_REF')!=BRANCH or os.environ.get('GITHUB_RUN_ATTEMPT')!='1':raise ValueError('run scope')
 if any(os.environ.get(k) for k in ('GH_TOKEN','GITHUB_TOKEN','ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','ADMIN_LAW_PREMIUM_MEDIA_KEY_B64')):raise ValueError('unexpected credential')
 scope=loads(Path(sys.argv[1]).read_bytes());scope_checked(scope)
 mark('index-fetch')
 status,_,raw=fetch(scope['prefix']+'index.json',INDEX_CAP)
 status_seen(status)
 if status!=200:raise ValueError('index status')
 mark('index-parse')
 jobs=parse_index(raw,scope)
 # Probe AL011 before queuing other objects:403/429 cannot start a bulk fallback.
 first=verify_object(jobs[0],probe_suffix=True)
 results=[first]+verify_batch(jobs[1:])
 result={'verified_objects':len(results),'verified_lessons':68,'range_checks':2*len(results),'index_sha256':sha(raw),'code_commit':os.environ['GITHUB_SHA'],'suffix_probe_status':results[0]['suffix_probe_status'],'suffix_verified':results[0]['suffix_verified'],'objects':results}
 Path(os.environ['VERIFY_OUTPUT']).write_text(json.dumps(result,separators=(',',':')));print(json.dumps({'verified_objects':len(results),'range_checks':2*len(results),'index_sha256':sha(raw),'suffix_probe_status':results[0]['suffix_probe_status'],'suffix_verified':results[0]['suffix_verified']}))
if __name__=='__main__':
 try:main()
 except DiagnosticError as e:print(json.dumps(e.report,separators=(',',':')));sys.exit(1)
 except Exception:print('{"failed":true,"exception_class":"OtherError"}');sys.exit(1)
