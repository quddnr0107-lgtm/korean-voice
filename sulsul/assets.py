import hashlib,json,pathlib,urllib.request
ROOT=pathlib.Path(__file__).resolve().parent
meta=json.loads((ROOT/'model-provenance.json').read_text());target=pathlib.Path('supertonic3')
def valid(data,item):
    expected=item.get('lfs',{}).get('sha256')
    if len(data)!=item['size']:return False
    if expected:return hashlib.sha256(data).hexdigest()==expected
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==item['blobId']
for item in meta['siblings']:
    name=item['rfilename']
    if not (name.startswith('onnx/') or name in ['voice_styles/F2.json','voice_styles/F4.json']):continue
    dst=target/name;dst.parent.mkdir(parents=True,exist_ok=True)
    expected=item.get('lfs',{}).get('sha256')
    if dst.exists() and valid(dst.read_bytes(),item):continue
    url='https://huggingface.co/Supertone/supertonic-3/resolve/'+meta['sha']+'/'+name
    tmp=dst.with_suffix('.download')
    with urllib.request.urlopen(url,timeout=120) as response:tmp.write_bytes(response.read())
    if not valid(tmp.read_bytes(),item):raise ValueError('Model/style checksum mismatch: '+name)
    tmp.replace(dst);print(name,dst.stat().st_size)
