import json,os,hashlib,subprocess,ast
from pathlib import Path
p=Path(os.environ['ADMIN_OUT']);here=Path(__file__).resolve().parent
# Read recipe literal without importing or executing renderer.
mod=ast.parse((here/'render_free.py').read_text());recipe=next(ast.literal_eval(n.value) for n in mod.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='RECIPE' for t in n.targets))
corpus=json.loads((here/'free-corpus.json').read_text());assert len(corpus['lessons'])==10
lessons=[]
for source in corpus['lessons']:
 n=source['number'];assert 1<=n<=10;ident=f'admin-law-v2-AL-{n:03d}'
 m=json.loads((p/(ident+'.json')).read_text())
 key=hashlib.sha256(json.dumps({'text_sha':source['narration_sha256'],'recipe':recipe},sort_keys=True).encode()).hexdigest()
 assert m['id']==ident and m['number']==n and m['access']=='free' and m['voice']==recipe and m['checkpoint_hash']==key and m['narration_text']==source['narration_text']
 assert m['validation']['clipped_samples']==0 and m['validation']['tail_hum_chunks']==0 and m['validation']['max_silence_s']<4
 subprocess.run(['gh','release','download','admin-law-v2-free-20261026','--repo','quddnr0107-lgtm/korean-voice','--pattern',ident+'.mp3','--dir',str(p)],check=True)
 assert hashlib.sha256((p/(ident+'.mp3')).read_bytes()).hexdigest()==m['audio_sha256']
 lessons.append(m)
assert [x['number'] for x in lessons]==list(range(1,11))
manifest={'namespace':'admin-law/v2/free','status':'complete','expected':10,'count':10,'source_sha256':corpus['source_sha256'],'voice':recipe,'lessons':lessons,'premium_audio_included':False,'listening_verified':False}
(p/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));print('Complete free lessons',len(lessons))
