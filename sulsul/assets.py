import hashlib,json,pathlib,urllib.request
ROOT=pathlib.Path(__file__).resolve().parent
meta=json.loads((ROOT/'model-provenance.json').read_text());target=pathlib.Path('supertonic3')
for item in meta['siblings']:
    name=item['rfilename']
    if not (name.startswith('onnx/') or name in ['voice_styles/F2.json','voice_styles/F4.json']):continue
    dst=target/name;dst.parent.mkdir(parents=True,exist_ok=True)
    expected=item.get('lfs',{}).get('sha256')
    if dst.exists() and (not expected or hashlib.sha256(dst.read_bytes()).hexdigest()==expected):continue
    url='https://huggingface.co/Supertone/supertonic-3/resolve/'+meta['sha']+'/'+name
    tmp=dst.with_suffix('.download')
    with urllib.request.urlopen(url,timeout=120) as response:tmp.write_bytes(response.read())
    if expected and hashlib.sha256(tmp.read_bytes()).hexdigest()!=expected:raise ValueError('Model checksum mismatch: '+name)
    tmp.replace(dst);print(name,dst.stat().st_size)
