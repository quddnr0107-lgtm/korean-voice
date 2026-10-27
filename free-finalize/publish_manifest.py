"""Exact free manifest publication after anonymous public-byte verification."""
import hashlib,json,os,re,subprocess,sys,tempfile,urllib.request,urllib.parse
from pathlib import Path
REPO='quddnr0107-lgtm/korean-voice'
sha=lambda b:hashlib.sha256(b).hexdigest()
class Redirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  u=urllib.parse.urlparse(newurl)
  if u.scheme!='https' or u.hostname not in ('github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'):raise ValueError('redirect host')
  return super().redirect_request(req,fp,code,msg,headers,newurl)
opener=urllib.request.build_opener(Redirect())
def gh(*a):return subprocess.run(['gh',*a,'--repo',REPO],capture_output=True,check=True,timeout=120).stdout

def main():
 raw=Path(sys.argv[1]).read_bytes();scope=json.loads(Path(sys.argv[2]).read_bytes());m=json.loads(raw)
 if os.environ['GITHUB_REPOSITORY']!=REPO or os.environ['GITHUB_RUN_ATTEMPT']!='1' or sha(raw)!=scope['manifest_sha256'] or len(raw)>2*1024*1024:raise ValueError('manifest pin')
 track=scope['track'];tag={'easy':'admin-law-v3-easy-r2-free-20261027','source':'admin-law-v3-source-free-20261027'}[track];revision={'easy':'easy-r2-20261027','source':'rebuild-r1-20261027'}[track];prefix=f'https://github.com/{REPO}/releases/download/{tag}/'
 if m['namespace']!='admin-law/v3/'+track or m['track']!=track or m['revision']!=revision or m['status']!='complete' or m['count']!=10 or m['premium_audio_included'] is not False or len(m['lessons'])!=10:raise ValueError('manifest scope')
 evidence=[]
 for n,l in enumerate(m['lessons'],1):
  ident=f'AL-{n:03}';url=prefix+f'admin-law-v3-{track}-{ident}.mp3'
  if l['id']!=ident or l['number']!=n or l['track']!=track or l['namespace']!=m['namespace'] or l['access']!='free' or l['audio_url']!=url or not l['narration_text'] or not re.fullmatch('[a-f0-9]{64}',l['audio_sha256']):raise ValueError('lesson scope')
  with opener.open(urllib.request.Request(url,headers={'Range':'bytes=0-15','Accept-Encoding':'identity'}),timeout=60) as r:
   cr=r.headers.get('Content-Range','');body=r.read(17)
   if r.status!=206 or len(body)!=16 or not re.fullmatch(r'bytes 0-15/[0-9]+',cr):raise ValueError('range response')
  digest=hashlib.sha256();size=0
  with opener.open(urllib.request.Request(url,headers={'Accept-Encoding':'identity'}),timeout=120) as r:
   if r.status!=200:raise ValueError('download status')
   while True:
    b=r.read(1024*1024)
    if not b:break
    size+=len(b)
    if size>64*1024*1024:raise ValueError('download bound')
    digest.update(b)
  if digest.hexdigest()!=l['audio_sha256']:raise ValueError('anonymous audio hash')
  evidence.append({'id':ident,'bytes':size,'audio_sha256':digest.hexdigest(),'range_status':206,'content_range':cr})
 names={a['name'] for a in json.loads(gh('release','view',tag,'--json','assets'))['assets']}
 if 'manifest.json' not in names:gh('release','upload',tag,sys.argv[1])
 with tempfile.TemporaryDirectory() as td:
  gh('release','download',tag,'--pattern','manifest.json','--dir',td)
  if (Path(td)/'manifest.json').read_bytes()!=raw:raise ValueError('immutable manifest conflict')
 with opener.open(prefix+'manifest.json',timeout=60) as r:
  if r.status!=200 or sha(r.read(2*1024*1024+1))!=scope['manifest_sha256']:raise ValueError('public manifest hash')
 result={'track':track,'revision':revision,'manifest_sha256':scope['manifest_sha256'],'manifest_url':prefix+'manifest.json','verified_lessons':10,'lessons':evidence}
 Path(os.environ['VERIFY_OUTPUT']).write_text(json.dumps(result));print(json.dumps({k:v for k,v in result.items() if k!='lessons'}))
if __name__=='__main__':
 try:main()
 except Exception:print('{"status":"failed","stage":"free-final-manifest"}');sys.exit(1)
