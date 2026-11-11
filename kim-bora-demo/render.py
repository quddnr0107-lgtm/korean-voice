"""One owner-requested lecture; source stays sealed, result goes only to recipient."""
import os,sys,json,base64,hashlib,io,contextlib
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey,X25519PublicKey
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
R=Path(__file__).resolve().parent
sys.path.insert(0,str(R.parent/'lecture-military-b/code'))
import lecture_crypto as c

def main():
 idx=int(sys.argv[1]);cfg=json.loads((R/'config.json').read_text());assert 0<=idx<cfg['count']
 val=os.environ.pop('ADMIN_LAW_PREMIUM_SOURCE_KEY_B64','');key=base64.b64decode(val,validate=True);val=None
 if len(key)!=32:raise ValueError('source key unavailable')
 receiver=Path(os.environ['BORA_RECEIVER']);public=json.loads((receiver/'receiver-public.json').read_text());wrapped=json.loads((receiver/'receiver-wrapped.json').read_text())
 source=json.loads((R/'source.sealed.json').read_text())
 text=json.loads(c.open_input(key,public,wrapped,source['record'],c.unb(source['cipher_b64']),'MS-001'))[idx];key=None;source=None;wrapped=None
 # Assets are installed before secret access. Native model never gets credentials or network.
 import socket
 def deny(*a,**k):raise RuntimeError('network disabled during synthesis')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
 import numpy as np,torch,soundfile as sf
 from qwen_tts import Qwen3TTSModel
 torch.set_num_threads(4);torch.manual_seed(cfg['seed']+idx)
 with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
  m=Qwen3TTSModel.from_pretrained(os.environ['BORA_MODEL'],device_map='cpu',dtype=torch.bfloat16,attn_implementation='sdpa',local_files_only=True,low_cpu_mem_usage=True)
  w,sr=m.generate_custom_voice(text=text,language='Korean',speaker=cfg['speaker'],max_new_tokens=850,do_sample=True,temperature=.8,top_p=.9)
 a=np.asarray(w[0]);seconds=len(a)/sr
 if not np.isfinite(a).all() or seconds<.3 or seconds>max(25,len(text)*.45+10) or float(np.sqrt(np.mean(a*a)))<.0001:raise ValueError('audio quality bounds')
 b=io.BytesIO();sf.write(b,a,sr,format='WAV');plain=b.getvalue()
 sender=X25519PrivateKey.generate();nonce=os.urandom(12);salt=os.urandom(32)
 k=HKDF(algorithm=hashes.SHA256(),length=32,salt=salt,info=b'kim-bora-one-lecture-v1').derive(sender.exchange(X25519PublicKey.from_public_bytes(c.unb(cfg['receiver_public']))))
 aad=f'kim-bora-part:{idx}'.encode();sealed=AESGCM(k).encrypt(nonce,plain,aad)
 out=Path('bora-cipher');out.mkdir(exist_ok=True)
 result=dict(index=idx,seconds=seconds,sample_rate=sr,sha256=hashlib.sha256(plain).hexdigest(),sender_public=c.b64(c.raw_public(sender.public_key())),salt=c.b64(salt),nonce=c.b64(nonce),cipher=c.b64(sealed))
 (out/f'part_{idx:03}.json').write_text(json.dumps(result));print(json.dumps(dict(index=idx,seconds=round(seconds,2),status='sealed')))
if __name__=='__main__':
 try:main()
 except Exception as e:print('BORA_RENDER_FAILED:'+type(e).__name__,file=sys.stderr);sys.exit(1)
