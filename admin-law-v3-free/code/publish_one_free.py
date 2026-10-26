"""Keyless immutable free asset publication. Never clobbers or deletes assets."""
import json,os,sys,subprocess,tempfile
from pathlib import Path
from private_pipeline import *
REPO='quddnr0107-lgtm/korean-voice'
def gh(*args):return subprocess.run(['gh',*args,'--repo',REPO],capture_output=True,check=True,timeout=120).stdout

def main():
 scope=json.loads(Path(sys.argv[1]).read_bytes());data=json.loads(Path(sys.argv[2]).read_bytes());root=Path(sys.argv[3]);ident=scope['id'];track=scope['track']
 if os.environ['GITHUB_REPOSITORY']!=REPO or not 1<=data['lesson']['number']<=10 or validate_input(data)!=ident or data['track']!=track or sha(Path(sys.argv[2]).read_bytes())!=scope['input_sha256']:raise ValueError('free scope')
 name='admin-law-v3-'+track+'-'+ident;mp3=root/(name+'.mp3');js=root/(name+'.json')
 if {x.name for x in root.iterdir()}!={mp3.name,js.name}:raise ValueError('output allowlist')
 audio=mp3.read_bytes();meta=json.loads(js.read_bytes());measured=('index','text','start_s','end_s','speed','tail_hum','attempts')
 expected=private_metadata(data,audio,[{k:s[k] for k in measured} for s in meta['segments']],meta['validation'])
 expected.update(access='free',source_commit=os.environ['GITHUB_SHA'],chunk_policy='contiguous-max180-v1',seed=20261026+data['lesson']['number']-1)
 if expected!=meta or meta['validation']['clipped_samples']!=0 or meta['validation']['tail_hum_chunks']!=0 or meta['validation']['max_silence_s']>=4:raise ValueError('metadata validation')
 tag='admin-law-v3-'+track+'-free-20261027'
 try:release=json.loads(gh('release','view',tag,'--json','assets'))
 except subprocess.CalledProcessError:
  try:gh('release','create',tag,'--target',os.environ['GITHUB_SHA'],'--title','Admin law '+track+' free','--notes','Additive track. Partial until final exact10 manifest is published. Legacy unchanged.','--latest=false')
  except subprocess.CalledProcessError:pass
  release=json.loads(gh('release','view',tag,'--json','assets'))
 assets={a['name']:a for a in release['assets']}
 for p in (mp3,js):
  if p.name in assets:
   with tempfile.TemporaryDirectory() as td:
    gh('release','download',tag,'--pattern',p.name,'--dir',td)
    if Path(td,p.name).read_bytes()!=p.read_bytes():raise ValueError('immutable asset conflict')
  else:gh('release','upload',tag,str(p))
 for p in (mp3,js):
  with tempfile.TemporaryDirectory() as td:
   gh('release','download',tag,'--pattern',p.name,'--dir',td)
   if sha(Path(td,p.name).read_bytes())!=sha(p.read_bytes()):raise ValueError('published hash')
 print(json.dumps({'id':ident,'track':track,'status':'published_verified','tag':tag,'audio_sha256':sha(audio),'metadata_sha256':sha(js.read_bytes())},separators=(',',':')))
if __name__=='__main__':
 try:main()
 except Exception:print('{"status":"failed","stage":"free-publication"}');sys.exit(1)
