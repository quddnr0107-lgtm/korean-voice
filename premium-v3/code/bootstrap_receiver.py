"""Approved protected receiver generation. SOURCE stays within this process."""
import base64,json,os,socket,sys
from pathlib import Path
from sealed_ingress import bootstrap,enc,digest

def main():
 if os.environ.get('GITHUB_REPOSITORY')!='quddnr0107-lgtm/korean-voice' or os.environ.get('GITHUB_RUN_ATTEMPT')!='1':raise ValueError('run scope')
 if os.environ.get('GITHUB_REF')!='refs/heads/admin-law-v2-premium-20261026':raise ValueError('branch scope')
 def deny(*a,**kw):raise RuntimeError('network disabled')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 value=os.environ.pop('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64',None)
 if not value:raise ValueError('source required')
 key=base64.b64decode(value,validate=True);value=None
 public,wrapped=bootstrap(key,os.environ['GITHUB_SHA'],os.environ['GITHUB_RUN_ID']);key=None
 out=Path(os.environ['RECEIVER_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
 if any(out.iterdir()):raise ValueError('fresh artifact directory')
 a=enc(public);b=enc(wrapped)
 (out/'receiver-public.json').write_bytes(a);(out/'receiver-wrapped.json').write_bytes(b)
 print(json.dumps({'status':'receiver_sealed','public_sha256':public['public_sha256'],'public_record_sha256':digest(a),'wrapped_record_sha256':digest(b)}))
if __name__=='__main__':
 try:main()
 except Exception:print('{"status":"failed","stage":"receiver-bootstrap"}');sys.exit(1)
