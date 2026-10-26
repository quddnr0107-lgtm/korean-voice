"""Exactly one pinned free lesson, no credentials and no remote publishing."""
import json,os,sys,tempfile
from pathlib import Path
from private_pipeline import *
from audio_backend import Backend,suppress_native_output,validate_audio,validate_checkpoint_metrics
from chunk_plan import chunks

def main():
 scope=json.loads(Path(sys.argv[1]).read_bytes());raw=Path(sys.argv[2]).read_bytes();data=json.loads(raw)
 if set(scope)!={'id','track','input_sha256'} or scope['id']!=validate_input(data) or scope['track']!=data['track'] or sha(raw)!=scope['input_sha256']:raise ValueError('scope pin')
 number=data['lesson']['number']
 if not 1<=number<=10:raise ValueError('free only')
 if os.environ['GITHUB_REPOSITORY']!='quddnr0107-lgtm/korean-voice' or os.environ['GITHUB_RUN_ATTEMPT']!='1':raise ValueError('run scope')
 out=Path(os.environ['ADMIN_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
 if any(out.iterdir()):raise ValueError('fresh outputs')
 with suppress_native_output():
  backend=Backend(Path(os.environ['ADMIN_ASSETS']),Path(sys.argv[3]),20261026+number-1)
  with tempfile.TemporaryDirectory(prefix='admin-law-free-') as td:audio,segments,metrics=backend.render(data['lesson']['narration_text'],td)
  meta=private_metadata(data,audio,segments,metrics)
  validate_checkpoint_metrics(meta,validate_audio(audio,backend.ff))
 meta.update(access='free',source_commit=os.environ['GITHUB_SHA'],chunk_policy='contiguous-max180-v1',seed=20261026+number-1)
 name='admin-law-v3-'+scope['track']+'-'+scope['id']
 (out/(name+'.mp3')).write_bytes(audio);(out/(name+'.json')).write_bytes(encoded(meta))
 print(json.dumps({'id':scope['id'],'track':scope['track'],'status':'validated','duration_s':metrics['decoded_duration_s'],'audio_sha256':sha(audio),'segments':len(segments)},separators=(',',':')))
if __name__=='__main__':
 try:main()
 except Exception:print('{"status":"failed","stage":"free-render"}');sys.exit(1)
