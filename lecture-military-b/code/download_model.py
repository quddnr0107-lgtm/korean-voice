"""Public assets only, before any protected key injection."""
import hashlib,json,sys,urllib.request,urllib.error,time
from pathlib import Path
root=Path(__file__).resolve().parents[1];p=json.loads((root/'model-pins.json').read_text());out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
for name,h in p['sha256'].items():
 if name not in ('config.json','LICENSE','onnx/duration_predictor.onnx','onnx/text_encoder.onnx','onnx/tts.json','onnx/unicode_indexer.json','onnx/vector_estimator.onnx','onnx/vocoder.onnx','voice_styles/F2.json','py/helper.py'):raise ValueError('asset allowlist')
 dest=out/name;dest.parent.mkdir(parents=True,exist_ok=True)
 url=p['helper_url'] if name=='py/helper.py' else f"https://huggingface.co/{p['repository']}/resolve/{p['revision']}/{name}"
 if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()==h:continue
 for attempt in range(4):
  try:
   with urllib.request.urlopen(url,timeout=180) as r:data=r.read(512*1024*1024)
   break
  except (urllib.error.URLError,TimeoutError):
   if attempt==3:raise
   time.sleep((1,4,10)[attempt])
 if hashlib.sha256(data).hexdigest()!=h:raise ValueError('model digest mismatch')
 dest.write_bytes(data)
print('{"status":"model_pins_verified"}')
