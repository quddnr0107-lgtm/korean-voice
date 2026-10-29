"""Throwaway-key end-to-end wrapper test with mocked synthesis. No operational key,
ONNX import, actual speech, GitHub call, or external connection is involved.
"""
import base64,copy,json,os,shutil,socket,sys,tempfile,time,types,unittest,zlib
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import lecture_crypto as c,render_sealed as r,delivery
from test_render_contract import Contract
class SyntheticWrapper(unittest.TestCase):
 def test_protected_pack_and_authenticated_reuse(self):
  source=b'S'*32;media=b'M'*32;sk=X25519PrivateKey.from_private_bytes(bytes(range(1,33)));pub=c.raw_public(sk.public_key());public=dict(c.PINNED_PUBLIC,public_key_b64=c.b64(pub),public_sha256=c.digest(pub))
  salt=b'A'*32;nonce=b'B'*12;raw=sk.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption());wrapped={'public':public,'salt_b64':c.b64(salt),'nonce_b64':c.b64(nonce),'sealed_private_b64':c.b64(AESGCM(c._derive(source,salt,c.LEGACY_INGRESS_VERSION,'receiver-wrap')).encrypt(nonce,raw,c.enc(public)))}
  payload=Contract().payload();recipe=payload['recipe'];counter=[]
  with tempfile.TemporaryDirectory() as td,patch.object(c,'PINNED_PUBLIC',public):
   root=Path(td);receiver=root/'receiver';out=root/'out';cp=root/'cp';inputs=root/'inputs';assets=root/'assets'
   for p in [receiver,out,cp,inputs,assets]:p.mkdir()
   (root/'recipe.json').write_text(json.dumps(recipe));(root/'model-pins.json').write_text(json.dumps({'sha256':{}}));(receiver/'receiver-public.json').write_text(json.dumps(public));(receiver/'receiver-wrapped.json').write_text(json.dumps(wrapped))
   record,cipher=c.seal_input(public,'MS-084',zlib.compress(c.enc(payload)));(inputs/'MS-084.sealed.json').write_text(json.dumps({'record':record,'cipher_b64':c.b64(cipher)}))
   def fake_render(lesson):
    counter.append(1);work=Path(os.environ['LECTURE_WORKDIR']);plain=b'SYNTHETIC-ONLY-NOT-MP3';(work/'audio/MS-084.part-0000.mp3').write_bytes(plain)
    meta={'id':'MS-084','title':'synthetic','validation':{'duration_s':1,'finite':True,'clipped_samples':0,'max_silence_s':0,'rms':.1},'audio_parts':[{'index':0,'start_sample':0,'end_sample':44100,'sample_rate':44100,'file_name':'MS-084.part-0000.mp3','audio_sha256':c.digest(plain)}],'segments':lesson['chunks'],'wall_s':0}
    (work/'metadata/MS-084.json').write_text(json.dumps(meta));return {}
   fake=types.SimpleNamespace(RECIPE=recipe,init_worker=lambda:None,render_lesson=fake_render)
   env={'GITHUB_REPOSITORY':c.REPO,'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/integrated-lectures-premium-20261028','GITHUB_WORKFLOW_REF':c.REPO+'/.github/workflows/military-service-b-premium.yml@refs/heads/integrated-lectures-premium-20261028','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','LECTURE_JOB_STARTED_AT':str(int(time.time())),'LECTURE_RECEIVER_DIR':str(receiver),'LECTURE_OUTPUT':str(out),'LECTURE_CHECKPOINT_DIR':str(cp),'LECTURE_MODEL_ROOT':str(assets),'RUNNER_TEMP':str(root),'ADMIN_LAW_PREMIUM_SOURCE_KEY_B64':c.b64(source),'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64':c.b64(media)}
   with patch.object(r,'ROOT',root),patch.dict(os.environ,env),patch.dict(sys.modules,{'audio_engine':fake}),patch.object(sys,'argv',['render_sealed.py','MS-084']),patch('preflight.require_approval',return_value={}),patch.object(r.signal,'signal'),patch.object(socket.socket,'connect',socket.socket.connect),patch.object(socket.socket,'connect_ex',socket.socket.connect_ex),patch.object(socket,'create_connection',socket.create_connection):
    r.main();kind,files=delivery.output_gate(out,'MS-084');self.assertEqual(kind,'complete');self.assertEqual(len(files),3)
    for p in out.iterdir():shutil.move(str(p),cp/p.name)
    os.environ['ADMIN_LAW_PREMIUM_SOURCE_KEY_B64']=c.b64(source);os.environ['ADMIN_LAW_PREMIUM_MEDIA_KEY_B64']=c.b64(media)
    r.main();self.assertEqual(counter,[1]);self.assertEqual(list(out.iterdir()),[])
if __name__=='__main__':unittest.main()
